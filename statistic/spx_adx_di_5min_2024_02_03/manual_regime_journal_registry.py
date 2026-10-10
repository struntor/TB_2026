from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import manual_regime_version_manifest as manifest
import manual_regime_sizing_v2_manifest as sizing_v2
import manual_regime_fresh_stop_v3_manifest as fresh_stop_v3
import manual_regime_gap_quarter_v4_manifest as gap_quarter_v4
from src.journal.models import (
    RuleDefinitionRecord,
    RuleVersionRecord,
    StrategyVariantRecord,
    StrategyVariantRuleRecord,
    deterministic_id,
)


def rule_version_id(rule_id: str, semver: str) -> str:
    return deterministic_id("rule-version", rule_id, semver)


def _logic_hash(rule_id: str) -> str:
    identity = f"{manifest.ENGINE_CODE_SHA256}:{rule_id}:{manifest.RULE_VERSION}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def verify_baseline_code_hash() -> None:
    engine_path = Path(__file__).with_name("manual_regime_rules_backtest.py")
    actual = hashlib.sha256(engine_path.read_bytes()).hexdigest()
    if actual != manifest.ENGINE_CODE_SHA256:
        raise RuntimeError(
            "manual-regime engine code differs from the immutable baseline; "
            "create a new engine/rule/strategy variant version before running it"
        )


def register_baseline_journal_manifest(store: Any) -> str:
    """Idempotently register the immutable baseline before starting a run."""
    verify_baseline_code_hash()
    memberships: list[StrategyVariantRuleRecord] = []
    with store.transaction():
        for position, rule in enumerate(manifest.RULES):
            store.register_rule(
                RuleDefinitionRecord(
                    rule_id=rule.rule_id,
                    name=rule.display_name,
                    description=(
                        "Frozen manual-regime baseline rule; its implementation "
                        "is anchored by the engine source hash."
                    ),
                    metadata={"category": rule.category},
                )
            )
            version_id = rule_version_id(rule.rule_id, rule.version)
            store.register_rule_version(
                RuleVersionRecord(
                    rule_version_id=version_id,
                    rule_id=rule.rule_id,
                    semver=rule.version,
                    logic_hash=_logic_hash(rule.rule_id),
                    code_hash=manifest.ENGINE_CODE_SHA256,
                    engine_version=manifest.ENGINE_VERSION,
                    created_at_utc=manifest.SNAPSHOT_CREATED_AT_UTC,
                    parameters={},
                    metadata={"manifest_hash": manifest.manifest_hash()},
                )
            )
            memberships.append(
                StrategyVariantRuleRecord(
                    variant_id=manifest.STRATEGY_VARIANT_ID,
                    rule_id=rule.rule_id,
                    rule_version_id=version_id,
                    position=position,
                    role=rule.category,
                    enabled=True,
                    parameters={},
                )
            )
        return store.register_strategy_variant(
            StrategyVariantRecord(
                variant_id=manifest.STRATEGY_VARIANT_ID,
                strategy_id=manifest.STRATEGY_ID,
                variant_version="1.0.0",
                name="Manual regime baseline 1.0.0",
                engine_version=manifest.ENGINE_VERSION,
                money_management_version=manifest.MONEY_MANAGEMENT_VERSION,
                config_hash=manifest.manifest_hash(),
                data_revision="run-specific",
                fee_model="run-specific",
                slippage_model="run-specific",
                created_at_utc=manifest.SNAPSHOT_CREATED_AT_UTC,
                metadata={
                    "immutable_snapshot": True,
                    "engine_code_sha256": manifest.ENGINE_CODE_SHA256,
                },
            ),
            memberships,
        )


def register_regime_sizing_v2_journal_manifest(store: Any) -> str:
    """Register V2 while preserving the immutable V1 rule definitions."""
    register_baseline_journal_manifest(store)
    sizing_v2.verify_code_hashes()
    memberships = [
        StrategyVariantRuleRecord(
            variant_id=sizing_v2.STRATEGY_VARIANT_ID,
            rule_id=rule.rule_id,
            rule_version_id=rule_version_id(rule.rule_id, rule.version),
            position=position,
            role=rule.category,
            enabled=True,
            parameters={},
        )
        for position, rule in enumerate(manifest.RULES)
    ]
    with store.transaction():
        return store.register_strategy_variant(
            StrategyVariantRecord(
                variant_id=sizing_v2.STRATEGY_VARIANT_ID,
                strategy_id=sizing_v2.STRATEGY_ID,
                variant_version=sizing_v2.VARIANT_VERSION,
                name="Manual regime baseline 2.0.0 - graded regime/volume sizing",
                engine_version=sizing_v2.ENGINE_VERSION,
                money_management_version=sizing_v2.MONEY_MANAGEMENT_VERSION,
                config_hash=sizing_v2.manifest_hash(),
                data_revision="run-specific",
                fee_model="run-specific",
                slippage_model="run-specific",
                created_at_utc=sizing_v2.SNAPSHOT_CREATED_AT_UTC,
                metadata={
                    "immutable_snapshot": True,
                    "parent_variant_id": manifest.STRATEGY_VARIANT_ID,
                    "parent_manifest_hash": manifest.manifest_hash(),
                    "position_sizing_code_sha256": (
                        sizing_v2.POSITION_SIZING_CODE_SHA256
                    ),
                    "cluster_score_code_sha256": (
                        sizing_v2.CLUSTER_SCORE_CODE_SHA256
                    ),
                    "legacy_shadow_variant_id": manifest.STRATEGY_VARIANT_ID,
                },
            ),
            memberships,
        )


def register_fresh_stop_v3_journal_manifest(store: Any) -> str:
    """Register V3 and the revised failed-countermove rule version."""
    register_regime_sizing_v2_journal_manifest(store)
    fresh_stop_v3.verify_code_hashes()
    revised_rule_id = fresh_stop_v3.FRESH_STOP_POLICY["affected_rule"]
    revised_version = fresh_stop_v3.RULE_VERSIONS[revised_rule_id]
    revised_logic_hash = hashlib.sha256(
        (
            f"{fresh_stop_v3.FRESH_STOP_CODE_SHA256}:"
            f"{revised_rule_id}:{revised_version}"
        ).encode("utf-8")
    ).hexdigest()
    memberships: list[StrategyVariantRuleRecord] = []
    with store.transaction():
        store.register_rule_version(
            RuleVersionRecord(
                rule_version_id=rule_version_id(revised_rule_id, revised_version),
                rule_id=revised_rule_id,
                semver=revised_version,
                logic_hash=revised_logic_hash,
                code_hash=fresh_stop_v3.FRESH_STOP_CODE_SHA256,
                engine_version=fresh_stop_v3.ENGINE_VERSION,
                created_at_utc=fresh_stop_v3.SNAPSHOT_CREATED_AT_UTC,
                parameters=fresh_stop_v3.FRESH_STOP_POLICY,
                metadata={"manifest_hash": fresh_stop_v3.manifest_hash()},
            )
        )
        for position, rule in enumerate(manifest.RULES):
            version = fresh_stop_v3.RULE_VERSIONS[rule.rule_id]
            memberships.append(
                StrategyVariantRuleRecord(
                    variant_id=fresh_stop_v3.STRATEGY_VARIANT_ID,
                    rule_id=rule.rule_id,
                    rule_version_id=rule_version_id(rule.rule_id, version),
                    position=position,
                    role=rule.category,
                    enabled=True,
                    parameters=(
                        fresh_stop_v3.FRESH_STOP_POLICY
                        if rule.rule_id == revised_rule_id
                        else {}
                    ),
                )
            )
        return store.register_strategy_variant(
            StrategyVariantRecord(
                variant_id=fresh_stop_v3.STRATEGY_VARIANT_ID,
                strategy_id=fresh_stop_v3.STRATEGY_ID,
                variant_version=fresh_stop_v3.VARIANT_VERSION,
                name="Manual regime baseline 3.0.0 - fresh failed-countermove stop",
                engine_version=fresh_stop_v3.ENGINE_VERSION,
                money_management_version=fresh_stop_v3.MONEY_MANAGEMENT_VERSION,
                config_hash=fresh_stop_v3.manifest_hash(),
                data_revision="run-specific",
                fee_model="run-specific",
                slippage_model="run-specific",
                created_at_utc=fresh_stop_v3.SNAPSHOT_CREATED_AT_UTC,
                metadata={
                    "immutable_snapshot": True,
                    "parent_variant_id": sizing_v2.STRATEGY_VARIANT_ID,
                    "parent_manifest_hash": sizing_v2.manifest_hash(),
                    "fresh_stop_code_sha256": fresh_stop_v3.FRESH_STOP_CODE_SHA256,
                    "fresh_stop_policy": fresh_stop_v3.FRESH_STOP_POLICY,
                },
            ),
            memberships,
        )


def register_gap_quarter_v4_journal_manifest(store: Any) -> str:
    """Register V4 while retaining V3 entry, stop and rule versions."""
    register_fresh_stop_v3_journal_manifest(store)
    gap_quarter_v4.verify_code_hashes()
    memberships = [
        StrategyVariantRuleRecord(
            variant_id=gap_quarter_v4.STRATEGY_VARIANT_ID,
            rule_id=rule.rule_id,
            rule_version_id=rule_version_id(
                rule.rule_id,
                gap_quarter_v4.RULE_VERSIONS[rule.rule_id],
            ),
            position=position,
            role=rule.category,
            enabled=True,
            parameters=(
                gap_quarter_v4.GAP_BREAKDOWN_QUARTER_POLICY
                if rule.rule_id
                == gap_quarter_v4.GAP_BREAKDOWN_QUARTER_POLICY["affected_rule"]
                else {}
            ),
        )
        for position, rule in enumerate(manifest.RULES)
    ]
    with store.transaction():
        return store.register_strategy_variant(
            StrategyVariantRecord(
                variant_id=gap_quarter_v4.STRATEGY_VARIANT_ID,
                strategy_id=gap_quarter_v4.STRATEGY_ID,
                variant_version=gap_quarter_v4.VARIANT_VERSION,
                name=(
                    "Manual regime baseline 4.0.0 - gap-breakdown quarter sizing"
                ),
                engine_version=gap_quarter_v4.ENGINE_VERSION,
                money_management_version=(
                    gap_quarter_v4.MONEY_MANAGEMENT_VERSION
                ),
                config_hash=gap_quarter_v4.manifest_hash(),
                data_revision="run-specific",
                fee_model="run-specific",
                slippage_model="run-specific",
                created_at_utc=gap_quarter_v4.SNAPSHOT_CREATED_AT_UTC,
                metadata={
                    "immutable_snapshot": True,
                    "parent_variant_id": fresh_stop_v3.STRATEGY_VARIANT_ID,
                    "parent_manifest_hash": fresh_stop_v3.manifest_hash(),
                    "gap_breakdown_quarter_code_sha256": (
                        gap_quarter_v4.GAP_BREAKDOWN_QUARTER_CODE_SHA256
                    ),
                    "gap_breakdown_quarter_policy": (
                        gap_quarter_v4.GAP_BREAKDOWN_QUARTER_POLICY
                    ),
                },
            ),
            memberships,
        )
