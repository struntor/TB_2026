from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
import json
import msvcrt
from pathlib import Path
import subprocess
import sys
import time as time_module
from typing import Callable, Iterable, Sequence
from zoneinfo import ZoneInfo

import pandas as pd

from src.bot.resource_monitor import PhaseResourceMonitor


ET = ZoneInfo("America/New_York")
DEFAULT_PROJECTS_ROOT = Path(r"D:\PycharmProjects")
DEFAULT_TRADING_ROOT = Path(r"D:\Trading")
DEFAULT_STATE_PATH = DEFAULT_TRADING_ROOT / "journal" / "trading_bot_24_7_state.json"
DEFAULT_LOCK_PATH = DEFAULT_TRADING_ROOT / "journal" / "trading_bot_24_7.lock"
DEFAULT_LOG_PATH = DEFAULT_TRADING_ROOT / "logs" / "trading_bot_24_7.log"
DEFAULT_PERFORMANCE_LOG_PATH = DEFAULT_TRADING_ROOT / "logs" / "trading_bot_performance.jsonl"


def _console_text(value: str) -> str:
    """Return text that is safe for the active Windows console encoding."""
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    return value.encode(encoding, errors="replace").decode(encoding)


def _nth_weekday(year: int, month: int, weekday: int, occurrence: int) -> date:
    current = date(year, month, 1)
    offset = (weekday - current.weekday()) % 7
    return current + timedelta(days=offset + 7 * (occurrence - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    if month == 12:
        current = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        current = date(year, month + 1, 1) - timedelta(days=1)
    return current - timedelta(days=(current.weekday() - weekday) % 7)


def _observed_fixed(day: date) -> date:
    if day.weekday() == 5:
        return day - timedelta(days=1)
    if day.weekday() == 6:
        return day + timedelta(days=1)
    return day


def _easter_sunday(year: int) -> date:
    """Gregorian Easter (Anonymous Gregorian algorithm)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = (h + l - 7 * m + 114) % 31 + 1
    return date(year, month, day)


def nyse_holidays(year: int) -> set[date]:
    holidays = {
        _observed_fixed(date(year, 1, 1)),
        _nth_weekday(year, 1, 0, 3),  # Martin Luther King Jr. Day
        _nth_weekday(year, 2, 0, 3),  # Washington's Birthday
        _easter_sunday(year) - timedelta(days=2),
        _last_weekday(year, 5, 0),
        _observed_fixed(date(year, 6, 19)),
        _observed_fixed(date(year, 7, 4)),
        _nth_weekday(year, 9, 0, 1),
        _nth_weekday(year, 11, 3, 4),
        _observed_fixed(date(year, 12, 25)),
    }
    # New Year's Day can be observed on 31 December of the preceding year.
    next_new_year = _observed_fixed(date(year + 1, 1, 1))
    if next_new_year.year == year:
        holidays.add(next_new_year)
    return {item for item in holidays if item.year == year}


def is_us_equity_session(day: date) -> bool:
    return day.weekday() < 5 and day not in nyse_holidays(day.year)


def previous_us_equity_session(day: date) -> date:
    candidate = day - timedelta(days=1)
    while not is_us_equity_session(candidate):
        candidate -= timedelta(days=1)
    return candidate


def next_us_equity_session_on_or_after(day: date) -> date:
    candidate = day
    while not is_us_equity_session(candidate):
        candidate += timedelta(days=1)
    return candidate


def parse_clock(value: str) -> time:
    try:
        parsed = datetime.strptime(value, "%H:%M").time()
    except ValueError as exc:
        raise ValueError(f"invalid HH:MM time: {value!r}") from exc
    return parsed


@dataclass(frozen=True, slots=True)
class OrchestratorConfig:
    python_executable: Path
    projects_root: Path
    trading_root: Path
    journal_path: Path
    state_path: Path
    lock_path: Path
    log_path: Path
    performance_log_path: Path
    host: str
    port: int
    sync_client_id: int
    live_client_id: int
    workers: int
    prepare_et: time
    latest_launch_et: time
    retry_seconds: float
    risk_per_trade: float
    place_orders: bool
    allow_live_account: bool
    account: str | None
    enable_failed_auction_fly: bool
    enable_spxw_15dte_put_credit: bool
    enable_spxw_0dte_bull_put_iu30: bool
    enable_spxw_75dte_put_bwb: bool
    enable_stock_ivr70_put_credit: bool
    spxw_75dte_bwb_initial_entry_work_minutes: int
    spxw_75dte_bwb_entry_work_minutes: int
    spxw_75dte_bwb_final_entry_work_minutes: int
    spxw_75dte_bwb_max_risk_usd: float
    options_account_value_usd: float
    options_max_risk_fraction: float
    enable_xsp_vix_pullback: bool = False
    xsp_vix_pullback_entry_work_minutes: int = 10
    run_journal_sync: bool = True
    run_data_download: bool = True
    run_backtest: bool = True
    run_live_trading: bool = True
    performance_sample_seconds: float = 0.5
    dual_broker: bool = False
    production_host: str | None = None
    production_port: int | None = None
    production_sync_client_id: int | None = None
    production_live_client_id: int | None = None
    production_account: str | None = None
    production_place_orders: bool = False

    @property
    def bot_root(self) -> Path:
        return self.projects_root / "IBKR_TradingBot"

    @property
    def engine_root(self) -> Path:
        return (
            self.projects_root
            / "PythonProject"
            / "statistic"
            / "spx_adx_di_5min_2024_02_03"
        )

    @property
    def downloader_path(self) -> Path:
        return self.projects_root / "PythonProject" / "Daten_Download" / "Download_Asset_Data.py"


class SingleInstanceLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._handle = None

    def __enter__(self) -> "SingleInstanceLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self.path.open("a+b")
        self._handle.seek(0)
        if self._handle.read(1) == b"":
            self._handle.seek(0)
            self._handle.write(b"0")
            self._handle.flush()
        self._handle.seek(0)
        try:
            msvcrt.locking(self._handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            self._handle.close()
            self._handle = None
            raise RuntimeError("another 24/7 trading-bot orchestrator is already running") from exc
        return self

    def __exit__(self, *_: object) -> None:
        if self._handle is not None:
            self._handle.seek(0)
            msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
            self._handle.close()
            self._handle = None


class StateStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> dict[str, object]:
        if not self.path.exists():
            return {"schema_version": 1, "cycles": {}}
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if payload.get("schema_version") != 1 or not isinstance(payload.get("cycles"), dict):
            raise RuntimeError(f"invalid orchestrator state: {self.path}")
        return payload

    def save(self, payload: dict[str, object]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(self.path.suffix + ".tmp")
        temp.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        temp.replace(self.path)

    def phase_status(self, session_day: date, phase: str) -> str | None:
        payload = self.load()
        cycle = payload["cycles"].get(session_day.isoformat(), {})
        return cycle.get("phases", {}).get(phase, {}).get("status")

    def mark(
        self,
        session_day: date,
        phase: str,
        status: str,
        *,
        details: dict[str, object] | None = None,
    ) -> None:
        payload = self.load()
        cycles = payload["cycles"]
        cycle = cycles.setdefault(
            session_day.isoformat(),
            {
                "session_day": session_day.isoformat(),
                "previous_session": previous_us_equity_session(session_day).isoformat(),
                "phases": {},
            },
        )
        cycle["phases"][phase] = {
            "status": status,
            "updated_at_utc": datetime.now(tz=ZoneInfo("UTC")).isoformat().replace("+00:00", "Z"),
            "details": details or {},
        }
        cutoff = session_day - timedelta(days=45)
        payload["cycles"] = {
            key: value for key, value in cycles.items() if date.fromisoformat(key) >= cutoff
        }
        self.save(payload)


class DailyTradingOrchestrator:
    PHASES = ("journal_sync", "raw_data", "backtest", "live_trading")

    def __init__(
        self,
        config: OrchestratorConfig,
        *,
        now: Callable[[], datetime] | None = None,
        sleeper: Callable[[float], None] | None = None,
    ) -> None:
        self.config = config
        self.state = StateStore(config.state_path)
        self.now = now or (lambda: datetime.now(tz=ET))
        self.sleeper = sleeper or time_module.sleep

    def log(self, message: str) -> None:
        stamp = datetime.now(tz=ET).strftime("%Y-%m-%d %H:%M:%S %Z")
        line = f"[{stamp}] {message}"
        print(_console_text(line), flush=True)
        self.config.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.config.log_path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    def _run_command(self, phase: str, command: Sequence[str], cwd: Path) -> None:
        self.log(f"{phase}: starting {' '.join(command)}")
        process = subprocess.Popen(
            list(command),
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        monitor: PhaseResourceMonitor | None = None
        try:
            monitor = PhaseResourceMonitor(
                phase=phase,
                command=command,
                log_path=self.config.performance_log_path,
                sample_seconds=self.config.performance_sample_seconds,
            )
            monitor.attach(process.pid)
        except BaseException as exc:
            monitor = None
            self.log(
                f"{phase}: performance monitor unavailable; "
                f"continuing without metrics ({type(exc).__name__}: {exc})"
            )
        assert process.stdout is not None
        try:
            with self.config.log_path.open("a", encoding="utf-8") as log_handle:
                for line in process.stdout:
                    print(_console_text(line), end="", flush=True)
                    log_handle.write(line)
                    log_handle.flush()
            return_code = process.wait()
        finally:
            if process.poll() is None:
                process.wait()
            return_code = int(process.returncode or 0)
            if monitor is not None:
                try:
                    performance = monitor.finish(return_code)
                    tree = performance["process_tree"]
                    self.log(
                        f"{phase}: resources wall={performance['wall_seconds']:.1f}s "
                        f"peak_private={tree['peak_private_bytes'] / (1024 ** 3):.2f}GiB "
                        f"avg_cores={tree['average_core_equivalents']:.2f} "
                        f"peak_processes={tree['peak_process_count']}"
                    )
                except BaseException as exc:
                    self.log(
                        f"{phase}: performance log failed; subprocess result is unchanged "
                        f"({type(exc).__name__}: {exc})"
                    )
        if return_code != 0:
            raise RuntimeError(f"{phase} failed with exit code {return_code}")
        self.log(f"{phase}: completed")

    def journal_sync_command(
        self,
        session_day: date,
        *,
        production: bool = False,
    ) -> list[str]:
        previous = previous_us_equity_session(session_day)
        if production:
            if not self.config.dual_broker:
                raise ValueError("production journal sync requires dual-broker mode")
            host = self.config.production_host
            port = self.config.production_port
            client_id = self.config.production_sync_client_id
            account = self.config.production_account
            if host is None or port is None or client_id is None or account is None:
                raise ValueError("production broker configuration is incomplete")
        else:
            host = self.config.host
            port = self.config.port
            client_id = self.config.sync_client_id
            account = self.config.account
        command = [
            str(self.config.python_executable), "-u", "-m", "src.journal.tws_sync",
            "--host", host,
            "--port", str(port),
            "--client-id", str(client_id),
            "--journal-path", str(self.config.journal_path),
            "--execution-start-date", previous.isoformat(),
            "--sync-day", session_day.isoformat(),
        ]
        if account:
            command.extend(("--account", account))
        if production or self.config.allow_live_account:
            command.append("--allow-live-account")
        return command

    def downloader_command(self) -> list[str]:
        return [str(self.config.python_executable), "-u", str(self.config.downloader_path)]

    def backtest_command(self, session_day: date) -> list[str]:
        return [
            str(self.config.python_executable), "-u",
            str(self.config.bot_root / "scripts" / "run_manual_regime_baseline_v1.py"),
            "--workers", str(self.config.workers),
            "--database", str(self.config.journal_path),
            "--end-exclusive", session_day.isoformat(),
        ]

    def _backtest_manifest(self, session_day: date) -> Path:
        end_inclusive = session_day - timedelta(days=1)
        return (
            self.config.trading_root
            / "journal"
            / "baselines"
            / "manual-regime-baseline@1.0.0"
            / f"2024-01-01_{end_inclusive:%Y-%m-%d}"
            / "run_manifest.json"
        )

    def backtest_run_id(self, session_day: date) -> str:
        manifest_path = self._backtest_manifest(session_day)
        if not manifest_path.exists():
            raise RuntimeError(f"backtest manifest missing: {manifest_path}")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        run_id = str(manifest.get("run_id") or "")
        if not run_id:
            raise RuntimeError(f"backtest manifest has no run_id: {manifest_path}")
        return run_id

    def live_command(
        self,
        session_day: date,
        model_history_run_id: str,
        *,
        enable_production: bool | None = None,
    ) -> list[str]:
        command = [
            str(self.config.python_executable), "-u",
            str(self.config.engine_root / "live_manual_regime_tws_bot.py"),
            "--day", session_day.isoformat(),
            "--start-local", "00:00",
            "--decision-fetch-et", "09:40",
            "--risk-per-trade", str(self.config.risk_per_trade),
            "--host", self.config.host,
            "--port", str(self.config.port),
            "--client-id", str(self.config.live_client_id),
            "--max-workers", str(self.config.workers),
            "--journal-path", str(self.config.journal_path),
            "--model-history-run-id", model_history_run_id,
            "--options-account-value-usd", str(self.config.options_account_value_usd),
            "--options-max-risk-fraction", str(self.config.options_max_risk_fraction),
            "--spxw-75dte-bwb-initial-entry-work-minutes", str(self.config.spxw_75dte_bwb_initial_entry_work_minutes),
            "--spxw-75dte-bwb-entry-work-minutes", str(self.config.spxw_75dte_bwb_entry_work_minutes),
            "--spxw-75dte-bwb-final-entry-work-minutes", str(self.config.spxw_75dte_bwb_final_entry_work_minutes),
            "--spxw-75dte-bwb-max-risk-usd", str(self.config.spxw_75dte_bwb_max_risk_usd),
            "--xsp-vix-pullback-entry-work-minutes", str(self.config.xsp_vix_pullback_entry_work_minutes),
        ]
        if self.config.account:
            command.extend(("--account", self.config.account))
        if self.config.place_orders:
            command.append("--place-orders")
        if self.config.allow_live_account:
            command.append("--allow-live-account")
        if self.config.enable_failed_auction_fly:
            command.append("--enable-failed-auction-fly")
        if self.config.enable_spxw_15dte_put_credit:
            command.append("--enable-spxw-15dte-put-credit")
        if self.config.enable_spxw_0dte_bull_put_iu30:
            command.append("--enable-spxw-0dte-bull-put-iu30")
        if self.config.enable_spxw_75dte_put_bwb:
            command.append("--enable-spxw-75dte-put-bwb")
        if self.config.enable_xsp_vix_pullback:
            command.append("--enable-xsp-vix-pullback")
        if self.config.enable_stock_ivr70_put_credit:
            command.append("--enable-stock-ivr70-put-credit")
        if self.config.place_orders and not self.config.allow_live_account:
            command.append("--enable-lhf-eod-profit-protection")
        production_enabled = self.config.dual_broker and (
            True if enable_production is None else bool(enable_production)
        )
        if production_enabled:
            required = {
                "production_host": self.config.production_host,
                "production_port": self.config.production_port,
                "production_live_client_id": self.config.production_live_client_id,
                "production_account": self.config.production_account,
            }
            missing = [key for key, value in required.items() if value is None]
            if missing:
                raise ValueError(
                    "production broker configuration is incomplete: " + ", ".join(missing)
                )
            command.extend(
                (
                    "--dual-broker",
                    "--production-host", str(self.config.production_host),
                    "--production-port", str(self.config.production_port),
                    "--production-client-id", str(self.config.production_live_client_id),
                    "--production-account", str(self.config.production_account),
                )
            )
            if self.config.production_place_orders:
                command.append("--production-place-orders")
        return command

    def validate_raw_data(self, session_day: date) -> dict[str, object]:
        previous = previous_us_equity_session(session_day)
        sys.path.insert(0, str(self.config.engine_root))
        import current_live_baseline_config as live_config

        symbols = live_config.load_optimized_live_top50_symbols()
        failures: list[str] = []
        latest: dict[str, str] = {}
        raw_dir = self.config.trading_root / "Rohdaten"
        for symbol in symbols:
            path = raw_dir / f"Rohdaten_{symbol}.csv"
            if not path.exists():
                failures.append(f"{symbol}: missing file")
                continue
            frame = pd.read_csv(path, usecols=["date"])
            timestamps = pd.to_datetime(frame["date"], errors="coerce").dropna()
            if timestamps.empty:
                failures.append(f"{symbol}: no valid timestamps")
                continue
            last_day = timestamps.max().date()
            latest[symbol] = last_day.isoformat()
            if last_day < previous:
                failures.append(
                    f"{symbol}: latest={last_day.isoformat()} expected>={previous.isoformat()}"
                )
                continue
            session_bar_count = int((timestamps.dt.date == previous).sum())
            if session_bar_count < 70:
                failures.append(
                    f"{symbol}: only {session_bar_count} bars for {previous.isoformat()}"
                )
        if failures:
            raise RuntimeError("raw-data validation failed: " + "; ".join(failures))
        return {"symbol_count": len(symbols), "required_session": previous.isoformat()}

    def _phase(
        self,
        session_day: date,
        name: str,
        action: Callable[[], dict[str, object] | None],
    ) -> None:
        if self.state.phase_status(session_day, name) == "completed":
            self.log(f"{name}: already completed for {session_day}; skipping")
            return
        self.state.mark(session_day, name, "running")
        try:
            details = action() or {}
        except BaseException as exc:
            self.state.mark(
                session_day,
                name,
                "failed",
                details={"error": f"{type(exc).__name__}: {exc}"},
            )
            raise
        self.state.mark(session_day, name, "completed", details=details)

    def run_cycle(self, session_day: date) -> None:
        if not is_us_equity_session(session_day):
            raise ValueError(f"not a US equity session: {session_day}")
        self.log(
            f"cycle {session_day}: previous_session={previous_us_equity_session(session_day)}"
        )
        production_sync_ready = not self.config.dual_broker
        if self.config.run_journal_sync:
            self._phase(
                session_day,
                "journal_sync",
                lambda: self._command_action(
                    "journal_sync", self.journal_sync_command(session_day), self.config.bot_root
                ),
            )
            if self.config.dual_broker:
                self._optional_phase(
                    session_day,
                    "journal_sync_production",
                    lambda: self._command_action(
                        "journal_sync_production",
                        self.journal_sync_command(session_day, production=True),
                        self.config.bot_root,
                    ),
                )
                production_sync_ready = (
                    self.state.phase_status(session_day, "journal_sync_production")
                    == "completed"
                )
        elif self.config.dual_broker:
            production_sync_ready = False
            self.log(
                "journal_sync_production: skipped by configuration; production "
                "routing is disabled for this cycle"
            )
        if self.config.run_data_download:
            self._phase(
                session_day,
                "raw_data",
                lambda: self._raw_data_action(session_day),
            )
        else:
            self.validate_raw_data(session_day)
        if self.config.run_backtest:
            self._phase(
                session_day,
                "backtest",
                lambda: self._backtest_action(session_day),
            )
        model_run_id = self.backtest_run_id(session_day)
        if not self.config.run_live_trading:
            self.log(
                f"live_trading: skipped for preparation-only cycle {session_day}; "
                f"model_history_run_id={model_run_id}"
            )
            return
        self.ensure_live_launch_allowed(session_day)
        self._phase(
            session_day,
            "live_trading",
            lambda: self._command_action(
                "live_trading",
                self.live_command(
                    session_day,
                    model_run_id,
                    enable_production=production_sync_ready,
                ),
                self.config.engine_root,
                details={"model_history_run_id": model_run_id},
            ),
        )

    def ensure_live_launch_allowed(self, session_day: date) -> None:
        now_et = self.now().astimezone(ET)
        if (
            now_et.date() == session_day
            and now_et.time() > self.config.latest_launch_et
            and self.state.phase_status(session_day, "live_trading") != "completed"
        ):
            raise RuntimeError(
                f"live launch cutoff {self.config.latest_launch_et:%H:%M} ET passed; "
                "late stock entries are prohibited"
            )

    def _command_action(
        self,
        phase: str,
        command: Sequence[str],
        cwd: Path,
        *,
        details: dict[str, object] | None = None,
    ) -> dict[str, object]:
        self._run_command(phase, command, cwd)
        return details or {}

    def _optional_phase(
        self,
        session_day: date,
        name: str,
        action: Callable[[], dict[str, object] | None],
    ) -> None:
        """Run a broker-specific phase without blocking the independent account.

        Production TWS is deliberately optional: an unavailable or mismatched live
        endpoint must disable live-account work, but must not stop the paper pipeline.
        """
        if self.state.phase_status(session_day, name) == "completed":
            self.log(f"{name}: already completed for {session_day}; skipping")
            return
        self.state.mark(session_day, name, "running")
        try:
            details = action() or {}
        except BaseException as exc:
            error = f"{type(exc).__name__}: {exc}"
            self.state.mark(
                session_day,
                name,
                "unavailable",
                details={"error": error, "fail_closed": True},
            )
            self.log(f"{name}: unavailable; production disabled for this cycle ({error})")
            return
        self.state.mark(session_day, name, "completed", details=details)

    def _raw_data_action(self, session_day: date) -> dict[str, object]:
        self._run_command("raw_data", self.downloader_command(), self.config.downloader_path.parent)
        return self.validate_raw_data(session_day)

    def _backtest_action(self, session_day: date) -> dict[str, object]:
        self._run_command("backtest", self.backtest_command(session_day), self.config.bot_root)
        return {"model_history_run_id": self.backtest_run_id(session_day)}

    def run_forever(self) -> None:
        self.log("24/7 orchestrator started")
        while True:
            now_et = self.now().astimezone(ET)
            session_day = next_us_equity_session_on_or_after(now_et.date())
            if (
                session_day == now_et.date()
                and self.state.phase_status(session_day, "live_trading") == "completed"
            ):
                session_day = next_us_equity_session_on_or_after(
                    session_day + timedelta(days=1)
                )
            prepare_at = datetime.combine(session_day, self.config.prepare_et, tzinfo=ET)
            if now_et < prepare_at:
                wait_seconds = min((prepare_at - now_et).total_seconds(), 300.0)
                self.sleeper(max(1.0, wait_seconds))
                continue
            if now_et.date() == session_day and now_et.time() > self.config.latest_launch_et:
                if self.state.phase_status(session_day, "live_trading") != "completed":
                    next_session = next_us_equity_session_on_or_after(session_day + timedelta(days=1))
                    self.log(
                        f"cycle {session_day}: launch cutoff passed; no late entries, "
                        f"waiting for {next_session}"
                    )
                    wait_until = datetime.combine(next_session, self.config.prepare_et, tzinfo=ET)
                    self.sleeper(min(max(1.0, (wait_until - now_et).total_seconds()), 300.0))
                    continue
            try:
                self.run_cycle(session_day)
            except KeyboardInterrupt:
                raise
            except BaseException as exc:
                self.log(f"cycle {session_day}: {type(exc).__name__}: {exc}")
                self.sleeper(max(1.0, self.config.retry_seconds))
                continue
            next_session = next_us_equity_session_on_or_after(session_day + timedelta(days=1))
            self.log(f"cycle {session_day}: completed; next session {next_session}")
            self.sleeper(60.0)
