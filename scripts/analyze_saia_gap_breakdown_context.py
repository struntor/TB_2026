from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd


BASELINE_DIR = Path(
    r"D:\Trading\journal\baselines\manual-regime-baseline@1.0.0"
    r"\2024-01-01_2026-08-13"
)
AUDIT_PATH = Path(
    r"D:\Trading\journal\research\manual_regime_v3_full_equity_2026_08_13"
    r"\trade_audit.csv"
)
OUTPUT_DIR = Path(
    r"D:\Trading\journal\research\saia_gap_breakdown_context_2026_08_14"
)
RULE = "bearish_gap_breakdown_continuation_short"


def metrics(frame: pd.DataFrame) -> dict[str, float | int]:
    pnl = frame["pnl"].astype(float)
    gross_profit = pnl[pnl > 0].sum()
    gross_loss = -pnl[pnl < 0].sum()
    return {
        "trades": len(frame),
        "wins": int(pnl.gt(0).sum()),
        "losses": int(pnl.lt(0).sum()),
        "win_rate_pct": 100.0 * pnl.gt(0).mean() if len(frame) else np.nan,
        "net_pnl": pnl.sum(),
        "avg_pnl": pnl.mean() if len(frame) else np.nan,
        "profit_factor": gross_profit / gross_loss if gross_loss else np.inf,
    }


def main() -> None:
    audit = pd.read_csv(AUDIT_PATH, parse_dates=["entry_date", "exit_date"])
    trades = audit[
        (audit["variant"] == "V3 current")
        & audit["executed"].astype(bool)
        & audit["rule"].eq(RULE)
    ].copy()
    keys = set(trades["signal_key"].astype(str))

    context_rows: list[dict[str, object]] = []
    with (BASELINE_DIR / "decision_records.jsonl").open("r", encoding="utf-8") as handle:
        for line in handle:
            record = json.loads(line)
            key = str(record.get("signal_key", ""))
            if key not in keys:
                continue
            context = record.get("context") or {}
            context_rows.append(
                {
                    "signal_key": key,
                    "profile_price_location": context.get("profile_price_location"),
                    "range_lower_subtype": context.get("range_lower_subtype"),
                    "true_breakdown_escalation": context.get("true_breakdown_escalation"),
                    "decision_vs_poc_atr": context.get("range_lower_decision_vs_poc_atr"),
                    "decision_vs_val_atr": context.get(
                        "range_lower_decision_vs_value_low_atr"
                    ),
                    "range_lower_risk_score": context.get("range_lower_risk_score"),
                    "profile_balance_score": context.get("profile_balance_score"),
                    "profile_value_width_atr": context.get("profile_value_width_atr"),
                    "volume_price_phase": context.get("volume_price_phase"),
                    "mature_break": context.get("mature_break"),
                    "bars_since_support_break": context.get("bars_since_support_break"),
                }
            )

    enriched = trades.merge(
        pd.DataFrame(context_rows), on="signal_key", how="left", validate="one_to_one"
    )
    inside_lower = enriched["profile_price_location"].eq("inside_value_lower")
    attempt = enriched["range_lower_subtype"].eq("breakdown_attempt")
    no_escalation = ~enriched["true_breakdown_escalation"].fillna(False).astype(bool)
    near_poc = enriched["decision_vs_poc_atr"].abs().le(0.15)
    at_or_above_poc = enriched["decision_vs_poc_atr"].ge(0)

    masks = {
        "all_gap_breakdown": pd.Series(True, index=enriched.index),
        "inside_value_lower": inside_lower,
        "inside_lower_breakdown_attempt": inside_lower & attempt,
        "saia_broad_no_escalation": inside_lower & attempt & no_escalation,
        "saia_near_poc_0p15atr": inside_lower & attempt & no_escalation & near_poc,
        "saia_at_or_above_poc": inside_lower & attempt & no_escalation & at_or_above_poc,
    }
    summary = pd.DataFrame(
        [{"segment": name, **metrics(enriched[mask])} for name, mask in masks.items()]
    )

    broad = masks["saia_broad_no_escalation"]
    affected = enriched[broad].sort_values("entry_date")
    base = metrics(enriched)
    variants: list[dict[str, float | int | str]] = []
    for label, multiplier in (("veto", 0.0), ("half_size", 0.5)):
        adjusted = enriched.copy()
        adjusted.loc[broad, "pnl"] *= multiplier
        result = metrics(adjusted)
        variants.append(
            {
                "variant": label,
                "affected_trades": int(broad.sum()),
                "net_pnl": result["net_pnl"],
                "net_change": float(result["net_pnl"]) - float(base["net_pnl"]),
                "profit_factor": result["profit_factor"],
            }
        )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    enriched.to_csv(OUTPUT_DIR / "gap_breakdown_enriched.csv", index=False)
    affected.to_csv(OUTPUT_DIR / "saia_like_trades.csv", index=False)
    summary.to_csv(OUTPUT_DIR / "segment_summary.csv", index=False)
    pd.DataFrame(variants).to_csv(OUTPUT_DIR / "variant_summary.csv", index=False)
    print(summary.round(3).to_string(index=False))
    print("\nVariants")
    print(pd.DataFrame(variants).round(3).to_string(index=False))
    print("\nSAIA-like by year")
    affected = affected.assign(year=affected["entry_date"].dt.year)
    print(
        affected.groupby("year", group_keys=False)
        .apply(lambda group: pd.Series(metrics(group)), include_groups=False)
        .round(3)
        .to_string()
    )
    print("\nRecent SAIA-like trades")
    print(
        affected[
            [
                "entry_date",
                "symbol",
                "pnl",
                "exit_reason",
                "decision_vs_poc_atr",
                "decision_vs_val_atr",
                "range_lower_risk_score",
            ]
        ]
        .tail(20)
        .round(3)
        .to_string(index=False)
    )
    print(f"\nwrote={OUTPUT_DIR}")


if __name__ == "__main__":
    main()
