from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path
import sys
from typing import Sequence


BOT_ROOT = Path(__file__).resolve().parents[1]
if str(BOT_ROOT) not in sys.path:
    sys.path.insert(0, str(BOT_ROOT))

from src.bot.daily_orchestrator import (
    DEFAULT_LOCK_PATH,
    DEFAULT_LOG_PATH,
    DEFAULT_PERFORMANCE_LOG_PATH,
    DEFAULT_PROJECTS_ROOT,
    DEFAULT_STATE_PATH,
    DEFAULT_TRADING_ROOT,
    DailyTradingOrchestrator,
    OrchestratorConfig,
    SingleInstanceLock,
    parse_clock,
)
from src.journal.store import DEFAULT_JOURNAL_PATH


def parse_args(
    arguments: Sequence[str] | None = None,
    *,
    fixed_session_day: date | None = None,
) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "one-session pipeline for TWS sync, raw data, backtest and trading"
            if fixed_session_day is not None
            else "24/7 supervisor for data, backtest and TWS trading"
        )
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7496)
    parser.add_argument("--sync-client-id", type=int, default=63)
    parser.add_argument("--live-client-id", type=int, default=62)
    parser.add_argument("--workers", type=int, default=24)
    parser.add_argument("--prepare-et", default="05:00")
    parser.add_argument("--latest-launch-et", default="09:35")
    parser.add_argument("--retry-seconds", type=float, default=300.0)
    parser.add_argument("--risk-per-trade", type=float, default=100.0)
    parser.add_argument("--account")
    parser.add_argument("--allow-live-account", action="store_true")
    parser.add_argument("--place-orders", action="store_true")
    parser.add_argument(
        "--dual-broker",
        action="store_true",
        help=(
            "keep the existing endpoint as the required paper broker and add one "
            "fail-closed production endpoint for the approved stock strategy"
        ),
    )
    parser.add_argument("--production-host")
    parser.add_argument("--production-port", type=int)
    parser.add_argument("--production-sync-client-id", type=int)
    parser.add_argument("--production-live-client-id", type=int)
    parser.add_argument("--production-account")
    parser.add_argument(
        "--production-place-orders",
        action="store_true",
        help="explicitly permit orders on the validated non-DU production account",
    )
    parser.add_argument("--enable-failed-auction-fly", action="store_true")
    parser.add_argument("--enable-spxw-15dte-put-credit", action="store_true")
    parser.add_argument("--enable-spxw-0dte-bull-put-iu30", action="store_true")
    parser.add_argument("--enable-spxw-75dte-put-bwb", action="store_true")
    parser.add_argument("--spxw-75dte-bwb-initial-entry-work-minutes", type=int, default=5)
    parser.add_argument("--spxw-75dte-bwb-entry-work-minutes", type=int, default=5)
    parser.add_argument("--spxw-75dte-bwb-final-entry-work-minutes", type=int, default=5)
    parser.add_argument("--spxw-75dte-bwb-max-risk-usd", type=float, default=10_500.0)
    parser.add_argument("--enable-xsp-vix-pullback", action="store_true")
    parser.add_argument("--xsp-vix-pullback-entry-work-minutes", type=int, default=10)
    parser.add_argument("--enable-stock-ivr70-put-credit", action="store_true")
    parser.add_argument("--options-account-value-usd", type=float, default=25_000.0)
    parser.add_argument("--options-max-risk-fraction", type=float, default=0.10)
    parser.add_argument("--journal-path", type=Path, default=DEFAULT_JOURNAL_PATH)
    parser.add_argument("--state-path", type=Path, default=DEFAULT_STATE_PATH)
    parser.add_argument("--lock-path", type=Path, default=DEFAULT_LOCK_PATH)
    parser.add_argument("--log-path", type=Path, default=DEFAULT_LOG_PATH)
    parser.add_argument(
        "--performance-log-path", type=Path, default=DEFAULT_PERFORMANCE_LOG_PATH
    )
    parser.add_argument("--performance-sample-seconds", type=float, default=0.5)
    parser.add_argument("--projects-root", type=Path, default=DEFAULT_PROJECTS_ROOT)
    parser.add_argument("--trading-root", type=Path, default=DEFAULT_TRADING_ROOT)
    parser.add_argument("--skip-journal-sync", action="store_true")
    parser.add_argument("--skip-data-download", action="store_true")
    parser.add_argument("--skip-backtest", action="store_true")
    parser.add_argument("--skip-live-trading", action="store_true")
    if fixed_session_day is None:
        parser.add_argument(
            "--once-day",
            help="run one YYYY-MM-DD session immediately (testing/recovery), then exit",
        )
    else:
        parser.set_defaults(once_day=fixed_session_day.isoformat())
    return parser.parse_args(arguments)


def main(
    arguments: Sequence[str] | None = None,
    *,
    fixed_session_day: date | None = None,
) -> None:
    args = parse_args(arguments, fixed_session_day=fixed_session_day)
    if args.workers < 1:
        raise ValueError("--workers must be positive")
    if args.retry_seconds <= 0:
        raise ValueError("--retry-seconds must be positive")
    if args.performance_sample_seconds <= 0:
        raise ValueError("--performance-sample-seconds must be positive")
    if args.skip_live_trading and not args.once_day:
        raise ValueError("--skip-live-trading is only allowed with --once-day")
    if args.spxw_75dte_bwb_initial_entry_work_minutes <= 0:
        raise ValueError("--spxw-75dte-bwb-initial-entry-work-minutes must be positive")
    if args.spxw_75dte_bwb_entry_work_minutes <= 0:
        raise ValueError("--spxw-75dte-bwb-entry-work-minutes must be positive")
    if args.spxw_75dte_bwb_final_entry_work_minutes <= 0:
        raise ValueError("--spxw-75dte-bwb-final-entry-work-minutes must be positive")
    production_values = {
        "--production-host": args.production_host,
        "--production-port": args.production_port,
        "--production-sync-client-id": args.production_sync_client_id,
        "--production-live-client-id": args.production_live_client_id,
        "--production-account": args.production_account,
    }
    if args.dual_broker:
        missing = [name for name, value in production_values.items() if value is None]
        if missing:
            raise ValueError(
                "--dual-broker requires " + ", ".join(missing)
            )
        if not args.account:
            raise ValueError("--dual-broker requires the exact paper --account")
        if not str(args.account).upper().startswith("DU"):
            raise ValueError("dual-broker paper --account must start with DU")
        if str(args.production_account).upper().startswith("DU"):
            raise ValueError("--production-account must be a non-DU account")
        if args.account == args.production_account:
            raise ValueError("paper and production accounts must differ")
        if int(args.production_port) <= 0:
            raise ValueError("--production-port must be positive")
        if int(args.production_sync_client_id) < 0:
            raise ValueError("--production-sync-client-id must be non-negative")
        if int(args.production_live_client_id) < 0:
            raise ValueError("--production-live-client-id must be non-negative")
        if args.allow_live_account:
            raise ValueError(
                "--allow-live-account is a legacy single-endpoint option and cannot be "
                "combined with --dual-broker"
            )
        if (
            args.host == args.production_host
            and args.port == args.production_port
            and args.live_client_id == args.production_live_client_id
        ):
            raise ValueError("paper and production live endpoints need distinct client IDs")
        if (
            args.host == args.production_host
            and args.port == args.production_port
            and args.sync_client_id == args.production_sync_client_id
        ):
            raise ValueError("paper and production sync endpoints need distinct client IDs")
    elif args.production_place_orders or any(
        value is not None for value in production_values.values()
    ):
        raise ValueError("production options require --dual-broker")
    config = OrchestratorConfig(
        python_executable=Path(sys.executable),
        projects_root=args.projects_root,
        trading_root=args.trading_root,
        journal_path=args.journal_path,
        state_path=args.state_path,
        lock_path=args.lock_path,
        log_path=args.log_path,
        performance_log_path=args.performance_log_path,
        host=args.host,
        port=args.port,
        sync_client_id=args.sync_client_id,
        live_client_id=args.live_client_id,
        workers=args.workers,
        prepare_et=parse_clock(args.prepare_et),
        latest_launch_et=parse_clock(args.latest_launch_et),
        retry_seconds=args.retry_seconds,
        risk_per_trade=args.risk_per_trade,
        place_orders=args.place_orders,
        allow_live_account=args.allow_live_account,
        account=args.account,
        enable_failed_auction_fly=args.enable_failed_auction_fly,
        enable_spxw_15dte_put_credit=args.enable_spxw_15dte_put_credit,
        enable_spxw_0dte_bull_put_iu30=args.enable_spxw_0dte_bull_put_iu30,
        enable_spxw_75dte_put_bwb=args.enable_spxw_75dte_put_bwb,
        enable_stock_ivr70_put_credit=args.enable_stock_ivr70_put_credit,
        spxw_75dte_bwb_initial_entry_work_minutes=args.spxw_75dte_bwb_initial_entry_work_minutes,
        spxw_75dte_bwb_entry_work_minutes=args.spxw_75dte_bwb_entry_work_minutes,
        spxw_75dte_bwb_final_entry_work_minutes=args.spxw_75dte_bwb_final_entry_work_minutes,
        spxw_75dte_bwb_max_risk_usd=args.spxw_75dte_bwb_max_risk_usd,
        options_account_value_usd=args.options_account_value_usd,
        options_max_risk_fraction=args.options_max_risk_fraction,
        enable_xsp_vix_pullback=args.enable_xsp_vix_pullback,
        xsp_vix_pullback_entry_work_minutes=args.xsp_vix_pullback_entry_work_minutes,
        run_journal_sync=not args.skip_journal_sync,
        run_data_download=not args.skip_data_download,
        run_backtest=not args.skip_backtest,
        run_live_trading=not args.skip_live_trading,
        performance_sample_seconds=args.performance_sample_seconds,
        dual_broker=args.dual_broker,
        production_host=args.production_host,
        production_port=args.production_port,
        production_sync_client_id=args.production_sync_client_id,
        production_live_client_id=args.production_live_client_id,
        production_account=args.production_account,
        production_place_orders=args.production_place_orders,
    )
    orchestrator = DailyTradingOrchestrator(config)
    with SingleInstanceLock(config.lock_path):
        if args.once_day:
            orchestrator.run_cycle(date.fromisoformat(args.once_day))
        else:
            orchestrator.run_forever()


if __name__ == "__main__":
    main()
