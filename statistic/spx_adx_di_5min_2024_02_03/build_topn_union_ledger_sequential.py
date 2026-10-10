from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from manual_regime_rules_backtest import run_backtest


OUT_DIR = Path(__file__).resolve().parent


def load_mode_map(path: Path) -> dict[str, str]:
    ledger = pd.read_csv(path)
    if ledger.empty or "symbol" not in ledger.columns or "learn_mode" not in ledger.columns:
        return {}
    return (
        ledger[["symbol", "learn_mode"]]
        .dropna()
        .assign(symbol=lambda frame: frame["symbol"].astype(str).str.upper())
        .drop_duplicates("symbol")
        .set_index("symbol")["learn_mode"]
        .astype(str)
        .to_dict()
    )


def load_symbols(path: Path) -> list[str]:
    trades = pd.read_csv(path)
    if trades.empty or "symbol" not in trades.columns:
        raise ValueError(f"No symbol column in {path}")
    return sorted(trades["symbol"].astype(str).str.upper().unique())


def load_variant_symbols(path: Path, variant: str) -> list[str]:
    span = pd.read_csv(path)
    rows = span[span["variant"].astype(str) == variant]
    if rows.empty:
        raise ValueError(f"No variant={variant} in {path}")
    symbols = str(rows.iloc[0]["symbols"]).split(",")
    return [symbol.strip().upper() for symbol in symbols if symbol.strip()]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--top-trades", default=str(OUT_DIR / "cutoff_forward_top50_variants_live_history_548d_trades.csv"))
    parser.add_argument("--span", default=str(OUT_DIR / "cutoff_forward_top50_variants_live_history_548d_span.csv"))
    parser.add_argument("--variant", default=None)
    parser.add_argument("--mode-ledger", default=str(OUT_DIR / "trade_ledger_baseline_best_modes_live_history_548d.csv"))
    parser.add_argument("--start", default="2023-01-01")
    parser.add_argument("--end", default="2026-06-18")
    parser.add_argument("--max-history-days", type=int, default=548)
    parser.add_argument("--output", default=str(OUT_DIR / "trade_ledger_top50_union_live_history_548d_post_break_range.csv"))
    args = parser.parse_args()

    symbols = load_variant_symbols(Path(args.span), args.variant) if args.variant else load_symbols(Path(args.top_trades))
    mode_map = load_mode_map(Path(args.mode_ledger))
    rows: list[pd.DataFrame] = []
    for idx, symbol in enumerate(symbols, start=1):
        learn_mode = mode_map.get(symbol, "off")
        try:
            signals, trades, _ = run_backtest(
                symbol,
                args.start,
                args.end,
                learn_long_mode=learn_mode,
                max_history_days=args.max_history_days,
            )
        except Exception as exc:
            print(f"{idx}/{len(symbols)} {symbol}: {type(exc).__name__}", flush=True)
            continue
        if trades.empty or "track" not in trades.columns:
            print(f"{idx}/{len(symbols)} {symbol}: no trades", flush=True)
            continue
        baseline = trades[trades["track"] == "baseline"].copy()
        if baseline.empty:
            print(f"{idx}/{len(symbols)} {symbol}: no baseline", flush=True)
            continue
        baseline["entry_date"] = pd.to_datetime(baseline["entry_date"]).dt.normalize()
        baseline["exit_date"] = pd.to_datetime(baseline["exit_date"]).dt.normalize()
        baseline = baseline[baseline["entry_date"] >= pd.Timestamp("2024-01-01")].copy()
        baseline["symbol"] = symbol
        baseline["learn_mode"] = learn_mode
        context_cols = [
            "day",
            "regime",
            "big_picture_regime",
            "daily_structure_phase",
            "range_basis",
            "range_location",
            "profile_value_range",
            "profile_shape",
            "profile_price_location",
            "post_break_midrange",
            "range_lower_subtype",
            "range_lower_risk_score",
            "range_lower_value_pos",
            "range_lower_decision_vs_value_low_atr",
            "range_lower_open_vs_value_low_atr",
            "range_lower_decision_vs_poc_atr",
            "range_lower_detail",
        ]
        if not signals.empty and all(col in signals.columns for col in context_cols):
            context = signals[context_cols].copy()
            context["entry_date"] = pd.to_datetime(context["day"]).dt.normalize()
            context = context.drop(columns=["day"]).rename(columns={"regime": "signal_regime_at_entry"})
            baseline = baseline.merge(context, on="entry_date", how="left")
        baseline["equity_after_trade"] = baseline["points"].astype(float).cumsum()
        rows.append(baseline)
        if idx % 10 == 0 or idx == len(symbols):
            print(f"{idx}/{len(symbols)} done", flush=True)

    ledger = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()
    ledger.to_csv(args.output, index=False)
    print(args.output)
    print(f"rows={len(ledger)} symbols={ledger['symbol'].nunique() if not ledger.empty and 'symbol' in ledger.columns else 0}")


if __name__ == "__main__":
    main()
