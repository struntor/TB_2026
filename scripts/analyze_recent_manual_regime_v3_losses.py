from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd


BASELINE_DIR = Path(
    r"D:\Trading\journal\baselines\manual-regime-baseline@1.0.0"
    r"\2024-01-01_2026-08-13"
)
V3_AUDIT_PATH = Path(
    r"D:\Trading\journal\research\manual_regime_v3_full_equity_2026_08_13"
    r"\trade_audit.csv"
)
FRESH_AUDIT_PATH = Path(
    r"D:\Trading\journal\research\failed_countermove_fresh_confirmation"
    r"\trade_audit.csv"
)
RAW_DIR = Path(r"D:\Trading\Rohdaten")
OUTPUT_DIR = Path(
    r"D:\Trading\journal\research\manual_regime_v3_recent_losses_2026_08_13"
)
RECENT_EXIT_START = pd.Timestamp("2026-08-01")
END = pd.Timestamp("2026-08-13 23:59:59")
TARGET_RULE = "bearish_failed_countermove_short_baseline"


def load_contexts(signal_keys: set[str]) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    with (BASELINE_DIR / "decision_records.jsonl").open(
        "r", encoding="utf-8"
    ) as handle:
        for line in handle:
            record = json.loads(line)
            key = str(record.get("signal_key", ""))
            if key in signal_keys:
                result[key] = dict(record.get("context") or {})
    return result


def load_recent_losses() -> pd.DataFrame:
    audit = pd.read_csv(V3_AUDIT_PATH, parse_dates=["entry_date", "exit_date"])
    audit = audit[
        (audit["variant"] == "V3 current")
        & audit["executed"].astype(bool)
        & audit["pnl"].lt(0)
        & audit["exit_date"].ge(RECENT_EXIT_START)
    ].copy()
    source = pd.read_csv(
        BASELINE_DIR / "trades.csv",
        parse_dates=["entry_date", "exit_date"],
    )[
        [
            "signal_key",
            "entry",
            "stop",
            "target",
            "side",
            "regime_at_entry",
            "size",
        ]
    ]
    audit = audit.merge(source, on="signal_key", how="left", validate="one_to_one")

    fresh = pd.read_csv(FRESH_AUDIT_PATH)
    fresh = fresh[fresh["variant"] == "fresh_stop_structure_only"][
        ["signal_key", "entry_new", "stop_new", "target_new"]
    ].drop_duplicates("signal_key")
    audit = audit.merge(fresh, on="signal_key", how="left", validate="one_to_one")
    target = audit["rule"].eq(TARGET_RULE)
    audit.loc[target, "entry"] = audit.loc[target, "entry_new"]
    audit.loc[target, "stop"] = audit.loc[target, "stop_new"]
    audit.loc[target, "target"] = audit.loc[target, "target_new"]
    return audit


def enrich_path_stats(losses: pd.DataFrame) -> pd.DataFrame:
    contexts = load_contexts(set(losses["signal_key"].astype(str)))
    rows: list[dict[str, object]] = []
    for _, row in losses.iterrows():
        bars = pd.read_csv(
            RAW_DIR / f"Rohdaten_{str(row['symbol']).upper()}.csv",
            usecols=["date", "open", "high", "low", "close", "volume"],
            parse_dates=["date"],
        ).sort_values("date")
        window = bars[
            bars["date"].between(pd.Timestamp(row["entry_date"]), pd.Timestamp(row["exit_date"]))
        ].copy()
        entry = float(row["entry"])
        stop = float(row["stop"])
        target = float(row["target"])
        risk = abs(entry - stop)
        if str(row["side"]).lower() == "short":
            favorable = entry - window["low"]
            adverse = window["high"] - entry
            target_r = (entry - target) / risk
        else:
            favorable = window["high"] - entry
            adverse = entry - window["low"]
            target_r = (target - entry) / risk
        mfe_index = favorable.idxmax()
        context = contexts.get(str(row["signal_key"]), {})
        rows.append(
            {
                **row.to_dict(),
                "risk_points": risk,
                "target_r": target_r,
                "mfe_r": float(favorable.max() / risk),
                "mae_r": float(adverse.max() / risk),
                "mfe_time": window.loc[mfe_index, "date"],
                "reached_0p5r": bool(favorable.ge(0.5 * risk).any()),
                "reached_1r": bool(favorable.ge(risk).any()),
                "holding_sessions": int(window["date"].dt.normalize().nunique()),
                "atr14": context.get("atr14"),
                "daily_structure_phase": context.get("daily_structure_phase"),
                "big_picture_regime": context.get("big_picture_regime"),
                "big_picture_state": context.get("big_picture_state"),
                "volume_price_phase": context.get("volume_price_phase"),
                "bearish_countermove_subregime": context.get(
                    "bearish_countermove_subregime"
                ),
            }
        )
    return pd.DataFrame(rows)


def rule_history() -> pd.DataFrame:
    audit = pd.read_csv(V3_AUDIT_PATH)
    audit = audit[(audit["variant"] == "V3 current") & audit["executed"].astype(bool)]
    rows: list[dict[str, object]] = []
    for rule, group in audit.groupby("rule"):
        pnl = pd.to_numeric(group["pnl"], errors="coerce").fillna(0.0)
        gains = pnl[pnl > 0].sum()
        losses = -pnl[pnl < 0].sum()
        rows.append(
            {
                "rule": rule,
                "trades": len(group),
                "wins": int(pnl.gt(0).sum()),
                "losses": int(pnl.lt(0).sum()),
                "win_rate_pct": 100.0 * pnl.gt(0).mean(),
                "net_pnl": pnl.sum(),
                "profit_factor": gains / losses if losses > 0 else np.inf,
            }
        )
    return pd.DataFrame(rows).sort_values("net_pnl", ascending=False)


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    losses = enrich_path_stats(load_recent_losses())
    rules = rule_history()
    losses.to_csv(OUTPUT_DIR / "recent_losses_enriched.csv", index=False)
    rules.to_csv(OUTPUT_DIR / "rule_history.csv", index=False)
    columns = [
        "symbol",
        "rule",
        "entry_date",
        "exit_date",
        "exit_reason",
        "pnl",
        "risk_multiplier",
        "holding_sessions",
        "mfe_r",
        "mae_r",
        "reached_0p5r",
        "reached_1r",
        "daily_structure_phase",
        "big_picture_regime",
        "volume_price_phase",
    ]
    print(losses[columns].round(3).sort_values("pnl").to_string(index=False))
    recent_rules = set(losses["rule"])
    print("\nRule history")
    print(
        rules[rules["rule"].isin(recent_rules)].round(3).to_string(index=False)
    )
    print(f"\nwrote={OUTPUT_DIR}")


if __name__ == "__main__":
    main()
