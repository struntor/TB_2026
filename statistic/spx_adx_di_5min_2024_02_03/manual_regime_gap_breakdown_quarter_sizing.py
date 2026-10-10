from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from pathlib import Path
from typing import Any

import pandas as pd

from manual_regime_position_sizing import daily_prices


POLICY_VERSION = "gap-breakdown-regime-volume-quarter-sizing-v1"
TARGET_RULE = "bearish_gap_breakdown_continuation_short"
RISK_MULTIPLIER = 0.25


@dataclass(frozen=True, slots=True)
class GapBreakdownQuarterContext:
    trading_day: str
    symbol: str
    volume_price_phase: str
    spx_context_available: bool
    asset_context_available: bool
    spx_net_atr_20: float | None
    spx_direction_bucket: str
    spx_return_20_pct: float | None
    asset_return_20_pct: float | None
    relative_strength_20_pct: float | None
    volume_trigger: bool
    relative_strength_trigger: bool
    quarter_size_trigger: bool
    errors: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def spx_direction_bucket(net_atr_20: float | None) -> str:
    if net_atr_20 is None or not math.isfinite(float(net_atr_20)):
        return "unavailable"
    value = float(net_atr_20)
    if value <= -2.0:
        return "strong_bear"
    if value <= 0.0:
        return "mild_bear"
    if value <= 2.0:
        return "mild_bull"
    return "strong_bull"


def quarter_size_decision(
    *,
    rule: str,
    volume_price_phase: str,
    direction_bucket: str,
    relative_strength_20_pct: float | None,
) -> tuple[float, tuple[str, ...]]:
    if str(rule) != TARGET_RULE:
        return 1.0, ("rule_not_affected",)

    phase = str(volume_price_phase or "")
    direction = str(direction_bucket or "unavailable")
    volume_trigger = phase == "high_volume_breakout" or (
        phase == "high_volume_breakdown" and direction != "strong_bull"
    )
    relative_strength_trigger = (
        direction == "strong_bull"
        and relative_strength_20_pct is not None
        and math.isfinite(float(relative_strength_20_pct))
        and float(relative_strength_20_pct) >= 0.0
    )
    reasons: list[str] = []
    if volume_trigger:
        reasons.append("adverse_volume_phase_for_gap_breakdown_short")
    if relative_strength_trigger:
        reasons.append("nonnegative_relative_strength_in_strong_bull_spx")
    if reasons:
        return RISK_MULTIPLIER, tuple(reasons)
    return 1.0, ("gap_breakdown_quarter_condition_not_met",)


def _return_20_pct(daily: pd.DataFrame) -> float:
    if len(daily) < 21:
        raise ValueError("20-session return needs at least 21 completed sessions")
    latest = float(daily.iloc[-1]["close"])
    prior = float(daily.iloc[-21]["close"])
    if not math.isfinite(latest) or not math.isfinite(prior) or prior <= 0:
        raise ValueError("20-session return has invalid closes")
    return 100.0 * (latest / prior - 1.0)


def _spx_features(
    trading_day: pd.Timestamp,
    raw_dir: Path,
) -> tuple[float, float]:
    daily = daily_prices(raw_dir / "Rohdaten_SPX.csv", trading_day)
    spx_return = _return_20_pct(daily)
    atr14 = float(daily.iloc[-1]["atr14"])
    if not math.isfinite(atr14) or atr14 <= 0:
        raise ValueError("SPX ATR14 is unavailable")
    change_20 = float(daily.iloc[-1]["close"]) - float(daily.iloc[-21]["close"])
    return change_20 / atr14, spx_return


def build_gap_breakdown_quarter_contexts(
    trades: pd.DataFrame,
    trading_day: str | pd.Timestamp,
    *,
    raw_dir: Path,
) -> dict[str, GapBreakdownQuarterContext]:
    if trades.empty:
        return {}
    day = pd.Timestamp(trading_day).tz_localize(None).normalize()
    affected = trades.loc[trades["rule"].astype(str).eq(TARGET_RULE)].copy()
    if affected.empty:
        return {}

    spx_net_atr: float | None = None
    spx_return: float | None = None
    spx_error: str | None = None
    try:
        spx_net_atr, spx_return = _spx_features(day, raw_dir)
    except Exception as exc:
        spx_error = f"{type(exc).__name__}: {exc}"
    direction = spx_direction_bucket(spx_net_atr)

    asset_returns: dict[str, float | None] = {}
    asset_errors: dict[str, str | None] = {}
    for symbol in sorted(affected["symbol"].astype(str).str.upper().unique()):
        try:
            asset_returns[symbol] = _return_20_pct(
                daily_prices(raw_dir / f"Rohdaten_{symbol}.csv", day)
            )
            asset_errors[symbol] = None
        except Exception as exc:
            asset_returns[symbol] = None
            asset_errors[symbol] = f"{type(exc).__name__}: {exc}"

    contexts: dict[str, GapBreakdownQuarterContext] = {}
    for row in affected.itertuples(index=False):
        payload = row._asdict()
        symbol = str(payload["symbol"]).upper()
        asset_return = asset_returns[symbol]
        relative_strength = (
            float(asset_return) - float(spx_return)
            if asset_return is not None and spx_return is not None
            else None
        )
        phase = str(payload.get("volume_price_phase", "") or "")
        multiplier, reasons = quarter_size_decision(
            rule=str(payload["rule"]),
            volume_price_phase=phase,
            direction_bucket=direction,
            relative_strength_20_pct=relative_strength,
        )
        errors = tuple(
            error for error in (spx_error, asset_errors[symbol]) if error is not None
        )
        contexts[str(payload["signal_key"])] = GapBreakdownQuarterContext(
            trading_day=day.date().isoformat(),
            symbol=symbol,
            volume_price_phase=phase,
            spx_context_available=spx_error is None,
            asset_context_available=asset_errors[symbol] is None,
            spx_net_atr_20=spx_net_atr,
            spx_direction_bucket=direction,
            spx_return_20_pct=spx_return,
            asset_return_20_pct=asset_return,
            relative_strength_20_pct=relative_strength,
            volume_trigger=(
                "adverse_volume_phase_for_gap_breakdown_short" in reasons
            ),
            relative_strength_trigger=(
                "nonnegative_relative_strength_in_strong_bull_spx" in reasons
            ),
            quarter_size_trigger=multiplier == RISK_MULTIPLIER,
            errors=errors,
        )
    return contexts


def apply_gap_breakdown_quarter_sizing(
    trades: pd.DataFrame,
    trading_day: str | pd.Timestamp,
    *,
    raw_dir: Path,
) -> pd.DataFrame:
    result = trades.copy()
    if result.empty:
        return result
    contexts = build_gap_breakdown_quarter_contexts(
        result,
        trading_day,
        raw_dir=raw_dir,
    )
    parent_sizes: list[float] = []
    multipliers: list[float] = []
    reasons: list[str] = []
    context_payloads: list[dict[str, Any]] = []
    for row in result.itertuples(index=False):
        payload = row._asdict()
        parent_size = float(payload.get("size", 1.0) or 1.0)
        context = contexts.get(str(payload["signal_key"]))
        if context is None:
            multiplier = 1.0
            row_reasons = ("rule_not_affected",)
            context_payload: dict[str, Any] = {}
        else:
            multiplier, row_reasons = quarter_size_decision(
                rule=str(payload["rule"]),
                volume_price_phase=context.volume_price_phase,
                direction_bucket=context.spx_direction_bucket,
                relative_strength_20_pct=context.relative_strength_20_pct,
            )
            context_payload = context.to_dict()
        parent_sizes.append(parent_size)
        multipliers.append(multiplier)
        reasons.append("|".join(row_reasons))
        context_payloads.append(context_payload)
    result["v3_parent_shadow_size"] = parent_sizes
    result["gap_breakdown_quarter_risk_multiplier"] = multipliers
    result["size"] = (
        result["v3_parent_shadow_size"]
        * result["gap_breakdown_quarter_risk_multiplier"]
    )
    result["gap_breakdown_quarter_policy_version"] = POLICY_VERSION
    result["gap_breakdown_quarter_reasons"] = reasons
    result["gap_breakdown_quarter_context"] = context_payloads
    return result

