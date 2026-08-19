from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd
from ib_insync import util

if __package__ in {None, ""}:
    sys.path.append(str(Path(__file__).resolve().parents[2]))
    sys.path.append(str(Path(__file__).resolve().parents[3] / "PythonProject" / "statistic" / "spx_adx_di_5min_2024_02_03"))

from manual_regime_bot_api import get_trade_openings_for_symbols
from src.bot.basket_selector import StaticBasketSelector
from src.bot.bot_log import BotLogCapture
from src.data.ib_connection import IBConnectionConfig, IBConnectionManager
from src.data.ib_market_data import build_contract
from src.data.market_data_hub import MarketDataHub
from src.execution.ib_order_executor import IBOrderExecutor
from src.models.money_management import MoneyManagementConfig
from src.risk.money_manager import MoneyManagementBlock


US_TZ = "America/New_York"
LIVE_OVERLAY_ENV = "MANUAL_REGIME_BAR_OVERLAY_DIR"


def _resolve_path(path_str: str) -> Path:
    path = Path(path_str)
    if path.is_absolute():
        return path
    return Path(__file__).resolve().parents[2] / path


def _parse_symbols(raw: str | None, basket_symbols: list[str]) -> list[str]:
    if raw:
        wanted = [sym.strip().upper() for sym in raw.split(",") if sym.strip()]
        result = [sym for sym in wanted if sym in basket_symbols]
        if result:
            return result
    return list(basket_symbols)


def _to_naive_session_ts(ts: object) -> pd.Timestamp:
    stamp = pd.Timestamp(ts)
    if stamp.tzinfo is not None:
        stamp = stamp.tz_convert(US_TZ).tz_localize(None)
    return stamp


def _write_overlay_csv(overlay_dir: Path, symbol: str, boots: list[dict[str, object]]) -> Path:
    overlay_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, object]] = []
    for boot in boots:
        ts = _to_naive_session_ts(boot["start"])
        rows.append(
            {
                "date": ts.strftime("%Y-%m-%d %H:%M:%S"),
                "open": float(boot["open"]),
                "high": float(boot["high"]),
                "low": float(boot["low"]),
                "close": float(boot["close"]),
                "volume": float(boot["volume"]),
                "average": float(boot.get("close", 0.0)),
                "barcount": int(boot.get("bar_count", 1)),
            }
        )
    overlay_path = overlay_dir / f"Rohdaten_{symbol.upper()}.csv"
    pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume", "average", "barcount"]).to_csv(
        overlay_path,
        index=False,
    )
    return overlay_path


def _fmt_hms(seconds: int) -> str:
    hours, rem = divmod(max(seconds, 0), 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _wait_until_target(target: pd.Timestamp, poll_seconds: int) -> None:
    print(f"engine_start_target={target.isoformat(sep=' ', timespec='seconds')}")
    while True:
        remaining = int((target - pd.Timestamp.now(tz=target.tz)).total_seconds())
        if remaining <= 0:
            print("engine_start=reached")
            break
        print(f"countdown_to_engine={_fmt_hms(remaining)}")
        time.sleep(min(max(poll_seconds, 1), remaining))


def main() -> None:
    default_day = pd.Timestamp.now(tz=US_TZ).date().isoformat()
    parser = argparse.ArgumentParser(
        description="Probe today's trades using the first two 5m bars from IB and optionally transmit bracket orders."
    )
    parser.add_argument("--config", default=str(Path("configs") / "baskets_v1.json"))
    parser.add_argument("--day", default=default_day)
    parser.add_argument("--start", default="2023-01-01")
    parser.add_argument("--symbols", default="")
    parser.add_argument("--bootstrap-bars", type=int, default=2)
    parser.add_argument("--decision-log", default="")
    parser.add_argument("--runtime-log", default="")
    parser.add_argument("--market-open-delay-minutes", type=int, default=10)
    parser.add_argument("--countdown-poll-seconds", type=int, default=30)
    parser.add_argument("--skip-engine-wait", action="store_true")
    parser.add_argument("--money-config", default=str(Path("configs") / "money_management_v1.json"))
    parser.add_argument("--bot-log", default="")
    parser.add_argument("--execute-orders", action="store_true")
    parser.add_argument("--entry-order-type", choices=["MKT", "LMT"], default="LMT")
    parser.add_argument("--bracket-style", choices=["classic", "manual_oca"], default="classic")
    parser.add_argument("--outside-rth", action="store_true")
    parser.add_argument("--allow-index-orders", action="store_true")
    parser.add_argument("--post-submit-hold-seconds", type=float, default=None)
    parser.add_argument("--execution-log", default="")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7496)
    parser.add_argument("--client-id", type=int, default=21)
    parser.add_argument("--output", default=str(Path("runs") / "live_trade_probe_summary.csv"))
    args = parser.parse_args()

    config_path = _resolve_path(args.config)
    output_path = _resolve_path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    decision_log_path = _resolve_path(args.decision_log) if args.decision_log else None
    if decision_log_path is not None:
        decision_log_path.parent.mkdir(parents=True, exist_ok=True)
    money_config = MoneyManagementConfig.from_config(_resolve_path(args.money_config))
    execution_log_path = _resolve_path(args.execution_log) if args.execution_log else None

    selector = StaticBasketSelector.from_config(config_path)
    basket = selector.baskets[selector.default_basket]
    symbols = _parse_symbols(args.symbols, list(basket.symbols))
    session_date = pd.Timestamp(args.day).date()
    runtime_log_path = _resolve_path(args.runtime_log) if args.runtime_log else _resolve_path(
        Path("runs") / "runtime_logs" / args.day / f"{selector.default_basket}.log"
    )
    runtime_log_path.parent.mkdir(parents=True, exist_ok=True)
    bot_log_path = _resolve_path(args.bot_log) if args.bot_log else _resolve_path(
        Path("runs") / "bot_logs" / args.day / f"{selector.default_basket}.log"
    )
    if execution_log_path is None:
        execution_log_path = _resolve_path(
            Path("runs") / "execution_logs" / args.day / f"{selector.default_basket}.csv"
        )

    bot_log_capture: BotLogCapture | None = None
    bot_log_capture = BotLogCapture(bot_log_path)
    bot_log_capture.__enter__()

    print(f"basket={selector.default_basket}")
    print(f"assets={len(basket.symbols)}")
    print(f"probe_day={args.day}")
    print(f"probe_symbols={','.join(symbols)}")
    print(f"bootstrap_bars={args.bootstrap_bars}")
    print(f"market_open_delay_minutes={args.market_open_delay_minutes}")
    print(f"money_account_size_usd={money_config.account_size_usd}")
    print(f"money_risk_per_trade_pct={money_config.risk_per_trade_pct}")
    print(f"money_risk_budget_usd={money_config.risk_budget_usd:.2f}")
    print(f"money_max_open_trades={money_config.max_open_trades}")
    print(f"money_max_open_trades_long={money_config.max_open_trades_long}")
    print(f"money_max_open_trades_short={money_config.max_open_trades_short}")
    print(f"execute_orders={'yes' if args.execute_orders else 'no'}")
    print(f"entry_order_type={args.entry_order_type}")
    print(f"bracket_style={args.bracket_style}")
    print(f"outside_rth={'yes' if (args.outside_rth or args.execute_orders) else 'no'}")
    effective_post_submit_hold_seconds = (
        args.post_submit_hold_seconds if args.post_submit_hold_seconds is not None else (10.0 if args.execute_orders else 0.0)
    )
    print(f"post_submit_hold_seconds={effective_post_submit_hold_seconds}")
    print(f"bot_log={bot_log_path}")
    if decision_log_path is not None:
        print(f"decision_log={decision_log_path}")
    if runtime_log_path is not None:
        print(f"runtime_log={runtime_log_path}")
    if execution_log_path is not None:
        print(f"execution_log={execution_log_path}")

    cfg = IBConnectionConfig(host=args.host, port=args.port, client_id=args.client_id, readonly=not args.execute_orders)
    manager = IBConnectionManager(cfg)
    summary_rows: list[dict[str, object]] = []
    summary_row_by_symbol: dict[str, dict[str, object]] = {}
    combined_log_parts: list[str] = []
    combined_runtime_log_parts: list[str] = []
    results_by_symbol: dict[str, object] = {}

    try:
        ib = manager.connect()
        print("ib=connected")
        hub = MarketDataHub(ib)

        market_open_target = pd.Timestamp(f"{args.day} 09:30:00").tz_localize(US_TZ)
        engine_start_target = market_open_target + pd.Timedelta(minutes=args.market_open_delay_minutes)
        if args.skip_engine_wait:
            print("engine_wait=skipped")
        else:
            _wait_until_target(engine_start_target, args.countdown_poll_seconds)

        with TemporaryDirectory(dir=str(output_path.parent)) as tmpdir:
            tmpdir_path = Path(tmpdir)
            overlay_dir = tmpdir_path / "live_raw_overlay"
            batch_log_dir = tmpdir_path / "engine_logs"
            batch_log_dir.mkdir(parents=True, exist_ok=True)
            trade_symbols: list[str] = []

            for symbol in symbols:
                print(f"asset_phase=start symbol={symbol}")
                row: dict[str, object] = {
                    "day": args.day,
                    "symbol": symbol,
                    "status": "ok",
                    "trade_count": 0,
                    "error": "",
                }
                try:
                    contract, what_to_show = build_contract(symbol, "US")
                    qualified = ib.qualifyContracts(contract)
                    if not qualified:
                        raise RuntimeError("unqualified_contract")
                    contract = qualified[0]
                    print(
                        f"asset_phase=bootstrap_start symbol={symbol} "
                        f"contract={getattr(contract, 'secType', None)} "
                        f"localSymbol={getattr(contract, 'localSymbol', None)}"
                    )

                    deadline = time.time() + 900
                    boots: list[dict[str, object]] = []
                    while True:
                        boots = hub.bootstrap_session_5m_bars(
                            symbol,
                            contract,
                            what_to_show,
                            session_date=session_date,
                            count=args.bootstrap_bars,
                            seed=False,
                        )
                        if len(boots) >= args.bootstrap_bars:
                            hub.seed_5m_bars(symbol, boots)
                            break
                        if time.time() >= deadline:
                            raise RuntimeError(
                                f"bootstrap_session_5m_bars returned {len(boots)} row(s), expected {args.bootstrap_bars}"
                            )
                        print(
                            f"waiting_for_bootstrap_bars {symbol} have={len(boots)} "
                            f"need={args.bootstrap_bars}"
                        )
                        ib.sleep(15)
                    for idx, boot in enumerate(boots, start=1):
                        print(
                            f"bootstrap5m[{idx}] {symbol} start={boot['start']} end={boot['end']} "
                            f"o={boot['open']} h={boot['high']} l={boot['low']} c={boot['close']} v={boot['volume']}"
                        )

                    _write_overlay_csv(overlay_dir, symbol, boots)
                    print(f"asset_phase=engine_ready symbol={symbol} overlay={overlay_dir / f'Rohdaten_{symbol.upper()}.csv'}")
                    trade_symbols.append(symbol)
                except Exception as exc:
                    row["status"] = "error"
                    row["error"] = f"{type(exc).__name__}: {exc}"
                    print(f"error {symbol}={row['error']}")
                summary_rows.append(row)
                summary_row_by_symbol[symbol] = row

            if trade_symbols:
                previous_overlay_dir = os.environ.get(LIVE_OVERLAY_ENV)
                os.environ[LIVE_OVERLAY_ENV] = str(overlay_dir)
                try:
                    engine_phase_started = time.perf_counter()
                    symbol_batches = get_trade_openings_for_symbols(
                        trade_symbols,
                        args.day,
                        start=args.start,
                        history_lookback_days=540,
                        structure_lookback=10,
                        bull_continuation_mode="combo",
                        learn_long_mode="off",
                        learn_long_window=5,
                        learn_long_min_trades=3,
                        learn_long_min_win_rate=0.45,
                        learn_long_cooldown_days=15,
                        log_dir=batch_log_dir,
                    )
                    engine_phase_elapsed = time.perf_counter() - engine_phase_started
                finally:
                    if previous_overlay_dir is None:
                        os.environ.pop(LIVE_OVERLAY_ENV, None)
                    else:
                        os.environ[LIVE_OVERLAY_ENV] = previous_overlay_dir

                print(
                    f"asset_phase=engine_total_done symbols={len(trade_symbols)} "
                    f"elapsed_seconds={engine_phase_elapsed:.2f}"
                )
                results_by_symbol = {item.symbol: item.batch for item in symbol_batches}

                day_stamp = pd.Timestamp(args.day).normalize().date()
                for symbol in trade_symbols:
                    batch = results_by_symbol.get(symbol)
                    if batch is None:
                        continue
                    row = summary_row_by_symbol.get(symbol)
                    if row is not None:
                        row["trade_count"] = int(len(batch.trades))
                        row["engine_seconds"] = round(float(engine_phase_elapsed), 2)
                    print(f"asset_phase=engine_done symbol={symbol} trades={len(batch.trades)}")
                    if batch.trades.empty:
                        print(f"trades {symbol}=none")
                    else:
                        print(f"trades {symbol} count={len(batch.trades)}")
                        print(batch.trades.to_string(index=False))

                    decision_path = batch_log_dir / f"Trading_Engine_{symbol}_{day_stamp}_decision.log"
                    runtime_path = batch_log_dir / f"Trading_Engine_{symbol}_{day_stamp}.log"
                    if decision_path.exists():
                        text = decision_path.read_text(encoding="utf-8").strip()
                        if text:
                            combined_log_parts.append(text)
                    if runtime_path.exists():
                        text = runtime_path.read_text(encoding="utf-8").strip()
                        if text:
                            combined_runtime_log_parts.append(text)

            if results_by_symbol:
                money_manager = MoneyManagementBlock(money_config)
                executor = IBOrderExecutor(
                    ib,
                    entry_order_type=args.entry_order_type,
                    bracket_style=args.bracket_style,
                    allow_index_orders=args.allow_index_orders,
                    outside_rth=(args.outside_rth or args.execute_orders),
                )
                exposure = executor.current_open_exposure()
                existing_symbols = set(exposure.symbols)
                print(f"open_trade_symbols={','.join(sorted(existing_symbols)) if existing_symbols else '-'}")
                print(f"open_trade_total={exposure.total_open}")
                print(f"open_trade_long={exposure.long_open}")
                print(f"open_trade_short={exposure.short_open}")
                print(f"remaining_total_slots={max(money_config.max_open_trades - exposure.total_open, 0)}")
                print(f"remaining_long_slots={max(money_config.max_open_trades_long - exposure.long_open, 0)}")
                print(f"remaining_short_slots={max(money_config.max_open_trades_short - exposure.short_open, 0)}")
                if existing_symbols:
                    print(f"existing_open_trade_symbols={','.join(sorted(existing_symbols))}")

                tradeable_results: dict[str, object] = {}
                blocked_for_execution: list[str] = []
                for symbol, batch in results_by_symbol.items():
                    contract, _ = build_contract(symbol, "US")
                    qualified = ib.qualifyContracts(contract)
                    if not qualified:
                        blocked_for_execution.append(symbol)
                        continue
                    contract = qualified[0]
                    if getattr(contract, "secType", "").upper() == "IND" and not args.allow_index_orders:
                        blocked_for_execution.append(symbol)
                        continue
                    tradeable_results[symbol] = batch

                if blocked_for_execution:
                    print(f"execution_blocked_symbols={','.join(sorted(set(blocked_for_execution)))}")

                print("asset_phase=planning_start")
                planning_started = time.perf_counter()
                plan = money_manager.plan_from_result_map(
                    tradeable_results,
                    symbol_order=symbols,
                    current_open_trades=exposure.total_open,
                    current_open_long_trades=exposure.long_open,
                    current_open_short_trades=exposure.short_open,
                )
                planning_elapsed = time.perf_counter() - planning_started
                print(f"money_plan_candidates={len(plan)}")
                print(f"money_plan_accepted={int(plan['allowed'].sum()) if not plan.empty else 0}")
                print(f"money_plan_blocked={int((~plan['allowed']).sum()) if not plan.empty else 0}")
                print(f"asset_phase=planning_done elapsed_seconds={planning_elapsed:.2f}")

                print("asset_phase=execution_start")
                execution_started = time.perf_counter()
                execution_df = executor.submit_plan(
                    plan,
                    transmit=args.execute_orders,
                    existing_symbols=existing_symbols,
                )
                execution_elapsed = time.perf_counter() - execution_started
                if execution_log_path is not None:
                    execution_log_path.parent.mkdir(parents=True, exist_ok=True)
                    execution_df.to_csv(execution_log_path, index=False)
                    print(f"execution_log_written={execution_log_path}")
                if execution_df.empty:
                    print("execution_rows=0")
                else:
                    print(f"execution_rows={len(execution_df)}")
                    print(execution_df.to_string(index=False))
                print(f"asset_phase=execution_done elapsed_seconds={execution_elapsed:.2f}")
                if args.execute_orders and effective_post_submit_hold_seconds > 0:
                    print(f"holding_connection_seconds={effective_post_submit_hold_seconds}")
                    ib.sleep(effective_post_submit_hold_seconds)
    finally:
        pd.DataFrame(summary_rows).to_csv(output_path, index=False)
        if decision_log_path is not None:
            decision_log_path.write_text(
                "\n".join(combined_log_parts) + ("\n" if combined_log_parts else ""),
                encoding="utf-8",
            )
        if runtime_log_path is not None:
            runtime_log_path.write_text(
                "\n".join(combined_runtime_log_parts) + ("\n" if combined_runtime_log_parts else ""),
                encoding="utf-8",
            )
        manager.disconnect()
        print("ib=disconnected")
        print(f"summary={output_path}")
        if decision_log_path is not None:
            print(f"decision_log={decision_log_path}")
        if runtime_log_path is not None:
            print(f"runtime_log={runtime_log_path}")
        if bot_log_capture is not None:
            bot_log_capture.__exit__(None, None, None)


if __name__ == "__main__":
    util.patchAsyncio()
    main()
