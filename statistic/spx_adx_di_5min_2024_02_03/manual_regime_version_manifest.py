from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from types import MappingProxyType
from typing import Any, Mapping


STRATEGY_ID = "multi-asset-manual-regime-top50"
STRATEGY_VERSION = "manual-regime-top50-ruleset-v1"
STRATEGY_VARIANT_ID = "manual-regime-baseline@1.0.0"
ENGINE_VERSION = "manual-regime-engine@1.0.0"
MONEY_MANAGEMENT_VERSION = "fixed-risk-per-trade@1.0.0"
RULE_VERSION = "1.0.0"
ENGINE_CODE_SHA256 = "b94b8e1f4ca6da88f13651485e4044f0376770341ef56f3fd1081963b349c6aa"
SNAPSHOT_CREATED_AT_UTC = "2026-07-15T00:00:00Z"


@dataclass(frozen=True, slots=True)
class RuleSnapshot:
    """Immutable identity of one rule in the current production baseline."""

    rule_id: str
    version: str
    category: str
    display_name: str


_ENTRY_RULE_IDS = (
    "bullish_crash_repair_reversal_long",
    "bullish_post_bear_bull_reentry_long",
    "bullish_repair_range_breakout",
    "bullish_pullback_reclaim_long",
    "bullish_pullback_confirmed_higher_low",
    "bullish_add_on_higher_low",
    "bullish_stair_step_weekly_green_streak_combo",
    "bullish_weekly_green_streak_continuation",
    "bullish_stair_step_continuation",
    "bullish_stair_step_weekly_green_streak_continuation",
    "bullish_tier3_projected_high_continuation_long",
    "bullish_tier3_full_trend_participation_breakout",
    "bullish_tier3_small_participation_breakout",
    "bearish_lower_high_failure_short",
    "bearish_gap_breakdown_continuation_short",
    "bearish_failed_countermove_short_baseline",
)

RULES = tuple(
    RuleSnapshot(
        rule_id=rule_id,
        version=RULE_VERSION,
        category="entry",
        display_name=rule_id.replace("_", " "),
    )
    for rule_id in _ENTRY_RULE_IDS
) + (
    RuleSnapshot("__decision__", RULE_VERSION, "system", "daily decision"),
    RuleSnapshot("__market_data__", RULE_VERSION, "system", "market data availability"),
)

RULES_BY_ID: Mapping[str, RuleSnapshot] = MappingProxyType(
    {snapshot.rule_id: snapshot for snapshot in RULES}
)


def canonical_rule_id(primary_rule: str) -> str:
    """Return the stable technical identity used by the existing signal key."""
    rule_id = str(primary_rule).strip().lower()
    if not rule_id:
        raise ValueError("primary_rule must not be empty")
    return rule_id


def rule_version(primary_rule: str) -> str:
    snapshot = RULES_BY_ID.get(canonical_rule_id(primary_rule))
    return snapshot.version if snapshot is not None else RULE_VERSION


def manifest_payload() -> dict[str, Any]:
    return {
        "strategy_id": STRATEGY_ID,
        "strategy_version": STRATEGY_VERSION,
        "strategy_variant_id": STRATEGY_VARIANT_ID,
        "engine_version": ENGINE_VERSION,
        "money_management_version": MONEY_MANAGEMENT_VERSION,
        "engine_code_sha256": ENGINE_CODE_SHA256,
        "snapshot_created_at_utc": SNAPSHOT_CREATED_AT_UTC,
        "rules": [asdict(rule) for rule in RULES],
    }


def manifest_hash() -> str:
    encoded = json.dumps(
        manifest_payload(),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
