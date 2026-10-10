from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

from analyze_top50_regime_holding_time import build_signal_context
from build_topn_union_ledger_sequential import load_mode_map
from compare_cutoff_forward_topn_variants import RANK_PATH, VariantConfig, load_cutoff_topn
from simulate_selected_symbols_portfolio_mm import build_trades, simulate_portfolio, split_summary, summarize

import apply_refined_veto_and_analyze_cluster_cooldown as active_tools
import run_top100_gap_lhf_rule_test as top100_tools
import sweep_prior_churn_warning_veto as churn_tools


OUT_DIR = Path(__file__).resolve().parent
MODE_LEDGER = OUT_DIR / "trade_ledger_baseline_best_modes_live_history_548d.csv"
PAIR_SELECTION = OUT_DIR / "cutoff_forward_top50_variants_live_history_548d_post_break_range_symbol_rule_selection.csv"

START = "2023-01-01"
END = "2026-06-18"
MAX_HISTORY_DAYS = 548
START_CAPITAL = 10_000.0
RISK_PCT = 0.01
TOP_N = 50
CUTOFFS = ["2025-11", "2026-03"]

OUT_SUMMARY = OUT_DIR / "current_baseline_cutoff_2025_11_vs_2026_03_summary.csv"
OUT_SPLIT = OUT_DIR / "current_baseline_cutoff_2025_11_vs_2026_03_split.csv"
OUT_TRADES = OUT_DIR / "current_baseline_cutoff_2025_11_vs_2026_03_trades.csv"
OUT_EQUITY = OUT_DIR / "current_baseline_cutoff_2025_11_vs_2026_03_equity.csv"
OUT_PLOT = OUT_DIR / "current_baseline_cutoff_2025_11_vs_2026_03_equity.png"


def clean(value: object) -> str:
    if pd.isna(value):
        return ""
    text = str(value)
    return "" if text.lower() == "nan" else text


def variant(cutoff: str) -> VariantConfig:
    return VariantConfig(
        name=f"cutoff_{cutoff.replace('-', '_')}_top50_current_baseline",
        cutoff=cutoff,
        top_n=TOP_N,
        symbols=load_cutoff_topn(RANK_PATH, cutoff, TOP_N),
    )


def rename_context_columns(context: pd.DataFrame) -> pd.DataFrame:
    return top100_tools.rename_context_columns(context)


def enrich_ledger_light(ledger: pd.DataFrame, context: pd.DataFrame) -> pd.DataFrame:
    df = ledger.copy()
    df["symbol"] = df["symbol"].astype(str).str.upper()
    df["entry_date"] = pd.to_datetime(df["entry_date"])
    df["exit_date"] = pd.to_datetime(df["exit_date"])
    df["entry_day"] = df["entry_date"].dt.normalize()
    ctx = rename_context_columns(context.copy())
    ctx["symbol"] = ctx["symbol"].astype(str).str.upper()
    df = df.merge(ctx, on=["symbol", "entry_day"], how="left")
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
        if col in df.columns and base_col in df.columns:
            df[col] = df[col].where(df[col].notna(), df[base_col])
    return df


def add_context_after_transforms(ledger: pd.DataFrame, enriched: pd.DataFrame) -> pd.DataFrame:
    context_cols = [
        "signal_regime_fresh",
        "big_picture_regime_fresh",
        "big_picture_state",
        "daily_structure_phase_fresh",
        "bearish_countermove_subregime",
        "range_basis_fresh",
        "range_location_fresh",
        "range_lower_subtype",
        "range_lower_risk_score",
        "range_lower_detail",
        "profile_shape_fresh",
        "profile_price_location_fresh",
        "post_break_midrange_fresh",
        "volume_price_phase",
    ]
    base = top100_tools.add_veto_match_columns(ledger)
    ctx = top100_tools.add_veto_match_columns(enriched)
    keep_cols = [
        "_match_symbol",
        "_match_entry_date",
        "_match_rule",
        "_match_side",
        "_match_entry",
        "_match_stop",
        "_match_target",
        "_match_seq",
    ] + [col for col in context_cols if col in ctx.columns]
    merged = base.merge(
        ctx[keep_cols],
        on=[
            "_match_symbol",
            "_match_entry_date",
            "_match_rule",
            "_match_side",
            "_match_entry",
            "_match_stop",
            "_match_target",
            "_match_seq",
        ],
        how="left",
        suffixes=("", "_ctx"),
    )
    for col in context_cols:
        ctx_col = f"{col}_ctx"
        if ctx_col in merged.columns:
            merged[col] = merged[ctx_col]
    return merged.drop(columns=[col for col in merged.columns if col.startswith("_match_") or col.endswith("_ctx")], errors="ignore")


def refined_veto_mask(df: pd.DataFrame) -> pd.Series:
    return (
        df["rule"].astype(str).eq("bearish_failed_countermove_short_baseline")
        & df["side"].astype(str).eq("short")
        & df["daily_structure_phase_fresh"].map(clean).eq("trend_or_unclassified")
        & df["volume_price_phase"].map(clean).eq("normal_volume_trend")
        & df["bearish_countermove_subregime"].map(clean).eq("bearish_countermove_weak_retest")
    )


def apply_active_ledger_rules(base: pd.DataFrame, enriched: pd.DataFrame) -> pd.DataFrame:
    if base.empty:
        return base
    gap, gap_audit = top100_tools.apply_gap_fix(base, enriched)
    lhf, lhf_audit = top100_tools.apply_lhf_vetoes(gap, enriched)
    churn_config = churn_tools.ChurnConfig("strict_gap_short_only", target_rules=("bearish_gap_breakdown_continuation_short",))
    churned, churn_audit = churn_tools.apply_veto(lhf, churn_config)
    with_context = add_context_after_transforms(churned, enriched)
    refined_removed = with_context.loc[refined_veto_mask(with_context)].copy()
    refined = with_context.loc[~refined_veto_mask(with_context)].copy()
    refined["track"] = "baseline"
    refined["active_rule_stack"] = "gapfix_lhf_prior_churn_failed_countermove"
    return refined


def apply_cluster_to_simulated_trades(trades_df: pd.DataFrame) -> pd.DataFrame:
    cluster_config = active_tools.ClusterConfig(
        name="hard_lhf_cluster_veto_lb2_loss5_net350",
        lookback_days=2,
        min_losses=5,
        max_net_pnl=-350.0,
        target_rules=("bearish_lower_high_failure_short",),
    )
    kept, _skipped = active_tools.apply_cluster_filter(trades_df, cluster_config)
    kept = active_tools.prepare_trades(kept)
    return kept


def load_pair_selection(cutoff: str) -> set[tuple[str, str]]:
    pairs = pd.read_csv(PAIR_SELECTION)
    variant_name = f"cutoff_{cutoff}_top50"
    pairs = pairs[pairs["variant"].astype(str).eq(variant_name)].copy()
    if pairs.empty:
        raise ValueError(f"No pair selection for {variant_name}")
    return {
        (str(row.symbol).upper(), str(row.rule))
        for row in pairs.itertuples(index=False)
        if bool(row.keep)
    }


def simulate_cutoff(cutoff: str, transformed_ledger: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    cfg = variant(cutoff)
    keep_pairs = load_pair_selection(cutoff)
    filtered = transformed_ledger[
        transformed_ledger["symbol"].astype(str).str.upper().isin({symbol.upper() for symbol in cfg.symbols})
        & transformed_ledger.apply(lambda row: (str(row["symbol"]).upper(), str(row["rule"])) in keep_pairs, axis=1)
    ].copy()
    trades = build_trades(filtered, cfg.symbols, cfg.train_start.strftime("%Y-%m-%d"))
    trades_df = simulate_portfolio(trades, START_CAPITAL, RISK_PCT, compound=False)
    trades_df = apply_cluster_to_simulated_trades(trades_df)
    trades_df["variant"] = cfg.name
    summary_df = summarize(trades_df, START_CAPITAL)
    summary_df["variant"] = cfg.name
    split_df = split_summary(trades_df, START_CAPITAL, cfg.forward_start)
    split_df["variant"] = cfg.name
    return trades_df, summary_df, split_df


def main() -> None:
    variants = [variant(cutoff) for cutoff in CUTOFFS]
    symbols = sorted({symbol for cfg in variants for symbol in cfg.symbols})
    print(f"symbols={len(symbols)}", flush=True)

    ledger = pd.read_csv(MODE_LEDGER, parse_dates=["entry_date", "exit_date"])
    ledger["symbol"] = ledger["symbol"].astype(str).str.upper()
    ledger = ledger[ledger["symbol"].isin(symbols)].copy()
    ledger = ledger[ledger["track"].astype(str).eq("baseline")].copy()
    mode_map = load_mode_map(MODE_LEDGER)
    context = build_signal_context(symbols, START, END, MAX_HISTORY_DAYS, mode_map)
    enriched = enrich_ledger_light(ledger, context)
    transformed = apply_active_ledger_rules(ledger, enriched)

    all_trades = []
    all_summary = []
    all_split = []
    all_equity = []
    for cutoff in CUTOFFS:
        trades, summary, split = simulate_cutoff(cutoff, transformed)
        all_trades.append(trades)
        all_summary.append(summary)
        all_split.append(split)
        executed = trades[trades["executed"].astype(bool)].copy()
        equity = executed[["exit_date", "equity_after", "variant"]].copy()
        equity["normalized_equity"] = equity["equity_after"] / START_CAPITAL
        all_equity.append(equity)

    trades_df = pd.concat(all_trades, ignore_index=True)
    summary_df = pd.concat(all_summary, ignore_index=True)
    split_df = pd.concat(all_split, ignore_index=True)
    equity_df = pd.concat(all_equity, ignore_index=True)

    trades_df.to_csv(OUT_TRADES, index=False)
    summary_df.to_csv(OUT_SUMMARY, index=False)
    split_df.to_csv(OUT_SPLIT, index=False)
    equity_df.to_csv(OUT_EQUITY, index=False)

    plt.figure(figsize=(14, 7))
    for name, group in equity_df.groupby("variant"):
        plt.plot(group["exit_date"], group["normalized_equity"], linewidth=2.0, label=name)
    for cutoff in CUTOFFS:
        cfg = variant(cutoff)
        plt.axvline(cfg.forward_start, linestyle="--", linewidth=1.0, alpha=0.45, label=f"{cutoff} forward")
    plt.axhline(1.0, color="#9CA3AF", linestyle="--", linewidth=1.0)
    plt.title("Current active baseline: cutoff 2025-11 vs 2026-03 top50 baskets")
    plt.xlabel("Exit date")
    plt.ylabel("Normalized equity")
    plt.grid(True, alpha=0.25)
    plt.legend()
    plt.tight_layout()
    plt.savefig(OUT_PLOT, dpi=150)
    plt.close()

    print(summary_df.round(4).to_string(index=False))
    print("\nsplit")
    print(split_df.round(4).to_string(index=False))
    print(f"\nplot={OUT_PLOT}")


if __name__ == "__main__":
    main()
