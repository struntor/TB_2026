from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import decision_records as decision_model
import manual_regime_sizing_v2_manifest as parent_manifest
import manual_regime_version_manifest as baseline_manifest
from manual_regime_failed_countermove_fresh_stop import (
    MINIMUM_CRV,
    OPENING_WINDOW,
    POLICY_VERSION,
    PRIOR_SESSION_WINDOW,
    TARGET_RULE,
)


STRATEGY_ID = parent_manifest.STRATEGY_ID
STRATEGY_VERSION = (
    "manual-regime-top50-ruleset-v1-cluster-top1-regime-sizing-fresh-stop"
)
STRATEGY_VARIANT_ID = "manual-regime-baseline@3.0.0"
VARIANT_VERSION = "3.0.0"
ENGINE_VERSION = "manual-regime-engine@1.1.0"
MONEY_MANAGEMENT_VERSION = parent_manifest.MONEY_MANAGEMENT_VERSION
SNAPSHOT_CREATED_AT_UTC = "2026-08-14T00:00:00Z"
FRESH_STOP_CODE_SHA256 = (
    "8f69213ae1f4d115c9948097357396b1cef7e27ade59ffca53f6428db87df1e4"
)

RULE_VERSIONS = {
    rule.rule_id: ("1.1.0" if rule.rule_id == TARGET_RULE else rule.version)
    for rule in baseline_manifest.RULES
}

FRESH_STOP_POLICY = {
    "policy_version": POLICY_VERSION,
    "affected_rule": TARGET_RULE,
    "entry_timing_changed": False,
    "stop": (
        "max(V2 stop, prior-session 12:00-15:55 high + ATR tolerance, "
        "09:30-09:35 high + ATR tolerance)"
    ),
    "minimum_crv_after_stop": MINIMUM_CRV,
    "prior_session_window": list(PRIOR_SESSION_WINDOW),
    "opening_window": list(OPENING_WINDOW),
    "parent_shadow_variant_id": parent_manifest.STRATEGY_VARIANT_ID,
}


def verify_code_hashes() -> None:
    parent_manifest.verify_code_hashes()
    path = Path(__file__).with_name("manual_regime_failed_countermove_fresh_stop.py")
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != FRESH_STOP_CODE_SHA256:
        raise RuntimeError(
            "fresh-stop code differs from immutable manual-regime V3 manifest; "
            "create a new strategy/rule version"
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
        "stop_target_logic_changed": True,
        "fresh_stop_code_sha256": FRESH_STOP_CODE_SHA256,
        "fresh_stop_policy": FRESH_STOP_POLICY,
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
