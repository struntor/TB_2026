from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from compare_cutoff_forward_topn_variants import VariantConfig, run_variant
from current_live_baseline_config import LIVE_PAIR_SELECTION_PATH, load_optimized_live_top50_symbols
from simulate_selected_symbols_portfolio_mm import build_trades, load_ledger, simulate_portfolio, split_summary, summarize


OUT_DIR = Path(__file__).resolve().parent
CURRENT_LEDGER = OUT_DIR / "trade_ledger_cutoff_2025_11_top50_live_history_548d_monitor60m_value_middle_early_no_followthrough.csv"
BASE_PAIR_SELECTION = LIVE_PAIR_SELECTION_PATH
OUT_PREFIX = OUT_DIR / "lhf_entry_veto_portfolio_sweep"
BEST_OUTPUT_STEM = "cutoff_2025_11_top50_live_history_548d_monitor60m_early_lhf_veto_combo"

START_CAPITAL = 10_000.0
RISK_PCT = 0.01
MIN_TRADES = 5


@dataclass(frozen=True)
class VetoRule:
    name: str
    conditions: tuple[tuple[str, str], ...]


RULES = [
    VetoRule(
        "veto_bearish_pressure_trend_above_value",
        (
            ("big_picture_regime_fresh", "bearish_pressure"),
            ("daily_structure_phase_fresh", "trend_or_unclassified"),
            ("profile_price_location_fresh", "above_value"),
        ),
    ),
    VetoRule(
        "veto_countermove_bearish_pressure_trend",
        (
            ("signal_regime_fresh", "bearish_countermove"),
            ("big_picture_regime_fresh", "bearish_pressure"),
            ("daily_structure_phase_fresh", "trend_or_unclassified"),
        ),
    ),
    VetoRule(
        "veto_bearish_primary_lower_inside_value_lower",
        (
            ("big_picture_regime_fresh", "bearish_primary"),
            ("daily_structure_phase_fresh", "post_break_range_lower"),
            ("profile_price_location_fresh", "inside_value_lower"),
        ),
    ),
    VetoRule(
        "veto_bearish_confirmed_bullish_primary_upper",
        (
            ("signal_regime_fresh", "bearish_confirmed"),
            ("big_picture_regime_fresh", "bullish_primary"),
            ("daily_structure_phase_fresh", "post_break_range_upper"),
        ),
    ),
    VetoRule(
        "veto_middle_inside_value_low_volume_pullback",
        (
            ("daily_structure_phase_fresh", "post_break_range_middle"),
            ("profile_price_location_fresh", "inside_value_middle"),
            ("volume_price_phase", "low_volume_pullback"),
        ),
    ),
]


def clean(value: object) -> str:
    if pd.isna(value):
        return ""
    text = str(value)
    return "" if text.lower() == "nan" else text


def add_match_columns(df: pd.DataFrame) -> pd.DataFrame:
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


def load_context() -> pd.DataFrame:
    path = OUT_DIR / "top50_loss_damage_trades_enriched.csv"
    df = pd.read_csv(path, parse_dates=["entry_date", "exit_date"])
    df = df[
        df["variant"].astype(str).eq("monitor60_early_no_followthrough_fixed_pairs")
        & df["rule"].astype(str).eq("bearish_lower_high_failure_short")
        & df["side"].astype(str).eq("short")
        & df["executed"].astype(bool)
    ].copy()
    df = add_match_columns(df)
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
        "pnl_dollars",
        "exit_reason",
        "loss_diagnosis",
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
        "exit_reason",
        "loss_diagnosis",
    ]:
        if col in out.columns:
            out[col] = out[col].map(clean)
    return out


def variant_config() -> VariantConfig:
    return VariantConfig(
        name="cutoff_2025-11_top50",
        cutoff="2025-11",
        top_n=50,
        symbols=load_optimized_live_top50_symbols(),
    )


def rule_mask(df: pd.DataFrame, rule: VetoRule) -> pd.Series:
    mask = (
        df["rule"].astype(str).eq("bearish_lower_high_failure_short")
        & df["side"].astype(str).eq("short")
    )
    for col, value in rule.conditions:
        mask &= df[col].map(clean).eq(value)
    return mask


def apply_rules(base: pd.DataFrame, rules: list[VetoRule], context: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    work = add_match_columns(base).merge(
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
        suffixes=("", "_ctx"),
    )
    work["veto_rule"] = ""
    veto_mask = pd.Series(False, index=work.index)
    audit_rows = []
    for rule in rules:
        mask = rule_mask(work, rule) & ~veto_mask
        if mask.any():
            work.loc[mask, "veto_rule"] = rule.name
            veto_mask |= mask
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
                        "pnl_dollars_context": subset.get("pnl_dollars", pd.NA),
                        "exit_reason_context": subset.get("exit_reason_ctx", subset.get("exit_reason", "")),
                        "loss_diagnosis": subset.get("loss_diagnosis", ""),
                        "signal_regime_fresh": subset.get("signal_regime_fresh", ""),
                        "big_picture_regime_fresh": subset.get("big_picture_regime_fresh", ""),
                        "daily_structure_phase_fresh": subset.get("daily_structure_phase_fresh", ""),
                        "profile_price_location_fresh": subset.get("profile_price_location_fresh", ""),
                        "volume_price_phase": subset.get("volume_price_phase", ""),
                    }
                )
            )
    out = work.loc[~veto_mask].copy()
    out = out.drop(columns=[col for col in out.columns if col.startswith("_match_") or col.endswith("_ctx")], errors="ignore")
    out = out.drop(
        columns=[
            "veto_rule",
            "signal_regime_fresh",
            "big_picture_regime_fresh",
            "big_picture_state",
            "daily_structure_phase_fresh",
            "bearish_countermove_subregime",
            "profile_price_location_fresh",
            "volume_price_phase",
            "pnl_dollars",
            "loss_diagnosis",
        ],
        errors="ignore",
    )
    if "equity_after_trade" in out.columns:
        out["equity_after_trade"] = pd.to_numeric(out["points"], errors="coerce").fillna(0.0).cumsum()
    audit = pd.concat(audit_rows, ignore_index=True) if audit_rows else pd.DataFrame()
    return out, audit


def run_fixed_pairs(ledger: pd.DataFrame, variant: VariantConfig) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
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
    return trades_df, summarize(trades_df, START_CAPITAL), split_summary(trades_df, START_CAPITAL, variant.forward_start)


def flatten(prefix: str, summary: pd.DataFrame, split: pd.DataFrame) -> dict[str, object]:
    out = {f"{prefix}_{key}": value for key, value in summary.iloc[0].to_dict().items()}
    for part in split.itertuples(index=False):
        out[f"{prefix}_{part.segment}_net_profit"] = float(part.net_profit)
        out[f"{prefix}_{part.segment}_profit_factor"] = float(part.profit_factor)
        out[f"{prefix}_{part.segment}_max_drawdown"] = float(part.max_drawdown)
    return out


def summarize_audit(audit: pd.DataFrame) -> dict[str, object]:
    if audit.empty:
        return {
            "vetoed_trades": 0,
            "vetoed_context_pnl": 0.0,
            "vetoed_wins": 0,
            "vetoed_losses": 0,
        }
    pnl = pd.to_numeric(audit["pnl_dollars_context"], errors="coerce").fillna(0.0)
    return {
        "vetoed_trades": int(len(audit)),
        "vetoed_context_pnl": float(pnl.sum()),
        "vetoed_wins": int((pnl > 0.0).sum()),
        "vetoed_losses": int((pnl <= 0.0).sum()),
    }


def main() -> None:
    base = load_ledger(CURRENT_LEDGER)
    context = load_context()
    variant = variant_config()
    rows = []
    audits = []

    variants: list[tuple[str, list[VetoRule]]] = [(rule.name, [rule]) for rule in RULES]

    # Add cumulative variants in the same order as the candidate list.
    cumulative: list[VetoRule] = []
    for rule in RULES:
        cumulative = [*cumulative, rule]
        variants.append(("cumulative_" + "_plus_".join(r.name.replace("veto_", "") for r in cumulative), cumulative))

    for name, rules in variants:
        print(name, flush=True)
        ledger, audit = apply_rules(base, rules, context)
        fixed_trades, fixed_summary, fixed_split = run_fixed_pairs(ledger, variant)
        recalc_trades, recalc_summary, recalc_split, _, _ = run_variant(ledger, variant, MIN_TRADES)
        audit_summary = summarize_audit(audit)
        rows.append(
            {
                "variant_name": name,
                "rules": ",".join(rule.name for rule in rules),
                **audit_summary,
                "fixed_executed_trades": int(fixed_trades["executed"].sum()),
                "recalc_executed_trades": int(recalc_trades["executed"].sum()),
                **flatten("fixed", fixed_summary, fixed_split),
                **flatten("recalc", recalc_summary, recalc_split),
            }
        )
        if not audit.empty:
            audit["variant_name"] = name
            audits.append(audit)

        if len(rules) == len(RULES):
            best_prefix = OUT_DIR / BEST_OUTPUT_STEM
            ledger.to_csv(OUT_DIR / f"trade_ledger_{BEST_OUTPUT_STEM}.csv", index=False)
            fixed_trades.assign(variant=variant.name).to_csv(best_prefix.with_name(best_prefix.name + "_fixed_pairs_trades.csv"), index=False)
            fixed_summary.assign(variant=variant.name).to_csv(best_prefix.with_name(best_prefix.name + "_fixed_pairs_summary.csv"), index=False)
            fixed_split.assign(variant=variant.name).to_csv(best_prefix.with_name(best_prefix.name + "_fixed_pairs_split_summary.csv"), index=False)
            recalc_trades.assign(variant=variant.name).to_csv(best_prefix.with_name(best_prefix.name + "_trades.csv"), index=False)
            recalc_summary.assign(variant=variant.name).to_csv(best_prefix.with_name(best_prefix.name + "_summary.csv"), index=False)
            recalc_split.assign(variant=variant.name).to_csv(best_prefix.with_name(best_prefix.name + "_split_summary.csv"), index=False)

    summary = pd.DataFrame(rows).sort_values(["fixed_net_profit", "recalc_net_profit"], ascending=[False, False])
    audit_df = pd.concat(audits, ignore_index=True) if audits else pd.DataFrame()
    summary.to_csv(f"{OUT_PREFIX}_summary.csv", index=False)
    audit_df.to_csv(f"{OUT_PREFIX}_audit.csv", index=False)

    show_cols = [
        "variant_name",
        "vetoed_trades",
        "vetoed_context_pnl",
        "vetoed_wins",
        "vetoed_losses",
        "fixed_net_profit",
        "fixed_profit_factor",
        "fixed_post_split_net_profit",
        "fixed_max_drawdown",
        "recalc_net_profit",
        "recalc_profit_factor",
    ]
    print(summary[show_cols].round(3).to_string(index=False))
    print(f"\nwrote {OUT_PREFIX}_summary.csv")
    print(f"wrote {OUT_PREFIX}_audit.csv")


if __name__ == "__main__":
    main()
