from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import socket
import threading
import time
from typing import Any, Sequence

try:
    import psutil
except ModuleNotFoundError as exc:  # Telemetry must never prevent trading startup.
    psutil = None  # type: ignore[assignment]
    _PSUTIL_IMPORT_ERROR: BaseException | None = exc
else:
    _PSUTIL_IMPORT_ERROR = None


SCHEMA_VERSION = "trading-bot-phase-resources-v1"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _cpu_seconds(process: psutil.Process) -> float:
    observed = process.cpu_times()
    return float(observed.user + observed.system)


def _io_bytes(process: psutil.Process) -> tuple[int, int]:
    observed = process.io_counters()
    return int(observed.read_bytes), int(observed.write_bytes)


def _memory_bytes(process: psutil.Process) -> tuple[int, int]:
    observed = process.memory_info()
    return int(observed.rss), int(getattr(observed, "private", observed.vms))


def _counter_delta(after: Any, before: Any, field: str) -> int | None:
    if after is None or before is None:
        return None
    return max(0, int(getattr(after, field)) - int(getattr(before, field)))


class PhaseResourceMonitor:
    """Sample one subprocess tree without changing its stdout or exit semantics."""

    def __init__(
        self,
        *,
        phase: str,
        command: Sequence[str],
        log_path: Path,
        sample_seconds: float = 0.5,
    ) -> None:
        if psutil is None:
            raise RuntimeError(
                "performance monitoring requires the optional psutil dependency"
            ) from _PSUTIL_IMPORT_ERROR
        if sample_seconds <= 0:
            raise ValueError("sample_seconds must be positive")
        self.phase = phase
        self.command = tuple(str(item) for item in command)
        self.log_path = Path(log_path)
        self.sample_seconds = float(sample_seconds)
        self.started_at_utc = utc_now_iso()
        self.started_monotonic = time.monotonic()
        self.root_pid: int | None = None
        self._root: psutil.Process | None = None
        self._known: dict[int, tuple[psutil.Process, float]] = {}
        self._cpu_max: dict[int, float] = {}
        self._read_max: dict[int, int] = {}
        self._write_max: dict[int, int] = {}
        self._peak_rss = 0
        self._peak_private = 0
        self._peak_process_count = 0
        self._peak_core_equivalents = 0.0
        self._peak_cpu_timestamp_utc: str | None = None
        self._peak_memory_timestamp_utc: str | None = None
        self._last_cpu_total = 0.0
        self._last_sample_monotonic = self.started_monotonic
        self._sample_count = 0
        self._system_cpu_samples: list[float] = []
        self._system_total_memory = int(psutil.virtual_memory().total)
        self._system_min_available_memory = int(psutil.virtual_memory().available)
        self._system_peak_used_memory = self._system_total_memory - self._system_min_available_memory
        self._disk_start = psutil.disk_io_counters()
        self._net_start = psutil.net_io_counters()
        self._tws_first: dict[tuple[int, float], tuple[float, int, int]] = {}
        self._tws_cpu_max: dict[tuple[int, float], float] = {}
        self._tws_read_max: dict[tuple[int, float], int] = {}
        self._tws_write_max: dict[tuple[int, float], int] = {}
        self._tws_peak_rss = 0
        self._tws_peak_private = 0
        self._errors: list[str] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def attach(self, root_pid: int) -> None:
        self.root_pid = int(root_pid)
        self._root = psutil.Process(self.root_pid)
        self._remember(self._root)
        psutil.cpu_percent(interval=None)
        self._sample()
        self._thread = threading.Thread(
            target=self._sample_loop,
            name=f"resource-monitor-{self.phase}",
            daemon=True,
        )
        self._thread.start()

    def _remember(self, process: psutil.Process) -> None:
        try:
            created = float(process.create_time())
        except (psutil.Error, OSError):
            return
        observed = self._known.get(process.pid)
        if observed is None or observed[1] == created:
            self._known[process.pid] = (process, created)

    def _discover_tree(self) -> None:
        candidates = [item[0] for item in self._known.values()]
        if self._root is not None:
            candidates.append(self._root)
        for process in candidates:
            try:
                for child in process.children(recursive=True):
                    self._remember(child)
            except (psutil.Error, OSError):
                continue

    def _live_tree(self) -> list[psutil.Process]:
        self._discover_tree()
        live: list[psutil.Process] = []
        for pid, (process, created) in tuple(self._known.items()):
            try:
                if process.is_running() and float(process.create_time()) == created:
                    live.append(process)
            except (psutil.Error, OSError):
                continue
        return live

    def _sample_tree(self, sampled_at: str, now_monotonic: float) -> None:
        current_rss = 0
        current_private = 0
        live_tree = self._live_tree()
        for process in live_tree:
            try:
                cpu = _cpu_seconds(process)
                read_bytes, write_bytes = _io_bytes(process)
                rss, private = _memory_bytes(process)
            except (psutil.Error, OSError):
                continue
            self._cpu_max[process.pid] = max(cpu, self._cpu_max.get(process.pid, 0.0))
            self._read_max[process.pid] = max(read_bytes, self._read_max.get(process.pid, 0))
            self._write_max[process.pid] = max(write_bytes, self._write_max.get(process.pid, 0))
            current_rss += rss
            current_private += private
        current_count = len(live_tree)
        self._peak_process_count = max(self._peak_process_count, current_count)
        if current_rss > self._peak_rss or current_private > self._peak_private:
            self._peak_memory_timestamp_utc = sampled_at
        self._peak_rss = max(self._peak_rss, current_rss)
        self._peak_private = max(self._peak_private, current_private)
        cpu_total = sum(self._cpu_max.values())
        elapsed = now_monotonic - self._last_sample_monotonic
        if elapsed > 0:
            cores = max(0.0, (cpu_total - self._last_cpu_total) / elapsed)
            if cores > self._peak_core_equivalents:
                self._peak_core_equivalents = cores
                self._peak_cpu_timestamp_utc = sampled_at
        self._last_cpu_total = cpu_total
        self._last_sample_monotonic = now_monotonic

    def _sample_tws(self) -> None:
        current_rss = 0
        current_private = 0
        for process in psutil.process_iter(("pid", "name", "create_time")):
            try:
                if str(process.info.get("name") or "").lower() != "tws.exe":
                    continue
                key = (int(process.pid), float(process.info["create_time"]))
                cpu = _cpu_seconds(process)
                read_bytes, write_bytes = _io_bytes(process)
                rss, private = _memory_bytes(process)
            except (psutil.Error, OSError, TypeError, ValueError):
                continue
            self._tws_first.setdefault(key, (cpu, read_bytes, write_bytes))
            self._tws_cpu_max[key] = max(cpu, self._tws_cpu_max.get(key, cpu))
            self._tws_read_max[key] = max(read_bytes, self._tws_read_max.get(key, read_bytes))
            self._tws_write_max[key] = max(write_bytes, self._tws_write_max.get(key, write_bytes))
            current_rss += rss
            current_private += private
        self._tws_peak_rss = max(self._tws_peak_rss, current_rss)
        self._tws_peak_private = max(self._tws_peak_private, current_private)

    def _sample(self) -> None:
        sampled_at = utc_now_iso()
        now_monotonic = time.monotonic()
        try:
            self._sample_tree(sampled_at, now_monotonic)
            memory = psutil.virtual_memory()
            self._system_min_available_memory = min(
                self._system_min_available_memory, int(memory.available)
            )
            self._system_peak_used_memory = max(
                self._system_peak_used_memory, int(memory.total - memory.available)
            )
            self._system_cpu_samples.append(float(psutil.cpu_percent(interval=None)))
            self._sample_tws()
            self._sample_count += 1
        except BaseException as exc:
            self._errors.append(f"{type(exc).__name__}: {exc}")

    def _sample_loop(self) -> None:
        while not self._stop.wait(self.sample_seconds):
            self._sample()

    def finish(self, return_code: int) -> dict[str, Any]:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(2.0, self.sample_seconds * 4.0))
        self._sample()
        completed_at = utc_now_iso()
        wall_seconds = max(0.0, time.monotonic() - self.started_monotonic)
        cpu_seconds = sum(self._cpu_max.values())
        logical_cpus = int(psutil.cpu_count(logical=True) or os.cpu_count() or 1)
        physical_cpus = psutil.cpu_count(logical=False)
        disk_end = psutil.disk_io_counters()
        net_end = psutil.net_io_counters()
        tws_cpu_seconds = 0.0
        tws_read_bytes = 0
        tws_write_bytes = 0
        for key, (first_cpu, first_read, first_write) in self._tws_first.items():
            tws_cpu_seconds += max(0.0, self._tws_cpu_max.get(key, first_cpu) - first_cpu)
            tws_read_bytes += max(0, self._tws_read_max.get(key, first_read) - first_read)
            tws_write_bytes += max(0, self._tws_write_max.get(key, first_write) - first_write)
        average_cores = cpu_seconds / wall_seconds if wall_seconds > 0 else 0.0
        summary: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "event": "phase_resource_summary",
            "phase": self.phase,
            "started_at_utc": self.started_at_utc,
            "completed_at_utc": completed_at,
            "wall_seconds": round(wall_seconds, 3),
            "return_code": int(return_code),
            "command": list(self.command),
            "root_pid": self.root_pid,
            "host": socket.gethostname(),
            "platform": platform.platform(),
            "logical_cpu_count": logical_cpus,
            "physical_cpu_count": int(physical_cpus) if physical_cpus is not None else None,
            "sample_seconds": self.sample_seconds,
            "sample_count": self._sample_count,
            "process_tree": {
                "total_processes_observed": len(self._known),
                "peak_process_count": self._peak_process_count,
                "cpu_seconds": round(cpu_seconds, 3),
                "average_core_equivalents": round(average_cores, 3),
                "average_cpu_percent_of_machine": round(average_cores / logical_cpus * 100.0, 3),
                "peak_core_equivalents": round(self._peak_core_equivalents, 3),
                "peak_cpu_percent_of_machine": round(self._peak_core_equivalents / logical_cpus * 100.0, 3),
                "peak_cpu_at_utc": self._peak_cpu_timestamp_utc,
                "peak_working_set_bytes": self._peak_rss,
                "peak_private_bytes": self._peak_private,
                "peak_memory_at_utc": self._peak_memory_timestamp_utc,
                "read_bytes": sum(self._read_max.values()),
                "write_bytes": sum(self._write_max.values()),
            },
            "system": {
                "total_memory_bytes": self._system_total_memory,
                "minimum_available_memory_bytes": self._system_min_available_memory,
                "peak_used_memory_bytes": self._system_peak_used_memory,
                "average_cpu_percent": round(
                    sum(self._system_cpu_samples) / len(self._system_cpu_samples), 3
                ) if self._system_cpu_samples else None,
                "peak_cpu_percent": round(max(self._system_cpu_samples), 3) if self._system_cpu_samples else None,
                "disk_read_bytes": _counter_delta(disk_end, self._disk_start, "read_bytes"),
                "disk_write_bytes": _counter_delta(disk_end, self._disk_start, "write_bytes"),
                "network_sent_bytes": _counter_delta(net_end, self._net_start, "bytes_sent"),
                "network_received_bytes": _counter_delta(net_end, self._net_start, "bytes_recv"),
            },
            "tws": {
                "processes_observed": len(self._tws_first),
                "cpu_seconds": round(tws_cpu_seconds, 3),
                "peak_working_set_bytes": self._tws_peak_rss,
                "peak_private_bytes": self._tws_peak_private,
                "read_bytes": tws_read_bytes,
                "write_bytes": tws_write_bytes,
            },
            "monitor_errors": self._errors,
        }
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(summary, ensure_ascii=False, sort_keys=True) + "\n")
        return summary
