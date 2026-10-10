from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from build_topn_union_ledger_sequential import load_mode_map
from manual_regime_rules_backtest import load_bars, run_backtest


OUT_DIR = Path(__file__).resolve().parent
LEDGER_PATH = OUT_DIR / "trade_ledger_cutoff_2025_11_top50_live_history_548d_phase_test.csv"
MODE_LEDGER = OUT_DIR / "trade_ledger_baseline_best_modes_live_history_548d.csv"
SIGNAL_CONTEXT_COLS = [
    "day",
    "regime",
    "big_picture_regime",
    "big_picture_state",
    "bearish_countermove_subregime",
    "daily_structure_phase",
    "range_basis",
    "range_location",
    "range_lower_subtype",
    "range_lower_risk_score",
    "range_lower_detail",
    "profile_value_range",
    "profile_shape",
    "profile_price_location",
    "post_break_midrange",
    "volume_price_phase",
]


def build_signal_context(symbols: list[str], start: str, end: str, max_history_days: int, mode_map: dict[str, str]) -> pd.DataFrame:
    parts: list[pd.DataFrame] = []
    for idx, symbol in enumerate(symbols, start=1):
        learn_mode = mode_map.get(symbol, "off")
        print(f"signals {idx}/{len(symbols)} {symbol} learn={learn_mode}", flush=True)
        signals, _, _ = run_backtest(
            symbol,
            start,
            end,
            learn_long_mode=learn_mode,
            max_history_days=max_history_days,
        )
        if signals.empty:
            continue
        cols = [col for col in SIGNAL_CONTEXT_COLS if col in signals.columns]
        context = signals[cols].copy()
        context["symbol"] = symbol
        context["entry_day"] = pd.to_datetime(context["day"]).dt.normalize()
        context = context.drop(columns=["day"])
        parts.append(context)
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def add_mfe_mae_for_symbol(symbol: str, trades: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
    bars = load_bars(symbol, start, end)
    rows: list[pd.Series] = []
    for _, row in trades.iterrows():
        entry_dt = pd.Timestamp(row["entry_date"])
        exit_dt = pd.Timestamp(row["exit_date"])
        window = bars[(bars["date"] >= entry_dt) & (bars["date"] <= exit_dt)].copy()
        out = row.copy()
        if window.empty:
            out["mfe_points"] = None
            out["mae_points"] = None
            out["mfe_r"] = None
            out["mae_r"] = None
            out["mfe_to_final_ratio"] = None
            rows.append(out)
            continue
        entry = float(row["entry"])
        side = str(row["side"])
        stop = float(row["stop"])
        risk_points = abs(entry - stop)
        if side == "long":
            mfe = float(window["high"].max()) - entry
            mae = float(window["low"].min()) - entry
        else:
            mfe = entry - float(window["low"].min())
            mae = entry - float(window["high"].max())
        points = float(row["points"])
        out["mfe_points"] = mfe
        out["mae_points"] = mae
        out["mfe_r"] = mfe / risk_points if risk_points > 0 else None
        out["mae_r"] = mae / risk_points if risk_points > 0 else None
        out["mfe_to_final_ratio"] = mfe / points if points > 0 else None
        rows.append(out)
    return pd.DataFrame(rows)


def enrich_trades(ledger: pd.DataFrame, context: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
    df = ledger.copy()
    df["symbol"] = df["symbol"].astype(str).str.upper()
    df["entry_date"] = pd.to_datetime(df["entry_date"])
    df["exit_date"] = pd.to_datetime(df["exit_date"])
    df["entry_day"] = df["entry_date"].dt.normalize()

    if not context.empty:
        context = context.copy()
        context["symbol"] = context["symbol"].astype(str).str.upper()
        rename = {
            "regime": "signal_regime_fresh",
            "big_picture_regime": "big_picture_regime_fresh",
            "daily_structure_phase": "daily_structure_phase_fresh",
            "range_basis": "range_basis_fresh",
            "range_location": "range_location_fresh",
            "profile_shape": "profile_shape_fresh",
            "profile_price_location": "profile_price_location_fresh",
            "post_break_midrange": "post_break_midrange_fresh",
        }
        context = context.rename(columns={k: v for k, v in rename.items() if k in context.columns})
        df = df.merge(context, on=["symbol", "entry_day"], how="left")

    for col in [
        "signal_regime_fresh",
        "big_picture_regime_fresh",
        "daily_structure_phase_fresh",
        "range_basis_fresh",
        "range_location_fresh",
        "profile_shape_fresh",
        "profile_price_location_fresh",
        "post_break_midrange_fresh",
    ]:
        base_col = col.replace("_fresh", "")
        if base_col in df.columns:
            df[col] = df[col].where(df[col].notna(), df[base_col])

    df["hold_hours"] = (df["exit_date"] - df["entry_date"]).dt.total_seconds() / 3600.0
    df["hold_calendar_days"] = (df["exit_date"].dt.normalize() - df["entry_date"].dt.normalize()).dt.days
    df["risk_points"] = (df["entry"].astype(float) - df["stop"].astype(float)).abs()
    df["points_r"] = df["points"].astype(float) / df["risk_points"].replace(0, pd.NA)

    parts = []
    for symbol, group in df.groupby("symbol"):
        print(f"mfe/mae {symbol} trades={len(group)}", flush=True)
        parts.append(add_mfe_mae_for_symbol(symbol, group, start, end))
    return pd.concat(parts, ignore_index=True) if parts else df


def profit_factor(series: pd.Series) -> float:
    gains = float(series[series > 0].sum())
    losses = float(-series[series < 0].sum())
    if losses == 0:
        return float("inf") if gains > 0 else 0.0
    return gains / losses


def summarize_by(df: pd.DataFrame, group_cols: list[str], min_trades: int = 1) -> pd.DataFrame:
    group_cols = [col for col in group_cols if col in df.columns]
    if not group_cols:
        return pd.DataFrame()
    rows = []
    for keys, group in df.groupby(group_cols, dropna=False):
        if not isinstance(keys, tuple):
            keys = (keys,)
        if len(group) < min_trades:
            continue
        points = group["points"].astype(float)
        row = {col: key for col, key in zip(group_cols, keys)}
        row.update(
            {
                "trades": int(len(group)),
                "symbols": int(group["symbol"].nunique()),
                "points": float(points.sum()),
                "avg_points": float(points.mean()),
                "win_rate": float((points > 0).mean()),
                "profit_factor": profit_factor(points),
                "median_hold_hours": float(group["hold_hours"].median()),
                "avg_hold_hours": float(group["hold_hours"].mean()),
                "p75_hold_hours": float(group["hold_hours"].quantile(0.75)),
                "median_hold_days": float(group["hold_calendar_days"].median()),
                "target_rate": float(group["exit_reason"].astype(str).str.contains("target").mean()),
                "stop_rate": float(group["exit_reason"].astype(str).str.contains("stop").mean()),
                "avg_mfe_r": float(pd.to_numeric(group["mfe_r"], errors="coerce").mean()),
                "avg_mae_r": float(pd.to_numeric(group["mae_r"], errors="coerce").mean()),
                "median_mfe_r": float(pd.to_numeric(group["mfe_r"], errors="coerce").median()),
                "median_mae_r": float(pd.to_numeric(group["mae_r"], errors="coerce").median()),
            }
        )
        rows.append(row)
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values(["points", "trades"], ascending=[True, False]).reset_index(drop=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ledger", default=str(LEDGER_PATH))
    parser.add_argument("--mode-ledger", default=str(MODE_LEDGER))
    parser.add_argument("--start", default="2023-01-01")
    parser.add_argument("--end", default="2026-06-18")
    parser.add_argument("--max-history-days", type=int, default=548)
    parser.add_argument("--output-prefix", default=str(OUT_DIR / "top50_regime_holding_time"))
    args = parser.parse_args()

    ledger = pd.read_csv(args.ledger, parse_dates=["entry_date", "exit_date"])
    symbols = sorted(ledger["symbol"].astype(str).str.upper().unique().tolist())
    mode_map = load_mode_map(Path(args.mode_ledger))
    context = build_signal_context(symbols, args.start, args.end, args.max_history_days, mode_map)
    enriched = enrich_trades(ledger, context, args.start, args.end)
    prefix = Path(args.output_prefix)
    enriched.to_csv(f"{prefix}_trades_enriched.csv", index=False)

    tables = {
        "by_rule": summarize_by(enriched, ["rule", "side"], min_trades=3),
        "by_signal_regime": summarize_by(enriched, ["signal_regime_fresh", "side"], min_trades=3),
        "by_big_picture": summarize_by(enriched, ["big_picture_regime_fresh", "side"], min_trades=3),
        "by_daily_phase": summarize_by(enriched, ["daily_structure_phase_fresh", "side"], min_trades=3),
        "by_lower_subtype": summarize_by(
            enriched[enriched["daily_structure_phase_fresh"].astype(str).eq("post_break_range_lower")],
            ["range_lower_subtype", "rule", "side"],
            min_trades=3,
        ),
        "by_phase_rule": summarize_by(enriched, ["daily_structure_phase_fresh", "rule", "side"], min_trades=5),
        "by_countermove_subregime": summarize_by(enriched, ["bearish_countermove_subregime", "rule", "side"], min_trades=5),
    }
    for name, table in tables.items():
        table.to_csv(f"{prefix}_{name}.csv", index=False)

    print("\nWorst by daily phase")
    print(tables["by_daily_phase"].head(20).round(3).to_string(index=False))
    print("\nLower subtype by rule")
    print(tables["by_lower_subtype"].round(3).to_string(index=False))
    print("\nRule holding summary")
    print(tables["by_rule"].round(3).to_string(index=False))
    print(f"\nwrote_prefix={prefix}")


if __name__ == "__main__":
    main()
