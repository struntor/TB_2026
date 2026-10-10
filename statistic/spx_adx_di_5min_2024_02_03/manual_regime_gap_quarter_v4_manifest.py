from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import decision_records as decision_model
import manual_regime_fresh_stop_v3_manifest as parent_manifest
import manual_regime_version_manifest as baseline_manifest
from manual_regime_gap_breakdown_quarter_sizing import (
    POLICY_VERSION,
    RISK_MULTIPLIER,
    TARGET_RULE,
)


STRATEGY_ID = parent_manifest.STRATEGY_ID
STRATEGY_VERSION = (
    "manual-regime-top50-ruleset-v1-cluster-top1-regime-sizing-fresh-stop-"
    "gap-breakdown-quarter"
)
STRATEGY_VARIANT_ID = "manual-regime-baseline@4.0.0"
VARIANT_VERSION = "4.0.0"
ENGINE_VERSION = parent_manifest.ENGINE_VERSION
MONEY_MANAGEMENT_VERSION = "graded-structure-volume-gap-quarter-sizing@2.0.0"
SNAPSHOT_CREATED_AT_UTC = "2026-08-14T00:00:00Z"
GAP_BREAKDOWN_QUARTER_CODE_SHA256 = (
    "7523d1efb56dd8ff0193e6845f66ea4748f230dd8c6a28b213cf02b1849d5bdf"
)

RULE_VERSIONS = dict(parent_manifest.RULE_VERSIONS)

GAP_BREAKDOWN_QUARTER_POLICY = {
    "policy_version": POLICY_VERSION,
    "affected_rule": TARGET_RULE,
    "risk_multiplier": RISK_MULTIPLIER,
    "feature_timing": "completed daily sessions strictly before entry day",
    "volume_trigger": (
        "high_volume_breakout OR "
        "(high_volume_breakdown AND SPX direction is not strong_bull)"
    ),
    "relative_strength_trigger": (
        "SPX net 20-session move above 2 ATR AND asset 20-session return "
        "minus SPX 20-session return is nonnegative"
    ),
    "missing_context_policy": (
        "apply any independently observable volume trigger; otherwise retain parent size"
    ),
    "parent_shadow_variant_id": parent_manifest.STRATEGY_VARIANT_ID,
    "parent_shadow_execution": "virtual quantity only; no second broker order",
}


def verify_code_hashes() -> None:
    parent_manifest.verify_code_hashes()
    path = Path(__file__).with_name(
        "manual_regime_gap_breakdown_quarter_sizing.py"
    )
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != GAP_BREAKDOWN_QUARTER_CODE_SHA256:
        raise RuntimeError(
            "gap-breakdown quarter-sizing code differs from immutable manual-regime "
            "V4 manifest; create a new strategy/money-management version"
        )


def manifest_payload() -> dict[str, Any]:
    return {
        "strategy_id": STRATEGY_ID,
        "strategy_version": STRATEGY_VERSION,
        "strategy_variant_id": STRATEGY_VARIANT_ID,
        "variant_version": VARIANT_VERSION,
        "engine_version": ENGINE_VERSION,
        "money_management_version": MONEY_MANAGEMENT_VERSION,
        "snapshot_created_at_utc": SNAPSHOT_CREATED_AT_UTC,
        "parent_variant_id": parent_manifest.STRATEGY_VARIANT_ID,
        "parent_manifest_hash": parent_manifest.manifest_hash(),
        "entry_rule_logic_changed": False,
        "stop_target_logic_changed": False,
        "position_sizing_changed": True,
        "gap_breakdown_quarter_code_sha256": GAP_BREAKDOWN_QUARTER_CODE_SHA256,
        "gap_breakdown_quarter_policy": GAP_BREAKDOWN_QUARTER_POLICY,
        "rules": [
            {
                "rule_id": rule.rule_id,
                "version": RULE_VERSIONS[rule.rule_id],
                "category": rule.category,
                "display_name": rule.display_name,
            }
            for rule in baseline_manifest.RULES
        ],
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


def version_context() -> decision_model.DecisionVersionContext:
    return decision_model.DecisionVersionContext(
        strategy_variant_id=STRATEGY_VARIANT_ID,
        engine_version=ENGINE_VERSION,
        money_management_version=MONEY_MANAGEMENT_VERSION,
        rule_versions=RULE_VERSIONS,
        manifest_hash=manifest_hash(),
    )
