from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

import decision_records as decision_model


RAW_DIR = Path(r"D:\Trading\Rohdaten")
OUT_DIR = Path(__file__).resolve().parent
LIVE_OVERLAY_ENV = "MANUAL_REGIME_BAR_OVERLAY_DIR"


"""
Deterministic implementation of the manually documented rules:

- no-lookahead decision on the second 5-minute bar of the session
- ATR-coupled tolerance zones
- sideways, bullish tier1/2/3, bearish attempt/confirmed/countermove regimes
- bullish pullback/higher-low long
- bullish tier3 small-participation breakout with 1R target
- optional adaptive long gating based on prior completed trades
- bearish failed-countermove baseline short
- experimental bearish lower-high failure wide-structure-stop variant removed
  after symbol-level testing showed it to be weak outside SPX

Not included as a trade rule:
- repair_range_breakout_to_higher_volume_node needs a separate SPY-derived
  volume-profile input and is documented as a later research branch, not as
  part of the current ATR/regime state machine.

Important implementation choice:
- "relevant high/low" is made deterministic by confirmed multi-scale swing
  levels. This replaces the first rigid trailing-window implementation.
"""


SWING_SCALES = (
    (1, 1, 1, "micro"),
    (2, 1, 2, "short"),
    (3, 2, 3, "medium"),
    (5, 2, 5, "major"),
    (8, 3, 8, "macro"),
)


@dataclass
class OpenTrade:
    track: str
    rule: str
    side: str
    entry_date: pd.Timestamp
    entry_price: float
    stop: float
    target: float
    size: float
    regime_at_entry: str
    labels: str = ""
    entry_labels: str = ""


@dataclass(frozen=True)
class SwingLevel:
    kind: str
    day: str
    idx: int
    price: float
    scale: str
    scale_weight: int
    prominence: float


def load_bars(symbol: str, start: str, end_exclusive: str) -> pd.DataFrame:
    path = RAW_DIR / f"Rohdaten_{symbol.upper()}.csv"
    if not path.exists():
        raise FileNotFoundError(path)
    df = pd.read_csv(path, parse_dates=["date"])
    overlay_root = os.environ.get(LIVE_OVERLAY_ENV)
    if overlay_root:
        overlay_path = Path(overlay_root) / f"Rohdaten_{symbol.upper()}.csv"
        if overlay_path.exists():
            overlay_df = pd.read_csv(overlay_path, parse_dates=["date"])
            df = pd.concat([df, overlay_df], ignore_index=True, sort=False)
            df = df.drop_duplicates(subset=["date"], keep="last")
    df = df[(df["date"] >= start) & (df["date"] < end_exclusive)].copy()
    if df.empty:
        raise ValueError(f"No rows for {symbol} in {start}..{end_exclusive}")
    df["day"] = df["date"].dt.date.astype(str)
    df["time"] = df["date"].dt.strftime("%H:%M")
    return df.sort_values("date").reset_index(drop=True)


def build_daily(df: pd.DataFrame) -> pd.DataFrame:
    daily = df.groupby("day", as_index=False).agg(
        date=("date", "max"),
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
    )
    prev_close = daily["close"].shift(1)
    daily["tr"] = pd.concat(
        [
            daily["high"] - daily["low"],
            (daily["high"] - prev_close).abs(),
            (daily["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    daily.loc[daily.index[0], "tr"] = daily.loc[daily.index[0], "high"] - daily.loc[daily.index[0], "low"]
    daily["atr14"] = daily["tr"].rolling(14, min_periods=14).mean()
    return daily


def classify_volume_price_phase(prior: pd.DataFrame, atr14: float) -> tuple[str, str]:
    if prior.empty or "volume" not in prior.columns:
        return "volume_unavailable", "volume_source=missing"
    volume = pd.to_numeric(prior["volume"], errors="coerce").fillna(0.0)
    if float(volume.tail(min(len(volume), 20)).sum()) <= 0:
        return "volume_unavailable", "volume_source=zero"

    weekly = prior.copy()
    weekly["day_ts"] = pd.to_datetime(weekly["day"])
    weekly = (
        weekly.set_index("day_ts")
        .resample("W-FRI")
        .agg(
            open=("open", "first"),
            high=("high", "max"),
            low=("low", "min"),
            close=("close", "last"),
            volume=("volume", "sum"),
        )
        .dropna(subset=["open", "high", "low", "close"])
    )
    weekly = weekly[weekly["volume"] > 0]
    if len(weekly) < 8:
        return "volume_insufficient_history", f"weekly_samples={len(weekly)}"

    recent = weekly.tail(3)
    baseline = weekly.iloc[:-3].tail(8)
    if baseline.empty:
        return "volume_insufficient_history", f"weekly_samples={len(weekly)}"

    recent_volume = float(recent["volume"].mean())
    baseline_volume = float(baseline["volume"].mean())
    volume_ratio = recent_volume / baseline_volume if baseline_volume > 0 else 0.0
    net_move = float(recent["close"].iloc[-1] - recent["open"].iloc[0])
    range_move = float(recent["high"].max() - recent["low"].min())
    atr_base = max(float(atr14), 1e-9)
    net_atr = net_move / atr_base
    range_atr = range_move / atr_base

    high_volume = volume_ratio >= 1.25
    low_volume = volume_ratio <= 0.75
    price_stall = abs(net_atr) <= 0.75 and range_atr <= 3.0
    detail = "|".join(
        [
            f"weeks=3",
            f"volume_ratio={volume_ratio:.2f}",
            f"net_atr={net_atr:.2f}",
            f"range_atr={range_atr:.2f}",
        ]
    )
    if high_volume and price_stall:
        return "high_volume_stall", detail
    if high_volume and net_atr > 0.75:
        return "high_volume_breakout", detail
    if high_volume and net_atr < -0.75:
        return "high_volume_breakdown", detail
    if low_volume and net_atr < -0.25:
        return "low_volume_pullback", detail
    if price_stall:
        return "normal_volume_stall", detail
    return "normal_volume_trend", detail


def labels_for_small_tier3_long(prior: pd.DataFrame, atr14: float) -> str:
    labels: list[str] = []
    atr_history = prior["atr14"].dropna()
    if len(atr_history) >= 20:
        atr20_mean = float(atr_history.tail(20).mean())
        atr20_start = float(atr_history.iloc[-20])
        if atr14 < atr20_mean and atr14 <= 0.90 * atr20_start:
            labels.append("late_bull_compression_small_long")
    return "|".join(labels)


def append_label(labels: str, label: str) -> str:
    if not labels:
        return label
    existing = labels.split("|")
    if label in existing:
        return labels
    return f"{labels}|{label}"


def weekly_green_streak(prior: pd.DataFrame, current_day: str, max_red_weeks: int = 1) -> tuple[int, int, int]:
    """Completed weekly trend streak known at the decision date.

    The week containing current_day is excluded so a mid-week partial candle
    cannot affect the decision. Counting walks backward until more than
    max_red_weeks have appeared.
    """
    if prior.empty:
        return (0, 0, 0)
    history = prior.copy()
    history["week"] = pd.to_datetime(history["date"]).dt.to_period("W-FRI")
    current_week = pd.Timestamp(current_day).to_period("W-FRI")
    history = history[history["week"] != current_week]
    if history.empty:
        return (0, 0, 0)

    weekly = (
        history.groupby("week", as_index=False)
        .agg(open=("open", "first"), close=("close", "last"))
        .sort_values("week")
    )
    total = 0
    green = 0
    red = 0
    for _, week in weekly.iloc[::-1].iterrows():
        is_green = float(week["close"]) > float(week["open"])
        if is_green:
            green += 1
        else:
            red += 1
            if red > max_red_weeks:
                break
        total += 1
    return (total, green, red)


def classify_big_picture_regime(prior: pd.DataFrame, atr14: float, tol: float, current_day: str | None = None) -> dict[str, object]:
    """Classify the completed weekly structure above the daily setup layer."""
    empty = {
        "big_picture_regime": "insufficient_history",
        "big_picture_state": "insufficient_weekly_swings",
        "weekly_last_high": None,
        "weekly_prev_high": None,
        "weekly_last_high_day": None,
        "weekly_prev_high_day": None,
        "weekly_last_low": None,
        "weekly_prev_low": None,
        "weekly_last_low_day": None,
        "weekly_prev_low_day": None,
        "weekly_higher_high": False,
        "weekly_higher_low": False,
        "weekly_lower_high": False,
        "weekly_lower_low": False,
    }
    if prior.empty or len(prior) < 40:
        return empty

    weekly = prior.copy()
    weekly["week_ts"] = pd.to_datetime(weekly["day"]).dt.to_period("W-FRI").dt.to_timestamp(how="end")
    if current_day is not None:
        current_week_end = pd.Timestamp(current_day).to_period("W-FRI").to_timestamp(how="end")
        weekly = weekly[weekly["week_ts"] < current_week_end].copy()
    weekly = (
        weekly.groupby("week_ts", as_index=False)
        .agg(
            day=("day", "last"),
            date=("date", "last"),
            open=("open", "first"),
            high=("high", "max"),
            low=("low", "min"),
            close=("close", "last"),
            volume=("volume", "sum"),
        )
        .dropna(subset=["open", "high", "low", "close"])
        .reset_index(drop=True)
    )
    if len(weekly) < 12:
        return empty

    weekly_tol = max(float(tol), 0.12 * float(atr14))
    levels = detect_confirmed_swings(weekly, weekly_tol)
    highs = [level for level in levels if level.kind == "high" and level.scale_weight >= 2]
    lows = [level for level in levels if level.kind == "low" and level.scale_weight >= 2]
    if len(highs) < 2 or len(lows) < 2:
        return {
            **empty,
            "big_picture_regime": "transition",
            "big_picture_state": "insufficient_confirmed_weekly_structure",
        }

    prev_high, last_high = highs[-2], highs[-1]
    prev_low, last_low = lows[-2], lows[-1]
    higher_high = last_high.price > prev_high.price + weekly_tol
    higher_low = last_low.price > prev_low.price + weekly_tol
    lower_high = last_high.price < prev_high.price - weekly_tol
    lower_low = last_low.price < prev_low.price - weekly_tol

    if higher_high and higher_low:
        regime = "bullish_primary"
        state = "weekly_higher_high_higher_low"
    elif lower_high and lower_low:
        regime = "bearish_primary"
        state = "weekly_lower_high_lower_low"
    elif lower_low or lower_high:
        regime = "bearish_pressure"
        state = "weekly_bearish_mixed_structure"
    elif higher_high or higher_low:
        regime = "bullish_repair"
        state = "weekly_bullish_mixed_structure"
    else:
        regime = "transition"
        state = "weekly_range_or_equal_structure"

    return {
        "big_picture_regime": regime,
        "big_picture_state": state,
        "weekly_last_high": last_high.price,
        "weekly_prev_high": prev_high.price,
        "weekly_last_high_day": last_high.day,
        "weekly_prev_high_day": prev_high.day,
        "weekly_last_low": last_low.price,
        "weekly_prev_low": prev_low.price,
        "weekly_last_low_day": last_low.day,
        "weekly_prev_low_day": prev_low.day,
        "weekly_higher_high": bool(higher_high),
        "weekly_higher_low": bool(higher_low),
        "weekly_lower_high": bool(lower_high),
        "weekly_lower_low": bool(lower_low),
    }


def bullish_repair_pressure(prior: pd.DataFrame, decision_price: float, open_price: float, tol: float) -> bool:
    """Detect a visible bullish repair after a bearish phase.

    This is intentionally based only on completed daily bars plus current
    open/decision price. It catches cases where several rising pivot lows have
    already formed and price is reclaiming recent bounce highs, so fresh
    bearish failed-countermove shorts are no longer appropriate.
    """
    if len(prior) < 8:
        return False
    recent = prior.tail(25).reset_index(drop=True)
    pivot_lows: list[tuple[int, float]] = []
    for idx in range(1, len(recent) - 1):
        low = float(recent.loc[idx, "low"])
        if low < float(recent.loc[idx - 1, "low"]) and low < float(recent.loc[idx + 1, "low"]):
            pivot_lows.append((idx, low))
    if len(pivot_lows) < 3:
        return False

    last_three = pivot_lows[-3:]
    rising_lows = last_three[1][1] > last_three[0][1] + tol and last_three[2][1] > last_three[1][1] + tol
    if not rising_lows:
        return False

    first_low_idx = last_three[0][0]
    last_low_idx = last_three[-1][0]
    prior_bounce = recent.iloc[first_low_idx:last_low_idx]
    if prior_bounce.empty:
        return False
    bounce_high = float(prior_bounce["high"].max())
    return open_price > bounce_high + tol or decision_price > bounce_high + tol


def bullish_crash_repair_reversal(
    prior: pd.DataFrame,
    decision_price: float,
    open_price: float,
    atr14: float,
    tol: float,
) -> tuple[bool, float | None, float | None]:
    """Detect a post-crash repair long with two rising lows and a breakout.

    Uses only completed daily bars plus current open/decision price. The stop is
    the latest confirmed repair pivot low; the target is set by the caller to 1R.
    """
    if len(prior) < 12:
        return (False, None, None)

    recent = prior.tail(30).reset_index(drop=True)
    atr_history = recent["atr14"].dropna()
    if len(atr_history) < 5:
        return (False, None, None)

    max_recent_atr = float(atr_history.max())
    atr_cooling = atr14 <= 0.75 * max_recent_atr
    if not atr_cooling:
        return (False, None, None)

    crash_low_idx = int(recent["low"].idxmin())
    pivot_lows: list[tuple[int, float]] = []
    for idx in range(max(1, crash_low_idx + 1), len(recent) - 1):
        low = float(recent.loc[idx, "low"])
        if low < float(recent.loc[idx - 1, "low"]) and low < float(recent.loc[idx + 1, "low"]):
            pivot_lows.append((idx, low))
    if len(pivot_lows) < 2:
        return (False, None, None)

    first_low_idx, first_low = pivot_lows[-2]
    last_low_idx, last_low = pivot_lows[-1]
    rising_repair_lows = last_low > first_low + max(tol, 0.25 * atr14)
    if not rising_repair_lows:
        return (False, None, None)

    repair_base = recent.iloc[first_low_idx : last_low_idx + 1]
    if repair_base.empty:
        return (False, None, None)
    breakout_level = float(repair_base["high"].max())
    prior_close = float(prior.iloc[-1]["close"])
    breakout_confirmed = (
        prior_close > breakout_level + tol
        or (open_price > breakout_level + tol and decision_price > breakout_level + tol)
    )
    if not breakout_confirmed:
        return (False, None, None)

    not_too_extended = decision_price - last_low <= 2.50 * atr14
    if not not_too_extended:
        return (False, None, None)

    return (True, last_low, breakout_level)


def classify_bearish_countermove(
    prior: pd.DataFrame,
    decision_price: float,
    broken_support: float | None,
    bars_since_support_break: int | None,
    atr14: float,
    bullish_repair_filter: bool,
) -> str:
    if broken_support is None:
        return ""

    atr_history = prior["atr14"].dropna()
    atr20_ratio = 1.0
    atr5_ratio = 1.0
    if len(atr_history) >= 20:
        atr20_ratio = atr14 / float(atr_history.tail(20).mean())
    if len(atr_history) >= 5:
        atr5_ratio = atr14 / float(atr_history.iloc[-5])

    recent = prior.tail(5)
    rising_close_count = int((recent["close"].diff() > 0).sum()) if len(recent) >= 2 else 0
    distance_to_broken_atr = (decision_price - broken_support) / max(atr14, 1e-9)
    bars = bars_since_support_break if bars_since_support_break is not None else 0

    if bullish_repair_filter or rising_close_count >= 4 or (bars > 2 and distance_to_broken_atr > -0.25):
        return "bearish_countermove_repair_rebound"
    if atr20_ratio >= 1.35 or (atr20_ratio >= 1.20 and atr5_ratio >= 1.20):
        return "bearish_countermove_volatility_reversal"
    return "bearish_countermove_weak_retest"


def detect_structural_reset(
    prior: pd.DataFrame,
    current_idx: int,
    decision_price: float,
    atr14: float,
    tol: float,
    last_reset_idx: int | None,
    lookback_days: int = 180,
) -> dict[str, object] | None:
    """Detect a major path break where old regime anchors should expire.

    This is intentionally event-based, not calendar-based: old trend structure
    can stay relevant for a long time, but a fast post-peak drawdown with lower
    lows usually makes pre-break anchors misleading.
    """
    if len(prior) < 80:
        return None
    if last_reset_idx is not None and current_idx - last_reset_idx < 20:
        return None

    recent = prior.tail(lookback_days)
    if len(recent) < 60:
        return None

    peak_idx = int(recent["high"].astype(float).idxmax())
    peak_row = prior.loc[peak_idx]
    peak_price = float(peak_row["high"])
    if peak_price <= 0:
        return None
    days_since_peak = current_idx - peak_idx
    if days_since_peak < 15 or days_since_peak > 90:
        return None

    drawdown_pct = decision_price / peak_price - 1.0
    material_drawdown = drawdown_pct <= -0.18 or (drawdown_pct <= -0.15 and days_since_peak <= 45)
    if not material_drawdown:
        return None

    after_peak = prior[prior.index >= peak_idx].copy()
    if len(after_peak) < 10:
        return None
    after_peak_low = float(after_peak["low"].min())
    close_to_post_peak_low = decision_price <= after_peak_low + max(0.75 * atr14, 3.0 * tol)

    pre_peak = recent[recent.index < peak_idx].tail(60)
    pre_peak_range = float(pre_peak["high"].max() - pre_peak["low"].min()) if not pre_peak.empty else 0.0
    post_peak_range = float(after_peak["high"].max() - after_peak["low"].min())
    faster_drawdown = days_since_peak <= 45 or post_peak_range >= 1.15 * max(pre_peak_range, atr14)

    post_peak_lows = after_peak["low"].astype(float)
    lower_low_count = int((post_peak_lows.diff() < -tol).sum()) if len(post_peak_lows) >= 2 else 0
    support_window = recent[recent.index < peak_idx].tail(80)
    old_support = None
    broke_old_support = False
    if not support_window.empty:
        support_lows = support_window["low"].astype(float)
        broken_candidates = support_lows[support_lows > decision_price + tol]
        if not broken_candidates.empty:
            old_support = float(broken_candidates.min())
            broke_old_support = True
        else:
            old_support = float(support_lows.max())

    if not (close_to_post_peak_low or broke_old_support):
        return None
    if not (faster_drawdown or lower_low_count >= 3):
        return None

    return {
        "reason": "major_post_peak_drawdown",
        "peak_idx": peak_idx,
        "peak_day": str(peak_row["day"]),
        "peak_price": peak_price,
        "drawdown_pct": drawdown_pct,
        "days_since_peak": days_since_peak,
        "old_support": old_support,
        "broke_old_support": bool(broke_old_support),
        "faster_drawdown": bool(faster_drawdown),
        "lower_low_count": lower_low_count,
    }


def entry_labels_for_trade(trade: OpenTrade) -> str:
    labels = [
        f"track:{trade.track}",
        f"rule:{trade.rule}",
        f"side:{trade.side}",
        f"regime:{trade.regime_at_entry}",
    ]
    labels.append("size:small" if trade.size < 1.0 else "size:full")

    if trade.rule.startswith("bullish_"):
        labels.append("context:bullish")
    elif trade.rule.startswith("bearish_"):
        labels.append("context:bearish")

    if "breakout" in trade.rule:
        labels.append("trigger:breakout")
    if "pullback" in trade.rule or "higher_low" in trade.rule:
        labels.append("trigger:pullback_or_higher_low")
    if "reclaim" in trade.rule:
        labels.append("trigger:reclaim")
    if "crash_repair_reversal" in trade.rule:
        labels.append("trigger:crash_repair_reversal")
    if "lower_high_failure" in trade.rule:
        labels.append("trigger:lower_high_failure")
    if "failed_countermove" in trade.rule:
        labels.append("trigger:failed_countermove")
    if "breakdown_retest" in trade.rule:
        labels.append("trigger:breakdown_retest")
    if "gap_breakdown_continuation" in trade.rule:
        labels.append("trigger:gap_breakdown_continuation")
    if "weekly_green_streak" in trade.rule:
        labels.append("trigger:weekly_green_streak")
    if "stair_step" in trade.rule:
        labels.append("trigger:stair_step")
    if "projected_high" in trade.rule:
        labels.append("trigger:projected_high")
    if "post_bear_bull_reentry" in trade.rule:
        labels.append("trigger:post_bear_bull_reentry")
    if "wide_structure_stop" in trade.rule:
        labels.append("stop:wide_structure")
    if trade.rule.endswith("_1r") or "small_participation" in trade.rule or "full_trend_participation" in trade.rule:
        labels.append("target:1r")
    else:
        labels.append("target:structure_or_acceptance")

    return "|".join(labels)


def detect_decision_time(df: pd.DataFrame) -> str:
    first_day = df["day"].iloc[0]
    times = sorted(df[df["day"] == first_day]["time"].unique())
    if len(times) < 2:
        raise ValueError("Need at least two intraday bars to determine decision time")
    return times[1]


def add_decision_prices(df: pd.DataFrame, daily: pd.DataFrame, decision_time: str) -> pd.DataFrame:
    decision = (
        df[df["time"] == decision_time]
        .groupby("day", as_index=False)
        .agg(
            decision_datetime=("date", "first"),
            decision_open=("open", "first"),
            decision_high=("high", "first"),
            decision_low=("low", "first"),
            decision_price=("close", "first"),
        )
    )
    return daily.merge(decision, on="day", how="left")


def asset_min_tick_zone(symbol: str, prior_close: float) -> float:
    if symbol.upper() in {"SPX", "SPXW"}:
        return 2.0
    return max(0.05, 0.001 * prior_close)


def tolerance(symbol: str, atr14: float, prior_close: float) -> float:
    if pd.isna(atr14):
        return asset_min_tick_zone(symbol, prior_close)
    return max(asset_min_tick_zone(symbol, prior_close), 0.05 * atr14)


def detect_confirmed_swings(history: pd.DataFrame, tol: float) -> list[SwingLevel]:
    levels: dict[tuple[str, str], SwingLevel] = {}
    n = len(history)
    if n < 4:
        return []

    highs = history["high"].astype(float).tolist()
    lows = history["low"].astype(float).tolist()
    days = history["day"].tolist()
    source_indices = list(history.index)
    for left, right, weight, label in SWING_SCALES:
        if n < left + right + 1:
            continue
        for idx in range(left, n - right):
            source_idx = int(source_indices[idx])
            high_window = highs[idx - left : idx + right + 1]
            low_window = lows[idx - left : idx + right + 1]
            day = days[idx]
            high_prominence = highs[idx] - min(low_window)
            low_prominence = max(high_window) - lows[idx]

            if highs[idx] >= max(high_window) - 1e-9 and high_prominence >= tol:
                key = ("high", day)
                candidate = SwingLevel("high", day, source_idx, highs[idx], label, weight, high_prominence)
                old = levels.get(key)
                if old is None or (candidate.scale_weight, candidate.prominence) > (old.scale_weight, old.prominence):
                    levels[key] = candidate

            if lows[idx] <= min(low_window) + 1e-9 and low_prominence >= tol:
                key = ("low", day)
                candidate = SwingLevel("low", day, source_idx, lows[idx], label, weight, low_prominence)
                old = levels.get(key)
                if old is None or (candidate.scale_weight, candidate.prominence) > (old.scale_weight, old.prominence):
                    levels[key] = candidate

    return sorted(levels.values(), key=lambda level: (level.idx, level.kind))


def _score_level(level: SwingLevel, current_idx: int, reference_price: float, tol: float) -> tuple[float, float, int]:
    age = max(current_idx - level.idx, 1)
    distance_atr_units = abs(reference_price - level.price) / max(tol, 1e-9)
    # Prominence and scale matter, but very old distant levels should not dominate.
    score = level.scale_weight * 3.0 + min(level.prominence / max(tol, 1e-9), 8.0) - 0.08 * age - 0.15 * distance_atr_units
    return (score, level.prominence, level.idx)


def relevant_support(levels: list[SwingLevel], current_idx: int, reference_price: float, tol: float) -> SwingLevel | None:
    lows = [level for level in levels if level.kind == "low" and level.price < reference_price + 2.0 * tol]
    if not lows:
        return None
    # A structural break is judged against the closest known support zone below/near price.
    return max(lows, key=lambda level: (level.price, level.scale_weight, level.prominence, level.idx))


def relevant_resistance(levels: list[SwingLevel], current_idx: int, reference_price: float, tol: float) -> SwingLevel | None:
    highs = [level for level in levels if level.kind == "high" and level.price > reference_price - 2.0 * tol]
    if not highs:
        return None
    # A breakout is judged against the closest known resistance zone above/near price.
    return min(highs, key=lambda level: (level.price, -level.scale_weight, -level.prominence, -level.idx))


def nearest_resistance_above(levels: list[SwingLevel], entry: float, tol: float) -> SwingLevel | None:
    highs = [level for level in levels if level.kind == "high" and level.price > entry + tol]
    if not highs:
        return None
    return min(highs, key=lambda level: (level.price - entry, -level.scale_weight, -level.prominence))


def nearest_support_below(levels: list[SwingLevel], entry: float, tol: float) -> SwingLevel | None:
    lows = [level for level in levels if level.kind == "low" and level.price < entry - tol]
    if not lows:
        return None
    return max(lows, key=lambda level: (level.price, level.scale_weight, level.prominence))


def prior_extreme_target(history: pd.DataFrame, side: str, entry: float, tol: float) -> float | None:
    if history.empty:
        return None
    if side == "long":
        highs = history[history["high"] > entry + tol]["high"].astype(float)
        return float(highs.max()) if not highs.empty else None
    lows = history[history["low"] < entry - tol]["low"].astype(float)
    return float(lows.min()) if not lows.empty else None


def price_acceptance_target(
    intraday: pd.DataFrame,
    decision_dt: pd.Timestamp,
    side: str,
    entry: float,
    atr14: float,
    tol: float,
    lookback_days: int = 30,
) -> float | None:
    """Nearest accepted price shelf from pre-decision 5-minute time-at-price.

    SPX bars in the local raw file do not carry usable volume. The proxy is
    therefore a TPO-style distribution: every 5-minute bar contributes to all
    price bins covered by its high/low range. This keeps the rule mathematical
    and no-lookahead while approximating the manually used acceptance zones.
    """
    past = intraday[intraday["date"] < decision_dt].copy()
    if past.empty:
        return None
    days = sorted(past["day"].unique())[-lookback_days:]
    past = past[past["day"].isin(days)]
    if past.empty:
        return None

    bin_size = max(tol, 0.04 * atr14, 0.01)
    min_price = float(past["low"].min())
    max_price = float(past["high"].max())
    if max_price <= min_price:
        return None

    bins: dict[int, int] = {}
    for _, bar in past.iterrows():
        lo = int((float(bar["low"]) - min_price) // bin_size)
        hi = int((float(bar["high"]) - min_price) // bin_size)
        for bucket in range(lo, hi + 1):
            bins[bucket] = bins.get(bucket, 0) + 1

    if not bins:
        return None

    ordered = sorted(bins)
    counts = {bucket: bins.get(bucket - 1, 0) + bins.get(bucket, 0) + bins.get(bucket + 1, 0) for bucket in ordered}
    threshold = pd.Series(list(counts.values())).quantile(0.65)
    min_target_move = max(0.60 * atr14, 6.0 * tol)
    if side == "long":
        candidates = [
            min_price + (bucket + 0.5) * bin_size
            for bucket in ordered
            if min_price + (bucket + 0.5) * bin_size > entry + min_target_move and counts[bucket] >= threshold
        ]
        return float(min(candidates)) if candidates else None

    candidates = [
        min_price + (bucket + 0.5) * bin_size
        for bucket in ordered
        if min_price + (bucket + 0.5) * bin_size < entry - min_target_move and counts[bucket] >= threshold
    ]
    return float(max(candidates)) if candidates else None


def repair_breakout_target(history: pd.DataFrame, entry: float, tol: float) -> float | None:
    """Nearest price shelf above entry from recent daily extremes.

    This approximates the manual use of nearby acceptance/resistance zones
    without hard-coded dates or SPY volume-profile inputs.
    """
    if history.empty:
        return None
    recent = history.tail(30)
    candidates = pd.concat([recent["high"], recent["close"]], ignore_index=True).astype(float)
    candidates = candidates[candidates > entry + 0.75 * tol]
    if candidates.empty:
        return None
    return float(candidates.min())


def fallback_structural_long_target(levels: list[SwingLevel], entry: float, tol: float) -> float | None:
    target_level = nearest_resistance_above(levels, entry, tol)
    return target_level.price if target_level is not None else None


def latest_swing(levels: list[SwingLevel], kind: str) -> SwingLevel | None:
    candidates = [level for level in levels if level.kind == kind]
    if not candidates:
        return None
    return max(candidates, key=lambda level: level.idx)


def latest_structural_swing(levels: list[SwingLevel], kind: str, min_scale_weight: int = 2) -> SwingLevel | None:
    candidates = [level for level in levels if level.kind == kind and level.scale_weight >= min_scale_weight]
    if not candidates:
        return None
    return max(candidates, key=lambda level: level.idx)


def previous_swing(levels: list[SwingLevel], level: SwingLevel) -> SwingLevel | None:
    candidates = [other for other in levels if other.kind == level.kind and other.idx < level.idx]
    if not candidates:
        return None
    return max(candidates, key=lambda other: other.idx)


def previous_structural_swing(levels: list[SwingLevel], level: SwingLevel, min_scale_weight: int = 2) -> SwingLevel | None:
    candidates = [
        other
        for other in levels
        if other.kind == level.kind and other.idx < level.idx and other.scale_weight >= min_scale_weight
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda other: other.idx)


def count_recent_higher_swings(
    levels: list[SwingLevel],
    kind: str,
    tol: float,
    min_idx: int = 0,
    min_scale_weight: int = 1,
) -> int:
    candidates = sorted(
        [level for level in levels if level.kind == kind and level.idx >= min_idx and level.scale_weight >= min_scale_weight],
        key=lambda level: level.idx,
    )
    if len(candidates) < 2:
        return 0
    count = 0
    for prev, cur in zip(candidates, candidates[1:]):
        if kind == "high" and cur.price > prev.price + tol:
            count += 1
        elif kind == "low" and cur.price > prev.price + tol:
            count += 1
    return count


def count_progressive_structural_highs(levels: list[SwingLevel], min_idx: int = 0, min_scale_weight: int = 2) -> int:
    best = None
    count = 0
    for level in sorted(
        [level for level in levels if level.kind == "high" and level.idx >= min_idx and level.scale_weight >= min_scale_weight],
        key=lambda level: level.idx,
    ):
        if best is None or level.price > best:
            best = level.price
            count += 1
    return count


def detect_post_break_midrange(
    levels: list[SwingLevel],
    decision_price: float,
    atr14: float,
    tol: float,
    current_idx: int,
    lookback_bars: int = 30,
) -> dict[str, object]:
    result: dict[str, object] = {
        "post_break_midrange": False,
        "post_break_range_low": None,
        "post_break_range_low_floor": None,
        "post_break_range_latest_low": None,
        "post_break_range_high": None,
        "post_break_range_high_peak": None,
        "post_break_range_mid": None,
        "post_break_range_width_atr": None,
        "post_break_mid_distance_atr": None,
        "post_break_range_detail": "",
    }
    min_idx = max(0, current_idx - lookback_bars)
    recent = [level for level in levels if level.idx >= min_idx]
    highs = [level for level in recent if level.kind == "high"]
    lows = [level for level in recent if level.kind == "low"]
    if len(highs) < 2 or len(lows) < 2 or atr14 <= 0:
        return result

    cluster_tolerance = max(0.35 * atr14, 4.0 * tol)
    low_anchor = min(lows, key=lambda level: level.price)
    low_cluster = [level for level in lows if abs(level.price - low_anchor.price) <= cluster_tolerance]
    post_low_highs = [level for level in highs if level.idx > low_anchor.idx]
    if len(post_low_highs) < 2:
        return result
    high_anchor = max(post_low_highs, key=lambda level: level.price)
    high_cluster = [level for level in post_low_highs if abs(level.price - high_anchor.price) <= cluster_tolerance]
    if len(low_cluster) < 2 or len(high_cluster) < 2:
        return result

    range_low = sum(level.price for level in low_cluster) / len(low_cluster)
    range_high = sum(level.price for level in high_cluster) / len(high_cluster)
    if range_high <= range_low:
        return result
    range_width_atr = (range_high - range_low) / atr14
    if range_width_atr < 1.40:
        return result

    range_mid = (range_low + range_high) / 2.0
    mid_distance_atr = abs(decision_price - range_mid) / atr14
    upper_breakout = decision_price > range_high + tol
    lower_breakdown = decision_price < range_low - tol
    is_midrange = mid_distance_atr <= 0.35 and not upper_breakout and not lower_breakdown
    result.update(
        {
            "post_break_midrange": bool(is_midrange),
            "post_break_range_low": range_low,
            "post_break_range_low_floor": min(level.price for level in low_cluster),
            "post_break_range_latest_low": max(low_cluster, key=lambda level: level.idx).price,
            "post_break_range_high": range_high,
            "post_break_range_high_peak": max(level.price for level in high_cluster),
            "post_break_range_mid": range_mid,
            "post_break_range_width_atr": range_width_atr,
            "post_break_mid_distance_atr": mid_distance_atr,
            "post_break_range_detail": (
                f"lows={','.join(level.day for level in sorted(low_cluster, key=lambda level: level.idx))}"
                f"|highs={','.join(level.day for level in sorted(high_cluster, key=lambda level: level.idx))}"
            ),
        }
    )
    return result


def classify_intraday_value_profile(
    intraday: pd.DataFrame,
    decision_dt: pd.Timestamp,
    decision_price: float,
    atr14: float,
    tol: float,
    lookback_days: int = 10,
) -> dict[str, object]:
    result: dict[str, object] = {
        "profile_value_range": False,
        "profile_lookback_days": lookback_days,
        "profile_poc": None,
        "profile_value_low": None,
        "profile_value_high": None,
        "profile_value_width_atr": None,
        "profile_price_location": "",
        "profile_balance_score": None,
        "profile_shape": "",
        "profile_source": "",
    }
    if intraday.empty or atr14 <= 0:
        return result

    start_dt = pd.Timestamp(decision_dt).normalize() - pd.Timedelta(days=lookback_days)
    past = intraday[(intraday["date"] < decision_dt) & (intraday["date"] >= start_dt)].copy()
    if past.empty or len(past) < 20:
        return result

    bin_size = max(tol, 0.04 * atr14, 0.01)
    min_price = float(past["low"].min())
    max_price = float(past["high"].max())
    if max_price <= min_price:
        return result

    use_volume = "volume" in past.columns and float(pd.to_numeric(past["volume"], errors="coerce").fillna(0.0).sum()) > 0
    bins: dict[int, float] = {}
    for _, bar in past.iterrows():
        lo = int((float(bar["low"]) - min_price) // bin_size)
        hi = int((float(bar["high"]) - min_price) // bin_size)
        if hi < lo:
            continue
        span = hi - lo + 1
        weight = float(bar["volume"]) / span if use_volume else 1.0
        for bucket in range(lo, hi + 1):
            bins[bucket] = bins.get(bucket, 0.0) + weight
    if not bins:
        return result

    total = sum(bins.values())
    poc_bucket = max(bins, key=lambda bucket: (bins[bucket], -abs((min_price + (bucket + 0.5) * bin_size) - decision_price)))
    selected = {poc_bucket}
    selected_weight = bins[poc_bucket]
    left = poc_bucket - 1
    right = poc_bucket + 1
    while selected_weight < 0.70 * total and (left in bins or right in bins):
        left_weight = bins.get(left, -1.0)
        right_weight = bins.get(right, -1.0)
        if right_weight >= left_weight:
            if right in bins:
                selected.add(right)
                selected_weight += bins[right]
            right += 1
        else:
            if left in bins:
                selected.add(left)
                selected_weight += bins[left]
            left -= 1

    value_low = min_price + min(selected) * bin_size
    value_high = min_price + (max(selected) + 1) * bin_size
    poc = min_price + (poc_bucket + 0.5) * bin_size
    value_width_atr = (value_high - value_low) / atr14
    if decision_price < value_low - tol:
        price_location = "below_value"
    elif decision_price > value_high + tol:
        price_location = "above_value"
    else:
        value_pos = (decision_price - value_low) / max(value_high - value_low, 1e-9)
        if value_pos < 0.33:
            price_location = "inside_value_lower"
        elif value_pos > 0.67:
            price_location = "inside_value_upper"
        else:
            price_location = "inside_value_middle"

    full_mid = (min_price + max_price) / 2.0
    half_width = max((max_price - min_price) / 2.0, 1e-9)
    balance_score = max(0.0, min(1.0, 1.0 - abs(poc - full_mid) / half_width))
    peak_share = bins[poc_bucket] / max(total, 1e-9)
    inside_value = price_location.startswith("inside_value")
    value_range = (
        inside_value
        and 0.45 <= value_width_atr <= 4.00
        and peak_share <= 0.35
    )
    balanced_d_profile = (
        price_location.startswith("inside_value")
        and 0.60 <= value_width_atr <= 4.00
        and balance_score >= 0.25
        and peak_share <= 0.30
    )
    if balanced_d_profile:
        shape = "d_profile"
    elif value_range:
        shape = "skewed_value_acceptance"
    else:
        shape = "unbalanced_or_trending"
    result.update(
        {
            "profile_value_range": bool(value_range),
            "profile_poc": poc,
            "profile_value_low": value_low,
            "profile_value_high": value_high,
            "profile_value_width_atr": value_width_atr,
            "profile_price_location": price_location,
            "profile_balance_score": balance_score,
            "profile_shape": shape,
            "profile_source": "volume" if use_volume else "tpo",
        }
    )
    return result


def classify_daily_structure_phase(
    post_break_context: bool,
    swing_range: dict[str, object],
    value_profile: dict[str, object],
    decision_price: float,
    open_price: float,
    atr14: float,
    distance_from_recent_high_atr: float,
    support_break: bool,
) -> dict[str, object]:
    empty_lower_context = {
        "range_lower_subtype": "",
        "range_lower_risk_score": None,
        "range_lower_value_pos": None,
        "range_lower_decision_vs_value_low_atr": None,
        "range_lower_open_vs_value_low_atr": None,
        "range_lower_decision_vs_poc_atr": None,
        "range_lower_detail": "",
    }
    swing_active = bool(swing_range.get("post_break_range_low") is not None and swing_range.get("post_break_range_high") is not None)
    value_active = bool(value_profile.get("profile_value_range"))
    if not post_break_context or not (swing_active or value_active):
        return {
            "daily_structure_phase": "trend_or_unclassified",
            "range_basis": "",
            "range_location": "",
            **empty_lower_context,
        }

    basis_parts = []
    if swing_active:
        basis_parts.append("swing_extremes")
    if value_active:
        basis_parts.append("value_profile")
    basis = "+".join(basis_parts)

    if swing_active:
        low = float(swing_range["post_break_range_low"])
        high = float(swing_range["post_break_range_high"])
        pos = (decision_price - low) / max(high - low, 1e-9)
    else:
        low = float(value_profile["profile_value_low"])
        high = float(value_profile["profile_value_high"])
        pos = (decision_price - low) / max(high - low, 1e-9)

    if pos < 0.0:
        location = "outside_down"
    elif pos < 0.33:
        location = "lower"
    elif pos <= 0.67:
        location = "middle"
    elif pos <= 1.0:
        location = "upper"
    else:
        location = "outside_up"

    lower_context = dict(empty_lower_context)
    if location == "lower" and atr14 > 0:
        profile_low = value_profile.get("profile_value_low")
        profile_high = value_profile.get("profile_value_high")
        profile_poc = value_profile.get("profile_poc")
        profile_width_atr = value_profile.get("profile_value_width_atr")
        profile_balance_score = value_profile.get("profile_balance_score")

        if value_active and profile_low is not None and profile_high is not None:
            lower_low = float(profile_low)
            lower_high = float(profile_high)
        else:
            lower_low = low
            lower_high = high
        lower_width = max(lower_high - lower_low, 1e-9)
        lower_value_pos = (decision_price - lower_low) / lower_width
        decision_vs_low_atr = (decision_price - lower_low) / atr14
        open_vs_low_atr = (open_price - lower_low) / atr14
        decision_vs_poc_atr = (
            (decision_price - float(profile_poc)) / atr14
            if value_active and profile_poc is not None
            else None
        )
        balance_score = (
            float(profile_balance_score)
            if value_active and profile_balance_score is not None
            else None
        )
        width_atr = (
            float(profile_width_atr)
            if value_active and profile_width_atr is not None
            else (lower_high - lower_low) / atr14
        )

        risk_flags: list[str] = []
        if lower_value_pos < 0.15:
            risk_flags.append("near_value_floor")
        if open_vs_low_atr < 0.10:
            risk_flags.append("weak_open_near_floor")
        if decision_vs_poc_atr is not None and decision_vs_poc_atr < -1.00:
            risk_flags.append("poc_overhead")
        if balance_score is not None and balance_score < 0.45:
            risk_flags.append("unbalanced_profile")
        if distance_from_recent_high_atr > 2.50:
            risk_flags.append("damaged_from_recent_high")
        if width_atr > 1.50:
            risk_flags.append("wide_value_area")
        if support_break:
            risk_flags.append("support_break")

        risk_score = len(risk_flags)
        if (
            risk_score >= 5
            and distance_from_recent_high_atr > 2.50
            and decision_vs_poc_atr is not None
            and decision_vs_poc_atr < -1.00
        ):
            subtype = "falling_knife"
        elif risk_score >= 4:
            subtype = "breakdown_attempt"
        elif (
            risk_score <= 2
            and (
                lower_value_pos >= 0.15
                or (balance_score is not None and balance_score >= 0.50)
                or (decision_vs_poc_atr is not None and decision_vs_poc_atr > -0.80)
            )
        ):
            subtype = "reclaim_candidate"
        else:
            subtype = "unclear"

        lower_context = {
            "range_lower_subtype": subtype,
            "range_lower_risk_score": risk_score,
            "range_lower_value_pos": lower_value_pos,
            "range_lower_decision_vs_value_low_atr": decision_vs_low_atr,
            "range_lower_open_vs_value_low_atr": open_vs_low_atr,
            "range_lower_decision_vs_poc_atr": decision_vs_poc_atr,
            "range_lower_detail": "|".join(risk_flags),
        }

    return {
        "daily_structure_phase": f"post_break_range_{location}",
        "range_basis": basis,
        "range_location": location,
        **lower_context,
    }


def simulate_trade_exit(
    df: pd.DataFrame,
    trade: OpenTrade,
    after_date: pd.Timestamp,
    through_date: pd.Timestamp,
) -> dict | None:
    day_bars = df[(df["date"] > after_date) & (df["date"] <= through_date)].copy()
    for _, bar in day_bars.iterrows():
        high = float(bar["high"])
        low = float(bar["low"])
        if trade.side == "long":
            stop_hit = low <= trade.stop
            target_hit = high >= trade.target
            if stop_hit and target_hit:
                # Conservative same-bar ordering.
                exit_price = trade.stop
                exit_reason = "stop_same_bar_conservative"
            elif stop_hit:
                exit_price = trade.stop
                exit_reason = "stop"
            elif target_hit:
                exit_price = trade.target
                exit_reason = "target"
            else:
                continue
            points = exit_price - trade.entry_price
        else:
            stop_hit = high >= trade.stop
            target_hit = low <= trade.target
            if stop_hit and target_hit:
                exit_price = trade.stop
                exit_reason = "stop_same_bar_conservative"
            elif stop_hit:
                exit_price = trade.stop
                exit_reason = "stop"
            elif target_hit:
                exit_price = trade.target
                exit_reason = "target"
            else:
                continue
            points = trade.entry_price - exit_price
        return {
            "track": trade.track,
            "rule": trade.rule,
            "side": trade.side,
            "entry_date": trade.entry_date,
            "exit_date": bar["date"],
            "entry": trade.entry_price,
            "stop": trade.stop,
            "target": trade.target,
            "exit": exit_price,
            "exit_reason": exit_reason,
            "points": points,
            "size": trade.size,
            "weighted_points": points * trade.size,
            "regime_at_entry": trade.regime_at_entry,
            "labels": trade.labels,
            "entry_labels": trade.entry_labels or entry_labels_for_trade(trade),
        }
    return None


def run_backtest(
    symbol: str,
    start: str,
    end_exclusive: str,
    structure_lookback: int = 10,
    bull_continuation_mode: str = "none",
    learn_long_mode: str = "off",
    learn_long_window: int = 5,
    learn_long_min_trades: int = 3,
    learn_long_min_win_rate: float = 0.45,
    learn_long_cooldown_days: int = 15,
    bars: pd.DataFrame | None = None,
    decision_log_path: str | Path | None = None,
    runtime_log_path: str | Path | None = None,
    structural_reset_mode: str = "off",
    max_history_days: int | None = None,
    decision_records_out: list[decision_model.DecisionRecord] | None = None,
    decision_records_jsonl_path: str | Path | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    if structural_reset_mode not in {"off", "log", "apply"}:
        raise ValueError("structural_reset_mode must be one of: off, log, apply")
    if bars is None:
        df = load_bars(symbol, start, end_exclusive)
    else:
        df = bars.copy()
        if "date" not in df.columns:
            raise ValueError("bars must contain a date column")
        df["date"] = pd.to_datetime(df["date"])
        df = df[(df["date"] >= pd.Timestamp(start)) & (df["date"] < pd.Timestamp(end_exclusive))].copy()
        if df.empty:
            raise ValueError(f"No rows for {symbol} in {start}..{end_exclusive}")
        if "day" not in df.columns:
            df["day"] = df["date"].dt.date.astype(str)
        if "time" not in df.columns:
            df["time"] = df["date"].dt.strftime("%H:%M")
        df = df.sort_values("date").reset_index(drop=True)
    decision_log_lines: list[str] = []
    decision_log_file = Path(decision_log_path) if decision_log_path is not None else None
    runtime_log_lines: list[str] = []
    runtime_log_file = Path(runtime_log_path) if runtime_log_path is not None else OUT_DIR / "Trading_Engine.log"
    structured_decisions: list[decision_model.DecisionRecord] = []
    strategy_config_hash = decision_model.stable_config_hash(
        {
            "symbol": symbol.upper(),
            "start": str(start),
            "end_exclusive": str(end_exclusive),
            "bars_supplied_by_caller": bars is not None,
            "structure_lookback": structure_lookback,
            "bull_continuation_mode": bull_continuation_mode,
            "learn_long_mode": learn_long_mode,
            "learn_long_window": learn_long_window,
            "learn_long_min_trades": learn_long_min_trades,
            "learn_long_min_win_rate": learn_long_min_win_rate,
            "learn_long_cooldown_days": learn_long_cooldown_days,
            "structural_reset_mode": structural_reset_mode,
            "max_history_days": max_history_days,
        }
    )

    def emit_structured_decision(
        *,
        decision_dt: pd.Timestamp,
        checks: dict[str, str],
        explanation: dict[str, str],
        context: dict[str, object],
        opened_trades: list[OpenTrade],
        closed_tracks: set[str],
    ) -> None:
        records = decision_model.build_decision_records(
            symbol=symbol,
            decision_time=decision_dt,
            strategy_version=decision_model.STRATEGY_VERSION,
            config_hash=strategy_config_hash,
            checks=checks,
            explanation=explanation,
            context=context,
            opened_trades=opened_trades,
        )
        structured_decisions.extend(records)
        if decision_log_file is not None:
            decision_log_lines.append(
                decision_model.format_log_line(
                    records,
                    closed_tracks=closed_tracks,
                    runtime=False,
                )
            )
        runtime_log_lines.append(
            decision_model.format_log_line(
                records,
                closed_tracks=closed_tracks,
                runtime=True,
            )
        )

    decision_time = detect_decision_time(df)
    daily = add_decision_prices(df, build_daily(df), decision_time).dropna(subset=["decision_price"]).reset_index(drop=True)

    regime = "sideways"
    higher_high_count = 0
    higher_low_count = 0
    regime_start_idx = 0
    broken_support = None
    broken_support_idx = None
    broken_resistance = None
    broken_resistance_idx = None
    breakdown_active = False
    breakout_active = False
    bullish_cycle_seen = False
    bearish_short_armed = False
    bearish_structure_damage = False
    pre_break_bullish_state = None
    last_bullish_entry_low_idx = None
    last_full_long_setup_low_idx = None
    last_pullback_low = None
    last_countermove_high = None
    last_countermove_high_idx = None
    last_bearish_regime_idx = None
    last_long_target_exit_idx = None
    used_lower_high_failure_idxs: set[int] = set()
    last_breakdown_retest_idx = None
    last_structural_reset_idx = None
    last_structural_reset_peak_idx = None
    last_exit_check_dt = None
    trades: list[OpenTrade] = []
    completed: list[dict] = []
    rows: list[dict] = []
    big_picture_cache: dict[str, dict[str, object]] = {}
    structure_epoch_idx = 0
    conservative_long_blocked_rules = {
        "bullish_crash_repair_reversal_long",
        "bullish_post_bear_bull_reentry_long",
        "bullish_tier3_full_trend_participation_breakout",
        "bullish_tier3_small_participation_breakout",
        "bullish_tier3_projected_high_continuation_long",
        "bullish_stair_step_weekly_green_streak_combo",
        "bullish_weekly_green_streak_continuation",
        "bullish_stair_step_continuation",
        "bullish_stair_step_weekly_green_streak_continuation",
        "bullish_add_on_higher_low",
    }

    def completed_long_history(current_day: pd.Timestamp, rule_name: str | None = None, regime_name: str | None = None) -> pd.DataFrame:
        if not completed:
            return pd.DataFrame()
        current_day = pd.Timestamp(current_day).normalize()
        completed_df = pd.DataFrame(completed).copy()
        if completed_df.empty:
            return pd.DataFrame()
        completed_df["entry_date"] = pd.to_datetime(completed_df["entry_date"]).dt.normalize()
        completed_df["exit_date"] = pd.to_datetime(completed_df["exit_date"]).dt.normalize()
        completed_df = completed_df.drop_duplicates(subset=["entry_date", "rule", "side", "entry", "stop", "target"])
        completed_df = completed_df[(completed_df["side"] == "long") & (completed_df["exit_date"] < current_day)].sort_values("exit_date")
        if max_history_days is not None:
            cutoff_day = current_day - pd.Timedelta(days=int(max_history_days))
            completed_df = completed_df[completed_df["exit_date"] >= cutoff_day]
        if rule_name is not None:
            completed_df = completed_df[completed_df["rule"] == rule_name]
        if regime_name is not None:
            completed_df = completed_df[completed_df["regime_at_entry"] == regime_name]
        return completed_df

    def rolling_points_and_wr(frame: pd.DataFrame) -> tuple[float, float, int]:
        if frame.empty:
            return 0.0, 0.0, 0
        recent = frame.tail(learn_long_window)
        points = float(recent["points"].sum())
        win_rate = float((recent["points"] > 0).mean())
        return points, win_rate, int(len(recent))

    def long_profile_for(current_day: pd.Timestamp, rule_name: str | None = None, regime_name: str | None = None) -> str:
        if learn_long_mode == "off":
            return "baseline"
        if learn_long_mode in {"family_gate", "regime_gate"} and rule_name is None:
            return "baseline"
        symbol_hist = completed_long_history(current_day)
        if len(symbol_hist) < learn_long_min_trades:
            return "baseline"

        if learn_long_mode == "symbol_gate":
            points, win_rate, _ = rolling_points_and_wr(symbol_hist)
            return "conservative" if points < 0 or win_rate < learn_long_min_win_rate else "baseline"

        if learn_long_mode == "family_gate":
            if rule_name is None:
                return "baseline"
            family_hist = completed_long_history(current_day, rule_name=rule_name)
            points, win_rate, count = rolling_points_and_wr(family_hist)
            if count < learn_long_min_trades:
                return "baseline"
            return "conservative" if points < 0 or win_rate < learn_long_min_win_rate else "baseline"

        if learn_long_mode == "regime_gate":
            if rule_name is None or regime_name is None:
                return "baseline"
            regime_hist = completed_long_history(current_day, rule_name=rule_name, regime_name=regime_name)
            points, win_rate, count = rolling_points_and_wr(regime_hist)
            if count < learn_long_min_trades:
                return "baseline"
            return "conservative" if points < 0 or win_rate < learn_long_min_win_rate else "baseline"

        if learn_long_mode == "size_shrink":
            points, win_rate, _ = rolling_points_and_wr(symbol_hist)
            return "size_shrink" if points < 0 or win_rate < learn_long_min_win_rate else "baseline"

        if learn_long_mode == "hybrid_gate":
            points, win_rate, _ = rolling_points_and_wr(symbol_hist)
            if points < 0 or win_rate < learn_long_min_win_rate:
                return "conservative"
            if rule_name is not None:
                family_hist = completed_long_history(current_day, rule_name=rule_name)
                f_points, f_wr, f_count = rolling_points_and_wr(family_hist)
                if f_count >= learn_long_min_trades and (f_points < 0 or f_wr < learn_long_min_win_rate):
                    return "conservative"
            if rule_name is not None and regime_name is not None:
                regime_hist = completed_long_history(current_day, rule_name=rule_name, regime_name=regime_name)
                r_points, r_wr, r_count = rolling_points_and_wr(regime_hist)
                if r_count >= learn_long_min_trades and (r_points < 0 or r_wr < learn_long_min_win_rate):
                    return "conservative"
            return "baseline"

        return "baseline"

    def long_trade_allowed(rule_name: str, current_day: pd.Timestamp, regime_name: str) -> tuple[bool, float, str]:
        profile = long_profile_for(current_day, rule_name=rule_name, regime_name=regime_name)
        if learn_long_mode == "off":
            return True, 1.0, profile
        if learn_long_mode == "symbol_gate":
            return rule_name not in conservative_long_blocked_rules or profile != "conservative", 1.0, profile
        if learn_long_mode == "family_gate":
            return profile != "conservative", 1.0, profile
        if learn_long_mode == "regime_gate":
            return profile != "conservative", 1.0, profile
        if learn_long_mode == "size_shrink":
            return True, (0.25 if profile == "size_shrink" else 1.0), profile
        if learn_long_mode == "hybrid_gate":
            if profile == "conservative":
                return rule_name not in conservative_long_blocked_rules, 1.0, profile
            return True, 1.0, profile
        return True, 1.0, profile

    def long_rule_allowed(rule_name: str, current_day: pd.Timestamp) -> bool:
        allowed, _, _ = long_trade_allowed(rule_name, current_day, current_policy_regime)
        return allowed

    def add_phase_context(
        explanation: dict[str, str],
        regime_name: str,
        volume_price_phase: str,
        volume_price_detail: str,
    ) -> dict[str, str]:
        result = dict(explanation)
        setup_state = result["setup_state"]
        primary_reason = result["primary_reason"]
        if result["trade_action"] == "trade":
            market_phase = "tradeable_breakout" if regime_name.startswith("bullish") else "tradeable_breakdown"
        elif regime_name == "bullish_tier3" and setup_state == "no_qualified_tier3_entry":
            if primary_reason in {"no_structural_breakout", "missing_structural_higher_low", "no_confirmed_high_after_pullback"}:
                market_phase = "bullish_consolidation"
            elif primary_reason in {"open_too_extended", "decision_too_extended"}:
                market_phase = "bullish_extended"
            else:
                market_phase = "bullish_no_qualified_entry"
        elif setup_state == "position_open":
            market_phase = "position_management"
        elif regime_name.startswith("bearish"):
            market_phase = "bearish_no_qualified_entry"
        elif regime_name.startswith("bullish"):
            market_phase = "bullish_no_qualified_entry"
        else:
            market_phase = "neutral_no_qualified_entry"

        if volume_price_phase == "high_volume_stall":
            risk_note = "possible_absorption_or_distribution"
        elif market_phase == "bullish_consolidation":
            risk_note = "consolidation_no_breakout"
        elif volume_price_phase == "volume_unavailable":
            risk_note = "volume_not_available_for_symbol"
        else:
            risk_note = "-"

        result["market_phase"] = market_phase
        result["volume_price_phase"] = volume_price_phase
        result["volume_price_detail"] = volume_price_detail
        result["risk_note"] = risk_note
        return result

    def explain_decision(
        regime_name: str,
        checks: dict[str, str],
        opened_rules: list[str],
        learned_blocked_rules: list[str],
        open_trade_count: int,
        exited_today: bool,
        long_target_cooldown: bool,
        bearish_structure_damage: bool,
        resistance_break: bool,
        latest_structural_high,
        latest_structural_low,
        prev_structural_low,
        levels_prior,
        row_index: int,
        open_price: float,
        decision_price: float,
        atr14_value: float,
        tolerance_value: float,
        higher_low_count_value: int,
        last_bullish_entry_low_idx,
        prior_high_value: float,
        projected_high_trigger: float | None,
    ) -> dict[str, str]:
        if opened_rules:
            opened = ",".join(opened_rules)
            return {
                "trade_action": "trade",
                "setup_state": "trade_opened",
                "primary_reason": opened,
                "setup_detail": f"opened={opened}",
            }

        blocked = [rule for rule, state in checks.items() if state == "blocked"]
        blocked.extend(learned_blocked_rules)
        if blocked:
            reason = "blocked_" + sorted(set(blocked))[0]
            return {
                "trade_action": "no_trade",
                "setup_state": "blocked",
                "primary_reason": reason,
                "setup_detail": "blocked_rules=" + ",".join(sorted(set(blocked))),
            }

        if exited_today:
            return {
                "trade_action": "no_trade",
                "setup_state": "cooldown_after_exit",
                "primary_reason": "track_closed_at_decision",
                "setup_detail": "closed_track_at_decision=true",
            }

        if long_target_cooldown and regime_name.startswith("bullish"):
            return {
                "trade_action": "no_trade",
                "setup_state": "cooldown_after_long_target",
                "primary_reason": "long_target_cooldown",
                "setup_detail": "long_target_cooldown=true",
            }

        if regime_name == "bullish_tier3":
            breakout_level = latest_structural_high.price if latest_structural_high is not None else prior_high_value
            structural_breakout = decision_price > breakout_level + tolerance_value and open_price > breakout_level
            high_after_low = (
                latest_structural_high is not None
                and latest_structural_low is not None
                and latest_structural_high.idx > latest_structural_low.idx
            )
            progressive_high_count = count_progressive_structural_highs(levels_prior, max(0, row_index - 70), 2)
            structural_higher_low = (
                latest_structural_low is not None
                and prev_structural_low is not None
                and latest_structural_low.price > prev_structural_low.price + tolerance_value
            )
            mature_higher_low_base = higher_low_count_value >= 2
            fresh_setup_low = (
                latest_structural_low is not None
                and (last_bullish_entry_low_idx is None or latest_structural_low.idx > last_bullish_entry_low_idx)
            )
            open_ext = (open_price - breakout_level) / atr14_value if atr14_value > 0 else 999.0
            decision_ext = (decision_price - breakout_level) / atr14_value if atr14_value > 0 else 999.0
            detail = "|".join(
                [
                    f"breakout_level={breakout_level:.2f}",
                    f"projected_high_trigger={projected_high_trigger:.2f}" if projected_high_trigger is not None else "projected_high_trigger=-",
                    f"resistance_break={str(bool(resistance_break)).lower()}",
                    f"structural_breakout={str(bool(structural_breakout)).lower()}",
                    f"high_after_low={str(bool(high_after_low)).lower()}",
                    f"progressive_highs={progressive_high_count}",
                    f"structural_higher_low={str(bool(structural_higher_low)).lower()}",
                    f"fresh_setup_low={str(bool(fresh_setup_low)).lower()}",
                    f"open_ext_atr={open_ext:.2f}",
                    f"decision_ext_atr={decision_ext:.2f}",
                    f"open_trades={open_trade_count}",
                ]
            )
            if not resistance_break:
                reason = "no_resistance_break"
            elif latest_structural_high is None:
                reason = "missing_structural_high"
            elif latest_structural_low is None:
                reason = "missing_structural_low"
            elif not structural_breakout:
                reason = "no_structural_breakout"
            elif not high_after_low:
                reason = "no_confirmed_high_after_pullback"
            elif progressive_high_count < 5:
                reason = "insufficient_progressive_highs"
            elif not structural_higher_low:
                reason = "missing_structural_higher_low"
            elif not mature_higher_low_base:
                reason = "immature_higher_low_base"
            elif not fresh_setup_low:
                reason = "stale_setup_low"
            elif open_ext > 0.75:
                reason = "open_too_extended"
            elif decision_ext > 1.25:
                reason = "decision_too_extended"
            elif open_trade_count > 0:
                reason = "open_trade_already_active"
            elif bearish_structure_damage:
                reason = "bearish_structure_damage"
            else:
                reason = "tier3_entry_conditions_incomplete"
            return {
                "trade_action": "no_trade",
                "setup_state": "no_qualified_tier3_entry",
                "primary_reason": reason,
                "setup_detail": detail,
            }

        if open_trade_count > 0:
            return {
                "trade_action": "no_trade",
                "setup_state": "position_open",
                "primary_reason": "open_trade_already_active",
                "setup_detail": f"open_trades={open_trade_count}",
            }

        return {
            "trade_action": "no_trade",
            "setup_state": "no_setup",
            "primary_reason": "no_rule_triggered",
            "setup_detail": "all_rule_checks_skipped",
        }

    for i, row in daily.iterrows():
        day = row["day"]
        day_ts = pd.Timestamp(day).normalize()
        open_trades_at_day_start = tuple(trades)
        history_cutoff = None
        if max_history_days is not None:
            history_cutoff = day_ts - pd.Timedelta(days=int(max_history_days))

            def idx_expired(idx: int | None) -> bool:
                if idx is None or idx < 0 or idx >= len(daily):
                    return False
                return pd.Timestamp(daily.iloc[int(idx)]["day"]).normalize() < history_cutoff

            if idx_expired(regime_start_idx):
                regime = "sideways"
                higher_high_count = 0
                higher_low_count = 0
                regime_start_idx = max(i - 1, 0)
                bullish_cycle_seen = False
                bearish_short_armed = False
                bearish_structure_damage = False
                pre_break_bullish_state = None
                last_pullback_low = None
            if idx_expired(broken_support_idx):
                broken_support = None
                broken_support_idx = None
                breakdown_active = False
            if idx_expired(broken_resistance_idx):
                broken_resistance = None
                broken_resistance_idx = None
                breakout_active = False
            if idx_expired(last_bullish_entry_low_idx):
                last_bullish_entry_low_idx = None
            if idx_expired(last_full_long_setup_low_idx):
                last_full_long_setup_low_idx = None
            if idx_expired(last_countermove_high_idx):
                last_countermove_high = None
                last_countermove_high_idx = None
            if idx_expired(last_bearish_regime_idx):
                last_bearish_regime_idx = None
            if idx_expired(last_long_target_exit_idx):
                last_long_target_exit_idx = None
            if idx_expired(last_breakdown_retest_idx):
                last_breakdown_retest_idx = None
            used_lower_high_failure_idxs = {idx for idx in used_lower_high_failure_idxs if not idx_expired(idx)}
            if pre_break_bullish_state is not None and idx_expired(int(pre_break_bullish_state["regime_start_idx"])):
                pre_break_bullish_state = None
        current_policy_regime = regime
        decision_checks: dict[str, str] = {
            "crash_repair": "skip",
            "post_bear_reentry": "skip",
            "repair_range_breakout": "skip",
            "pullback_reclaim": "skip",
            "pullback_confirmed": "skip",
            "add_on": "skip",
            "continuation": "skip",
            "tier3": "skip",
            "lower_high_failure": "skip",
            "gap_breakdown": "skip",
            "failed_countermove": "skip",
            "structural_reset": "skip",
            "post_break_midrange": "skip",
        }
        opened_rules: list[str] = []
        prior = daily.iloc[:i]
        if history_cutoff is not None:
            prior = prior[pd.to_datetime(prior["day"]) >= history_cutoff]
        if len(prior) < 25:
            warmup_explanation = {
                "trade_action": "no_trade",
                "setup_state": "warmup",
                "primary_reason": "insufficient_history",
                "setup_detail": f"prior_days={len(prior)}",
                "market_phase": "warmup",
                "volume_price_phase": "volume_insufficient_history",
                "volume_price_detail": f"prior_days={len(prior)}",
                "risk_note": "-",
            }
            rows.append(
                {
                    "day": day,
                    "regime": regime,
                    "baseline_signal": "warmup",
                    "experimental_signal": "warmup",
                    **warmup_explanation,
                }
            )
            warmup_checks = {**decision_checks, "warmup": "skip"}
            warmup_atr = float(row["atr14"]) if not pd.isna(row["atr14"]) else 0.0
            emit_structured_decision(
                decision_dt=pd.Timestamp(row["decision_datetime"]),
                checks=warmup_checks,
                explanation=warmup_explanation,
                context={
                    **rows[-1],
                    "day": day,
                    "open": float(row["open"]),
                    "decision_price": float(row["decision_price"]),
                    "atr14": warmup_atr,
                },
                opened_trades=[],
                closed_tracks=set(),
            )
            continue

        prev = daily.iloc[i - 1]
        before_prev = daily.iloc[: i - 1]
        if history_cutoff is not None:
            before_prev = before_prev[pd.to_datetime(before_prev["day"]) >= history_cutoff]
        structure_prior = prior[prior.index >= structure_epoch_idx]
        structure_before_prev = before_prev[before_prev.index >= structure_epoch_idx]
        if structure_prior.empty:
            structure_prior = prior
        if structure_before_prev.empty:
            structure_before_prev = before_prev
        prior_close = float(prev["close"])
        # Decision-time tolerance must be known before the trade decision.
        # The current row's ATR includes the full decision-day range, so use
        # the latest completed session instead.
        atr14 = float(prev["atr14"]) if not pd.isna(prev["atr14"]) else float(prev["tr"])
        tol = tolerance(symbol, atr14, prior_close)
        decision_price = float(row["decision_price"])
        open_price = float(row["open"])
        decision_dt = row["decision_datetime"]
        decision_bar_high = float(row["decision_high"])
        decision_bar_low = float(row["decision_low"])

        levels_prior = detect_confirmed_swings(structure_prior, tol)
        levels_before_prev = detect_confirmed_swings(structure_before_prev, tol)
        support_level = relevant_support(levels_prior, i, decision_price + atr14, tol)
        resistance_level = relevant_resistance(levels_prior, i, decision_price - atr14, tol)
        prev_support_level = relevant_support(levels_before_prev, i - 1, prior_close + atr14, tol)
        prev_resistance_level = relevant_resistance(levels_before_prev, i - 1, prior_close - atr14, tol)
        prior_low = support_level.price if support_level is not None else float(structure_prior["low"].min())
        prior_high = resistance_level.price if resistance_level is not None else float(structure_prior["high"].max())
        prev_ref_low = prev_support_level.price if prev_support_level is not None else float(structure_before_prev["low"].min())
        prev_ref_high = prev_resistance_level.price if prev_resistance_level is not None else float(structure_before_prev["high"].max())
        post_break_range = detect_post_break_midrange(levels_prior, decision_price, atr14, tol, i)
        post_break_midrange = (
            bool(post_break_range["post_break_midrange"])
            and breakdown_active
            and broken_support is not None
            and not regime.startswith("bullish")
        )

        # First manage open trades only up to the current decision timestamp.
        # Anything after the decision is not known while deciding today's signal.
        still_open: list[OpenTrade] = []
        exited_today = False
        exited_today_tracks: set[str] = set()
        for trade in trades:
            after_dt = trade.entry_date if last_exit_check_dt is None else max(trade.entry_date, last_exit_check_dt)
            exit_row = simulate_trade_exit(df, trade, after_dt, decision_dt)
            if exit_row is None:
                still_open.append(trade)
            else:
                completed.append(exit_row)
                exited_today = True
                exited_today_tracks.add(trade.track)
                if trade.side == "long" and exit_row["exit_reason"] == "target":
                    last_long_target_exit_idx = i
        trades = still_open
        last_exit_check_dt = decision_dt

        structural_reset = None
        structural_reset_applied = False
        if structural_reset_mode != "off":
            structural_reset = detect_structural_reset(
                prior,
                i,
                decision_price,
                atr14,
                tol,
                last_structural_reset_idx,
            )
            if (
                structural_reset is not None
                and last_structural_reset_peak_idx is not None
                and int(structural_reset["peak_idx"]) == int(last_structural_reset_peak_idx)
            ):
                structural_reset = None
            if structural_reset is not None:
                decision_checks["structural_reset"] = f"{structural_reset_mode}:{structural_reset['reason']}"
                if structural_reset_mode == "log":
                    last_structural_reset_idx = i
                    last_structural_reset_peak_idx = int(structural_reset["peak_idx"])
            if structural_reset is not None and structural_reset_mode == "apply":
                old_bullish_context = regime.startswith("bullish") or bullish_cycle_seen
                regime = "bearish_confirmed"
                regime_start_idx = i
                higher_high_count = 0
                higher_low_count = 0
                reset_support = structural_reset.get("old_support")
                if reset_support is not None and float(reset_support) > decision_price + tol:
                    broken_support = float(reset_support)
                else:
                    broken_support = decision_price + max(atr14, 4.0 * tol)
                broken_support_idx = i
                broken_resistance = None
                broken_resistance_idx = None
                breakdown_active = True
                breakout_active = False
                bullish_cycle_seen = False
                bearish_short_armed = old_bullish_context
                bearish_structure_damage = True
                pre_break_bullish_state = None
                last_pullback_low = None
                last_countermove_high = None
                last_countermove_high_idx = None
                last_breakdown_retest_idx = None
                used_lower_high_failure_idxs = set()
                last_bearish_regime_idx = i
                last_structural_reset_idx = i
                last_structural_reset_peak_idx = int(structural_reset["peak_idx"])
                structure_epoch_idx = i
                structural_reset_applied = True

        support_break = (prior_close < prev_ref_low - tol) or (decision_price < prior_low - tol and open_price < prior_low)
        resistance_break = (prior_close > prev_ref_high + tol) or (decision_price > prior_high + tol and open_price > prior_high)
        bullish_reclaim = (
            regime.startswith("bearish")
            and breakdown_active
            and broken_support is not None
            and decision_price > broken_support + tol
            and not post_break_midrange
        )
        bearish_reclaim = regime.startswith("bullish") and breakout_active and broken_resistance is not None and decision_price < broken_resistance - tol
        breakdown_retest = (
            breakdown_active
            and broken_support is not None
            and abs(decision_price - broken_support) <= 0.50 * atr14
            and decision_price <= broken_support + tol
            and not bullish_reclaim
        )
        if breakdown_retest:
            last_breakdown_retest_idx = i
        bars_since_support_break = i - broken_support_idx if broken_support_idx is not None else None

        latest_high = latest_swing(levels_prior, "high")
        latest_low = latest_swing(levels_prior, "low")
        latest_structural_high = latest_structural_swing(levels_prior, "high", 2)
        latest_structural_low = latest_structural_swing(levels_prior, "low", 2)
        prev_structural_low = previous_structural_swing(levels_prior, latest_structural_low, 2) if latest_structural_low is not None else None
        prev_high_swing = previous_swing(levels_prior, latest_high) if latest_high is not None else None
        prev_low_swing = previous_swing(levels_prior, latest_low) if latest_low is not None else None
        higher_high = latest_high is not None and prev_high_swing is not None and latest_high.price > prev_high_swing.price + tol
        lower_low = latest_low is not None and prev_low_swing is not None and latest_low.price < prev_low_swing.price - tol
        higher_low = latest_low is not None and prev_low_swing is not None and latest_low.price > prev_low_swing.price + tol
        structural_lower_low = (
            latest_structural_low is not None
            and prev_structural_low is not None
            and latest_structural_low.price < prev_structural_low.price - tol
        )

        repair_breakout_priority = (
            resistance_break
            and not structural_reset_applied
            and not post_break_midrange
            and not regime.startswith("bullish")
            and count_recent_higher_swings(levels_prior, "low", tol) >= 1
        )
        reclaimed_prior_bullish = False
        if pre_break_bullish_state is not None and bars_since_support_break is not None and bars_since_support_break > 2:
            pre_break_bullish_state = None

        if post_break_midrange:
            decision_checks["post_break_midrange"] = "blocked"

        if repair_breakout_priority and not bullish_cycle_seen:
            regime = "bullish_tier1"
            regime_start_idx = latest_low.idx if latest_low is not None else max(i - 1, 0)
            breakout_active = True
            breakdown_active = False
            broken_resistance = prior_high
            broken_resistance_idx = i
            bullish_cycle_seen = True
        elif support_break and not bullish_reclaim:
            if not regime.startswith("bearish"):
                if regime.startswith("bullish"):
                    pre_break_bullish_state = {
                        "regime": regime,
                        "regime_start_idx": regime_start_idx,
                        "higher_high_count": higher_high_count,
                        "higher_low_count": higher_low_count,
                        "last_pullback_low": last_pullback_low,
                    }
                regime = "bearish_attempt"
                regime_start_idx = latest_high.idx if latest_high is not None else max(i - 1, 0)
                higher_high_count = 0
                higher_low_count = 0
                last_pullback_low = None
                bearish_short_armed = bullish_cycle_seen
            breakdown_active = True
            breakout_active = False
            if structural_lower_low:
                bearish_structure_damage = True
            new_broken_support = max(prev_ref_low, prior_low)
            if broken_support is None or abs(new_broken_support - broken_support) > tol:
                broken_support = new_broken_support
                broken_support_idx = i
        elif bullish_reclaim:
            if pre_break_bullish_state is not None and (regime != "bearish_confirmed" or (bars_since_support_break is not None and bars_since_support_break <= 2)):
                regime = str(pre_break_bullish_state["regime"])
                regime_start_idx = int(pre_break_bullish_state["regime_start_idx"])
                higher_high_count = int(pre_break_bullish_state["higher_high_count"])
                higher_low_count = int(pre_break_bullish_state["higher_low_count"])
                last_pullback_low = pre_break_bullish_state["last_pullback_low"]
                reclaimed_prior_bullish = True
            else:
                regime = "bullish_repair"
                regime_start_idx = latest_low.idx if latest_low is not None else max(i - 1, 0)
                higher_high_count = 0
                higher_low_count = 0
                last_pullback_low = None
            breakout_active = True
            breakdown_active = False
            broken_resistance = prior_high
            broken_resistance_idx = i
            pre_break_bullish_state = None
        elif regime == "bullish_repair" and resistance_break and higher_low:
            regime = "bullish_tier1"
            regime_start_idx = latest_low.idx if latest_low is not None else max(i - 1, 0)
            breakout_active = True
            breakdown_active = False
            broken_resistance = prior_high
            broken_resistance_idx = i
            bullish_cycle_seen = True
        elif resistance_break and not regime.startswith("bearish") and regime != "bullish_repair":
            regime = "bullish_tier1"
            regime_start_idx = latest_low.idx if latest_low is not None else max(i - 1, 0)
            breakout_active = True
            breakdown_active = False
            broken_resistance = prior_high
            broken_resistance_idx = i
            bullish_cycle_seen = True

        if regime.startswith("bullish"):
            structure_start_idx = max(0, i - 70)
            higher_high_count = count_recent_higher_swings(levels_prior, "high", tol, structure_start_idx, min_scale_weight=2)
            higher_low_count = count_recent_higher_swings(levels_prior, "low", tol, structure_start_idx, min_scale_weight=2)
            if higher_low:
                last_pullback_low = latest_low.price
            if higher_high_count >= 5 and higher_low_count >= 2:
                regime = "bullish_tier3"
            elif higher_high_count >= 3 and higher_low_count >= 1:
                regime = "bullish_tier2"

        if regime == "bearish_attempt":
            second_close_below = broken_support is not None and prior_close < broken_support - tol
            no_reclaim_gap = broken_support is not None and decision_price < broken_support - tol and open_price < broken_support
            if second_close_below or lower_low or breakdown_retest or no_reclaim_gap:
                regime = "bearish_confirmed"
        elif regime == "bearish_confirmed":
            if breakdown_retest:
                regime = "bearish_countermove"
            if latest_high is not None and prev_high_swing is not None and latest_high.price < prev_high_swing.price + tol:
                last_countermove_high = latest_high.price
                last_countermove_high_idx = latest_high.idx

        baseline_signal = "no_trade"
        experimental_signal = "no_trade"
        learned_long_blocked_rules: list[str] = []
        current_long_profile = long_profile_for(day_ts)
        long_target_cooldown = last_long_target_exit_idx is not None and i - last_long_target_exit_idx <= 1
        bullish_repair_filter = bullish_repair_pressure(prior, decision_price, open_price, tol)
        bearish_countermove_subregime = ""
        if regime in {"bearish_attempt", "bearish_confirmed", "bearish_countermove"}:
            bearish_countermove_subregime = classify_bearish_countermove(
                prior,
                decision_price,
                broken_support,
                bars_since_support_break,
                atr14,
                bullish_repair_filter,
            )
        structural_higher_low_now = (
            latest_structural_low is not None
            and prev_structural_low is not None
            and latest_structural_low.price > prev_structural_low.price + tol
        )
        bullish_rebuild_breakout = (
            latest_structural_high is not None
            and latest_structural_low is not None
            and latest_structural_high.idx > latest_structural_low.idx
            and decision_price > latest_structural_high.price + tol
            and open_price > latest_structural_high.price
            and structural_higher_low_now
            and count_progressive_structural_highs(levels_prior, max(0, i - 70), 2) >= 5
        )
        if bearish_structure_damage and bullish_rebuild_breakout:
            bearish_structure_damage = False
        recent_60_high_for_phase = float(prior["high"].tail(60).max())
        distance_from_recent_high_atr = (recent_60_high_for_phase - decision_price) / max(atr14, 1e-9)
        weekly_streak_weeks, weekly_streak_green, weekly_streak_red = weekly_green_streak(prior, day)
        big_picture_key = str(pd.Timestamp(day).to_period("W-FRI"))
        if big_picture_key not in big_picture_cache:
            big_picture_cache[big_picture_key] = classify_big_picture_regime(prior, atr14, tol, day)
        big_picture = big_picture_cache[big_picture_key]
        post_break_context = breakdown_active and broken_support is not None and not regime.startswith("bullish")
        value_profile = classify_intraday_value_profile(df, decision_dt, decision_price, atr14, tol, lookback_days=10)
        daily_structure = classify_daily_structure_phase(
            post_break_context,
            post_break_range,
            value_profile,
            decision_price,
            open_price,
            atr14,
            distance_from_recent_high_atr,
            support_break,
        )
        if regime in {"bearish_attempt", "bearish_confirmed", "bearish_countermove"}:
            last_bearish_regime_idx = i
        bars_since_last_bearish_regime = (
            None if last_bearish_regime_idx is None else i - last_bearish_regime_idx
        )

        crash_repair_setup, crash_repair_stop_base, crash_repair_breakout_level = bullish_crash_repair_reversal(
            prior,
            decision_price,
            open_price,
            atr14,
            tol,
        )
        if (
            crash_repair_setup
            and not long_target_cooldown
            and regime in {"bearish_confirmed", "bearish_countermove"}
            and crash_repair_stop_base is not None
        ):
            stop = crash_repair_stop_base - tol
            risk = decision_price - stop
            if risk > 0:
                target = decision_price + risk
                if long_rule_allowed("bullish_crash_repair_reversal_long", day_ts):
                    added_tracks: set[str] = set()
                    for track in ("baseline", "experimental"):
                        track_open = any(t.track == track for t in trades)
                        if not track_open and track not in exited_today_tracks:
                            trades.append(
                                OpenTrade(
                                    track,
                                    "bullish_crash_repair_reversal_long",
                                    "long",
                                    decision_dt,
                                    decision_price,
                                    stop,
                                    target,
                                    1.0,
                                    regime,
                                    labels="bullish_crash_repair_reversal",
                                )
                            )
                            added_tracks.add(track)
                    decision_checks["crash_repair"] = "trade"
                    opened_rules.append("bullish_crash_repair_reversal_long")
                    if latest_structural_low is not None:
                        last_full_long_setup_low_idx = latest_structural_low.idx
                    if "baseline" in added_tracks:
                        baseline_signal = "crash_repair_long"
                    if "experimental" in added_tracks:
                        experimental_signal = "crash_repair_long"
                else:
                    decision_checks["crash_repair"] = "blocked"
                    learned_long_blocked_rules.append("bullish_crash_repair_reversal_long")

        # Post-bear bull re-entry: after a confirmed bearish phase, trade the
        # first strong bullish rebuild even if no classical upside target exists.
        fresh_post_bear_low = (
            latest_structural_low is not None
            and (last_bullish_entry_low_idx is None or latest_structural_low.idx > last_bullish_entry_low_idx)
        )
        post_bear_weekly_rebuild = weekly_streak_green >= 4 and weekly_streak_red <= 2
        recent_45 = prior.tail(45)
        post_bear_drawdown_depth_atr = (
            (float(recent_45["high"].max()) - float(recent_45["low"].min())) / max(atr14, 1e-9)
            if not recent_45.empty
            else 0.0
        )
        post_bear_new_high_extension_atr = (
            (decision_price - float(recent_45["high"].max())) / max(atr14, 1e-9)
            if not recent_45.empty
            else 0.0
        )
        post_bear_open_gap_extension_atr = (
            (open_price - float(recent_45["high"].max())) / max(atr14, 1e-9)
            if not recent_45.empty
            else 0.0
        )
        post_bear_bull_reentry = (
            not trades
            and not exited_today
            and not long_target_cooldown
            and regime == "bullish_tier3"
            and resistance_break
            and bars_since_last_bearish_regime is not None
            and 3 <= bars_since_last_bearish_regime <= 15
            and higher_high_count >= 5
            and higher_low_count >= 3
            and post_bear_weekly_rebuild
            and post_bear_drawdown_depth_atr >= 4.0
            and post_bear_new_high_extension_atr >= 0.25
            and post_bear_open_gap_extension_atr >= 0.15
            and fresh_post_bear_low
        )
        if post_bear_bull_reentry:
            stop_candidates = []
            if latest_structural_low is not None:
                stop_candidates.append(latest_structural_low.price)
            if latest_low is not None:
                stop_candidates.append(latest_low.price)
            accepted_support = price_acceptance_target(df, decision_dt, "short", decision_price, atr14, tol)
            if accepted_support is not None:
                stop_candidates.append(accepted_support)
            stop_candidates.append(prior_low)
            stop_candidates = [value for value in stop_candidates if value < decision_price - tol]
            if stop_candidates:
                stop_base = max(stop_candidates)
                stop = stop_base - tol
                risk = decision_price - stop
                if risk > 0:
                    target = decision_price + risk
                    labels = "bullish_post_bear_bull_reentry"
                    if long_rule_allowed("bullish_post_bear_bull_reentry_long", day_ts):
                        trades.append(
                            OpenTrade(
                                "baseline",
                                "bullish_post_bear_bull_reentry_long",
                                "long",
                                decision_dt,
                                decision_price,
                                stop,
                                target,
                                1.0,
                                regime,
                                labels=labels,
                            )
                        )
                        decision_checks["post_bear_reentry"] = "trade"
                        opened_rules.append("bullish_post_bear_bull_reentry_long")
                        trades.append(
                            OpenTrade(
                                "experimental",
                                "bullish_post_bear_bull_reentry_long",
                                "long",
                                decision_dt,
                                decision_price,
                                stop,
                                target,
                                1.0,
                                regime,
                                labels=labels,
                            )
                        )
                        last_full_long_setup_low_idx = latest_structural_low.idx
                        last_bullish_entry_low_idx = latest_structural_low.idx
                        baseline_signal = "post_bear_bull_reentry_long"
                        experimental_signal = "post_bear_bull_reentry_long"
                    else:
                        decision_checks["post_bear_reentry"] = "blocked"
                        learned_long_blocked_rules.append("bullish_post_bear_bull_reentry_long")

        # Repair-range breakout long: first bullish trade after a repair phase.
        if not trades and not exited_today and not long_target_cooldown and not bearish_structure_damage and regime in {"bullish_tier1", "bullish_tier2"} and resistance_break:
            recent_low_level = latest_swing(levels_prior, "low")
            stop_base = recent_low_level.price if recent_low_level is not None else prior_low
            accepted_support = price_acceptance_target(df, decision_dt, "short", decision_price, atr14, tol)
            if accepted_support is not None and accepted_support < decision_price - tol and decision_price - stop_base > 1.50 * atr14:
                stop_base = max(stop_base, accepted_support)
            target = price_acceptance_target(df, decision_dt, "long", decision_price, atr14, tol)
            if target is None:
                target = repair_breakout_target(prior, decision_price, tol)
            stop = stop_base - tol
            risk = decision_price - stop
            crv = ((target - decision_price) / risk) if risk > 0 and target is not None else 0
            if risk > 0 and crv < 0.90:
                structural_target = fallback_structural_long_target(levels_prior, decision_price, tol)
                structural_crv = ((structural_target - decision_price) / risk) if structural_target is not None else 0
                if structural_crv > crv:
                    target = structural_target
                    crv = structural_crv
            if risk > 0 and target is not None and crv >= 0.90:
                if long_rule_allowed("bullish_repair_range_breakout", day_ts):
                    trades.append(OpenTrade("baseline", "bullish_repair_range_breakout", "long", decision_dt, decision_price, stop, target, 1.0, regime))
                    trades.append(OpenTrade("experimental", "bullish_repair_range_breakout", "long", decision_dt, decision_price, stop, target, 1.0, regime))
                    decision_checks["repair_range_breakout"] = "trade"
                    opened_rules.append("bullish_repair_range_breakout")
                    if latest_low is not None:
                        last_full_long_setup_low_idx = latest_low.idx
                    baseline_signal = "long"
                    experimental_signal = "long"
                else:
                    decision_checks["repair_range_breakout"] = "blocked"
                    learned_long_blocked_rules.append("bullish_repair_range_breakout")

        # Baseline long: confirmed pullback higher-low in bullish regime.
        bear_bias_reclaim = bearish_short_armed and bullish_reclaim and not reclaimed_prior_bullish
        reclaim_has_higher_low = (
            latest_structural_low is not None
            and prev_structural_low is not None
            and latest_structural_low.price > prev_structural_low.price + tol
        )
        if (
            not trades
            and not exited_today
            and not long_target_cooldown
            and not bear_bias_reclaim
            and not bearish_structure_damage
            and reclaimed_prior_bullish
            and reclaim_has_higher_low
            and regime.startswith("bullish")
            and bullish_reclaim
            and broken_support is not None
        ):
            natural_target_level = nearest_resistance_above(levels_prior, decision_price, tol)
            accepted_target = price_acceptance_target(df, decision_dt, "long", decision_price, atr14, tol)
            natural_target = accepted_target if accepted_target is not None else (natural_target_level.price if natural_target_level is not None else None)
            accepted_support = price_acceptance_target(df, decision_dt, "short", decision_price, atr14, tol)
            stop_base = broken_support
            if accepted_support is not None and accepted_support < decision_price - tol and decision_price - stop_base > 1.50 * atr14:
                stop_base = max(stop_base, accepted_support)
            stop = stop_base - tol
            risk = decision_price - stop
            crv = ((natural_target - decision_price) / risk) if risk > 0 and natural_target is not None else 0
            if risk > 0 and crv < 1.0:
                structural_target = fallback_structural_long_target(levels_prior, decision_price, tol)
                structural_crv = ((structural_target - decision_price) / risk) if structural_target is not None else 0
                if structural_crv > crv:
                    natural_target = structural_target
                    crv = structural_crv
            if risk > 0 and natural_target is not None and crv >= 1.0:
                if long_rule_allowed("bullish_pullback_reclaim_long", day_ts):
                    trades.append(OpenTrade("baseline", "bullish_pullback_reclaim_long", "long", decision_dt, decision_price, stop, natural_target, 1.0, regime))
                    trades.append(OpenTrade("experimental", "bullish_pullback_reclaim_long", "long", decision_dt, decision_price, stop, natural_target, 1.0, regime))
                    decision_checks["pullback_reclaim"] = "trade"
                    opened_rules.append("bullish_pullback_reclaim_long")
                    if latest_low is not None:
                        last_full_long_setup_low_idx = latest_low.idx
                    baseline_signal = "long"
                    experimental_signal = "long"
                else:
                    decision_checks["pullback_reclaim"] = "blocked"
                    learned_long_blocked_rules.append("bullish_pullback_reclaim_long")

        if not trades and not exited_today and not long_target_cooldown and not bearish_structure_damage and regime.startswith("bullish") and last_pullback_low is not None:
            confirmation_high = latest_high.price if latest_high is not None else float(prev["high"])
            if decision_price > confirmation_high + tol:
                natural_target_level = nearest_resistance_above(levels_prior, decision_price, tol)
                accepted_target = price_acceptance_target(df, decision_dt, "long", decision_price, atr14, tol)
                natural_target = accepted_target if accepted_target is not None else (natural_target_level.price if natural_target_level is not None else None)
                accepted_support = price_acceptance_target(df, decision_dt, "short", decision_price, atr14, tol)
                stop_base = last_pullback_low
                if accepted_support is not None and accepted_support < decision_price - tol and decision_price - stop_base > 1.50 * atr14:
                    stop_base = max(stop_base, accepted_support)
                stop = stop_base - tol
                risk = decision_price - stop
                crv = ((natural_target - decision_price) / risk) if risk > 0 and natural_target is not None else 0
                if risk > 0 and crv < 1.0:
                    structural_target = fallback_structural_long_target(levels_prior, decision_price, tol)
                    structural_crv = ((structural_target - decision_price) / risk) if structural_target is not None else 0
                    if structural_crv > crv:
                        natural_target = structural_target
                        crv = structural_crv
                if risk > 0 and natural_target is not None and crv >= 1.0:
                    target = natural_target
                    if long_rule_allowed("bullish_pullback_confirmed_higher_low", day_ts):
                        trades.append(OpenTrade("baseline", "bullish_pullback_confirmed_higher_low", "long", decision_dt, decision_price, stop, target, 1.0, regime))
                        trades.append(OpenTrade("experimental", "bullish_pullback_confirmed_higher_low", "long", decision_dt, decision_price, stop, target, 1.0, regime))
                        decision_checks["pullback_confirmed"] = "trade"
                        opened_rules.append("bullish_pullback_confirmed_higher_low")
                        if latest_low is not None:
                            last_full_long_setup_low_idx = latest_low.idx
                        baseline_signal = "long"
                        experimental_signal = "long"
                    else:
                        decision_checks["pullback_confirmed"] = "blocked"
                        learned_long_blocked_rules.append("bullish_pullback_confirmed_higher_low")

        # Add-on long: same-direction addition only after a fresh pullback anchor.
        eligible_existing_full_longs = [
            trade
            for trade in trades
            if trade.side == "long" and trade.size >= 1.0 and trade.entry_date.strftime("%Y-%m-%d") < day
        ]
        existing_full_long = bool(eligible_existing_full_longs)
        add_on_anchor_idx = latest_low.idx if latest_low is not None else None
        add_on_anchor_low = latest_low.price if latest_low is not None else None
        if last_full_long_setup_low_idx is not None:
            pullback_window = prior[prior.index > last_full_long_setup_low_idx]
            if not pullback_window.empty:
                add_on_anchor_idx = int(pullback_window["low"].idxmin())
                add_on_anchor_low = float(pullback_window.loc[add_on_anchor_idx, "low"])
        fresh_full_long_anchor = (
            add_on_anchor_idx is not None
            and add_on_anchor_low is not None
            and (last_full_long_setup_low_idx is None or add_on_anchor_idx > last_full_long_setup_low_idx)
        )
        surviving_existing_long = any(
            trade.side == "long" and trade.size >= 1.0 and add_on_anchor_low is not None and add_on_anchor_low > trade.stop + tol
            for trade in eligible_existing_full_longs
        )
        if (
            existing_full_long
            and not exited_today
            and not long_target_cooldown
            and (not bearish_structure_damage or surviving_existing_long)
            and regime.startswith("bullish")
            and fresh_full_long_anchor
        ):
            confirmation_high = latest_high.price if latest_high is not None else float(prev["high"])
            add_on_confirmed = bullish_reclaim
            if add_on_confirmed:
                natural_target = price_acceptance_target(df, decision_dt, "long", decision_price, atr14, tol)
                if natural_target is None:
                    natural_target = fallback_structural_long_target(levels_prior, decision_price, tol)
                stop = add_on_anchor_low - tol
                risk = decision_price - stop
                crv = ((natural_target - decision_price) / risk) if risk > 0 and natural_target is not None else 0
                if risk > 0 and crv < 1.0:
                    structural_target = fallback_structural_long_target(levels_prior, decision_price, tol)
                    structural_crv = ((structural_target - decision_price) / risk) if structural_target is not None else 0
                    if structural_crv > crv:
                        natural_target = structural_target
                        crv = structural_crv
                if risk > 0 and crv < 1.0:
                    min_target = decision_price + risk
                    distant_swing_targets = sorted(
                        level.price
                        for level in levels_prior
                        if level.kind == "high" and level.price >= min_target + tol
                    )
                    if distant_swing_targets:
                        natural_target = distant_swing_targets[0]
                        crv = (natural_target - decision_price) / risk
                if risk > 0 and natural_target is not None and crv >= 1.0:
                    tracks_open = {trade.track for trade in eligible_existing_full_longs}
                    if long_rule_allowed("bullish_add_on_higher_low", day_ts):
                        if "baseline" in tracks_open:
                            trades.append(OpenTrade("baseline", "bullish_add_on_higher_low", "long", decision_dt, decision_price, stop, natural_target, 1.0, regime))
                        if "experimental" in tracks_open:
                            trades.append(OpenTrade("experimental", "bullish_add_on_higher_low", "long", decision_dt, decision_price, stop, natural_target, 1.0, regime))
                        decision_checks["add_on"] = "trade"
                        opened_rules.append("bullish_add_on_higher_low")
                        last_full_long_setup_low_idx = add_on_anchor_idx
                        baseline_signal = "add_long" if "baseline" in tracks_open else baseline_signal
                        experimental_signal = "add_long" if "experimental" in tracks_open else experimental_signal
                    else:
                        decision_checks["add_on"] = "blocked"
                        learned_long_blocked_rules.append("bullish_add_on_higher_low")

        # Optional ramp-continuation tests. These deliberately use a 1R target
        # when no natural target exists, so a strong stair-step or weekly trend
        # is not skipped solely because price is making new highs.
        continuation_tracks_open = {
            trade.track
            for trade in trades
            if trade.side == "long" and trade.size >= 1.0
        }
        continuation_candidate = (
            not exited_today
            and not long_target_cooldown
            and not bearish_structure_damage
            and regime in {"bullish_tier2", "bullish_tier3"}
            and resistance_break
        )
        stair_structure = higher_high_count >= 3 and higher_low_count >= 2
        weekly_ramp = weekly_streak_weeks >= 8 and weekly_streak_green >= 7 and weekly_streak_red <= 1
        stair_candidate = bull_continuation_mode in {"stair", "both"} and continuation_candidate and stair_structure
        weekly_candidate = bull_continuation_mode in {"weekly", "both"} and continuation_candidate and weekly_ramp
        combo_candidate = bull_continuation_mode == "combo" and continuation_candidate and stair_structure and weekly_ramp
        if stair_candidate or weekly_candidate or combo_candidate:
            stop_candidates = []
            if latest_structural_low is not None:
                stop_candidates.append(latest_structural_low.price)
            if latest_low is not None:
                stop_candidates.append(latest_low.price)
            stop_candidates.append(prior_low)
            valid_stop_candidates = [value for value in stop_candidates if value < decision_price - tol]
            if not valid_stop_candidates:
                decision_checks["continuation"] = "skip"
                continue
            stop_base = max(valid_stop_candidates)
            stop = stop_base - tol
            risk = decision_price - stop
            if risk > 0:
                if combo_candidate:
                    rule_name = "bullish_stair_step_weekly_green_streak_combo"
                elif weekly_candidate and not stair_candidate:
                    rule_name = "bullish_weekly_green_streak_continuation"
                elif stair_candidate and not weekly_candidate:
                    rule_name = "bullish_stair_step_continuation"
                else:
                    rule_name = "bullish_stair_step_weekly_green_streak_continuation"
                target = decision_price + risk
                if long_rule_allowed(rule_name, day_ts):
                    for track in ("baseline", "experimental"):
                        if track not in continuation_tracks_open:
                            trades.append(OpenTrade(track, rule_name, "long", decision_dt, decision_price, stop, target, 1.0, regime))
                    decision_checks["continuation"] = "trade"
                    opened_rules.append(rule_name)
                    if latest_structural_low is not None:
                        last_full_long_setup_low_idx = latest_structural_low.idx
                    if "baseline" not in continuation_tracks_open:
                        baseline_signal = "continuation_long"
                    if "experimental" not in continuation_tracks_open:
                        experimental_signal = "continuation_long"
                else:
                    decision_checks["continuation"] = "blocked"
                    learned_long_blocked_rules.append(rule_name)

        # Bullish tier3 projected-high continuation: use an unconfirmed
        # prior-day new high in a strong stair-step trend. This catches cases
        # where waiting for a confirmed swing high would miss the ramp.
        baseline_track_open = any(t.track == "baseline" for t in trades)
        experimental_track_open = any(t.track == "experimental" for t in trades)
        projected_high_trigger = None
        if (
            not exited_today
            and not long_target_cooldown
            and regime == "bullish_tier3"
            and latest_structural_high is not None
            and latest_structural_low is not None
            and latest_structural_high.idx > latest_structural_low.idx
            and higher_high_count >= 6
            and higher_low_count >= 5
            and resistance_break
            and (
                "late_bull_compression_small_long" not in labels_for_small_tier3_long(prior, atr14)
                or higher_low_count >= 7
            )
        ):
            prior_day_high = float(prev["high"])
            high_step = prior_day_high - latest_structural_high.price
            if 0 < high_step <= 0.50 * atr14:
                projected_high_trigger = latest_structural_high.price + 2.0 * high_step
                gap_triggered = open_price > projected_high_trigger + tol
                decision_triggered = decision_price > projected_high_trigger + tol
                if gap_triggered or decision_triggered:
                    stop = latest_structural_low.price - tol
                    risk = decision_price - stop
                    if risk > 0:
                        target = decision_price + risk
                        labels = "bullish_tier3_projected_high_continuation"
                        if long_rule_allowed("bullish_tier3_projected_high_continuation_long", day_ts):
                            if not baseline_track_open and "baseline" not in exited_today_tracks:
                                trades.append(
                                    OpenTrade(
                                        "baseline",
                                        "bullish_tier3_projected_high_continuation_long",
                                        "long",
                                        decision_dt,
                                        decision_price,
                                        stop,
                                        target,
                                        1.0,
                                        regime,
                                        labels=labels,
                                    )
                                )
                                baseline_signal = "projected_high_long"
                            if not experimental_track_open and "experimental" not in exited_today_tracks:
                                trades.append(
                                    OpenTrade(
                                        "experimental",
                                        "bullish_tier3_projected_high_continuation_long",
                                        "long",
                                        decision_dt,
                                        decision_price,
                                        stop,
                                        target,
                                        1.0,
                                        regime,
                                        labels=labels,
                                    )
                                )
                                experimental_signal = "projected_high_long"
                            decision_checks["tier3"] = "trade"
                            opened_rules.append("bullish_tier3_projected_high_continuation_long")
                            last_full_long_setup_low_idx = latest_structural_low.idx
                            last_bullish_entry_low_idx = latest_structural_low.idx
                        else:
                            decision_checks["tier3"] = "blocked"
                            learned_long_blocked_rules.append("bullish_tier3_projected_high_continuation_long")

        # Bullish tier3 small participation with 1R target when no full-size trade fired.
        if not trades and not exited_today and not long_target_cooldown and not bearish_structure_damage and regime == "bullish_tier3" and resistance_break:
            breakout_level = latest_structural_high.price if latest_structural_high is not None else prior_high
            structural_breakout = decision_price > breakout_level + tol and open_price > breakout_level
            high_confirmed_after_pullback = (
                latest_structural_high is not None
                and latest_structural_low is not None
                and latest_structural_high.idx > latest_structural_low.idx
            )
            open_ext = (open_price - breakout_level) / atr14 if atr14 > 0 else 999
            decision_ext = (decision_price - breakout_level) / atr14 if atr14 > 0 else 999
            progressive_high_count = count_progressive_structural_highs(levels_prior, max(0, i - 70), 2)
            structural_higher_low = (
                latest_structural_low is not None
                and prev_structural_low is not None
                and latest_structural_low.price > prev_structural_low.price + tol
            )
            mature_higher_low_base = higher_low_count >= 2
            fresh_setup_low = (
                latest_structural_low is not None
                and (last_bullish_entry_low_idx is None or latest_structural_low.idx > last_bullish_entry_low_idx)
            )
            if (
                structural_breakout
                and high_confirmed_after_pullback
                and progressive_high_count >= 5
                and structural_higher_low
                and mature_higher_low_base
                and fresh_setup_low
                and open_ext <= 0.75
                and decision_ext <= 1.25
            ):
                stop_base = latest_low.price if latest_low is not None else (last_pullback_low if last_pullback_low is not None else prior_low)
                stop = stop_base - tol
                risk = decision_price - stop
                if risk > 0:
                    target = decision_price + risk
                    labels = labels_for_small_tier3_long(prior, atr14)
                    strong_tier3_participation = (
                        "late_bull_compression_small_long" not in labels
                        and higher_high_count >= 6
                        and higher_low_count >= 5
                    )
                    size = 1.0 if strong_tier3_participation else 0.25
                    rule_name = (
                        "bullish_tier3_full_trend_participation_breakout"
                        if strong_tier3_participation
                        else "bullish_tier3_small_participation_breakout"
                    )
                    if long_rule_allowed(rule_name, day_ts):
                        if strong_tier3_participation:
                            labels = append_label(labels, "bullish_tier3_full_size_trend_participation")
                        trades.append(OpenTrade("baseline", rule_name, "long", decision_dt, decision_price, stop, target, size, regime, labels))
                        trades.append(OpenTrade("experimental", rule_name, "long", decision_dt, decision_price, stop, target, size, regime, labels))
                        decision_checks["tier3"] = "trade"
                        opened_rules.append(rule_name)
                        last_bullish_entry_low_idx = latest_structural_low.idx
                        baseline_signal = "full_tier3_long" if strong_tier3_participation else "small_long"
                        experimental_signal = "full_tier3_long" if strong_tier3_participation else "small_long"
                    else:
                        decision_checks["tier3"] = "blocked"
                        learned_long_blocked_rules.append(rule_name)

        # Baseline short: failed countermove.
        mature_break = bars_since_support_break is not None and bars_since_support_break >= 3
        first_fast_retest = breakdown_retest and bars_since_support_break is not None and bars_since_support_break <= 2
        baseline_track_open = any(t.track == "baseline" for t in trades)
        failed_countermove_fail_zone = None
        failed_countermove_failed_under_zone = None
        failed_countermove_stop = None
        failed_countermove_target = None
        failed_countermove_risk = None
        failed_countermove_crv = None
        lower_high_break_context = bars_since_support_break is not None and bars_since_support_break >= 1
        prev_green = float(prev["close"]) > float(prev["open"])
        recent_60_high = float(prior["high"].tail(60).max())
        distance_from_recent_high_atr = (recent_60_high - decision_price) / max(atr14, 1e-9)
        recent_bull_structure_count = count_progressive_structural_highs(levels_prior, max(0, i - 90), 2)
        prev_range_atr = (float(prev["high"]) - float(prev["low"])) / max(atr14, 1e-9)
        true_breakdown_escalation = (
            i >= 2
            and prev_range_atr >= 1.55
            and float(prev["close"]) < float(prev["open"])
            and float(prev["low"]) < float(daily.iloc[i - 2]["low"]) - tol
        )
        upper_bull_range_false_break_filter = (
            distance_from_recent_high_atr <= 2.00
            and recent_bull_structure_count >= 6
            and weekly_streak_green <= 4
            and weekly_streak_red <= 2
            and not true_breakdown_escalation
        )
        volatility_reversal_gap_block = (
            bearish_countermove_subregime == "bearish_countermove_volatility_reversal"
            and distance_from_recent_high_atr <= 4.00
            and weekly_streak_green <= 2
            and recent_bull_structure_count >= 6
        )
        if (
            not baseline_track_open
            and "baseline" not in exited_today_tracks
            and bearish_short_armed
            and not bullish_repair_filter
            and not prev_green
            and lower_high_break_context
            and not breakdown_retest
            and regime in {"bearish_confirmed", "bearish_countermove"}
            and i >= 2
            and (i - 1) not in used_lower_high_failure_idxs
        ):
            before_prev_row = daily.iloc[i - 2]
            prev_range = float(prev["high"]) - float(prev["low"])
            prior_peak = float(prior.iloc[:-1]["high"].tail(60).max()) if len(prior) > 1 else float(prev["high"])
            prior_bull_structure_count = count_progressive_structural_highs(levels_prior, max(0, i - 90), 2)
            recent_breakdown_retest_setup = last_breakdown_retest_idx is not None and i - last_breakdown_retest_idx <= 1
            prev_high_is_window_peak = (
                float(prev["high"]) >= float(prior.tail(3)["high"].max()) - 0.25 * tol
                or recent_breakdown_retest_setup
            )
            prev_made_bounce_high = float(prev["high"]) > float(before_prev_row["high"]) + tol
            prev_meaningful_bounce_range = prev_range >= 0.60 * atr14
            prev_held_off_low = prev_range > 0 and float(prev["close"]) > float(prev["low"]) + 0.25 * prev_range
            lower_high_vs_structure = float(prev["high"]) < prior_peak - tol
            failed_below_prev_high = decision_price < float(prev["high"]) - max(tol, 0.15 * atr14)
            if (
                prior_bull_structure_count >= 4
                and prev_high_is_window_peak
                and prev_made_bounce_high
                and prev_meaningful_bounce_range
                and prev_held_off_low
                and lower_high_vs_structure
                and failed_below_prev_high
            ):
                stop = float(prev["high"]) + tol
                risk = stop - decision_price
                if risk > 0:
                    min_reward_target = decision_price - 0.75 * risk
                    support_targets = sorted(
                        (
                            level.price
                            for level in levels_prior
                            if level.kind == "low" and level.price <= min_reward_target
                        ),
                        reverse=True,
                    )
                    target = support_targets[0] if support_targets else decision_price - risk
                    trades.append(OpenTrade("baseline", "bearish_lower_high_failure_short", "short", decision_dt, decision_price, stop, target, 1.0, regime))
                    trades.append(OpenTrade("experimental", "bearish_lower_high_failure_short", "short", decision_dt, decision_price, stop, target, 1.0, regime))
                    decision_checks["lower_high_failure"] = "trade"
                    opened_rules.append("bearish_lower_high_failure_short")
                    used_lower_high_failure_idxs.add(i - 1)
                    baseline_signal = "short"
                    experimental_signal = "short"
                    baseline_track_open = True

        # Baseline short: bearish gap/breakdown continuation after broken lows.
        # This covers trend-following breakdown days that are not a clean
        # lower-high/countermove failure.
        baseline_track_open = any(t.track == "baseline" for t in trades)
        slow_drawdown_gap_filter = (
            distance_from_recent_high_atr <= 3.50
            and atr14 <= 80.0
            and bearish_countermove_subregime in {
                "bearish_countermove_weak_retest",
                "bearish_countermove_repair_rebound",
            }
        )
        if (
            not baseline_track_open
            and "baseline" not in exited_today_tracks
            and bearish_short_armed
            and not bullish_repair_filter
            and regime == "bearish_confirmed"
            and support_break
            and not breakdown_retest
            and not post_break_midrange
            and not slow_drawdown_gap_filter
            and i >= 2
        ):
            before_prev_row = daily.iloc[i - 2]
            prev_bearish = float(prev["close"]) < float(prev["open"])
            prev_broke_lower = float(prev["low"]) < float(before_prev_row["low"]) - tol
            decision_below_recent_low = decision_price < prior_low - tol and open_price < prior_low + tol
            lower_lows_visible = lower_low or structural_lower_low or prev_broke_lower
            prior_bull_structure_count = count_progressive_structural_highs(levels_prior, max(0, i - 90), 2)
            if (
                prior_bull_structure_count >= 6
                and prev_bearish
                and prev_broke_lower
                and decision_below_recent_low
                and lower_lows_visible
            ):
                stop = float(prev["high"]) + tol
                risk = stop - decision_price
                if risk > 0:
                    min_reward_target = decision_price - 0.90 * risk
                    support_targets = sorted(
                        (
                            level.price
                            for level in levels_prior
                            if level.kind == "low" and level.price <= min_reward_target
                        ),
                        reverse=True,
                    )
                    target = support_targets[0] if support_targets else decision_price - risk
                    reward = decision_price - target
                    if reward / risk >= 0.90:
                        trades.append(OpenTrade("baseline", "bearish_gap_breakdown_continuation_short", "short", decision_dt, decision_price, stop, target, 1.0, regime))
                        trades.append(OpenTrade("experimental", "bearish_gap_breakdown_continuation_short", "short", decision_dt, decision_price, stop, target, 1.0, regime))
                        decision_checks["gap_breakdown"] = "trade"
                        opened_rules.append("bearish_gap_breakdown_continuation_short")
                        baseline_signal = "gap_breakdown_short"
                        experimental_signal = "gap_breakdown_short"
                        baseline_track_open = True

        if (
            not baseline_track_open
            and not exited_today
            and bearish_short_armed
            and not bullish_repair_filter
            and bearish_countermove_subregime != "bearish_countermove_volatility_reversal"
            and mature_break
            and not first_fast_retest
            and last_countermove_high is not None
            and regime in {"bearish_confirmed", "bearish_countermove"}
        ):
            fail_zone = last_countermove_high
            failed_countermove_fail_zone = fail_zone
            failed_under_zone = decision_price < fail_zone - tol and float(prev["close"]) < fail_zone
            failed_countermove_failed_under_zone = failed_under_zone
            if failed_under_zone:
                stop = fail_zone + tol
                natural_target_level = nearest_support_below(levels_prior, decision_price, tol)
                natural_target = natural_target_level.price if natural_target_level is not None else None
                risk = stop - decision_price
                crv = ((decision_price - natural_target) / risk) if risk > 0 and natural_target is not None else 0
                failed_countermove_stop = stop
                failed_countermove_target = natural_target
                failed_countermove_risk = risk
                failed_countermove_crv = crv
                if risk > 0 and natural_target is not None and crv >= 1.0:
                    target = natural_target
                    trades.append(OpenTrade("baseline", "bearish_failed_countermove_short_baseline", "short", decision_dt, decision_price, stop, target, 1.0, regime))
                    trades.append(OpenTrade("experimental", "bearish_failed_countermove_short_baseline", "short", decision_dt, decision_price, stop, target, 1.0, regime))
                    decision_checks["failed_countermove"] = "trade"
                    opened_rules.append("bearish_failed_countermove_short_baseline")
                    baseline_signal = "short"
                    experimental_signal = "short"

                    experimental_signal = "short"
                else:
                    decision_checks["failed_countermove"] = "blocked"

        decision_explanation = explain_decision(
            regime,
            decision_checks,
            opened_rules,
            learned_long_blocked_rules,
            len(trades),
            exited_today,
            long_target_cooldown,
            bearish_structure_damage,
            resistance_break,
            latest_structural_high,
            latest_structural_low,
            prev_structural_low,
            levels_prior,
            i,
            open_price,
            decision_price,
            atr14,
            tol,
            higher_low_count,
            last_bullish_entry_low_idx,
            prior_high,
            projected_high_trigger,
        )
        volume_price_phase, volume_price_detail = classify_volume_price_phase(prior, atr14)
        decision_explanation = add_phase_context(
            decision_explanation,
            regime,
            volume_price_phase,
            volume_price_detail,
        )

        rows.append(
            {
                "day": day,
                "decision_time": decision_time,
                "decision_price": decision_price,
                "open": open_price,
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "atr14": atr14,
                "atr_tolerance": tol,
                "prior_high": prior_high,
                "prior_low": prior_low,
                "prior_high_day": resistance_level.day if resistance_level is not None else None,
                "prior_low_day": support_level.day if support_level is not None else None,
                "prior_high_scale": resistance_level.scale if resistance_level is not None else None,
                "prior_low_scale": support_level.scale if support_level is not None else None,
                "regime": regime,
                "higher_high_count": higher_high_count,
                "higher_low_count": higher_low_count,
                "weekly_green_streak_weeks": weekly_streak_weeks,
                "weekly_green_streak_green": weekly_streak_green,
                "weekly_green_streak_red": weekly_streak_red,
                **big_picture,
                "bars_since_last_bearish_regime": bars_since_last_bearish_regime,
                "post_bear_drawdown_depth_atr": post_bear_drawdown_depth_atr,
                "post_bear_new_high_extension_atr": post_bear_new_high_extension_atr,
                "post_bear_open_gap_extension_atr": post_bear_open_gap_extension_atr,
                "broken_support": broken_support,
                "bars_since_support_break": bars_since_support_break,
                "last_countermove_high": last_countermove_high,
                "last_countermove_high_day": (
                    daily.iloc[int(last_countermove_high_idx)]["day"]
                    if last_countermove_high_idx is not None and 0 <= int(last_countermove_high_idx) < len(daily)
                    else None
                ),
                "mature_break": mature_break,
                "first_fast_retest": first_fast_retest,
                "failed_countermove_fail_zone": failed_countermove_fail_zone,
                "failed_countermove_failed_under_zone": failed_countermove_failed_under_zone,
                "failed_countermove_stop": failed_countermove_stop,
                "failed_countermove_target": failed_countermove_target,
                "failed_countermove_risk": failed_countermove_risk,
                "failed_countermove_crv": failed_countermove_crv,
                "bearish_short_armed": bearish_short_armed,
                "bullish_repair_filter": bullish_repair_filter,
                "bearish_countermove_subregime": bearish_countermove_subregime,
                "bullish_crash_repair_reversal": crash_repair_setup,
                "bullish_crash_repair_stop_base": crash_repair_stop_base,
                "bullish_crash_repair_breakout_level": crash_repair_breakout_level,
                "bullish_projected_high_trigger": projected_high_trigger,
                "slow_drawdown_gap_filter": slow_drawdown_gap_filter,
                "upper_bull_range_false_break_filter": upper_bull_range_false_break_filter,
                "volatility_reversal_gap_block": volatility_reversal_gap_block,
                "true_breakdown_escalation": true_breakdown_escalation,
                "distance_from_recent_high_atr": distance_from_recent_high_atr,
                "structure_epoch_day": daily.iloc[int(structure_epoch_idx)]["day"] if 0 <= int(structure_epoch_idx) < len(daily) else None,
                "structural_reset_mode": structural_reset_mode,
                "structural_reset_applied": structural_reset_applied,
                "structural_reset_reason": structural_reset["reason"] if structural_reset is not None else "",
                "structural_reset_peak_day": structural_reset["peak_day"] if structural_reset is not None else "",
                "structural_reset_drawdown_pct": structural_reset["drawdown_pct"] if structural_reset is not None else None,
                "structural_reset_days_since_peak": structural_reset["days_since_peak"] if structural_reset is not None else None,
                "support_break": support_break,
                "resistance_break": resistance_break,
                "breakdown_retest_from_below": breakdown_retest,
                "bullish_reclaim": bullish_reclaim,
                **post_break_range,
                **value_profile,
                **daily_structure,
                "baseline_signal": baseline_signal,
                "experimental_signal": experimental_signal,
                "long_profile": current_long_profile,
                "learned_long_blocked_rules": "|".join(sorted(set(learned_long_blocked_rules))),
                "open_trade_count": len(trades),
                **decision_explanation,
            }
        )
        opened_trades_today = [
            trade
            for trade in trades
            if not any(trade is prior_trade for prior_trade in open_trades_at_day_start)
        ]
        emit_structured_decision(
            decision_dt=pd.Timestamp(decision_dt),
            checks=decision_checks,
            explanation=decision_explanation,
            context=dict(rows[-1]),
            opened_trades=opened_trades_today,
            closed_tracks=exited_today_tracks,
        )

    # Process remaining known exits after the final decision timestamp, then
    # close leftovers at final close for accounting.
    if trades and last_exit_check_dt is not None:
        final_dt = df["date"].max()
        still_open = []
        for trade in trades:
            after_dt = max(trade.entry_date, last_exit_check_dt)
            exit_row = simulate_trade_exit(df, trade, after_dt, final_dt)
            if exit_row is None:
                still_open.append(trade)
            else:
                completed.append(exit_row)
        trades = still_open

    if trades:
        final = daily.iloc[-1]
        for trade in trades:
            exit_price = float(final["close"])
            points = exit_price - trade.entry_price if trade.side == "long" else trade.entry_price - exit_price
            completed.append(
                {
                    "track": trade.track,
                    "rule": trade.rule,
                    "side": trade.side,
                    "entry_date": trade.entry_date,
                    "exit_date": final["date"],
                    "entry": trade.entry_price,
                    "stop": trade.stop,
                    "target": trade.target,
                    "exit": exit_price,
                    "exit_reason": "end_of_test",
                    "points": points,
                    "size": trade.size,
                    "weighted_points": points * trade.size,
                    "regime_at_entry": trade.regime_at_entry,
                    "labels": trade.labels,
                    "entry_labels": trade.entry_labels or entry_labels_for_trade(trade),
                }
            )

    if decision_log_file is not None:
        decision_log_file.parent.mkdir(parents=True, exist_ok=True)
        decision_log_file.write_text("\n".join(decision_log_lines) + ("\n" if decision_log_lines else ""), encoding="utf-8")
    runtime_log_file.parent.mkdir(parents=True, exist_ok=True)
    runtime_log_file.write_text("\n".join(runtime_log_lines) + ("\n" if runtime_log_lines else ""), encoding="utf-8")
    if decision_records_jsonl_path is not None:
        decision_model.write_jsonl(decision_records_jsonl_path, structured_decisions)
    if decision_records_out is not None:
        decision_records_out.extend(structured_decisions)

    return pd.DataFrame(rows), pd.DataFrame(completed), daily


def summarize(trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame()
    return (
        trades.groupby("track", as_index=False)
        .agg(
            trades=("track", "size"),
            wins=("points", lambda s: int((s > 0).sum())),
            losses=("points", lambda s: int((s <= 0).sum())),
            points=("points", "sum"),
            weighted_points=("weighted_points", "sum"),
        )
        .sort_values("track")
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True, help="Exclusive end date")
    parser.add_argument("--lookback", type=int, default=10)
    parser.add_argument(
        "--bull-continuation-mode",
        choices=["none", "stair", "weekly", "both", "combo"],
        default="combo",
        help="Bullish ramp-continuation rules. Default combo is part of the baseline.",
    )
    parser.add_argument(
        "--learn-long-mode",
        choices=["off", "symbol_gate", "family_gate", "regime_gate", "hybrid_gate", "size_shrink"],
        default="off",
        help="Adaptive long gating based on prior completed trades.",
    )
    parser.add_argument("--learn-long-window", type=int, default=5)
    parser.add_argument("--learn-long-min-trades", type=int, default=3)
    parser.add_argument("--learn-long-min-win-rate", type=float, default=0.45)
    parser.add_argument("--learn-long-cooldown-days", type=int, default=15)
    parser.add_argument(
        "--structural-reset-mode",
        choices=["off", "log", "apply"],
        default="off",
        help="Event-based reset for stale regime anchors after major structure breaks.",
    )
    parser.add_argument(
        "--max-history-days",
        type=int,
        default=None,
        help="Limit rule history/state to this many calendar days, matching live-history semantics.",
    )
    parser.add_argument("--decision-log", default="")
    parser.add_argument("--runtime-log", default="")
    args = parser.parse_args()

    signals, trades, _ = run_backtest(
        args.symbol,
        args.start,
        args.end,
        args.lookback,
        args.bull_continuation_mode,
        args.learn_long_mode,
        args.learn_long_window,
        args.learn_long_min_trades,
        args.learn_long_min_win_rate,
        args.learn_long_cooldown_days,
        decision_log_path=args.decision_log or None,
        runtime_log_path=args.runtime_log or None,
        structural_reset_mode=args.structural_reset_mode,
        max_history_days=args.max_history_days,
    )
    stem = f"{args.symbol.upper()}_{args.start}_{args.end}_manual_rules".replace("-", "")
    if args.bull_continuation_mode != "combo":
        stem = f"{stem}_{args.bull_continuation_mode}"
    if args.learn_long_mode != "off":
        stem = f"{stem}_learn_long_{args.learn_long_mode}"
    signals_path = OUT_DIR / f"{stem}_signals.csv"
    trades_path = OUT_DIR / f"{stem}_trades.csv"
    summary_path = OUT_DIR / f"{stem}_summary.csv"
    signals.to_csv(signals_path, index=False)
    trades.to_csv(trades_path, index=False)
    summarize(trades).to_csv(summary_path, index=False)

    print(f"signals={signals_path}")
    print(f"trades={trades_path}")
    print(f"summary={summary_path}")
    if not trades.empty:
        print(summarize(trades).to_string(index=False))


if __name__ == "__main__":
    main()
