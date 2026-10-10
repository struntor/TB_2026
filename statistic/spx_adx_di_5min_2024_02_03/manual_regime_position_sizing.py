from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


POLICY_VERSION = "graded-structure-volume-sizing-v1"
LEGACY_SHADOW_VARIANT_ID = "manual-regime-baseline@1.0.0"
VULNERABLE_RULES = frozenset(
    {
        "bearish_gap_breakdown_continuation_short",
        "bullish_crash_repair_reversal_long",
        "bullish_pullback_reclaim_long",
        "bullish_tier3_full_trend_participation_breakout",
        "bullish_tier3_projected_high_continuation_long",
        "bullish_tier3_small_participation_breakout",
    }
)
CORRELATED_MARKET_CLUSTER = frozenset(
    {"SPX", "SPX_FUT", "SPY", "IVV", "VOO", "VTI", "OEF"}
)


@dataclass(frozen=True, slots=True)
class PositionSizingContext:
    trading_day: str
    structure_available: bool
    structure_session: str | None
    structure_score: int
    structure_phase: str
    structure_error: str | None
    volume_available: bool
    volume_session: str | None
    spy_value_area_low: float | None
    spy_highest_hvn: float | None
    spy_close: float | None
    spy_atr14: float | None
    prior_lower_rejection: bool
    prior_upper_hvn_distance_atr: float | None
    vp_overhead_magnet_short_risk: bool
    volume_error: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def confirmed_pivots(
    daily: pd.DataFrame,
    *,
    left: int = 3,
    right: int = 3,
) -> pd.DataFrame:
    """Return pivots on the session where they first become knowable."""
    high = daily["high"].to_numpy(float)
    low = daily["low"].to_numpy(float)
    rows: list[dict[str, object]] = []
    for pivot_index in range(left, len(daily) - right):
        high_window = high[pivot_index - left : pivot_index + right + 1]
        low_window = low[pivot_index - left : pivot_index + right + 1]
        confirmation_index = pivot_index + right
        if high[pivot_index] == np.max(high_window) and np.sum(
            high_window == high[pivot_index]
        ) == 1:
            rows.append(
                {
                    "kind": "high",
                    "pivot_index": pivot_index,
                    "pivot_day": daily.iloc[pivot_index]["day"],
                    "confirmation_index": confirmation_index,
                    "price": high[pivot_index],
                }
            )
        if low[pivot_index] == np.min(low_window) and np.sum(
            low_window == low[pivot_index]
        ) == 1:
            rows.append(
                {
                    "kind": "low",
                    "pivot_index": pivot_index,
                    "pivot_day": daily.iloc[pivot_index]["day"],
                    "confirmation_index": confirmation_index,
                    "price": low[pivot_index],
                }
            )
    if not rows:
        return pd.DataFrame(
            columns=["kind", "pivot_index", "pivot_day", "confirmation_index", "price"]
        )
    return pd.DataFrame(rows).sort_values(["confirmation_index", "kind"])


def structure_lifecycle(daily: pd.DataFrame) -> pd.DataFrame:
    """Causal 0/40/70/100 trend-break lifecycle used by the sizing policy."""
    result = daily.copy().reset_index(drop=True)
    result["structure_score"] = 0
    result["structure_phase"] = "directional_or_unknown"
    pivots = confirmed_pivots(result)
    highs: list[dict[str, object]] = []
    lows: list[dict[str, object]] = []
    trend = "unknown"
    active: dict[str, object] | None = None
    grouped = {
        int(index): frame for index, frame in pivots.groupby("confirmation_index")
    }

    for index in range(len(result)):
        atr = (
            float(result.at[index, "atr14"])
            if pd.notna(result.at[index, "atr14"])
            else math.nan
        )
        events = (
            grouped[index].sort_values("pivot_index").to_dict("records")
            if index in grouped
            else []
        )
        for pivot in events:
            history = highs if pivot["kind"] == "high" else lows
            previous = history[-1] if history else None
            history.append(pivot)
            if not math.isfinite(atr) or previous is None:
                continue
            price = float(pivot["price"])
            previous_price = float(previous["price"])

            if active is None:
                if (
                    pivot["kind"] == "low"
                    and trend == "up"
                    and price < previous_price - 0.20 * atr
                ):
                    active = {
                        "origin": "uptrend_break",
                        "break_day": pivot["pivot_day"],
                        "break_index": int(pivot["pivot_index"]),
                        "break_price": price,
                        "reference_high": float(highs[-1]["price"]),
                        "inward_high": math.nan,
                        "inward_low": math.nan,
                        "confirmed": False,
                    }
                elif (
                    pivot["kind"] == "high"
                    and trend == "down"
                    and price > previous_price + 0.20 * atr
                ):
                    active = {
                        "origin": "downtrend_break",
                        "break_day": pivot["pivot_day"],
                        "break_index": int(pivot["pivot_index"]),
                        "break_price": price,
                        "reference_low": float(lows[-1]["price"]),
                        "inward_high": math.nan,
                        "inward_low": math.nan,
                        "confirmed": False,
                    }
            elif active["origin"] == "uptrend_break":
                if pivot["kind"] == "low":
                    if price < float(active["break_price"]) - 0.10 * atr:
                        active["break_price"] = price
                        active["break_day"] = pivot["pivot_day"]
                        active["break_index"] = int(pivot["pivot_index"])
                        active["inward_low"] = math.nan
                    elif price > float(active["break_price"]) + 0.10 * atr:
                        active["inward_low"] = price
                elif price < float(active["reference_high"]) - 0.10 * atr:
                    active["inward_high"] = price
            else:
                if pivot["kind"] == "high":
                    if price > float(active["break_price"]) + 0.10 * atr:
                        active["break_price"] = price
                        active["break_day"] = pivot["pivot_day"]
                        active["break_index"] = int(pivot["pivot_index"])
                        active["inward_high"] = math.nan
                    elif price < float(active["break_price"]) - 0.10 * atr:
                        active["inward_high"] = price
                elif price > float(active["reference_low"]) + 0.10 * atr:
                    active["inward_low"] = price

            if (
                active is not None
                and math.isfinite(float(active["inward_high"]))
                and math.isfinite(float(active["inward_low"]))
            ):
                active["confirmed"] = True

        if len(highs) >= 2 and len(lows) >= 2 and math.isfinite(atr) and active is None:
            high_change = float(highs[-1]["price"]) - float(highs[-2]["price"])
            low_change = float(lows[-1]["price"]) - float(lows[-2]["price"])
            if high_change > 0.10 * atr and low_change > 0.10 * atr:
                trend = "up"
            elif high_change < -0.10 * atr and low_change < -0.10 * atr:
                trend = "down"

        if active is None or not math.isfinite(atr):
            continue
        lower_bound = (
            float(active["break_price"])
            if active["origin"] == "uptrend_break"
            else float(active["inward_low"])
            if math.isfinite(float(active["inward_low"]))
            else float(active["reference_low"])
        )
        upper_bound = (
            float(active["inward_high"])
            if math.isfinite(float(active["inward_high"]))
            else float(active["reference_high"])
            if active["origin"] == "uptrend_break"
            else float(active["break_price"])
        )
        close = float(result.at[index, "close"])
        confirmed = bool(active["confirmed"])
        breakout = confirmed and (
            close > upper_bound + 0.50 * atr or close < lower_bound - 0.50 * atr
        )
        age = index - int(active["break_index"])
        if breakout or age > 60:
            trend = (
                "up"
                if close > upper_bound
                else "down"
                if close < lower_bound
                else "unknown"
            )
            active = None
            continue
        has_inward_high = math.isfinite(float(active["inward_high"]))
        has_inward_low = math.isfinite(float(active["inward_low"]))
        if confirmed:
            score, phase = 100, "sideways_confirmed"
        elif has_inward_high or has_inward_low:
            score, phase = 70, "range_forming"
        else:
            score, phase = 40, "trend_break_warning"
        result.at[index, "structure_score"] = score
        result.at[index, "structure_phase"] = phase
    return result


def daily_prices(path: Path, trading_day: pd.Timestamp) -> pd.DataFrame:
    bars = pd.read_csv(path, parse_dates=["date"])
    if bars.empty:
        raise ValueError(f"no bars in {path}")
    required = {"date", "open", "high", "low", "close"}
    missing = required.difference(bars.columns)
    if missing:
        raise ValueError(f"{path} lacks columns {sorted(missing)}")
    stamps = pd.to_datetime(bars["date"], errors="coerce")
    if getattr(stamps.dt, "tz", None) is not None:
        stamps = stamps.dt.tz_localize(None)
    bars["day"] = stamps.dt.normalize()
    cutoff = pd.Timestamp(trading_day).tz_localize(None).normalize()
    bars = bars.loc[bars["day"].lt(cutoff)].copy()
    if bars.empty:
        raise ValueError(f"no completed sessions before {cutoff.date()} in {path}")
    daily = (
        bars.groupby("day", as_index=False)
        .agg(
            open=("open", "first"),
            high=("high", "max"),
            low=("low", "min"),
            close=("close", "last"),
        )
        .sort_values("day")
        .reset_index(drop=True)
    )
    previous_close = daily["close"].shift()
    daily["tr"] = pd.concat(
        [
            daily["high"] - daily["low"],
            (daily["high"] - previous_close).abs(),
            (daily["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    daily["atr14"] = daily["tr"].ewm(
        alpha=1 / 14,
        adjust=False,
        min_periods=14,
    ).mean()
    return daily


def volume_profile(prior_bars: pd.DataFrame, step: float = 0.5) -> dict[str, float]:
    if prior_bars.empty or float(prior_bars["volume"].sum()) <= 0:
        raise ValueError("volume profile needs positive prior volume")
    average = (
        prior_bars["average"]
        if "average" in prior_bars.columns
        else pd.Series(np.nan, index=prior_bars.index)
    )
    price = average.where(
        pd.to_numeric(average, errors="coerce").gt(0),
        (prior_bars["high"] + prior_bars["low"] + prior_bars["close"]) / 3,
    )
    bins = (price / step).round() * step
    profile = (
        prior_bars.assign(price_bin=bins)
        .groupby("price_bin")["volume"]
        .sum()
        .sort_index()
    )
    poc = float(profile.idxmax())
    target = 0.70 * float(profile.sum())
    prices = profile.index.astype(float).tolist()
    center = prices.index(poc)
    chosen = {poc}
    accumulated = float(profile.loc[poc])
    lower_index = center - 1
    upper_index = center + 1
    while accumulated < target and (
        lower_index >= 0 or upper_index < len(prices)
    ):
        lower_volume = (
            float(profile.iloc[lower_index]) if lower_index >= 0 else -1.0
        )
        upper_volume = (
            float(profile.iloc[upper_index])
            if upper_index < len(prices)
            else -1.0
        )
        if upper_volume >= lower_volume:
            chosen.add(float(prices[upper_index]))
            accumulated += upper_volume
            upper_index += 1
        else:
            chosen.add(float(prices[lower_index]))
            accumulated += lower_volume
            lower_index -= 1
    threshold = float(profile.quantile(0.90))
    high_volume_prices = profile.loc[profile.ge(threshold)].index.astype(float)
    return {
        "poc": poc,
        "val": min(chosen),
        "vah": max(chosen),
        "highest_hvn": float(high_volume_prices.max()),
    }


def _structure_context(
    trading_day: pd.Timestamp,
    spx_path: Path,
) -> dict[str, Any]:
    daily = structure_lifecycle(daily_prices(spx_path, trading_day))
    latest = daily.iloc[-1]
    return {
        "structure_available": True,
        "structure_session": pd.Timestamp(latest["day"]).date().isoformat(),
        "structure_score": int(latest["structure_score"]),
        "structure_phase": str(latest["structure_phase"]),
        "structure_error": None,
    }


def _volume_context(
    trading_day: pd.Timestamp,
    spy_path: Path,
) -> dict[str, Any]:
    bars = pd.read_csv(spy_path, parse_dates=["date"])
    required = {"date", "high", "low", "close", "volume"}
    missing = required.difference(bars.columns)
    if missing:
        raise ValueError(f"{spy_path} lacks columns {sorted(missing)}")
    stamps = pd.to_datetime(bars["date"], errors="coerce")
    if getattr(stamps.dt, "tz", None) is not None:
        stamps = stamps.dt.tz_localize(None)
    bars["session"] = stamps.dt.normalize()
    cutoff = pd.Timestamp(trading_day).tz_localize(None).normalize()
    bars = bars.loc[bars["session"].lt(cutoff)].copy()
    sessions = sorted(bars["session"].dropna().unique())
    if len(sessions) < 21:
        raise ValueError("SPY volume context needs 21 completed sessions")
    current_day = pd.Timestamp(sessions[-1])
    prior_days = sessions[-21:-1]
    prior = bars.loc[bars["session"].isin(prior_days)].copy()
    current = bars.loc[bars["session"].eq(current_day)].copy()
    profile = volume_profile(prior, 0.5)
    daily = daily_prices(spy_path, trading_day)
    atr = float(daily.iloc[-1]["atr14"])
    if not math.isfinite(atr) or atr <= 0:
        raise ValueError("SPY ATR14 is unavailable")
    current_low = float(current["low"].min())
    current_close = float(current["close"].iloc[-1])
    lower_rejection = current_low < profile["val"] and current_close >= profile["val"]
    upper_distance = (profile["highest_hvn"] - current_close) / atr
    return {
        "volume_available": True,
        "volume_session": current_day.date().isoformat(),
        "spy_value_area_low": float(profile["val"]),
        "spy_highest_hvn": float(profile["highest_hvn"]),
        "spy_close": current_close,
        "spy_atr14": atr,
        "prior_lower_rejection": bool(lower_rejection),
        "prior_upper_hvn_distance_atr": float(upper_distance),
        "vp_overhead_magnet_short_risk": bool(
            lower_rejection and upper_distance >= 1.0
        ),
        "volume_error": None,
    }


def build_position_sizing_context(
    trading_day: str | pd.Timestamp,
    *,
    spx_path: Path = Path(r"D:\Trading\Rohdaten\Rohdaten_SPX.csv"),
    spy_path: Path = Path(r"D:\Trading\Rohdaten\Rohdaten_SPY.csv"),
) -> PositionSizingContext:
    day = pd.Timestamp(trading_day).tz_localize(None).normalize()
    try:
        structure = _structure_context(day, spx_path)
    except Exception as exc:
        structure = {
            "structure_available": False,
            "structure_session": None,
            "structure_score": 0,
            "structure_phase": "unavailable_conservative_half",
            "structure_error": f"{type(exc).__name__}: {exc}",
        }
    try:
        volume = _volume_context(day, spy_path)
    except Exception as exc:
        volume = {
            "volume_available": False,
            "volume_session": None,
            "spy_value_area_low": None,
            "spy_highest_hvn": None,
            "spy_close": None,
            "spy_atr14": None,
            "prior_lower_rejection": False,
            "prior_upper_hvn_distance_atr": None,
            "vp_overhead_magnet_short_risk": False,
            "volume_error": f"{type(exc).__name__}: {exc}",
        }
    return PositionSizingContext(
        trading_day=day.date().isoformat(),
        **structure,
        **volume,
    )


def risk_multiplier(
    *,
    symbol: str,
    rule: str,
    side: str,
    context: PositionSizingContext,
) -> tuple[float, tuple[str, ...]]:
    multiplier = 1.0
    reasons: list[str] = []
    normalized_rule = str(rule)
    if normalized_rule in VULNERABLE_RULES:
        if not context.structure_available:
            multiplier = 0.50
            reasons.append("structure_context_unavailable_conservative_half")
        elif context.structure_score >= 100:
            multiplier = 0.50
            reasons.append("sideways_confirmed_score_100")
        elif context.structure_score == 70:
            multiplier = 0.75
            reasons.append("range_forming_score_70")

    cluster_short = (
        str(symbol).upper() in CORRELATED_MARKET_CLUSTER
        and str(side).lower() == "short"
    )
    if cluster_short:
        if not context.volume_available:
            multiplier = min(multiplier, 0.50)
            reasons.append("volume_context_unavailable_conservative_half")
        elif context.vp_overhead_magnet_short_risk:
            multiplier = min(multiplier, 0.50)
            reasons.append("overhead_volume_magnet_after_lower_rejection")
    return multiplier, tuple(reasons or ("full_size_no_derisk_condition",))


def apply_position_sizing(
    trades: pd.DataFrame,
    context: PositionSizingContext,
) -> pd.DataFrame:
    result = trades.copy()
    if result.empty:
        return result
    legacy_sizes: list[float] = []
    multipliers: list[float] = []
    reasons: list[str] = []
    for _, row in result.iterrows():
        legacy_size = float(row.get("size", 1.0) or 1.0)
        multiplier, row_reasons = risk_multiplier(
            symbol=str(row["symbol"]),
            rule=str(row["rule"]),
            side=str(row["side"]),
            context=context,
        )
        legacy_sizes.append(legacy_size)
        multipliers.append(multiplier)
        reasons.append("|".join(row_reasons))
    result["legacy_shadow_variant_id"] = LEGACY_SHADOW_VARIANT_ID
    result["legacy_shadow_size"] = legacy_sizes
    result["regime_volume_risk_multiplier"] = multipliers
    result["size"] = result["legacy_shadow_size"] * result[
        "regime_volume_risk_multiplier"
    ]
    result["position_sizing_policy_version"] = POLICY_VERSION
    result["position_sizing_reasons"] = reasons
    result["prior_structure_score"] = context.structure_score
    result["prior_structure_phase"] = context.structure_phase
    result["vp_overhead_magnet_short_risk"] = (
        context.vp_overhead_magnet_short_risk
    )
    result["position_sizing_context_available"] = (
        context.structure_available and context.volume_available
    )
    return result
