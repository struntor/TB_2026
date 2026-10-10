from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

from analyze_top50_regime_holding_time import SIGNAL_CONTEXT_COLS, add_mfe_mae_for_symbol
from build_topn_union_ledger_sequential import load_mode_map
from compare_cutoff_forward_topn_variants import RANK_PATH, VariantConfig, load_cutoff_topn, run_variant
from manual_regime_rules_backtest import run_backtest
from simulate_selected_symbols_portfolio_mm import build_trades, load_ledger, simulate_portfolio, split_summary, summarize
from sweep_lhf_entry_veto_portfolio import RULES as LHF_VETO_RULES
from sweep_lhf_entry_veto_portfolio import VetoRule, clean
from test_volatility_reversal_gap_short_active_monitor import MonitorConfig, load_bars, simulate_trade


OUT_DIR = Path(__file__).resolve().parent
MODE_LEDGER = OUT_DIR / "trade_ledger_baseline_best_modes_live_history_548d.csv"

CUTOFF = "2025-11"
TOP_N = 100
START = "2023-01-01"
TRADE_START = pd.Timestamp("2024-01-01")
END = "2026-06-18"
MAX_HISTORY_DAYS = 548
START_CAPITAL = 10_000.0
RISK_PCT = 0.01
MIN_TRADES = 5

BASE_STEM = "cutoff_2025_11_top100_live_history_548d_phase_test"
GAP_STEM = "cutoff_2025_11_top100_live_history_548d_monitor60m_value_middle_early_no_followthrough"
COMBO_STEM = "cutoff_2025_11_top100_live_history_548d_monitor60m_early_lhf_veto_combo"

BASE_LEDGER = OUT_DIR / f"trade_ledger_{BASE_STEM}.csv"
ENRICHED_LEDGER = OUT_DIR / "top100_regime_holding_time_trades_enriched.csv"
GAP_LEDGER = OUT_DIR / f"trade_ledger_{GAP_STEM}.csv"
GAP_AUDIT = OUT_DIR / f"trade_ledger_{GAP_STEM}_audit.csv"
COMBO_LEDGER = OUT_DIR / f"trade_ledger_{COMBO_STEM}.csv"
COMBO_AUDIT = OUT_DIR / f"trade_ledger_{COMBO_STEM}_audit.csv"
BASE_PAIR_SELECTION = OUT_DIR / f"{BASE_STEM}_symbol_rule_selection.csv"


@dataclass(frozen=True)
class PortfolioOutputs:
    trades: pd.DataFrame
    summary: pd.DataFrame
    split: pd.DataFrame
    equity: pd.DataFrame


def top100_variant() -> VariantConfig:
    return VariantConfig(
        name="cutoff_2025-11_top100",
        cutoff=CUTOFF,
        top_n=TOP_N,
        symbols=load_cutoff_topn(RANK_PATH, CUTOFF, TOP_N),
    )


def context_from_signals(signals: pd.DataFrame, symbol: str) -> pd.DataFrame:
    if signals.empty:
        return pd.DataFrame()
    cols = [col for col in SIGNAL_CONTEXT_COLS if col in signals.columns]
    extra_cols = [col for col in ("atr14", "atr_tolerance") if col in signals.columns]
    context = signals[cols + [col for col in extra_cols if col not in cols]].copy()
    context["symbol"] = symbol
    context["entry_day"] = pd.to_datetime(context["day"]).dt.normalize()
    return context.drop(columns=["day"], errors="ignore")


def build_baseline_and_context() -> tuple[pd.DataFrame, pd.DataFrame]:
    variant = top100_variant()
    mode_map = load_mode_map(MODE_LEDGER)
    ledgers: list[pd.DataFrame] = []
    contexts: list[pd.DataFrame] = []
    for idx, symbol in enumerate(variant.symbols, start=1):
        learn_mode = mode_map.get(symbol, "off")
        print(f"baseline {idx}/{len(variant.symbols)} {symbol} learn={learn_mode}", flush=True)
        try:
            signals, trades, _ = run_backtest(
                symbol,
                START,
                END,
                learn_long_mode=learn_mode,
                max_history_days=MAX_HISTORY_DAYS,
            )
        except Exception as exc:
            print(f"  skip {symbol}: {type(exc).__name__}: {exc}", flush=True)
            continue
        ctx = context_from_signals(signals, symbol)
        if not ctx.empty:
            contexts.append(ctx)
        if trades.empty or "track" not in trades.columns:
            continue
        base = trades[trades["track"].astype(str).eq("baseline")].copy()
        if base.empty:
            continue
        base["entry_date"] = pd.to_datetime(base["entry_date"]).dt.normalize()
        base["exit_date"] = pd.to_datetime(base["exit_date"]).dt.normalize()
        base = base[base["entry_date"] >= TRADE_START].copy()
        base["symbol"] = symbol
        base["learn_mode"] = learn_mode
        if "equity_after_trade" in base.columns:
            base = base.drop(columns=["equity_after_trade"])
        base["equity_after_trade"] = pd.to_numeric(base["points"], errors="coerce").fillna(0.0).cumsum()
        ledgers.append(base)
    ledger = pd.concat(ledgers, ignore_index=True) if ledgers else pd.DataFrame()
    context = pd.concat(contexts, ignore_index=True) if contexts else pd.DataFrame()
    return ledger, context


def rename_context_columns(context: pd.DataFrame) -> pd.DataFrame:
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
    return context.rename(columns={k: v for k, v in rename.items() if k in context.columns})


def enrich_ledger(ledger: pd.DataFrame, context: pd.DataFrame) -> pd.DataFrame:
    df = ledger.copy()
    df["symbol"] = df["symbol"].astype(str).str.upper()
    df["entry_date"] = pd.to_datetime(df["entry_date"])
    df["exit_date"] = pd.to_datetime(df["exit_date"])
    df["entry_day"] = df["entry_date"].dt.normalize()
    if not context.empty:
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
    df["hold_hours"] = (df["exit_date"] - df["entry_date"]).dt.total_seconds() / 3600.0
    df["hold_calendar_days"] = (df["exit_date"].dt.normalize() - df["entry_date"].dt.normalize()).dt.days
    df["risk_points"] = (pd.to_numeric(df["entry"], errors="coerce") - pd.to_numeric(df["stop"], errors="coerce")).abs()
    df["points_r"] = pd.to_numeric(df["points"], errors="coerce") / df["risk_points"].replace(0, pd.NA)

    parts: list[pd.DataFrame] = []
    for idx, (symbol, group) in enumerate(df.groupby("symbol"), start=1):
        print(f"mfe/mae {idx}/{df['symbol'].nunique()} {symbol} trades={len(group)}", flush=True)
        parts.append(add_mfe_mae_for_symbol(symbol, group, START, END))
    return pd.concat(parts, ignore_index=True) if parts else df


def write_recalc_portfolio_outputs(ledger_path: Path, stem: str, variant: VariantConfig) -> PortfolioOutputs:
    ledger = load_ledger(ledger_path)
    trades_df, summary_df, split_df, pair_df, span_df = run_variant(ledger, variant, MIN_TRADES)
    prefix = OUT_DIR / stem
    trades_df.assign(variant=variant.name).to_csv(prefix.with_name(prefix.name + "_trades.csv"), index=False)
    summary_df.assign(variant=variant.name).to_csv(prefix.with_name(prefix.name + "_summary.csv"), index=False)
    split_df.assign(variant=variant.name).to_csv(prefix.with_name(prefix.name + "_split_summary.csv"), index=False)
    pair_df.assign(variant=variant.name).to_csv(prefix.with_name(prefix.name + "_symbol_rule_selection.csv"), index=False)
    span_df.assign(variant=variant.name).to_csv(prefix.with_name(prefix.name + "_span.csv"), index=False)
    equity_df = write_equity(trades_df, stem, variant, f"{stem} recalc")
    return PortfolioOutputs(trades_df, summary_df, split_df, equity_df)


def write_fixed_pair_portfolio_outputs(ledger_path: Path, stem: str, variant: VariantConfig) -> PortfolioOutputs:
    ledger = load_ledger(ledger_path)
    pairs = pd.read_csv(BASE_PAIR_SELECTION)
    keep_pairs = {
        (str(row.symbol).upper(), str(row.rule))
        for row in pairs.itertuples(index=False)
        if bool(row.keep)
    }
    filtered = ledger[
        ledger["symbol"].astype(str).str.upper().isin({symbol.upper() for symbol in variant.symbols})
        & ledger.apply(lambda row: (str(row["symbol"]).upper(), str(row["rule"])) in keep_pairs, axis=1)
    ].copy()
    trades = build_trades(filtered, variant.symbols, variant.train_start.strftime("%Y-%m-%d"))
    trades_df = simulate_portfolio(trades, START_CAPITAL, RISK_PCT, compound=False)
    summary_df = summarize(trades_df, START_CAPITAL)
    split_df = split_summary(trades_df, START_CAPITAL, variant.forward_start)
    prefix = OUT_DIR / f"{stem}_fixed_pairs"
    trades_df.assign(variant=variant.name).to_csv(prefix.with_name(prefix.name + "_trades.csv"), index=False)
    summary_df.assign(variant=variant.name).to_csv(prefix.with_name(prefix.name + "_summary.csv"), index=False)
    split_df.assign(variant=variant.name).to_csv(prefix.with_name(prefix.name + "_split_summary.csv"), index=False)
    equity_df = write_equity(trades_df, f"{stem}_fixed_pairs", variant, f"{stem} fixed pairs")
    return PortfolioOutputs(trades_df, summary_df, split_df, equity_df)


def write_equity(trades_df: pd.DataFrame, stem: str, variant: VariantConfig, title: str) -> pd.DataFrame:
    executed = trades_df[trades_df["executed"]].copy() if "executed" in trades_df.columns else pd.DataFrame()
    equity_df = pd.DataFrame()
    if not executed.empty:
        equity_df = executed[["exit_date", "equity_after"]].copy()
        equity_df["variant"] = variant.name
        equity_df["normalized_equity"] = equity_df["equity_after"] / START_CAPITAL
    prefix = OUT_DIR / stem
    equity_df.to_csv(prefix.with_name(prefix.name + "_equity.csv"), index=False)
    if not equity_df.empty:
        plt.figure(figsize=(14, 7))
        plt.plot(equity_df["exit_date"], equity_df["normalized_equity"], linewidth=2.0, label=title)
        plt.axhline(1.0, color="#9CA3AF", linestyle="--", linewidth=1.0)
        plt.axvline(variant.forward_start, color="#DC2626", linestyle="--", linewidth=1.0, alpha=0.7, label="forward start")
        plt.title(f"cutoff 2025-11 top100: {title}")
        plt.xlabel("Exit date")
        plt.ylabel("Normalized equity")
        plt.grid(True, alpha=0.25)
        plt.legend()
        plt.tight_layout()
        plt.savefig(prefix.with_name(prefix.name + "_equity.png"), dpi=150)
        plt.close()
    return equity_df


def add_gap_match_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["_match_symbol"] = out["symbol"].astype(str).str.upper()
    out["_match_entry_date"] = pd.to_datetime(out["entry_date"]).dt.normalize()
    out["_match_exit_date"] = pd.to_datetime(out["exit_date"]).dt.normalize()
    out["_match_rule"] = out["rule"].astype(str)
    out["_match_side"] = out["side"].astype(str)
    for col in ("entry", "stop", "target"):
        out[f"_match_{col}"] = pd.to_numeric(out[col], errors="coerce").round(6)
    keys = [
        "_match_symbol",
        "_match_entry_date",
        "_match_exit_date",
        "_match_rule",
        "_match_side",
        "_match_entry",
        "_match_stop",
        "_match_target",
    ]
    out["_match_seq"] = out.groupby(keys, dropna=False).cumcount()
    return out


def add_veto_match_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["_match_symbol"] = out["symbol"].astype(str).str.upper()
    out["_match_entry_date"] = pd.to_datetime(out["entry_date"]).dt.normalize()
    out["_match_rule"] = out["rule"].astype(str)
    out["_match_side"] = out["side"].astype(str)
    for col in ("entry", "stop", "target"):
        out[f"_match_{col}"] = pd.to_numeric(out[col], errors="coerce").round(6)
    keys = [
        "_match_symbol",
        "_match_entry_date",
        "_match_rule",
        "_match_side",
        "_match_entry",
        "_match_stop",
        "_match_target",
    ]
    out["_match_seq"] = out.groupby(keys, dropna=False).cumcount()
    return out


def apply_gap_fix(base: pd.DataFrame, enriched: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    targets = enriched[
        enriched["rule"].astype(str).eq("bearish_gap_breakdown_continuation_short")
        & enriched["bearish_countermove_subregime"].astype(str).eq("bearish_countermove_volatility_reversal")
        & enriched["side"].astype(str).eq("short")
    ].copy()
    config = MonitorConfig(
        "60m_value_middle_early_no_followthrough",
        interval_minutes=60,
        exit_on_value_middle=True,
        min_age_minutes=60,
        value_middle_max_age_minutes=360,
        value_middle_max_mfe_r=0.50,
    )
    managed_rows: list[dict[str, object]] = []
    for idx, (symbol, group) in enumerate(targets.groupby("symbol"), start=1):
        symbol = str(symbol).upper()
        print(f"gap-monitor {idx}/{targets['symbol'].nunique()} {symbol}: {len(group)} trades", flush=True)
        bars = load_bars(symbol, START, END)
        for target_idx, row in group.iterrows():
            result = simulate_trade(row, bars, config)
            managed_rows.append(
                {
                    "_target_index": target_idx,
                    "managed_exit_date": pd.Timestamp(result["exit_date"]),
                    "managed_exit": float(result["exit"]),
                    "managed_points": float(result["points"]),
                    "managed_exit_reason": str(result["exit_reason"]),
                    "managed_hold_hours": result["hold_hours"],
                    "monitor_exit_state": result["monitor_exit_state"],
                    "max_mfe_r_seen": result["max_mfe_r_seen"],
                }
            )
    managed = pd.DataFrame(managed_rows)
    if managed.empty:
        out = base.copy()
        return out, pd.DataFrame()

    target_matches = add_gap_match_columns(targets).reset_index().rename(columns={"index": "_target_index"})
    result_cols = [
        "_target_index",
        "managed_exit_date",
        "managed_exit",
        "managed_points",
        "managed_exit_reason",
        "managed_hold_hours",
        "monitor_exit_state",
        "max_mfe_r_seen",
    ]
    target_results = target_matches.merge(managed[result_cols], on="_target_index", how="left")
    helper_cols = [col for col in target_results.columns if col.startswith("_match_")]
    merge_cols = helper_cols + [col for col in result_cols if col != "_target_index"]
    merged = add_gap_match_columns(base).merge(target_results[merge_cols], on=helper_cols, how="left")

    applied = merged["managed_points"].notna()
    merged["active_monitor_config"] = ""
    merged["active_monitor_applied"] = False
    merged["original_exit_date"] = pd.NaT
    merged["original_exit"] = pd.NA
    merged["original_exit_reason"] = ""
    merged["original_points"] = pd.NA
    merged["original_weighted_points"] = pd.NA

    if applied.any():
        size = pd.to_numeric(merged.loc[applied, "size"], errors="coerce").fillna(1.0)
        merged.loc[applied, "active_monitor_config"] = config.name
        merged.loc[applied, "active_monitor_applied"] = True
        merged.loc[applied, "original_exit_date"] = merged.loc[applied, "exit_date"]
        merged.loc[applied, "original_exit"] = merged.loc[applied, "exit"]
        merged.loc[applied, "original_exit_reason"] = merged.loc[applied, "exit_reason"]
        merged.loc[applied, "original_points"] = merged.loc[applied, "points"]
        merged.loc[applied, "original_weighted_points"] = merged.loc[applied, "weighted_points"]
        merged.loc[applied, "exit_date"] = pd.to_datetime(merged.loc[applied, "managed_exit_date"])
        merged.loc[applied, "exit"] = merged.loc[applied, "managed_exit"].astype(float)
        merged.loc[applied, "exit_reason"] = merged.loc[applied, "managed_exit_reason"].astype(str)
        merged.loc[applied, "points"] = merged.loc[applied, "managed_points"].astype(float)
        merged.loc[applied, "weighted_points"] = merged.loc[applied, "managed_points"].astype(float).to_numpy() * size.to_numpy()
        labels = merged.loc[applied, "entry_labels"].fillna("").astype(str)
        merged.loc[applied, "entry_labels"] = labels + f"|active_monitor:{config.name}"

    out = merged.drop(columns=[col for col in merged.columns if col.startswith("_match_")], errors="ignore")
    if "equity_after_trade" in out.columns:
        out["equity_after_trade"] = pd.to_numeric(out["points"], errors="coerce").fillna(0.0).cumsum()
    audit_cols = [
        "symbol",
        "entry_date",
        "rule",
        "side",
        "original_exit_date",
        "exit_date",
        "entry",
        "stop",
        "target",
        "original_points",
        "points",
        "original_exit_reason",
        "exit_reason",
        "managed_hold_hours",
        "monitor_exit_state",
        "max_mfe_r_seen",
    ]
    audit = out.loc[out["active_monitor_applied"], audit_cols].copy()
    audit["point_delta"] = pd.to_numeric(audit["points"], errors="coerce") - pd.to_numeric(audit["original_points"], errors="coerce")
    return out, audit


def build_lhf_context(enriched: pd.DataFrame) -> pd.DataFrame:
    df = enriched[
        enriched["rule"].astype(str).eq("bearish_lower_high_failure_short")
        & enriched["side"].astype(str).eq("short")
    ].copy()
    df = add_veto_match_columns(df)
    context_cols = [
        "_match_symbol",
        "_match_entry_date",
        "_match_rule",
        "_match_side",
        "_match_entry",
        "_match_stop",
        "_match_target",
        "_match_seq",
        "signal_regime_fresh",
        "big_picture_regime_fresh",
        "big_picture_state",
        "daily_structure_phase_fresh",
        "bearish_countermove_subregime",
        "profile_price_location_fresh",
        "volume_price_phase",
    ]
    out = df[[col for col in context_cols if col in df.columns]].copy()
    for col in [
        "signal_regime_fresh",
        "big_picture_regime_fresh",
        "big_picture_state",
        "daily_structure_phase_fresh",
        "bearish_countermove_subregime",
        "profile_price_location_fresh",
        "volume_price_phase",
    ]:
        if col in out.columns:
            out[col] = out[col].map(clean)
    return out


def veto_mask(df: pd.DataFrame, rule: VetoRule) -> pd.Series:
    mask = df["rule"].astype(str).eq("bearish_lower_high_failure_short") & df["side"].astype(str).eq("short")
    for col, value in rule.conditions:
        mask &= df[col].map(clean).eq(value)
    return mask


def apply_lhf_vetoes(base: pd.DataFrame, enriched: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    context = build_lhf_context(enriched)
    work = add_veto_match_columns(base).merge(
        context,
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
    )
    vetoed = pd.Series(False, index=work.index)
    audit_rows: list[pd.DataFrame] = []
    for rule in LHF_VETO_RULES:
        mask = veto_mask(work, rule) & ~vetoed
        if not mask.any():
            continue
        vetoed |= mask
        subset = work.loc[mask].copy()
        audit_rows.append(
            pd.DataFrame(
                {
                    "veto_rule": rule.name,
                    "symbol": subset["symbol"],
                    "entry_date": subset["entry_date"],
                    "exit_date": subset["exit_date"],
                    "rule": subset["rule"],
                    "side": subset["side"],
                    "points": subset["points"],
                    "weighted_points": subset["weighted_points"],
                    "signal_regime_fresh": subset.get("signal_regime_fresh", ""),
                    "big_picture_regime_fresh": subset.get("big_picture_regime_fresh", ""),
                    "daily_structure_phase_fresh": subset.get("daily_structure_phase_fresh", ""),
                    "profile_price_location_fresh": subset.get("profile_price_location_fresh", ""),
                    "volume_price_phase": subset.get("volume_price_phase", ""),
                }
            )
        )
    out = work.loc[~vetoed].copy()
    out = out.drop(
        columns=[
            col
            for col in out.columns
            if col.startswith("_match_")
            or col
            in {
                "signal_regime_fresh",
                "big_picture_regime_fresh",
                "big_picture_state",
                "daily_structure_phase_fresh",
                "bearish_countermove_subregime",
                "profile_price_location_fresh",
                "volume_price_phase",
            }
        ],
        errors="ignore",
    )
    if "equity_after_trade" in out.columns:
        out["equity_after_trade"] = pd.to_numeric(out["points"], errors="coerce").fillna(0.0).cumsum()
    audit = pd.concat(audit_rows, ignore_index=True) if audit_rows else pd.DataFrame()
    return out, audit


def max_drawdown(equity: pd.Series) -> float:
    if equity.empty:
        return 0.0
    return float((equity - equity.cummax()).min())


def ulcer_index(equity: pd.Series) -> float:
    if equity.empty:
        return 0.0
    drawdown_pct = (equity / equity.cummax() - 1.0).fillna(0.0)
    return float((drawdown_pct.pow(2).mean()) ** 0.5)


def rolling_worst(pnl: pd.Series, window: int) -> float:
    if len(pnl) < window:
        return float(pnl.sum()) if not pnl.empty else 0.0
    return float(pnl.rolling(window).sum().min())


def compact_stats(label: str, outputs: PortfolioOutputs, forward_start: pd.Timestamp) -> dict[str, object]:
    executed = outputs.trades[outputs.trades["executed"]].copy()
    summary = outputs.summary.iloc[0].to_dict()
    forward = executed[executed["exit_date"] >= forward_start].copy()
    return {
        "variant": label,
        "net_profit": float(summary["net_profit"]),
        "trades": int(summary["trades"]),
        "win_rate": float(summary["win_rate"]),
        "profit_factor": float(summary["profit_factor"]),
        "max_drawdown": float(summary["max_drawdown"]),
        "ulcer_index": ulcer_index(executed["equity_after"]) if not executed.empty else 0.0,
        "rolling20_worst_pnl": rolling_worst(executed["pnl_dollars"], 20) if not executed.empty else 0.0,
        "rolling50_worst_pnl": rolling_worst(executed["pnl_dollars"], 50) if not executed.empty else 0.0,
        "forward_net_profit": float(forward["pnl_dollars"].sum()) if not forward.empty else 0.0,
        "skipped_trades": int(summary["skipped_trades"]),
    }


def write_comparison_plot(series: list[tuple[str, pd.DataFrame]]) -> None:
    plt.figure(figsize=(14, 7))
    for label, equity in series:
        if equity.empty:
            continue
        plt.plot(equity["exit_date"], equity["normalized_equity"], linewidth=2.0, label=label)
    plt.axhline(1.0, color="#9CA3AF", linestyle="--", linewidth=1.0)
    plt.axvline(top100_variant().forward_start, color="#DC2626", linestyle="--", linewidth=1.0, alpha=0.7, label="forward start")
    plt.title("cutoff 2025-11 top100: baseline vs Gap-Fix + LHF-Vetos")
    plt.xlabel("Exit date")
    plt.ylabel("Normalized equity")
    plt.grid(True, alpha=0.25)
    plt.legend()
    plt.tight_layout()
    plt.savefig(OUT_DIR / "cutoff_2025_11_top100_live_history_548d_baseline_vs_gap_lhf_equity.png", dpi=150)
    plt.close()


def main() -> None:
    variant = top100_variant()
    print(f"top100_symbols={','.join(variant.symbols)}", flush=True)

    if BASE_LEDGER.exists() and ENRICHED_LEDGER.exists():
        print("reuse existing baseline/enriched ledgers", flush=True)
        base = pd.read_csv(BASE_LEDGER, parse_dates=["entry_date", "exit_date"])
        enriched = pd.read_csv(ENRICHED_LEDGER, parse_dates=["entry_date", "exit_date"])
    else:
        base, context = build_baseline_and_context()
        base.to_csv(BASE_LEDGER, index=False)
        enriched = enrich_ledger(base, context)
        enriched.to_csv(ENRICHED_LEDGER, index=False)

    baseline = write_recalc_portfolio_outputs(BASE_LEDGER, BASE_STEM, variant)

    if GAP_LEDGER.exists() and GAP_AUDIT.exists():
        print("reuse existing gap-fix ledger", flush=True)
        gap = pd.read_csv(GAP_LEDGER, parse_dates=["entry_date", "exit_date"])
        gap_audit = pd.read_csv(GAP_AUDIT, parse_dates=["entry_date", "exit_date"])
    else:
        gap, gap_audit = apply_gap_fix(base, enriched)
        gap.to_csv(GAP_LEDGER, index=False)
        gap_audit.to_csv(GAP_AUDIT, index=False)

    gap_recalc = write_recalc_portfolio_outputs(GAP_LEDGER, GAP_STEM, variant)
    gap_fixed = write_fixed_pair_portfolio_outputs(GAP_LEDGER, GAP_STEM, variant)

    if COMBO_LEDGER.exists() and COMBO_AUDIT.exists():
        print("reuse existing combo ledger", flush=True)
        combo_audit = pd.read_csv(COMBO_AUDIT, parse_dates=["entry_date", "exit_date"])
    else:
        combo, combo_audit = apply_lhf_vetoes(gap, enriched)
        combo.to_csv(COMBO_LEDGER, index=False)
        combo_audit.to_csv(COMBO_AUDIT, index=False)

    combo_recalc = write_recalc_portfolio_outputs(COMBO_LEDGER, COMBO_STEM, variant)
    combo_fixed = write_fixed_pair_portfolio_outputs(COMBO_LEDGER, COMBO_STEM, variant)

    stats = pd.DataFrame(
        [
            compact_stats("baseline_recalc_pairs", baseline, variant.forward_start),
            compact_stats("gapfix_recalc_pairs", gap_recalc, variant.forward_start),
            compact_stats("gapfix_fixed_pairs", gap_fixed, variant.forward_start),
            compact_stats("gapfix_lhf_veto_recalc_pairs", combo_recalc, variant.forward_start),
            compact_stats("gapfix_lhf_veto_fixed_pairs", combo_fixed, variant.forward_start),
        ]
    )
    stats.to_csv(OUT_DIR / "top100_gap_lhf_rule_test_comparison.csv", index=False)
    write_comparison_plot(
        [
            ("baseline", baseline.equity),
            ("gapfix fixed pairs", gap_fixed.equity),
            ("gapfix + LHF veto fixed pairs", combo_fixed.equity),
        ]
    )

    print("\ncomparison")
    print(stats.round(4).to_string(index=False))
    if not gap_audit.empty:
        print(
            "\ngap_audit "
            f"managed={len(gap_audit)} "
            f"point_delta={pd.to_numeric(gap_audit['point_delta'], errors='coerce').sum():.2f}"
        )
    if not combo_audit.empty:
        print(
            "lhf_veto_audit "
            f"vetoed={len(combo_audit)} "
            f"points_removed={pd.to_numeric(combo_audit['points'], errors='coerce').sum():.2f}"
        )


if __name__ == "__main__":
    main()
