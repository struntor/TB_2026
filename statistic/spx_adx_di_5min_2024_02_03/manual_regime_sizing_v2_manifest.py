from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import decision_records as decision_model
import manual_regime_version_manifest as baseline_manifest


STRATEGY_ID = baseline_manifest.STRATEGY_ID
STRATEGY_VERSION = "manual-regime-top50-ruleset-v1-cluster-top1-regime-sizing"
STRATEGY_VARIANT_ID = "manual-regime-baseline@2.0.0"
VARIANT_VERSION = "2.0.0"
ENGINE_VERSION = baseline_manifest.ENGINE_VERSION
MONEY_MANAGEMENT_VERSION = "graded-structure-volume-sizing@1.0.0"
SNAPSHOT_CREATED_AT_UTC = "2026-08-03T00:00:00Z"
POSITION_SIZING_CODE_SHA256 = (
    "aad17a614ce291a21bf22c56ff07bac1544bca8dce7847ce38713c40c2b87e4d"
)
CLUSTER_SCORE_CODE_SHA256 = (
    "2a83d0d31a70285f90fc2141687d07196b386732ec2e29dd70b7dc90d597072a"
)

POSITION_SIZING_POLICY = {
    "structure_context": "SPX completed daily sessions strictly before entry day",
    "structure_score_70": 0.75,
    "structure_score_100": 0.50,
    "structure_affected_rules": [
        "bearish_gap_breakdown_continuation_short",
        "bullish_crash_repair_reversal_long",
        "bullish_pullback_reclaim_long",
        "bullish_tier3_full_trend_participation_breakout",
        "bullish_tier3_projected_high_continuation_long",
        "bullish_tier3_small_participation_breakout",
    ],
    "volume_context": "SPY prior-session rejection against prior 20-session profile",
    "volume_short_cap": 0.50,
    "volume_trigger": "lower rejection and highest overhead HVN at least 1 ATR away",
    "missing_context_policy": "conservative 0.50 cap for affected trades",
    "combined_floor": 0.50,
    "legacy_shadow_variant_id": baseline_manifest.STRATEGY_VARIANT_ID,
}

CLUSTER_CHOICE_POLICY = {
    "rule": "bearish_lower_high_failure_short",
    "selection": "highest planned CRV, deterministic liquidity tie-break",
    "selected_rank": 1,
    "routing_scope": ["SPY", "IVV", "VOO", "VTI", "OEF"],
    "legacy_shadow": "all otherwise eligible cluster candidates",
}


def verify_code_hashes() -> None:
    root = Path(__file__).resolve().parent
    checks = {
        root / "manual_regime_position_sizing.py": POSITION_SIZING_CODE_SHA256,
        root / "lhf_cluster_rank_score.py": CLUSTER_SCORE_CODE_SHA256,
    }
    for path, expected in checks.items():
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            raise RuntimeError(
                f"{path.name} differs from immutable manual-regime V2 manifest; "
                "create a new strategy/money-management version"
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
        "parent_variant_id": baseline_manifest.STRATEGY_VARIANT_ID,
        "parent_manifest_hash": baseline_manifest.manifest_hash(),
        "entry_rule_logic_changed": False,
        "stop_target_logic_changed": False,
        "position_sizing_code_sha256": POSITION_SIZING_CODE_SHA256,
        "cluster_score_code_sha256": CLUSTER_SCORE_CODE_SHA256,
        "position_sizing_policy": POSITION_SIZING_POLICY,
        "cluster_choice_policy": CLUSTER_CHOICE_POLICY,
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
