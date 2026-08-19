from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Sequence


BOT_ROOT = Path(__file__).resolve().parents[1]
if str(BOT_ROOT) not in sys.path:
    sys.path.insert(0, str(BOT_ROOT))

from src.bot.daily_orchestrator import ET, is_us_equity_session, previous_us_equity_session


DEFAULT_PROJECTS_ROOT = Path(r"D:\PycharmProjects")
DEFAULT_TRADING_ROOT = Path(r"D:\Trading")
VARIANT_ID = "manual-regime-baseline@1.0.0"


def session_days(start: date, end: date) -> list[date]:
    if start > end:
        raise ValueError("start date must not be after end date")
    return [
        start + timedelta(days=offset)
        for offset in range((end - start).days + 1)
        if is_us_equity_session(start + timedelta(days=offset))
    ]


def default_window(today: date) -> tuple[date, date]:
    end = previous_us_equity_session(today)
    return end - timedelta(days=6), end


def manifest_path(trading_root: Path, end_inclusive: date) -> Path:
    return (
        trading_root
        / "journal"
        / "baselines"
        / VARIANT_ID
        / f"2024-01-01_{end_inclusive:%Y-%m-%d}"
        / "run_manifest.json"
    )


def has_completed_manifest(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return bool(payload.get("run_id"))


def missing_sessions(trading_root: Path, start: date, end: date) -> list[date]:
    return [day for day in session_days(start, end) if not has_completed_manifest(manifest_path(trading_root, day))]


def run_streamed(command: Sequence[str], cwd: Path, log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment["PYTHONIOENCODING"] = "utf-8"
    with log_path.open("a", encoding="utf-8") as log_handle:
        process = subprocess.Popen(
            list(command),
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=environment,
        )
        assert process.stdout is not None
        for line in process.stdout:
            console_encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
            print(line.encode(console_encoding, errors="replace").decode(console_encoding), end="", flush=True)
            log_handle.write(line)
            log_handle.flush()
        return_code = process.wait()
    if return_code != 0:
        raise RuntimeError(f"command failed with exit code {return_code}: {' '.join(command)}")


def parse_args(arguments: Sequence[str] | None = None) -> argparse.Namespace:
    default_start, default_end = default_window(datetime.now(tz=ET).date())
    parser = argparse.ArgumentParser(
        description="read-only raw-data download followed by missing daily backtests; never starts live trading"
    )
    parser.add_argument("--start-date", type=date.fromisoformat, default=default_start)
    parser.add_argument("--end-date", type=date.fromisoformat, default=default_end)
    parser.add_argument("--workers", type=int, default=24)
    parser.add_argument("--projects-root", type=Path, default=DEFAULT_PROJECTS_ROOT)
    parser.add_argument("--trading-root", type=Path, default=DEFAULT_TRADING_ROOT)
    parser.add_argument(
        "--database",
        type=Path,
        default=DEFAULT_TRADING_ROOT / "journal" / "trading_journal.sqlite",
    )
    parser.add_argument("--skip-data-download", action="store_true")
    parser.add_argument("--log-path", type=Path)
    return parser.parse_args(arguments)


def main(arguments: Sequence[str] | None = None) -> None:
    args = parse_args(arguments)
    if args.workers < 1:
        raise ValueError("--workers must be positive")
    days = session_days(args.start_date, args.end_date)
    if not days:
        raise ValueError("requested window contains no US equity session")
    log_path = args.log_path or args.trading_root / "logs" / f"data_backtest_catchup_{args.end_date:%Y%m%d}.log"
    downloader = args.projects_root / "PythonProject" / "Daten_Download" / "Download_Asset_Data.py"
    backtest = BOT_ROOT / "scripts" / "run_manual_regime_baseline_v1.py"

    print(f"catch-up window {days[0]}..{days[-1]}", flush=True)
    if not args.skip_data_download:
        print("raw_data: starting read-only historical download", flush=True)
        run_streamed([sys.executable, "-u", str(downloader)], downloader.parent, log_path)
        print("raw_data: completed", flush=True)

    missing = missing_sessions(args.trading_root, days[0], days[-1])
    if not missing:
        print("backtest: no missing daily manifests", flush=True)
        return
    print("backtest: missing sessions=" + ",".join(day.isoformat() for day in missing), flush=True)
    for day in missing:
        end_exclusive = day + timedelta(days=1)
        print(f"backtest {day}: starting", flush=True)
        run_streamed(
            [
                sys.executable,
                "-u",
                str(backtest),
                "--workers",
                str(args.workers),
                "--database",
                str(args.database),
                "--end-exclusive",
                end_exclusive.isoformat(),
            ],
            BOT_ROOT,
            log_path,
        )
        expected = manifest_path(args.trading_root, day)
        if not has_completed_manifest(expected):
            raise RuntimeError(f"backtest did not create a valid manifest: {expected}")
        print(f"backtest {day}: completed", flush=True)


if __name__ == "__main__":
    main()
