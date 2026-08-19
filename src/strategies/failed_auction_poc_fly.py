from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Literal

import numpy as np
import pandas as pd


STRATEGY_ID = "spy-failed-auction-poc-hard12-first-touch-options"
STRATEGY_VERSION = "1.0.0"
STRATEGY_VARIANT_ID = "spy-failed-auction-hard12-rvol100-120-1dte-u10l10@1.0.0"
ENGINE_VERSION = "failed-auction-live-engine@1.0.0"
RULE_ID = "entry.failed_auction_hard12_rvol_100_120"
RULE_VERSION = "1.0.0"
EXIT_RULE_ID = "exit.poc_touch_work_mid_30m"
EXIT_RULE_VERSION = "1.0.0"
SIGNAL_KEY_VERSION = "failed-auction-poc-fly-v1"


@dataclass(frozen=True, slots=True)
class FailedAuctionConfig:
    profile_lookback_sessions: int = 5
    rvol_lookback_sessions: int = 20
    price_bin_points: float = 0.25
    value_area_fraction: float = 0.70
    consecutive_inside_closes: int = 12
    rvol_minimum: float = 1.0
    rvol_maximum_exclusive: float = 1.2
    bar_minutes: int = 5
    upper_width_points: float = 10.0
    lower_width_points: float = 10.0
    strike_step: float = 1.0
    calendar_dte: int = 1
    quantity: int = 1


@dataclass(frozen=True, slots=True)
class FailedAuctionEvaluation:
    day: str
    status: Literal["no_setup", "pending_entry", "accepted", "blocked", "missing_data"]
    reason: str
    confirmation_time: pd.Timestamp | None
    entry_time: pd.Timestamp | None
    entry_price: float | None
    poc: float | None
    val: float | None
    vah: float | None
    profile_shape: str | None
    rvol: float | None
    body_strike: float | None
    factors: tuple[dict[str, object], ...]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def normalize_bars(source: pd.DataFrame) -> pd.DataFrame:
    required = {"date", "open", "high", "low", "close", "volume"}
    missing = required.difference(source.columns)
    if missing:
        raise ValueError(f"SPY bars are missing columns: {sorted(missing)}")
    bars = source[list(required)].copy()
    bars["date"] = pd.to_datetime(bars["date"], errors="coerce")
    if getattr(bars["date"].dt, "tz", None) is not None:
        bars["date"] = (
            bars["date"].dt.tz_convert("America/New_York").dt.tz_localize(None)
        )
    for column in ("open", "high", "low", "close", "volume"):
        bars[column] = pd.to_numeric(bars[column], errors="coerce")
    bars = bars.dropna().drop_duplicates("date", keep="last").sort_values("date")
    bars["day"] = bars["date"].dt.strftime("%Y-%m-%d")
    bars["clock"] = bars["date"].dt.strftime("%H:%M")
    return bars[bars["clock"].between("09:30", "15:55")].reset_index(drop=True)


def build_volume_profile(
    bars: pd.DataFrame, config: FailedAuctionConfig
) -> dict[str, float | str]:
    volume_by_bin: dict[int, float] = {}
    for row in bars.itertuples(index=False):
        low_bin = int(float(row.low) // config.price_bin_points)
        high_bin = int(float(row.high) // config.price_bin_points)
        if high_bin < low_bin:
            continue
        share = float(row.volume) / (high_bin - low_bin + 1)
        for bin_index in range(low_bin, high_bin + 1):
            volume_by_bin[bin_index] = volume_by_bin.get(bin_index, 0.0) + share
    if not volume_by_bin:
        raise ValueError("volume profile is empty")
    bins = pd.Series(volume_by_bin, dtype=float).sort_index()
    poc_bin = int(bins.idxmax())
    target = float(bins.sum()) * config.value_area_fraction
    positions = [int(value) for value in bins.index]
    center = positions.index(poc_bin)
    selected = {poc_bin}
    selected_volume = float(bins.loc[poc_bin])
    left, right = center - 1, center + 1
    while selected_volume < target and (left >= 0 or right < len(positions)):
        left_volume = float(bins.iloc[left]) if left >= 0 else -1.0
        right_volume = float(bins.iloc[right]) if right < len(positions) else -1.0
        if right_volume >= left_volume:
            selected.add(positions[right])
            selected_volume += right_volume
            right += 1
        else:
            selected.add(positions[left])
            selected_volume += left_volume
            left -= 1
    poc = poc_bin * config.price_bin_points
    val = min(selected) * config.price_bin_points
    vah = max(selected) * config.price_bin_points
    position = (poc - val) / max(vah - val, config.price_bin_points)
    shape = "b_profile" if position < 1 / 3 else "p_profile" if position > 2 / 3 else "d_profile"
    return {"poc": float(poc), "val": float(val), "vah": float(vah), "profile_shape": shape}


def _first_consecutive_true(values: pd.Series, required: int) -> int | None:
    run = 0
    for index, value in enumerate(values.astype(bool)):
        run = run + 1 if value else 0
        if run >= required:
            return index
    return None


def _causal_rvol(
    by_day: dict[str, pd.DataFrame],
    session_days: list[str],
    day_index: int,
    clock: str,
    lookback: int,
) -> float:
    current = float(
        by_day[session_days[day_index]].loc[
            by_day[session_days[day_index]]["clock"].le(clock), "volume"
        ].sum()
    )
    references = [
        float(by_day[day].loc[by_day[day]["clock"].le(clock), "volume"].sum())
        for day in session_days[day_index - lookback : day_index]
    ]
    references = [value for value in references if value > 0]
    if len(references) != lookback:
        return math.nan
    median = float(np.median(references))
    return current / median if median > 0 else math.nan


def _factor(code: str, observed: object, operator: str, threshold: object, passed: bool) -> dict[str, object]:
    return {
        "factor_code": code,
        "observed_value": observed,
        "operator": operator,
        "threshold": threshold,
        "passed": passed,
    }


def evaluate_day(
    source: pd.DataFrame,
    day: str | pd.Timestamp,
    config: FailedAuctionConfig = FailedAuctionConfig(),
    *,
    allow_pending_entry: bool = False,
) -> FailedAuctionEvaluation:
    bars = normalize_bars(source)
    target_day = pd.Timestamp(day).strftime("%Y-%m-%d")
    by_day = {
        key: frame.sort_values("date").reset_index(drop=True)
        for key, frame in bars.groupby("day", sort=True)
    }
    session_days = sorted(by_day)
    if target_day not in by_day:
        return FailedAuctionEvaluation(target_day, "missing_data", "current_session_missing", None, None, None, None, None, None, None, None, None, ())
    day_index = session_days.index(target_day)
    minimum_history = max(
        config.profile_lookback_sessions, config.rvol_lookback_sessions
    )
    if day_index < minimum_history:
        return FailedAuctionEvaluation(target_day, "missing_data", "insufficient_prior_sessions", None, None, None, None, None, None, None, None, None, ())
    current = by_day[target_day]
    profile_days = session_days[
        day_index - config.profile_lookback_sessions : day_index
    ]
    profile = build_volume_profile(
        bars[bars["day"].isin(profile_days)], config
    )
    factors: list[dict[str, object]] = []
    open_above = float(current.iloc[0]["open"]) > float(profile["vah"])
    factors.append(_factor("open_above_prior5_vah", float(current.iloc[0]["open"]), ">", float(profile["vah"]), open_above))
    if not open_above:
        return FailedAuctionEvaluation(target_day, "no_setup", "open_not_above_prior5_vah", None, None, None, float(profile["poc"]), float(profile["val"]), float(profile["vah"]), str(profile["profile_shape"]), None, None, tuple(factors))
    inside = current["close"].between(float(profile["val"]), float(profile["vah"]))
    signal_index = _first_consecutive_true(inside, config.consecutive_inside_closes)
    if signal_index is None:
        factors.append(_factor("hard12_inside_value", int(inside.astype(int).sum()), "consecutive>=", config.consecutive_inside_closes, False))
        return FailedAuctionEvaluation(target_day, "no_setup", "hard12_not_confirmed", None, None, None, float(profile["poc"]), float(profile["val"]), float(profile["vah"]), str(profile["profile_shape"]), None, None, tuple(factors))
    confirmation = pd.Timestamp(current.iloc[signal_index]["date"])
    signal_available = confirmation + pd.Timedelta(minutes=config.bar_minutes)
    factors.append(_factor("hard12_inside_value", config.consecutive_inside_closes, "consecutive>=", config.consecutive_inside_closes, True))
    rvol = _causal_rvol(
        by_day,
        session_days,
        day_index,
        str(current.iloc[signal_index]["clock"]),
        config.rvol_lookback_sessions,
    )
    rvol_pass = math.isfinite(rvol) and config.rvol_minimum <= rvol < config.rvol_maximum_exclusive
    factors.append(_factor("causal_rvol", rvol, "in_half_open_interval", [config.rvol_minimum, config.rvol_maximum_exclusive], rvol_pass))
    poc = float(profile["poc"])
    preentry_touch = bool(current.iloc[: signal_index + 1]["low"].le(poc).any())
    factors.append(_factor("poc_untouched_before_entry", preentry_touch, "equals", False, not preentry_touch))
    if not rvol_pass:
        return FailedAuctionEvaluation(target_day, "blocked", "causal_rvol_outside_1_00_1_20", confirmation, signal_available, None, poc, float(profile["val"]), float(profile["vah"]), str(profile["profile_shape"]), rvol, None, tuple(factors))
    if preentry_touch:
        return FailedAuctionEvaluation(target_day, "blocked", "poc_touched_before_entry", confirmation, signal_available, None, poc, float(profile["val"]), float(profile["vah"]), str(profile["profile_shape"]), rvol, None, tuple(factors))
    body = round(round(poc / config.strike_step) * config.strike_step, 8)
    if signal_index + 1 >= len(current):
        status = "pending_entry" if allow_pending_entry else "missing_data"
        reason = "awaiting_next_bar_open" if allow_pending_entry else "next_bar_missing"
        return FailedAuctionEvaluation(target_day, status, reason, confirmation, signal_available, None, poc, float(profile["val"]), float(profile["vah"]), str(profile["profile_shape"]), rvol, body, tuple(factors))
    entry_bar = current.iloc[signal_index + 1]
    entry_price = float(entry_bar["open"])
    entry_above = entry_price > poc
    factors.append(_factor("next_bar_open_above_poc", entry_price, ">", poc, entry_above))
    return FailedAuctionEvaluation(
        target_day,
        "accepted" if entry_above else "blocked",
        "failed_auction_hard12_rvol_accepted" if entry_above else "entry_at_or_below_poc",
        confirmation,
        pd.Timestamp(entry_bar["date"]),
        entry_price,
        poc,
        float(profile["val"]),
        float(profile["vah"]),
        str(profile["profile_shape"]),
        rvol,
        body,
        tuple(factors),
    )
