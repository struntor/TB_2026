from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


RULE = "bearish_failed_countermove_short_baseline"
DEFAULT_ARTIFACT_DIR = Path(
    r"D:\Trading\journal\baselines\manual-regime-baseline@1.0.0"
    r"\2024-01-01_2026-08-13"
)
DEFAULT_RAW_DIR = Path(r"D:\Trading\Rohdaten")
DEFAULT_OUTPUT_DIR = Path(
    r"D:\Trading\journal\research\failed_countermove_fresh_confirmation"
)
RISK_DOLLARS = 100.0


@dataclass(frozen=True)
class Variant:
    name: str
    deadline_bar_time: str | None
    require_rejection: bool = False
    allow_momentum: bool = False
    fresh_stop: bool = True
    min_stop_atr: float = 0.15


VARIANTS = (
    Variant("baseline", None, fresh_stop=False, min_stop_atr=0.0),
    Variant("fresh_stop_structure_only", None, min_stop_atr=0.0),
    Variant("fresh_stop_0p10atr_now", None, min_stop_atr=0.10),
    Variant("fresh_stop_0p15atr_now", None),
    Variant("fresh_stop_0p20atr_now", None, min_stop_atr=0.20),
    Variant("fresh_stop_0p25atr_now", None, min_stop_atr=0.25),
    Variant(
        "confirm_30m_reject_or_momentum",
        "09:55",
        require_rejection=True,
        allow_momentum=True,
    ),
    Variant(
        "confirm_60m_rejection_only",
        "10:25",
        require_rejection=True,
        allow_momentum=False,
    ),
    Variant(
        "confirm_60m_reject_or_momentum",
        "10:25",
        require_rejection=True,
        allow_momentum=True,
    ),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-dir", type=Path, default=DEFAULT_ARTIFACT_DIR)
    parser.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--primary-end-day",
        default="2026-08-12",
        help="Last day included in primary metrics; AMZN 2026-08-13 stays OOS.",
    )
    return parser.parse_args()


def load_target_trades(path: Path) -> pd.DataFrame:
    trades = pd.read_csv(path, parse_dates=["entry_date", "exit_date"])
    selected = trades[
        (trades["track"] == "baseline")
        & (trades["rule"] == RULE)
        & (trades["side"] == "short")
    ].copy()
    selected = selected.sort_values(["entry_date", "symbol"]).reset_index(drop=True)
    selected["trade_id"] = np.arange(1, len(selected) + 1)
    return selected


def load_contexts(path: Path, signal_keys: set[str]) -> dict[str, dict[str, Any]]:
    contexts: dict[str, dict[str, Any]] = {}
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            record = json.loads(line)
            key = str(record.get("signal_key", ""))
            if key not in signal_keys:
                continue
            if record.get("primary_rule") != RULE or record.get("track") != "baseline":
                continue
            context = dict(record.get("context") or {})
            required = {
                "atr14",
                "atr_tolerance",
                "failed_countermove_fail_zone",
                "failed_countermove_target",
            }
            if required.issubset(context):
                contexts[key] = context
    missing = signal_keys - set(contexts)
    if missing:
        raise RuntimeError(f"Missing decision contexts for {len(missing)} signals")
    return contexts


def load_symbol_bars(raw_dir: Path, symbol: str) -> pd.DataFrame:
    bars = pd.read_csv(
        raw_dir / f"Rohdaten_{symbol}.csv",
        usecols=["date", "open", "high", "low", "close"],
        parse_dates=["date"],
    ).sort_values("date")
    bars["day"] = bars["date"].dt.normalize()
    bars["time"] = bars["date"].dt.strftime("%H:%M")
    return bars


def previous_afternoon_high(bars: pd.DataFrame, entry_day: pd.Timestamp) -> float:
    prior_days = bars.loc[bars["day"] < entry_day, "day"]
    if prior_days.empty:
        raise RuntimeError(f"No prior session before {entry_day.date()}")
    prior_day = prior_days.max()
    afternoon = bars[
        (bars["day"] == prior_day)
        & (bars["time"] >= "12:00")
        & (bars["time"] <= "15:55")
    ]
    if afternoon.empty:
        afternoon = bars[bars["day"] == prior_day]
    return float(afternoon["high"].max())


def opening_window(
    bars: pd.DataFrame,
    entry_day: pd.Timestamp,
    through_time: str,
) -> pd.DataFrame:
    return bars[
        (bars["day"] == entry_day)
        & (bars["time"] >= "09:30")
        & (bars["time"] <= through_time)
    ].copy()


def next_bar(bars: pd.DataFrame, timestamp: pd.Timestamp) -> pd.Series | None:
    later = bars[bars["date"] > timestamp]
    if later.empty or later.iloc[0]["day"] != timestamp.normalize():
        return None
    return later.iloc[0]


def find_confirmation(
    bars: pd.DataFrame,
    entry_day: pd.Timestamp,
    decision_dt: pd.Timestamp,
    deadline_bar_time: str,
    fail_zone: float,
    tolerance: float,
    allow_momentum: bool,
) -> tuple[pd.Series | None, str, float]:
    initial = opening_window(bars, entry_day, decision_dt.strftime("%H:%M"))
    if initial.empty:
        return None, "missing_opening_bars", float("nan")
    opening_low = float(initial["low"].min())
    observed_high = float(initial["high"].max())
    candidates = bars[
        (bars["day"] == entry_day)
        & (bars["date"] > decision_dt)
        & (bars["time"] <= deadline_bar_time)
    ]
    for _, bar in candidates.iterrows():
        observed_high = max(observed_high, float(bar["high"]))
        rejection = float(bar["high"]) >= fail_zone and float(bar["close"]) < fail_zone
        momentum = (
            allow_momentum
            and float(bar["close"]) < opening_low - 0.10 * tolerance
        )
        if rejection or momentum:
            entry_bar = next_bar(bars, pd.Timestamp(bar["date"]))
            reason = "rejection" if rejection else "momentum"
            return entry_bar, reason, observed_high
    return None, "no_confirmation", observed_high


def simulate_exit(
    bars: pd.DataFrame,
    entry_dt: pd.Timestamp,
    entry: float,
    stop: float,
    target: float,
    end_dt: pd.Timestamp,
    include_entry_bar: bool,
) -> dict[str, Any]:
    if include_entry_bar:
        window = bars[(bars["date"] >= entry_dt) & (bars["date"] <= end_dt)]
    else:
        window = bars[(bars["date"] > entry_dt) & (bars["date"] <= end_dt)]
    for _, bar in window.iterrows():
        stop_hit = float(bar["high"]) >= stop
        target_hit = float(bar["low"]) <= target
        if stop_hit:
            return {
                "exit_date": pd.Timestamp(bar["date"]),
                "exit": stop,
                "exit_reason": "stop_same_bar_conservative" if target_hit else "stop",
                "marked_open": False,
            }
        if target_hit:
            return {
                "exit_date": pd.Timestamp(bar["date"]),
                "exit": target,
                "exit_reason": "target",
                "marked_open": False,
            }
    known = bars[bars["date"] <= end_dt]
    if known.empty:
        raise RuntimeError(f"No closing bar available at {end_dt}")
    final = known.iloc[-1]
    return {
        "exit_date": pd.Timestamp(final["date"]),
        "exit": float(final["close"]),
        "exit_reason": "end_of_test_mark",
        "marked_open": True,
    }


def baseline_result(row: pd.Series) -> dict[str, Any]:
    return {
        "executed": True,
        "confirmation_reason": "baseline_immediate",
        "entry_date_new": row["entry_date"],
        "entry_new": float(row["entry"]),
        "stop_new": float(row["stop"]),
        "target_new": float(row["target"]),
        "quantity_new": int(row["quantity"]),
        "exit_date_new": row["exit_date"],
        "exit_new": float(row["exit"]),
        "exit_reason_new": str(row["exit_reason"]),
        "pnl_new": float(row["pnl_dollars"]),
        "marked_open": str(row["exit_reason"]) == "end_of_test",
        "prior_afternoon_high": np.nan,
        "observed_confirmation_high": np.nan,
    }


def simulate_trade(
    row: pd.Series,
    context: dict[str, Any],
    bars: pd.DataFrame,
    variant: Variant,
    end_dt: pd.Timestamp,
) -> dict[str, Any]:
    if variant.name == "baseline":
        return baseline_result(row)

    decision_dt = pd.Timestamp(row["entry_date"])
    entry_day = decision_dt.normalize()
    atr = float(context["atr14"])
    tolerance = float(context["atr_tolerance"])
    fail_zone = float(context["failed_countermove_fail_zone"])
    target = float(context["failed_countermove_target"])
    prior_high = previous_afternoon_high(bars, entry_day)
    initial = opening_window(bars, entry_day, decision_dt.strftime("%H:%M"))
    observed_high = float(initial["high"].max())
    confirmation_reason = "fresh_stop_immediate"
    include_entry_bar = False

    if variant.deadline_bar_time is None:
        entry_dt = decision_dt
        entry = float(row["entry"])
    else:
        entry_bar, confirmation_reason, observed_high = find_confirmation(
            bars,
            entry_day,
            decision_dt,
            variant.deadline_bar_time,
            fail_zone,
            tolerance,
            variant.allow_momentum,
        )
        if entry_bar is None:
            return {
                "executed": False,
                "confirmation_reason": confirmation_reason,
                "entry_date_new": pd.NaT,
                "entry_new": np.nan,
                "stop_new": np.nan,
                "target_new": target,
                "quantity_new": 0,
                "exit_date_new": pd.NaT,
                "exit_new": np.nan,
                "exit_reason_new": "not_entered",
                "pnl_new": 0.0,
                "marked_open": False,
                "prior_afternoon_high": prior_high,
                "observed_confirmation_high": observed_high,
            }
        entry_dt = pd.Timestamp(entry_bar["date"])
        entry = float(entry_bar["open"])
        include_entry_bar = True

    structural_stop = max(fail_zone, prior_high, observed_high) + tolerance
    minimum_stop = entry + variant.min_stop_atr * atr
    stop = max(structural_stop, minimum_stop)
    risk = stop - entry
    reward = entry - target
    if risk <= 0.0 or reward / risk < 1.0:
        return {
            "executed": False,
            "confirmation_reason": "crv_below_1_after_fresh_stop",
            "entry_date_new": entry_dt,
            "entry_new": entry,
            "stop_new": stop,
            "target_new": target,
            "quantity_new": 0,
            "exit_date_new": pd.NaT,
            "exit_new": np.nan,
            "exit_reason_new": "not_entered",
            "pnl_new": 0.0,
            "marked_open": False,
            "prior_afternoon_high": prior_high,
            "observed_confirmation_high": observed_high,
        }
    quantity = math.floor(RISK_DOLLARS / risk)
    if quantity < 1:
        raise RuntimeError(f"Zero quantity for {row['symbol']} {entry_dt}")
    exit_result = simulate_exit(
        bars,
        entry_dt,
        entry,
        stop,
        target,
        end_dt,
        include_entry_bar,
    )
    pnl = (entry - float(exit_result["exit"])) * quantity
    return {
        "executed": True,
        "confirmation_reason": confirmation_reason,
        "entry_date_new": entry_dt,
        "entry_new": entry,
        "stop_new": stop,
        "target_new": target,
        "quantity_new": quantity,
        "exit_date_new": exit_result["exit_date"],
        "exit_new": exit_result["exit"],
        "exit_reason_new": exit_result["exit_reason"],
        "pnl_new": pnl,
        "marked_open": exit_result["marked_open"],
        "prior_afternoon_high": prior_high,
        "observed_confirmation_high": observed_high,
    }


def simulate_variant(
    trades: pd.DataFrame,
    contexts: dict[str, dict[str, Any]],
    bars_by_symbol: dict[str, pd.DataFrame],
    variant: Variant,
    primary_end_day: pd.Timestamp,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for _, trade in trades.iterrows():
        trade_day = pd.Timestamp(trade["entry_date"]).normalize()
        end_day = primary_end_day if trade_day <= primary_end_day else trade_day
        end_dt = end_day + pd.Timedelta(hours=15, minutes=55)
        result = simulate_trade(
            trade,
            contexts[str(trade["signal_key"])],
            bars_by_symbol[str(trade["symbol"])],
            variant,
            end_dt,
        )
        rows.append({**trade.to_dict(), "variant": variant.name, **result})
    result = pd.DataFrame(rows)
    result["pnl_delta"] = result["pnl_new"] - result["pnl_dollars"]
    result["risk_new"] = result["stop_new"] - result["entry_new"]
    result["crv_new"] = (
        (result["entry_new"] - result["target_new"]) / result["risk_new"]
    )
    return result


def summarize(frame: pd.DataFrame, primary_end_day: pd.Timestamp) -> dict[str, Any]:
    primary = frame[pd.to_datetime(frame["entry_date"]).dt.normalize() <= primary_end_day]
    executed = primary[primary["executed"]].copy()
    closed = executed[~executed["marked_open"]].copy()
    pnl = executed["pnl_new"].astype(float)
    ordered = executed.sort_values(["exit_date_new", "trade_id"])
    equity = ordered["pnl_new"].cumsum()
    drawdown = equity - equity.cummax().clip(lower=0.0)
    gains = pnl[pnl > 0].sum()
    losses = pnl[pnl < 0].sum()
    top3 = pnl.nlargest(3).sum()
    return {
        "variant": frame["variant"].iloc[0],
        "signals": len(primary),
        "executed": len(executed),
        "skipped": int((~primary["executed"]).sum()),
        "marked_open": int(executed["marked_open"].sum()),
        "net_pnl": pnl.sum(),
        "delta_vs_baseline_same_signals": primary["pnl_delta"].sum(),
        "win_rate_pct": (pnl > 0).mean() * 100.0 if len(pnl) else 0.0,
        "profit_factor": gains / abs(losses) if losses < 0 else np.inf,
        "max_drawdown": drawdown.min() if len(drawdown) else 0.0,
        "avg_pnl": pnl.mean() if len(pnl) else 0.0,
        "median_pnl": pnl.median() if len(pnl) else 0.0,
        "top3_pnl": top3,
        "net_without_top3": pnl.sum() - top3,
        "closed_trades": len(closed),
    }


def annual_summary(frame: pd.DataFrame, primary_end_day: pd.Timestamp) -> pd.DataFrame:
    data = frame[
        pd.to_datetime(frame["entry_date"]).dt.normalize() <= primary_end_day
    ].copy()
    data["entry_year"] = pd.to_datetime(data["entry_date"]).dt.year
    return (
        data.groupby(["variant", "entry_year"], as_index=False)
        .agg(
            signals=("trade_id", "size"),
            executed=("executed", "sum"),
            pnl=("pnl_new", "sum"),
            baseline_pnl=("pnl_dollars", "sum"),
        )
    )


def plot_equity(all_results: pd.DataFrame, primary_end_day: pd.Timestamp, path: Path) -> None:
    fig, axes = plt.subplots(2, 1, figsize=(13, 9))
    for ax, start, title in [
        (axes[0], pd.Timestamp("2024-01-01"), "Gesamter Test"),
        (axes[1], pd.Timestamp("2026-01-01"), "Nur 2026"),
    ]:
        for name, group in all_results.groupby("variant"):
            subset = group[
                group["executed"]
                & (pd.to_datetime(group["entry_date"]).dt.normalize() <= primary_end_day)
                & (pd.to_datetime(group["exit_date_new"]) >= start)
            ].sort_values(["exit_date_new", "trade_id"])
            if subset.empty:
                continue
            ax.step(
                subset["exit_date_new"],
                subset["pnl_new"].cumsum(),
                where="post",
                linewidth=2.0 if name in {"baseline", "confirm_60m_reject_or_momentum"} else 1.35,
                label=name,
            )
        ax.axhline(0, color="#777777", linewidth=0.8)
        ax.grid(alpha=0.22)
        ax.set_title(title)
        ax.set_ylabel("Kumuliertes P&L (USD)")
        ax.legend(fontsize=8, ncol=2)
    axes[1].set_xlabel("Exit-Zeitpunkt")
    fig.suptitle(f"{RULE}: frische Struktur und Opening-Bestätigung")
    fig.tight_layout()
    fig.savefig(path, dpi=170, bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    trades = load_target_trades(args.artifact_dir / "trades.csv")
    contexts = load_contexts(
        args.artifact_dir / "decision_records.jsonl",
        set(trades["signal_key"].astype(str)),
    )
    bars_by_symbol = {
        symbol: load_symbol_bars(args.raw_dir, symbol)
        for symbol in sorted(trades["symbol"].unique())
    }
    primary_end_day = pd.Timestamp(args.primary_end_day).normalize()
    results = [
        simulate_variant(trades, contexts, bars_by_symbol, variant, primary_end_day)
        for variant in VARIANTS
    ]
    all_results = pd.concat(results, ignore_index=True)
    summary = pd.DataFrame([summarize(result, primary_end_day) for result in results])
    annual = pd.concat(
        [annual_summary(result, primary_end_day) for result in results],
        ignore_index=True,
    )
    oos_amzn = all_results[
        (all_results["symbol"] == "AMZN")
        & (pd.to_datetime(all_results["entry_date"]).dt.normalize() == pd.Timestamp("2026-08-13"))
    ].copy()

    all_results.to_csv(args.output_dir / "trade_audit.csv", index=False)
    summary.to_csv(args.output_dir / "variant_summary.csv", index=False)
    annual.to_csv(args.output_dir / "annual_summary.csv", index=False)
    oos_amzn.to_csv(args.output_dir / "amzn_2026-08-13_oos.csv", index=False)
    plot_equity(all_results, primary_end_day, args.output_dir / "equity_comparison.png")
    print(summary.to_string(index=False, float_format=lambda value: f"{value:.2f}"))
    print("\nAnnual")
    print(annual.to_string(index=False, float_format=lambda value: f"{value:.2f}"))
    print("\nAMZN OOS")
    print(
        oos_amzn[
            [
                "variant", "executed", "confirmation_reason", "entry_date_new",
                "entry_new", "stop_new", "quantity_new", "exit_date_new",
                "exit_new", "exit_reason_new", "pnl_new",
            ]
        ].to_string(index=False, float_format=lambda value: f"{value:.2f}")
    )
    print(f"\nWrote {args.output_dir}")


if __name__ == "__main__":
    main()
