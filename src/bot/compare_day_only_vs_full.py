from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

if __package__ in {None, ""}:
    sys.path.append(str(Path(__file__).resolve().parents[2]))
    sys.path.append(str(Path(__file__).resolve().parents[3] / "PythonProject" / "statistic" / "spx_adx_di_5min_2024_02_03"))

from manual_regime_bot_api import get_trade_openings_for_day_only
import manual_regime_rules_backtest as rules


def _slice_day(frame: pd.DataFrame, day: str, column: str) -> pd.DataFrame:
    if frame.empty:
        return frame.copy().reset_index(drop=True)
    day_ts = pd.Timestamp(day).normalize()
    return frame[pd.to_datetime(frame[column]).dt.normalize() == day_ts].copy().reset_index(drop=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare day-only engine output with a full run.")
    parser.add_argument("--symbol", default="GPC")
    parser.add_argument("--day", default="2026-06-04")
    parser.add_argument("--start", default="2023-01-01")
    parser.add_argument("--full-end-exclusive", default="2026-06-19")
    args = parser.parse_args()

    day_batch = get_trade_openings_for_day_only(args.symbol, args.day, start=args.start)
    full_bars = rules.load_bars(args.symbol, args.start, args.full_end_exclusive)
    full_signals, full_trades, _ = rules.run_backtest(args.symbol, args.start, args.full_end_exclusive, bars=full_bars)

    full_day_signals = _slice_day(full_signals, args.day, "day")
    full_day_trades = _slice_day(full_trades, args.day, "entry_date")
    if not full_day_trades.empty and "symbol" not in full_day_trades.columns:
        full_day_trades["symbol"] = args.symbol
    day_signals = day_batch.signals.reset_index(drop=True)
    day_trades = day_batch.trades.reset_index(drop=True)

    signals_match = False
    trades_match = False
    signal_diff = ""
    trade_diff = ""

    try:
        pd.testing.assert_frame_equal(day_signals, full_day_signals, check_dtype=False, check_like=False)
        signals_match = True
    except AssertionError as exc:
        signal_diff = str(exc)

    try:
        pd.testing.assert_frame_equal(day_trades, full_day_trades, check_dtype=False, check_like=False)
        trades_match = True
    except AssertionError as exc:
        trade_diff = str(exc)

    print(f"symbol={args.symbol}")
    print(f"day={args.day}")
    print(f"signals_match={signals_match}")
    print(f"trades_match={trades_match}")
    print(f"day_signals_rows={len(day_signals)} full_day_signals_rows={len(full_day_signals)}")
    print(f"day_trades_rows={len(day_trades)} full_day_trades_rows={len(full_day_trades)}")

    if not signals_match:
        print(f"signals_diff={signal_diff}")
    if not trades_match:
        print(f"trades_diff={trade_diff}")

    if signals_match and trades_match:
        print("comparison=identical")
    else:
        print("comparison=different")


if __name__ == "__main__":
    main()
