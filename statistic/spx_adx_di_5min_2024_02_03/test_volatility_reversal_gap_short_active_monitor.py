from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from manual_regime_rules_backtest import classify_intraday_value_profile, load_bars


OUT_DIR = Path(__file__).resolve().parent
ENRICHED_TRADES = OUT_DIR / "top50_regime_holding_time_trades_enriched.csv"
OUT_PREFIX = OUT_DIR / "volatility_reversal_gap_short_active_monitor"


@dataclass(frozen=True)
class MonitorConfig:
    name: str
    interval_minutes: int
    exit_on_poc_reclaim: bool = False
    exit_on_value_middle: bool = False
    exit_on_value_lower_loss: bool = False
    min_age_minutes: int = 0
    no_progress_after_minutes: int | None = None
    no_progress_min_mfe_r: float | None = None
    value_middle_max_profit_r: float | None = None
    value_middle_max_age_minutes: int | None = None
    value_middle_max_mfe_r: float | None = None


CONFIGS = [
    MonitorConfig("baseline", interval_minutes=999999),
    MonitorConfig("15m_poc_reclaim", interval_minutes=15, exit_on_poc_reclaim=True, min_age_minutes=30),
    MonitorConfig("60m_poc_reclaim", interval_minutes=60, exit_on_poc_reclaim=True, min_age_minutes=60),
    MonitorConfig("15m_value_middle", interval_minutes=15, exit_on_value_middle=True, min_age_minutes=30),
    MonitorConfig("60m_value_middle", interval_minutes=60, exit_on_value_middle=True, min_age_minutes=60),
    MonitorConfig(
        "15m_value_middle_loss_only",
        interval_minutes=15,
        exit_on_value_middle=True,
        min_age_minutes=30,
        value_middle_max_profit_r=0.0,
    ),
    MonitorConfig(
        "60m_value_middle_loss_only",
        interval_minutes=60,
        exit_on_value_middle=True,
        min_age_minutes=60,
        value_middle_max_profit_r=0.0,
    ),
    MonitorConfig(
        "15m_value_middle_profit_le_0p25r",
        interval_minutes=15,
        exit_on_value_middle=True,
        min_age_minutes=30,
        value_middle_max_profit_r=0.25,
    ),
    MonitorConfig(
        "60m_value_middle_profit_le_0p25r",
        interval_minutes=60,
        exit_on_value_middle=True,
        min_age_minutes=60,
        value_middle_max_profit_r=0.25,
    ),
    MonitorConfig(
        "15m_value_middle_profit_le_0p50r",
        interval_minutes=15,
        exit_on_value_middle=True,
        min_age_minutes=30,
        value_middle_max_profit_r=0.50,
    ),
    MonitorConfig(
        "60m_value_middle_profit_le_0p50r",
        interval_minutes=60,
        exit_on_value_middle=True,
        min_age_minutes=60,
        value_middle_max_profit_r=0.50,
    ),
    MonitorConfig(
        "60m_value_middle_early_no_followthrough",
        interval_minutes=60,
        exit_on_value_middle=True,
        min_age_minutes=60,
        value_middle_max_age_minutes=360,
        value_middle_max_mfe_r=0.50,
    ),
    MonitorConfig(
        "15m_poc_or_no_progress_4h",
        interval_minutes=15,
        exit_on_poc_reclaim=True,
        min_age_minutes=30,
        no_progress_after_minutes=240,
        no_progress_min_mfe_r=0.35,
    ),
    MonitorConfig(
        "60m_poc_or_no_progress_4h",
        interval_minutes=60,
        exit_on_poc_reclaim=True,
        min_age_minutes=60,
        no_progress_after_minutes=240,
        no_progress_min_mfe_r=0.35,
    ),
    MonitorConfig(
        "15m_value_mid_or_no_progress_4h",
        interval_minutes=15,
        exit_on_value_middle=True,
        min_age_minutes=30,
        no_progress_after_minutes=240,
        no_progress_min_mfe_r=0.35,
    ),
    MonitorConfig(
        "60m_value_mid_or_no_progress_4h",
        interval_minutes=60,
        exit_on_value_middle=True,
        min_age_minutes=60,
        no_progress_after_minutes=240,
        no_progress_min_mfe_r=0.35,
    ),
    MonitorConfig(
        "15m_lower_loss_or_poc",
        interval_minutes=15,
        exit_on_poc_reclaim=True,
        exit_on_value_lower_loss=True,
        min_age_minutes=30,
    ),
    MonitorConfig(
        "60m_lower_loss_or_poc",
        interval_minutes=60,
        exit_on_poc_reclaim=True,
        exit_on_value_lower_loss=True,
        min_age_minutes=60,
    ),
]


def profit_factor(points: pd.Series) -> float:
    gains = float(points[points > 0].sum())
    losses = float(-points[points < 0].sum())
    if losses == 0.0:
        return float("inf") if gains > 0 else 0.0
    return gains / losses


def load_target_trades() -> pd.DataFrame:
    df = pd.read_csv(ENRICHED_TRADES, parse_dates=["entry_date", "exit_date"])
    return df[
        df["rule"].eq("bearish_gap_breakdown_continuation_short")
        & df["bearish_countermove_subregime"].eq("bearish_countermove_volatility_reversal")
        & df["side"].eq("short")
    ].copy()


def is_checkpoint(entry_dt: pd.Timestamp, bar_dt: pd.Timestamp, interval_minutes: int, min_age_minutes: int) -> bool:
    age_minutes = (bar_dt - entry_dt).total_seconds() / 60.0
    if age_minutes < min_age_minutes:
        return False
    if interval_minutes <= 5:
        return True
    return abs(age_minutes % interval_minutes) < 1e-9


def profile_reclaim_state(bars: pd.DataFrame, decision_dt: pd.Timestamp, price: float, atr: float, tol: float) -> dict[str, object]:
    profile = classify_intraday_value_profile(bars, decision_dt, price, atr, tol, lookback_days=10)
    poc = profile.get("profile_poc")
    price_location = str(profile.get("profile_price_location", ""))
    poc_reclaimed = poc is not None and pd.notna(poc) and price >= float(poc)
    value_middle_reclaimed = price_location in {"inside_value_middle", "inside_value_upper", "above_value"}
    lower_loss = price_location == "inside_value_lower" and poc_reclaimed
    return {
        "profile_price_location": price_location,
        "profile_poc": poc,
        "poc_reclaimed": bool(poc_reclaimed),
        "value_middle_reclaimed": bool(value_middle_reclaimed),
        "lower_loss": bool(lower_loss),
    }


def simulate_trade(row: pd.Series, bars: pd.DataFrame, config: MonitorConfig) -> dict[str, object]:
    entry_dt = pd.Timestamp(row["entry_date"])
    original_exit_dt = pd.Timestamp(row["exit_date"])
    if entry_dt.time() == pd.Timestamp("00:00").time():
        entry_dt = entry_dt.normalize() + pd.Timedelta(hours=9, minutes=35)
    if original_exit_dt.time() == pd.Timestamp("00:00").time():
        original_exit_dt = original_exit_dt.normalize() + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1)
    entry = float(row["entry"])
    stop = float(row["stop"])
    target = float(row["target"])
    risk = stop - entry
    atr = float(row.get("atr14", 0.0)) if "atr14" in row and pd.notna(row.get("atr14")) else abs(risk)
    tol = float(row.get("atr_tolerance", 0.0)) if "atr_tolerance" in row and pd.notna(row.get("atr_tolerance")) else 0.10 * max(atr, 1e-9)
    if risk <= 0:
        return {
            "exit_date": original_exit_dt,
            "exit": float(row["exit"]),
            "points": float(row["points"]),
            "exit_reason": "invalid_risk_keep_original",
            "hold_hours": float(row.get("hold_hours", 0.0)),
            "monitor_exit_state": "",
            "max_mfe_r_seen": None,
        }

    window = bars[(bars["date"] > entry_dt) & (bars["date"] <= original_exit_dt)].copy()
    max_mfe_r = 0.0
    last_monitor_state = ""
    for _, bar in window.iterrows():
        dt = pd.Timestamp(bar["date"])
        high = float(bar["high"])
        low = float(bar["low"])
        close = float(bar["close"])
        max_mfe_r = max(max_mfe_r, (entry - low) / risk)

        stop_hit = high >= stop
        target_hit = low <= target
        if stop_hit and target_hit:
            return {
                "exit_date": dt,
                "exit": stop,
                "points": entry - stop,
                "exit_reason": "stop_same_bar_conservative",
                "hold_hours": (dt - entry_dt).total_seconds() / 3600.0,
                "monitor_exit_state": last_monitor_state,
                "max_mfe_r_seen": max_mfe_r,
            }
        if stop_hit:
            return {
                "exit_date": dt,
                "exit": stop,
                "points": entry - stop,
                "exit_reason": "stop",
                "hold_hours": (dt - entry_dt).total_seconds() / 3600.0,
                "monitor_exit_state": last_monitor_state,
                "max_mfe_r_seen": max_mfe_r,
            }
        if target_hit:
            return {
                "exit_date": dt,
                "exit": target,
                "points": entry - target,
                "exit_reason": "target",
                "hold_hours": (dt - entry_dt).total_seconds() / 3600.0,
                "monitor_exit_state": last_monitor_state,
                "max_mfe_r_seen": max_mfe_r,
            }

        if config.name == "baseline":
            continue
        if not is_checkpoint(entry_dt, dt, config.interval_minutes, config.min_age_minutes):
            continue

        state = profile_reclaim_state(bars, dt, close, max(atr, 1e-9), tol)
        last_monitor_state = str(state["profile_price_location"])
        age_minutes = (dt - entry_dt).total_seconds() / 60.0
        exit_reason = None
        if config.exit_on_poc_reclaim and state["poc_reclaimed"]:
            exit_reason = "monitor_poc_reclaim"
        if config.exit_on_value_middle and state["value_middle_reclaimed"]:
            current_profit_r = (entry - close) / risk
            value_middle_allowed = True
            if config.value_middle_max_profit_r is not None and current_profit_r > config.value_middle_max_profit_r:
                value_middle_allowed = False
            if config.value_middle_max_age_minutes is not None and age_minutes > config.value_middle_max_age_minutes:
                value_middle_allowed = False
            if config.value_middle_max_mfe_r is not None and max_mfe_r > config.value_middle_max_mfe_r:
                value_middle_allowed = False
            if value_middle_allowed:
                exit_reason = "monitor_value_middle_reclaim"
        if config.exit_on_value_lower_loss and state["lower_loss"] and close >= entry:
            exit_reason = "monitor_lower_value_loss"
        if (
            exit_reason is None
            and config.no_progress_after_minutes is not None
            and config.no_progress_min_mfe_r is not None
            and age_minutes >= config.no_progress_after_minutes
            and max_mfe_r < config.no_progress_min_mfe_r
        ):
            exit_reason = "monitor_no_progress"

        if exit_reason is not None:
            return {
                "exit_date": dt,
                "exit": close,
                "points": entry - close,
                "exit_reason": exit_reason,
                "hold_hours": (dt - entry_dt).total_seconds() / 3600.0,
                "monitor_exit_state": last_monitor_state,
                "max_mfe_r_seen": max_mfe_r,
            }

    return {
        "exit_date": original_exit_dt,
        "exit": float(row["exit"]),
        "points": float(row["points"]),
        "exit_reason": str(row["exit_reason"]),
        "hold_hours": (original_exit_dt - entry_dt).total_seconds() / 3600.0,
        "monitor_exit_state": last_monitor_state,
        "max_mfe_r_seen": max_mfe_r,
    }


def run() -> tuple[pd.DataFrame, pd.DataFrame]:
    trades = load_target_trades().sort_values(["symbol", "entry_date"]).reset_index(drop=True)
    rows: list[dict[str, object]] = []
    for symbol, group in trades.groupby("symbol"):
        print(f"{symbol} {len(group)} trades", flush=True)
        bars = load_bars(symbol, "2023-01-01", "2026-06-18")
        for _, row in group.iterrows():
            for config in CONFIGS:
                result = simulate_trade(row, bars, config)
                rows.append(
                    {
                        "config": config.name,
                        "symbol": symbol,
                        "entry_date": row["entry_date"],
                        "original_exit_date": row["exit_date"],
                        "entry": row["entry"],
                        "stop": row["stop"],
                        "target": row["target"],
                        "original_points": row["points"],
                        "managed_exit_date": result["exit_date"],
                        "managed_exit": result["exit"],
                        "managed_points": result["points"],
                        "managed_exit_reason": result["exit_reason"],
                        "managed_hold_hours": result["hold_hours"],
                        "monitor_exit_state": result["monitor_exit_state"],
                        "max_mfe_r_seen": result["max_mfe_r_seen"],
                    }
                )
    results = pd.DataFrame(rows)
    summary_rows = []
    for config, group in results.groupby("config"):
        points = group["managed_points"].astype(float)
        summary_rows.append(
            {
                "config": config,
                "trades": int(len(group)),
                "points": float(points.sum()),
                "avg_points": float(points.mean()),
                "wins": int((points > 0).sum()),
                "losses": int((points <= 0).sum()),
                "win_rate": float((points > 0).mean()),
                "profit_factor": profit_factor(points),
                "median_hold_hours": float(group["managed_hold_hours"].median()),
                "avg_hold_hours": float(group["managed_hold_hours"].mean()),
                "monitor_exit_rate": float(group["managed_exit_reason"].astype(str).str.startswith("monitor_").mean()),
                "target_rate": float(group["managed_exit_reason"].astype(str).str.contains("target").mean()),
                "stop_rate": float(group["managed_exit_reason"].astype(str).str.contains("stop").mean()),
            }
        )
    summary = pd.DataFrame(summary_rows).sort_values(["points", "profit_factor"], ascending=[False, False])
    return results, summary


def main() -> None:
    results, summary = run()
    results.to_csv(f"{OUT_PREFIX}_trades.csv", index=False)
    summary.to_csv(f"{OUT_PREFIX}_summary.csv", index=False)
    print("\nsummary")
    print(summary.round(3).to_string(index=False))
    print(f"\nwrote_prefix={OUT_PREFIX}")


if __name__ == "__main__":
    main()
