from __future__ import annotations

from pathlib import Path

import pandas as pd


OUT_DIR = Path(__file__).resolve().parent

OPTIMIZED_LIVE_TOP50_PATH = OUT_DIR / "current_baseline_optimized_cutoff_2025_11_top50_symbols.csv"
LIVE_PAIR_SELECTION_PATH = OUT_DIR / "current_baseline_optimized_cutoff_2025_11_top50_symbol_rule_selection.csv"
OPTIMIZED_VS_OLD_TRADES_PATH = OUT_DIR / "current_baseline_optimized_cutoff_2025_11_top50_vs_old_trades.csv"

LIVE_CUTOFF = "2025-11"
OPTIMIZED_CURRENT_BASELINE_VARIANT = "optimized_cutoff_2025_11_top50_current_baseline"
LIVE_VARIANT = OPTIMIZED_CURRENT_BASELINE_VARIANT


def load_optimized_live_top50_symbols() -> list[str]:
    df = pd.read_csv(OPTIMIZED_LIVE_TOP50_PATH)
    if "symbol" not in df.columns:
        raise ValueError(f"{OPTIMIZED_LIVE_TOP50_PATH} has no symbol column")
    return df["symbol"].astype(str).str.upper().head(50).tolist()


def load_live_keep_pairs(variant: str = LIVE_VARIANT) -> set[tuple[str, str]]:
    df = pd.read_csv(LIVE_PAIR_SELECTION_PATH)
    df = df[df["variant"].astype(str).eq(variant)].copy()
    if df.empty:
        raise ValueError(f"No pair selection rows for {variant}")
    kept = df[df["keep"].astype(bool)].copy()
    return {(str(row.symbol).upper(), str(row.rule)) for row in kept.itertuples(index=False)}
