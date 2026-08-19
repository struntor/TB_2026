from __future__ import annotations

import math
import sys
import concurrent.futures as cf
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
STRATEGY_DIR = ROOT / "PythonProject" / "statistic" / "spx_adx_di_5min_2024_02_03"
OUT_DIR = ROOT / "IBKR_TradingBot" / "out"
ENGINE_LOG_DIR = OUT_DIR / "manual_regime_june_2026_engine_logs"

if str(STRATEGY_DIR) not in sys.path:
    sys.path.insert(0, str(STRATEGY_DIR))

import manual_regime_rules_backtest as rules
from manual_regime_bot_api import get_trade_openings_for_symbols
from live_manual_regime_tws_bot import (
    NON_ROUTABLE,
    apply_live_baseline_filters,
    load_keep_pairs,
    load_top50_symbols,
)


START = pd.Timestamp("2026-06-01")
END_EXCLUSIVE = pd.Timestamp("2026-07-01")
DATA_END_EXCLUSIVE = pd.Timestamp("2026-07-10")
RISK_PER_TRADE = 100.0
CUTOFF_VARIANT = "cutoff_2025-11_top50"
ENGINE_START = pd.Timestamp("2024-12-01")


def live_quantity(row: pd.Series) -> int:
    entry = float(row["entry"])
    stop = float(row["stop"])
    distance = abs(entry - stop)
    if not math.isfinite(distance) or distance <= 0:
        return 0
    size_mult = float(row.get("size", 1.0) or 1.0)
    return max(0, int(math.floor((RISK_PER_TRADE * size_mult) / distance)))


def run_symbol(symbol: str) -> pd.DataFrame:
    _, trades, _ = rules.run_backtest(
        symbol,
        ENGINE_START.strftime("%Y-%m-%d"),
        DATA_END_EXCLUSIVE.strftime("%Y-%m-%d"),
        structure_lookback=10,
        bull_continuation_mode="combo",
        learn_long_mode="off",
        runtime_log_path=ENGINE_LOG_DIR / f"Trading_Engine_{symbol}.log",
        decision_log_path=ENGINE_LOG_DIR / f"Trading_Engine_{symbol}_decision.log",
    )
    if trades.empty:
        return pd.DataFrame()
    trades = trades.copy()
    trades["symbol"] = symbol
    return trades


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ENGINE_LOG_DIR.mkdir(parents=True, exist_ok=True)

    symbols = [s for s in load_top50_symbols() if s not in NON_ROUTABLE]
    keep_pairs = load_keep_pairs(CUTOFF_VARIANT)
    print(f"running {len(symbols)} symbols from {ENGINE_START.date()} through {DATA_END_EXCLUSIVE.date()}", flush=True)

    parts: list[pd.DataFrame] = []
    with cf.ProcessPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(run_symbol, symbol): symbol for symbol in symbols}
        for future in cf.as_completed(futures):
            symbol = futures[future]
            frame = future.result()
            print(f"{symbol}: trades={len(frame)}", flush=True)
            if not frame.empty:
                parts.append(frame)

    if parts:
        all_trades = pd.concat(parts, ignore_index=True, sort=False)
    else:
        all_trades = pd.DataFrame()

    if all_trades.empty:
        month_trades = pd.DataFrame()
    else:
        all_trades["symbol"] = all_trades["symbol"].astype(str).str.upper()
        all_trades["rule"] = all_trades["rule"].astype(str)
        all_trades["entry_date"] = pd.to_datetime(all_trades["entry_date"])
        month_raw = all_trades[(all_trades["entry_date"] >= START) & (all_trades["entry_date"] < END_EXCLUSIVE)].copy()
        filtered_days: list[pd.DataFrame] = []
        for day, day_trades in month_raw.groupby(month_raw["entry_date"].dt.normalize()):
            filtered = apply_live_baseline_filters(day_trades.copy(), keep_pairs, pd.Timestamp(day))
            if not filtered.empty:
                filtered_days.append(filtered)
        month_trades = pd.concat(filtered_days, ignore_index=True, sort=False) if filtered_days else pd.DataFrame()

    rows: list[dict[str, object]] = []
    active_until_by_symbol: dict[str, pd.Timestamp] = {}
    skipped_existing = 0
    raw_after_filters = int(len(month_trades))

    if not month_trades.empty:
        month_trades = month_trades.sort_values(["entry_date", "symbol", "rule"]).reset_index(drop=True)
    for _, row in month_trades.iterrows():
        symbol = str(row["symbol"]).upper()
        entry_dt = pd.Timestamp(row["entry_date"])
        active_until = active_until_by_symbol.get(symbol)
        if active_until is not None and active_until > entry_dt:
            skipped_existing += 1
            continue
        qty = live_quantity(row)
        if qty <= 0:
            continue
        exit_dt = pd.Timestamp(row["exit_date"])
        points = float(row["points"])
        pnl = points * qty
        active_until_by_symbol[symbol] = exit_dt
        rows.append(
            {
                "symbol": symbol,
                "entry_date": entry_dt,
                "exit_date": exit_dt,
                "rule": str(row["rule"]),
                "side": str(row["side"]),
                "entry": float(row["entry"]),
                "stop": float(row["stop"]),
                "target": float(row["target"]),
                "exit": float(row["exit"]),
                "exit_reason": str(row["exit_reason"]),
                "points": points,
                "qty": qty,
                "pnl_dollars": pnl,
                "result": "win" if pnl > 0 else "loss",
                "size": float(row.get("size", 1.0) or 1.0),
                "regime_at_entry": str(row.get("regime_at_entry", "")),
            }
            )

    out = pd.DataFrame(rows).sort_values(["entry_date", "symbol", "rule"]).reset_index(drop=True)
    out_path = OUT_DIR / "manual_regime_backtest_june_2026_trades.csv"
    summary_path = OUT_DIR / "manual_regime_backtest_june_2026_summary.csv"
    out.to_csv(out_path, index=False)

    if out.empty:
        summary = pd.DataFrame(
            [{"trades": 0, "wins": 0, "losses": 0, "net_pnl_dollars": 0.0, "raw_after_filters": raw_after_filters, "skipped_existing": skipped_existing}]
        )
    else:
        pnl = pd.to_numeric(out["pnl_dollars"], errors="coerce").fillna(0.0)
        summary = pd.DataFrame(
            [
                {
                    "trades": int(len(out)),
                    "wins": int((pnl > 0).sum()),
                    "losses": int((pnl <= 0).sum()),
                    "net_pnl_dollars": float(pnl.sum()),
                    "avg_pnl_dollars": float(pnl.mean()),
                    "raw_after_filters": raw_after_filters,
                    "skipped_existing": skipped_existing,
                    "data_through": pd.Timestamp(rules.load_bars("SPY", "2026-07-09", "2026-07-10")["date"].max()),
                }
            ]
        )
    summary.to_csv(summary_path, index=False)

    print(summary.to_string(index=False), flush=True)
    if not out.empty:
        display_cols = [
            "symbol",
            "entry_date",
            "exit_date",
            "side",
            "rule",
            "entry",
            "stop",
            "target",
            "exit",
            "exit_reason",
            "qty",
            "pnl_dollars",
            "result",
        ]
        print(out[display_cols].to_string(index=False), flush=True)
    print(f"wrote {out_path}", flush=True)
    print(f"wrote {summary_path}", flush=True)


if __name__ == "__main__":
    main()
