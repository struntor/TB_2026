from __future__ import annotations

import hashlib
import json
from typing import Any

import decision_records as decision_model
import manual_regime_version_manifest as baseline_manifest


ENGINE_VERSION = "manual-regime-engine@1.2.0"
STRATEGY_VERSION = "manual-regime-top50-ruleset-v1-correctness-fixes"
STRATEGY_VARIANT_ID = "manual-regime-correctness-fixes@1.2.0"
MONEY_MANAGEMENT_VERSION = baseline_manifest.MONEY_MANAGEMENT_VERSION
ENGINE_CODE_SHA256 = "da68bac10f502a1ec71a594d33be19ee1ae22dead0233c5bc176b4543e9047b1"
SNAPSHOT_CREATED_AT_UTC = "2026-07-17T00:00:00Z"

CORRECTNESS_FIXES = (
    "volume_phase_uses_completed_w_fri_weeks_only",
    "invalid_continuation_stop_preserves_later_rules_and_decision_record",
)


def manifest_payload() -> dict[str, Any]:
    return {
        "strategy_id": baseline_manifest.STRATEGY_ID,
        "strategy_version": STRATEGY_VERSION,
        "strategy_variant_id": STRATEGY_VARIANT_ID,
        "engine_version": ENGINE_VERSION,
        "money_management_version": MONEY_MANAGEMENT_VERSION,
        "engine_code_sha256": ENGINE_CODE_SHA256,
        "snapshot_created_at_utc": SNAPSHOT_CREATED_AT_UTC,
        "baseline_manifest_hash": baseline_manifest.manifest_hash(),
        "baseline_engine_code_sha256": baseline_manifest.ENGINE_CODE_SHA256,
        "entry_rule_parameters_changed": False,
        "correctness_fixes": list(CORRECTNESS_FIXES),
        "rules": [
            {
                "rule_id": rule.rule_id,
                "version": rule.version,
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
        rule_versions={
            rule.rule_id: rule.version for rule in baseline_manifest.RULES
        },
        manifest_hash=manifest_hash(),
    )
