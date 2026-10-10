from __future__ import annotations

import hashlib
import json
from typing import Any

import decision_records as decision_model
import manual_regime_engine_v1_2_manifest as parent_manifest
import manual_regime_version_manifest as baseline_manifest


ENGINE_VERSION = "manual-regime-engine@1.3.0"
STRATEGY_VERSION = "manual-regime-top50-ruleset-v1-volume-weighted-acceptance"
STRATEGY_VARIANT_ID = "manual-regime-volume-weighted-acceptance@1.3.0"
MONEY_MANAGEMENT_VERSION = baseline_manifest.MONEY_MANAGEMENT_VERSION
ENGINE_CODE_SHA256 = "b264b2d817ba45b8f9183bb8749219ded004456310e35691d1062b1ab1ed1b4f"
SNAPSHOT_CREATED_AT_UTC = "2026-07-17T00:00:00Z"

ACCEPTANCE_PROFILE_CHANGE = {
    "lookback_days": 30,
    "bin_size": "max(atr_tolerance, 0.04 * atr14, 0.01)",
    "smoothing": "adjacent_three_bin_sum",
    "acceptance_quantile": 0.65,
    "minimum_target_move": "max(0.60 * atr14, 6.0 * atr_tolerance)",
    "volume_allocation": "bar_volume_divided_equally_across_covered_price_bins",
    "fallback": "v1.2_tpo_one_count_per_bar_per_covered_price_bin",
    "causal_cutoff": "bar_timestamp_strictly_before_decision_time",
}


def manifest_payload() -> dict[str, Any]:
    return {
        "strategy_id": baseline_manifest.STRATEGY_ID,
        "strategy_version": STRATEGY_VERSION,
        "strategy_variant_id": STRATEGY_VARIANT_ID,
        "engine_version": ENGINE_VERSION,
        "money_management_version": MONEY_MANAGEMENT_VERSION,
        "engine_code_sha256": ENGINE_CODE_SHA256,
        "snapshot_created_at_utc": SNAPSHOT_CREATED_AT_UTC,
        "parent_manifest_hash": parent_manifest.manifest_hash(),
        "parent_engine_code_sha256": parent_manifest.ENGINE_CODE_SHA256,
        "entry_rule_parameters_changed": False,
        "acceptance_profile_change": ACCEPTANCE_PROFILE_CHANGE,
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
