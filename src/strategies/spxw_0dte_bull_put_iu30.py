from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
from pathlib import Path
from typing import Any, Iterable, Mapping

import numpy as np
import pandas as pd


STRATEGY_ID = "spx-0dte-bull-put-iu30-butterfly"
STRATEGY_VERSION = "1.0.0-paper-0940"
STRATEGY_VARIANT_ID = f"{STRATEGY_ID}@{STRATEGY_VERSION}"
ENGINE_VERSION = "manual-regime-0940-context@1.0.0"
MONEY_MANAGEMENT_VERSION = "one-combo-shared-defined-risk@1.0.0"
SIGNAL_KEY_VERSION = "spx0-iu30-0940@1"
ENTRY_RULE_ID = "spx-0dte-breadth-stress-entry-0940"
ENTRY_RULE_VERSION = "1.0.0"


@dataclass(frozen=True, slots=True)
class Spxw0DteConfig:
    decision_time_et: str = "09:40:00"
    latest_submission_time_et: str = "09:50:00"
    spread_width_points: float = 20.0
    short_put_atr_offset: float = 1.0
    fly_width_points: float = 30.0
    fly_body_atr_offset: float = 0.25
    minimum_breadth_count: int = 1
    stress_return5_threshold: float = -0.023236636745547923
    stress_weekly_range_percentile: float = 0.92307692
    quantity: int = 1


@dataclass(frozen=True, slots=True)
class EntryEvaluation:
    day: str
    status: str
    reason: str
    decision_time: pd.Timestamp
    decision_price: float | None
    prior_atr14: float | None
    breadth_count: int
    breadth_symbols: tuple[str, ...]
    ret5_prev: float | None
    weekly_range_percentile52: float | None
    spx_regime: str | None
    spx_big_picture_regime: str | None
    spx_profile_price_location: str | None
    iu30_overlay: bool
    factors: tuple[dict[str, Any], ...]

    @property
    def accepted(self) -> bool:
        return self.status == "accepted"


def config_hash(config: Spxw0DteConfig) -> str:
    return hashlib.sha256(repr(asdict(config)).encode("utf-8")).hexdigest()


def _mapping(record: object) -> Mapping[str, Any]:
    if isinstance(record, Mapping):
        return record
    raw = getattr(record, "raw_record", None)
    return {
        "symbol": getattr(record, "symbol", None),
        "track": getattr(record, "track", None),
        "primary_rule": getattr(record, "primary_rule", None),
        "context": getattr(record, "context", None) or (
            raw.get("context", {}) if isinstance(raw, Mapping) else {}
        ),
    }


def _decision_contexts(records: Iterable[object]) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for record in records:
        item = _mapping(record)
        context = item.get("context")
        if not isinstance(context, Mapping) or not context:
            continue
        symbol = str(item.get("symbol") or context.get("symbol") or "").upper()
        if not symbol:
            continue
        is_decision = (
            str(item.get("track") or "") == "decision"
            or str(item.get("primary_rule") or "") == "__decision__"
        )
        if is_decision or symbol not in result:
            result[symbol] = context
    return result


def _finite(value: object) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if np.isfinite(parsed) else None


def prior_risk_context(raw_spx_path: Path, day: pd.Timestamp | str) -> dict[str, float | None]:
    bars = pd.read_csv(raw_spx_path, usecols=["date", "high", "low", "close"])
    bars["date"] = pd.to_datetime(bars["date"], errors="coerce")
    bars = bars.dropna(subset=["date"])
    bars["day"] = bars["date"].dt.normalize()
    daily = bars.groupby("day", as_index=False).agg(
        high=("high", "max"), low=("low", "min"), close=("close", "last")
    )
    target = pd.Timestamp(day).normalize()
    if target.tzinfo is not None:
        target = target.tz_localize(None)
    daily = daily[daily["day"] < target].sort_values("day").reset_index(drop=True)
    if len(daily) < 14:
        return {"atr14": None, "ret5": None, "weekly_range_percentile52": None}
    previous_close = daily["close"].shift(1)
    true_range = pd.concat(
        [
            daily["high"] - daily["low"],
            (daily["high"] - previous_close).abs(),
            (daily["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr14 = _finite(true_range.rolling(14, min_periods=14).mean().iloc[-1])
    ret5 = _finite(daily["close"].iloc[-1] / daily["close"].iloc[-6] - 1.0) if len(daily) >= 6 else None
    weekly = daily.set_index("day").resample("W-FRI").agg(
        high=("high", "max"), low=("low", "min"), close=("close", "last")
    )
    # A week ending on/after the decision day is incomplete and must not be used.
    weekly = weekly[weekly.index < target]
    weekly["range_pct"] = (weekly["high"] - weekly["low"]) / weekly["close"]
    percentile = weekly["range_pct"].rolling(52, min_periods=1).apply(
        lambda values: float(pd.Series(values).rank(pct=True).iloc[-1]), raw=False
    )
    weekly_pct = _finite(percentile.iloc[-1]) if not percentile.empty else None
    return {"atr14": atr14, "ret5": ret5, "weekly_range_percentile52": weekly_pct}


def evaluate_entry(
    records: Iterable[object],
    top50_symbols: Iterable[str],
    day: pd.Timestamp | str,
    raw_spx_path: Path,
    config: Spxw0DteConfig = Spxw0DteConfig(),
) -> EntryEvaluation:
    day_text = pd.Timestamp(day).strftime("%Y-%m-%d")
    decision_time = pd.Timestamp(f"{day_text} {config.decision_time_et}")
    contexts = _decision_contexts(records)
    spx = contexts.get("SPX")
    risk = prior_risk_context(raw_spx_path, day)
    top = {str(symbol).upper() for symbol in top50_symbols}
    breadth_symbols = tuple(
        sorted(
            symbol
            for symbol, context in contexts.items()
            if symbol in top
            and str(context.get("regime")) == "bearish_countermove"
            and str(context.get("big_picture_regime")) == "bearish_primary"
            and str(context.get("daily_structure_phase")) == "trend_or_unclassified"
            and str(context.get("profile_price_location")) == "above_value"
            and str(context.get("volume_price_phase")) == "normal_volume_trend"
        )
    )
    decision_price = _finite(spx.get("decision_price")) if spx else None
    atr = risk["atr14"]
    ret5 = risk["ret5"]
    weekly_pct = risk["weekly_range_percentile52"]
    spx_regime = str(spx.get("regime")) if spx else None
    spx_big = str(spx.get("big_picture_regime")) if spx else None
    spx_location = str(spx.get("profile_price_location")) if spx else None
    stress_veto = (
        ret5 is not None and ret5 <= config.stress_return5_threshold
    ) or (
        weekly_pct is not None and weekly_pct >= config.stress_weekly_range_percentile
    )
    spx_veto = spx_regime == "bearish_countermove" and spx_big == "bearish_pressure"
    iu30 = (
        spx_regime == "bullish_tier3"
        and spx_big == "bullish_primary"
        and spx_location == "inside_value_upper"
    )
    checks = (
        ("complete_spx_0940_context", decision_price, "is_not_null", None, spx is not None and decision_price is not None),
        ("prior_atr14_available", atr, "is_not_null", None, atr is not None and atr > 0),
        ("top50_breadth_context", len(breadth_symbols), ">=", config.minimum_breadth_count, len(breadth_symbols) >= config.minimum_breadth_count),
        ("five_session_stress_veto", ret5, ">", config.stress_return5_threshold, not (ret5 is not None and ret5 <= config.stress_return5_threshold)),
        ("weekly_range_stress_veto", weekly_pct, "<", config.stress_weekly_range_percentile, not (weekly_pct is not None and weekly_pct >= config.stress_weekly_range_percentile)),
        ("spx_countermove_pressure_veto", {"regime": spx_regime, "big_picture": spx_big}, "not_both", {"regime": "bearish_countermove", "big_picture": "bearish_pressure"}, not spx_veto),
    )
    factors = tuple(
        {"factor_code": code, "observed_value": observed, "operator": op, "threshold": threshold, "passed": bool(passed)}
        for code, observed, op, threshold, passed in checks
    )
    failed = next((factor for factor in factors if not factor["passed"]), None)
    if failed is None:
        status, reason = "accepted", "0940_breadth_entry_accepted"
    elif failed["factor_code"] in {"complete_spx_0940_context", "prior_atr14_available"}:
        status, reason = "missing_data", str(failed["factor_code"])
    elif stress_veto or spx_veto:
        status, reason = "vetoed", str(failed["factor_code"])
    else:
        status, reason = "no_setup", str(failed["factor_code"])
    return EntryEvaluation(
        day_text, status, reason, decision_time, decision_price, atr,
        len(breadth_symbols), breadth_symbols, ret5, weekly_pct,
        spx_regime, spx_big, spx_location, iu30, factors,
    )
