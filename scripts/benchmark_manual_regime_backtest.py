from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys


BOT_ROOT = Path(__file__).resolve().parents[1]
if str(BOT_ROOT) not in sys.path:
    sys.path.insert(0, str(BOT_ROOT))

from src.bot.resource_monitor import PhaseResourceMonitor


DEFAULT_BACKTEST = BOT_ROOT / "scripts" / "run_manual_regime_baseline_v1.py"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Measure one Manual-Regime backtest without TWS or order routing"
    )
    parser.add_argument("--workers", type=int, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--performance-log", type=Path, required=True)
    parser.add_argument("--end-exclusive", required=True)
    parser.add_argument("--sample-seconds", type=float, default=0.5)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.workers < 1:
        raise ValueError("--workers must be positive")
    command = [
        sys.executable,
        "-u",
        str(DEFAULT_BACKTEST),
        "--workers",
        str(args.workers),
        "--database",
        str(args.database),
        "--end-exclusive",
        args.end_exclusive,
    ]
    monitor = PhaseResourceMonitor(
        phase=f"backtest_workers_{args.workers}",
        command=command,
        log_path=args.performance_log,
        sample_seconds=args.sample_seconds,
    )
    process = subprocess.Popen(command, cwd=BOT_ROOT)
    monitor.attach(process.pid)
    return_code = process.wait()
    summary = monitor.finish(return_code)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    if return_code != 0:
        raise SystemExit(return_code)


if __name__ == "__main__":
    main()
