"""Test broken-support inclusion in the failed-countermove fresh stop.

This is research only.  It reuses the preregistered 101-signal universe and
changes one causal input:

    stop = max(fail zone, prior afternoon high, opening high, broken support)
           + ATR tolerance

The production strategy is not modified by this script.
"""

from __future__ import annotations

import math
from pathlib import Path

import pandas as pd

from scripts import backtest_failed_countermove_fresh_confirmation as base


OUTPUT_DIR = Path(
    r"D:\Trading\journal\research\failed_countermove_broken_support_stop_20260831"
)
PRIMARY_END = pd.Timestamp("2026-08-12")


def simulate_broken_support_variant(
    row: pd.Series,
    context: dict[str, object],
    bars: pd.DataFrame,
) -> dict[str, object]:
    entry_dt = pd.Timestamp(row["entry_date"])
    entry_day = entry_dt.normalize()
    entry = float(row["entry"])
    target = float(context["failed_countermove_target"])
    tolerance = float(context["atr_tolerance"])
    atr14 = float(context["atr14"])
    fail_zone = float(context["failed_countermove_fail_zone"])
    prior_high = base.previous_afternoon_high(bars, entry_day)
    opening = base.opening_window(bars, entry_day, entry_dt.strftime("%H:%M"))
    opening_high = float(opening["high"].max())
    raw_support = context.get("broken_support")
    broken_support = (
        float(raw_support)
        if raw_support is not None and math.isfinite(float(raw_support))
        else float("-inf")
    )
    stop_without_support = max(fail_zone, prior_high, opening_high) + tolerance
    stop = max(fail_zone, prior_high, opening_high, broken_support) + tolerance
    risk = stop - entry
    reward = entry - target
    crv = reward / risk if risk > 0 else float("-inf")
    stop_changed = stop > stop_without_support + 1e-12
    support_distance_atr = (
        (broken_support - stop_without_support) / atr14
        if math.isfinite(broken_support) and atr14 > 0
        else float("inf")
    )
    if risk <= 0.0 or crv < 1.0:
        return {
            "executed": False,
            "entry": entry,
            "target": target,
            "stop_without_support": stop_without_support,
            "stop": stop,
            "broken_support": broken_support,
            "stop_changed": stop_changed,
            "support_distance_atr": support_distance_atr,
            "crv": crv,
            "quantity": 0,
            "exit_date": pd.NaT,
            "exit": float("nan"),
            "exit_reason": "crv_below_1_after_broken_support_stop",
            "pnl": 0.0,
        }
    quantity = math.floor(base.RISK_DOLLARS / risk)
    end_day = PRIMARY_END if entry_day <= PRIMARY_END else entry_day
    result = base.simulate_exit(
        bars,
        entry_dt,
        entry,
        stop,
        target,
        end_day + pd.Timedelta(hours=15, minutes=55),
        include_entry_bar=False,
    )
    pnl = (entry - float(result["exit"])) * quantity
    return {
        "executed": True,
        "entry": entry,
        "target": target,
        "stop_without_support": stop_without_support,
        "stop": stop,
        "broken_support": broken_support,
        "stop_changed": stop_changed,
        "support_distance_atr": support_distance_atr,
        "crv": crv,
        "quantity": quantity,
        "exit_date": result["exit_date"],
        "exit": result["exit"],
        "exit_reason": result["exit_reason"],
        "pnl": pnl,
    }


def stats(
    frame: pd.DataFrame,
    pnl_column: str,
    executed_column: str = "executed",
) -> dict[str, object]:
    pnl = pd.to_numeric(frame[pnl_column], errors="coerce").fillna(0.0)
    gains = float(pnl[pnl > 0].sum())
    losses = float(-pnl[pnl < 0].sum())
    return {
        "signals": int(len(frame)),
        "executed": int(frame[executed_column].astype(bool).sum()),
        "wins": int((pnl > 0).sum()),
        "losses": int((pnl < 0).sum()),
        "net_pnl": float(pnl.sum()),
        "profit_factor": gains / losses if losses > 0 else float("inf"),
    }


def max_drawdown(frame: pd.DataFrame, pnl_column: str, exit_column: str) -> float:
    ordered = frame.dropna(subset=[exit_column]).sort_values([exit_column, "signal_key"])
    equity = pd.to_numeric(ordered[pnl_column], errors="coerce").fillna(0.0).cumsum()
    return float((equity - equity.cummax()).min()) if not equity.empty else 0.0


def main() -> None:
    trades = base.load_target_trades(base.DEFAULT_ARTIFACT_DIR / "trades.csv")
    keys = set(trades["signal_key"].astype(str))
    contexts = base.load_contexts(
        base.DEFAULT_ARTIFACT_DIR / "decision_records.jsonl", keys
    )
    bars_by_symbol = {
        symbol: base.load_symbol_bars(base.DEFAULT_RAW_DIR, symbol)
        for symbol in sorted(set(trades["symbol"].astype(str)))
    }
    current_rows: list[dict[str, object]] = []
    support_rows: list[dict[str, object]] = []
    current_variant = base.Variant("fresh_stop_structure_only", None, min_stop_atr=0.0)
    for _, trade in trades.iterrows():
        key = str(trade["signal_key"])
        symbol = str(trade["symbol"])
        day = pd.Timestamp(trade["entry_date"]).normalize()
        end_day = PRIMARY_END if day <= PRIMARY_END else day
        current = base.simulate_trade(
            trade,
            contexts[key],
            bars_by_symbol[symbol],
            current_variant,
            end_day + pd.Timedelta(hours=15, minutes=55),
        )
        current_rows.append(
            {
                "signal_key": key,
                "current_executed": current["executed"],
                "current_stop": current["stop_new"],
                "current_exit_date": current["exit_date_new"],
                "current_crv": (
                    (current["entry_new"] - current["target_new"])
                    / (current["stop_new"] - current["entry_new"])
                    if current["executed"]
                    else float("nan")
                ),
                "current_pnl": current["pnl_new"],
            }
        )
        support_rows.append(
            {
                "signal_key": key,
                "symbol": symbol,
                "entry_date": trade["entry_date"],
                **simulate_broken_support_variant(
                    trade, contexts[key], bars_by_symbol[symbol]
                ),
            }
        )

    audit = pd.DataFrame(support_rows).merge(
        pd.DataFrame(current_rows), on="signal_key", validate="one_to_one"
    )
    audit["pnl_delta"] = audit["pnl"] - audit["current_pnl"]
    primary = audit[pd.to_datetime(audit["entry_date"]).dt.normalize() <= PRIMARY_END]
    summaries: list[dict[str, object]] = []
    for scope, frame in (
        ("primary_through_2026_08_12", primary),
        ("all_101_signals", audit),
    ):
        summaries.append(
            {
                "scope": scope,
                "variant": "current_fresh_stop",
                **stats(frame, "current_pnl", "current_executed"),
                "max_drawdown": max_drawdown(frame, "current_pnl", "current_exit_date"),
            }
        )
        summaries.append(
            {
                "scope": scope,
                "variant": "all_broken_support_levels",
                **stats(frame, "pnl"),
                "max_drawdown": max_drawdown(frame, "pnl", "exit_date"),
            }
        )
        for cap in (0.05, 0.10, 0.15, 0.25, 0.50):
            key = f"pnl_cap_{cap:.2f}atr"
            executed_key = f"executed_cap_{cap:.2f}atr"
            exit_key = f"exit_cap_{cap:.2f}atr"
            use_support = frame["stop_changed"] & frame["support_distance_atr"].le(cap)
            audit.loc[frame.index, key] = frame["current_pnl"].where(~use_support, frame["pnl"])
            audit.loc[frame.index, executed_key] = frame["current_executed"].where(~use_support, frame["executed"])
            audit.loc[frame.index, exit_key] = frame["current_exit_date"].where(
                ~use_support, frame["exit_date"]
            ).fillna(frame["entry_date"])
            scoped = audit.loc[frame.index]
            summaries.append(
                {
                    "scope": scope,
                    "variant": f"nearby_support_max_{cap:.2f}atr",
                    **stats(scoped, key, executed_key),
                    "changed_signals": int(use_support.sum()),
                    "max_drawdown": max_drawdown(scoped, key, exit_key),
                }
            )
    summary = pd.DataFrame(summaries)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    audit.to_csv(OUTPUT_DIR / "trade_audit.csv", index=False)
    summary.to_csv(OUTPUT_DIR / "summary.csv", index=False)
    print(summary.round(3).to_string(index=False))
    print("\nChanged stop signals")
    changed = audit[audit["stop_changed"]].copy()
    print(
        changed[
            [
                "symbol", "entry_date", "broken_support", "stop_without_support",
                "stop", "crv", "current_pnl", "pnl", "pnl_delta", "exit_reason",
            ]
        ].round(3).to_string(index=False)
    )
    print(f"\nwrote={OUTPUT_DIR}")


if __name__ == "__main__":
    main()
