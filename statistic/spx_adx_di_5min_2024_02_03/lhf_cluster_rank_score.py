from __future__ import annotations

from dataclasses import asdict, dataclass
import math
import re
from typing import Any, Mapping, Sequence


SCORE_VERSION = "lhf-cluster-rank-v1"
CHOICE_SCORE_VERSION = "lhf-cluster-choice-crv-v1"
LHF_RULE = "bearish_lower_high_failure_short"
BROAD_MARKET_ETF_CLUSTER = ("DIA", "IVV", "OEF", "SPY", "VOO", "VTI")
BROAD_MARKET_ETF_LIQUIDITY_PRIORITY = ("SPY", "IVV", "VOO", "VTI", "DIA", "OEF")


@dataclass(frozen=True)
class LhfScoreInput:
    signal_key: str
    symbol: str
    signal_regime: str
    big_picture_regime: str
    big_picture_state: str
    daily_structure_phase: str
    bearish_countermove_subregime: str
    volume_price_phase: str
    bars_since_support_break: int | None
    distance_from_recent_high_atr: float | None
    net_atr: float | None
    entry: float
    stop: float
    target: float


@dataclass(frozen=True)
class LhfScoreResult:
    signal_key: str
    symbol: str
    score_version: str
    score: float
    choice_score_version: str
    choice_score: float | None
    structural_alignment: float
    setup_quality: float
    reward_quality: float
    relative_weakness: float
    planned_crv: float | None
    relative_distance_rank: float
    relative_impulse_rank: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _clean(value: Any) -> str:
    return "" if value is None else str(value).strip().lower()


def _finite_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _context_text(context: Mapping[str, Any], *names: str) -> str:
    for name in names:
        value = context.get(name)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def parse_volume_detail_net_atr(value: Any) -> float | None:
    match = re.search(r"(?:^|\|)net_atr=([-+]?\d+(?:\.\d+)?)", str(value or ""))
    return _finite_float(match.group(1)) if match else None


def input_from_record_and_trade(
    record: Any,
    trade: Mapping[str, Any],
) -> LhfScoreInput:
    context = record.context if hasattr(record, "context") else record["context"]
    explanation = context.get("decision_explanation") or {}
    if not isinstance(explanation, Mapping):
        explanation = {}
    volume_detail = explanation.get("volume_price_detail") or context.get("volume_price_detail")
    return LhfScoreInput(
        signal_key=str(getattr(record, "signal_key", trade.get("signal_key", ""))),
        symbol=str(getattr(record, "symbol", trade.get("symbol", ""))).upper(),
        signal_regime=_context_text(context, "signal_regime_fresh", "regime"),
        big_picture_regime=_context_text(
            context,
            "big_picture_regime_fresh",
            "big_picture_regime",
        ),
        big_picture_state=_context_text(context, "big_picture_state"),
        daily_structure_phase=_context_text(
            context,
            "daily_structure_phase_fresh",
            "daily_structure_phase",
        ),
        bearish_countermove_subregime=_context_text(
            context,
            "bearish_countermove_subregime",
        ),
        volume_price_phase=_context_text(context, "volume_price_phase")
        or str(explanation.get("volume_price_phase") or ""),
        bars_since_support_break=(
            int(context["bars_since_support_break"])
            if _finite_float(context.get("bars_since_support_break")) is not None
            else None
        ),
        distance_from_recent_high_atr=_finite_float(
            context.get("distance_from_recent_high_atr")
        ),
        net_atr=parse_volume_detail_net_atr(volume_detail),
        entry=float(trade["entry"]),
        stop=float(trade["stop"]),
        target=float(trade["target"]),
    )


def _lookup(value: str, mapping: Mapping[str, float], default: float = 0.0) -> float:
    return float(mapping.get(_clean(value), default))


def _break_freshness(bars: int | None) -> float:
    if bars is None or bars < 1:
        return 0.0
    if bars <= 2:
        return 1.0
    if bars <= 4:
        return 0.70
    if bars <= 7:
        return 0.30
    return 0.0


def _extension_quality(distance_atr: float | None) -> float:
    if distance_atr is None or distance_atr < 0.0:
        return 0.0
    if distance_atr < 1.0:
        return 0.10
    if distance_atr < 2.0:
        return 0.40
    if distance_atr <= 4.0:
        return 1.0
    if distance_atr <= 5.0:
        return 0.70
    if distance_atr <= 6.0:
        return 0.40
    return 0.10


def planned_crv(item: LhfScoreInput) -> float | None:
    risk = item.stop - item.entry
    reward = item.entry - item.target
    if not math.isfinite(risk) or not math.isfinite(reward) or risk <= 0.0 or reward <= 0.0:
        return None
    return reward / risk


def _reward_quality(crv: float | None) -> float:
    if crv is None:
        return 0.0
    # 0.75R is structurally weak; 2R or more receives the full component.
    return min(max((crv - 0.75) / 1.25, 0.0), 1.0)


def _relative_ranks(values: Sequence[float | None], *, higher_is_stronger: bool) -> list[float]:
    usable = [(index, value) for index, value in enumerate(values) if value is not None]
    if len(usable) <= 1:
        return [0.5] * len(values)
    sorted_values = sorted({float(value) for _, value in usable})
    strength_by_value: dict[float, float] = {}
    denominator = max(len(sorted_values) - 1, 1)
    for position, value in enumerate(sorted_values):
        ascending_rank = position / denominator
        strength_by_value[value] = (
            ascending_rank if higher_is_stronger else 1.0 - ascending_rank
        )
    return [
        strength_by_value.get(float(value), 0.5) if value is not None else 0.5
        for value in values
    ]


def score_cluster(items: Sequence[LhfScoreInput]) -> list[LhfScoreResult]:
    """Score simultaneous LHF candidates using decision-time information only."""
    if not items:
        return []
    keys = [item.signal_key for item in items]
    if len(keys) != len(set(keys)):
        raise ValueError("LHF cluster score inputs contain duplicate signal keys")

    distance_ranks = _relative_ranks(
        [item.distance_from_recent_high_atr for item in items],
        higher_is_stronger=True,
    )
    # More negative net_atr means the preceding price impulse was weaker.
    impulse_ranks = _relative_ranks(
        [item.net_atr for item in items],
        higher_is_stronger=False,
    )

    signal_regime = {
        "bearish_confirmed": 1.0,
        "bearish_countermove": 0.50,
        "bearish_attempt": 0.20,
    }
    big_picture_regime = {
        "bearish_primary": 1.0,
        "bearish_pressure": 0.70,
        "bearish_transition": 0.40,
        "bullish_primary": 0.0,
    }
    big_picture_state = {
        "weekly_lower_high_lower_low": 1.0,
        "weekly_bearish_mixed_structure": 0.65,
        "weekly_range_or_mixed": 0.35,
        "weekly_higher_high_higher_low": 0.0,
    }
    structure_phase = {
        "post_break_range_lower": 1.0,
        "post_break_range_middle": 0.70,
        "trend_or_unclassified": 0.40,
        "post_break_range_upper": 0.0,
    }
    countermove = {
        "bearish_countermove_weak_retest": 1.0,
        "bearish_countermove_repair_rebound": 0.50,
        "bearish_countermove_volatility_reversal": 0.0,
    }
    volume_phase = {
        "low_volume_pullback": 1.0,
        "normal_volume_trend": 0.50,
        "high_volume_reversal": 0.0,
    }

    results: list[LhfScoreResult] = []
    for item, distance_rank, impulse_rank in zip(items, distance_ranks, impulse_ranks):
        structural = (
            10.0 * _lookup(item.signal_regime, signal_regime)
            + 12.0 * _lookup(item.big_picture_regime, big_picture_regime)
            + 13.0 * _lookup(item.big_picture_state, big_picture_state)
        )
        setup = (
            10.0 * _break_freshness(item.bars_since_support_break)
            + 10.0 * _extension_quality(item.distance_from_recent_high_atr)
            + 7.0 * _lookup(item.daily_structure_phase, structure_phase)
            + 4.0 * _lookup(item.bearish_countermove_subregime, countermove, 0.50)
            + 4.0 * _lookup(item.volume_price_phase, volume_phase, 0.50)
        )
        crv = planned_crv(item)
        reward = 15.0 * _reward_quality(crv)
        relative = 7.5 * distance_rank + 7.5 * impulse_rank
        total = structural + setup + reward + relative
        results.append(
            LhfScoreResult(
                signal_key=item.signal_key,
                symbol=item.symbol,
                score_version=SCORE_VERSION,
                score=round(total, 6),
                choice_score_version=CHOICE_SCORE_VERSION,
                choice_score=round(crv, 6) if crv is not None else None,
                structural_alignment=round(structural, 6),
                setup_quality=round(setup, 6),
                reward_quality=round(reward, 6),
                relative_weakness=round(relative, 6),
                planned_crv=round(crv, 6) if crv is not None else None,
                relative_distance_rank=round(distance_rank, 6),
                relative_impulse_rank=round(impulse_rank, 6),
            )
        )
    return sorted(results, key=lambda result: (-result.score, result.symbol))


def rank_cluster_choices(results: Sequence[LhfScoreResult]) -> list[LhfScoreResult]:
    """Rank by planned reward/risk; measured liquidity is the deterministic tie-breaker."""
    liquidity_rank = {
        symbol: position
        for position, symbol in enumerate(BROAD_MARKET_ETF_LIQUIDITY_PRIORITY)
    }
    return sorted(
        results,
        key=lambda result: (
            -(result.choice_score if result.choice_score is not None else -math.inf),
            liquidity_rank.get(result.symbol, len(liquidity_rank)),
            result.symbol,
        ),
    )
