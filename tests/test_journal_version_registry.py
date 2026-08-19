from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sqlite3
import unittest

from src.journal.models import (
    RuleDefinitionRecord,
    RuleVersionRecord,
    SignalDecisionRecord,
    StrategyRunRecord,
    StrategyVariantRecord,
    StrategyVariantRuleRecord,
    deterministic_id,
)
from src.journal.store import (
    IdentityConflictError,
    JournalStore,
    _MIGRATION_1,
)


TEST_DB = Path(r"D:\Trading\journal_version_registry_test.sqlite")
UPGRADE_DB = Path(r"D:\Trading\journal_version_upgrade_test.sqlite")


def cleanup(path: Path) -> None:
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(f"{path}{suffix}")
        if candidate.exists():
            candidate.unlink()


class JournalVersionRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        cleanup(TEST_DB)
        self.store = JournalStore(TEST_DB)

    def tearDown(self) -> None:
        self.store.close()
        cleanup(TEST_DB)

    @staticmethod
    def rule() -> RuleDefinitionRecord:
        return RuleDefinitionRecord(
            rule_id="entry.crash_repair_long",
            name="Crash repair long",
            description="Long repair entry after a qualified crash regime.",
            metadata={"owner": "manual-regime"},
        )

    @classmethod
    def rule_version(cls) -> RuleVersionRecord:
        rule = cls.rule()
        return RuleVersionRecord(
            rule_version_id=deterministic_id("rule-version", rule.rule_id, "1.0.0"),
            rule_id=rule.rule_id,
            semver="1.0.0",
            logic_hash="sha256:logic-v1",
            code_hash="git:baseline",
            engine_version="manual-regime-engine@1.0.0",
            created_at_utc="2026-07-15T00:00:00Z",
            parameters={"repair_days": 3},
            metadata={},
        )

    @classmethod
    def variant(cls) -> tuple[StrategyVariantRecord, StrategyVariantRuleRecord]:
        version = cls.rule_version()
        variant_id = "manual-regime-baseline@1.0.0"
        return (
            StrategyVariantRecord(
                variant_id=variant_id,
                strategy_id="manual-regime",
                variant_version="1.0.0",
                name="Manual regime baseline",
                engine_version="manual-regime-engine@1.0.0",
                money_management_version="fixed-risk-per-trade@1.0.0",
                config_hash="sha256:config-v1",
                data_revision="prices-2026-07-15",
                fee_model="ibkr-tiered@2026-07",
                slippage_model="next-open-plus-1bp@1.0.0",
                created_at_utc="2026-07-15T00:00:00Z",
                metadata={"status": "baseline"},
            ),
            StrategyVariantRuleRecord(
                variant_id=variant_id,
                rule_id=version.rule_id,
                rule_version_id=version.rule_version_id,
                position=0,
                role="entry",
                enabled=True,
                parameters={"weight": 1},
            ),
        )

    def register_baseline(self) -> tuple[StrategyVariantRecord, RuleVersionRecord]:
        rule = self.rule()
        version = self.rule_version()
        variant, membership = self.variant()
        self.store.register_rule(rule)
        self.store.register_rule_version(version)
        self.store.register_strategy_variant(variant, [membership])
        return variant, version

    def test_registry_is_idempotent_and_queryable(self) -> None:
        variant, version = self.register_baseline()
        self.register_baseline()

        self.assertEqual(self.store.count("rules"), 1)
        self.assertEqual(self.store.count("rule_versions"), 1)
        self.assertEqual(self.store.count("strategy_variants"), 1)
        self.assertEqual(self.store.count("strategy_variant_rules"), 1)
        self.assertEqual(self.store.get_rule(version.rule_id)["name"], "Crash repair long")
        self.assertEqual(
            self.store.get_strategy_variant(variant.variant_id)["engine_version"],
            "manual-regime-engine@1.0.0",
        )
        self.assertEqual(
            self.store.list_strategy_variant_rules(variant.variant_id)[0]["role"],
            "entry",
        )

    def test_rule_and_variant_semantics_are_immutable(self) -> None:
        variant, version = self.register_baseline()

        with self.assertRaises(IdentityConflictError):
            self.store.register_rule(replace(self.rule(), description="changed"))
        with self.assertRaises(IdentityConflictError):
            self.store.register_rule_version(replace(version, logic_hash="sha256:changed"))
        with self.assertRaises(IdentityConflictError):
            self.store.register_strategy_variant(
                replace(variant, slippage_model="perfect-fill@1.0.0"),
                [self.variant()[1]],
            )
        with self.assertRaises(IdentityConflictError):
            self.store.register_strategy_variant(variant, [])

    def test_runs_and_decisions_can_reference_exact_versions(self) -> None:
        variant, version = self.register_baseline()
        run = StrategyRunRecord(
            run_id="backtest:baseline:2026-07-15",
            strategy_id="manual-regime",
            strategy_version="1.0.0",
            config_hash="sha256:config-v1",
            run_type="backtest",
            environment="test",
            started_at_utc="2026-07-15T08:00:00Z",
            ended_at_utc=None,
            status="running",
            parameters={},
            metadata={},
            variant_id=variant.variant_id,
        )
        self.store.start_run(run)
        decision = SignalDecisionRecord(
            decision_id="decision:baseline:1",
            run_id=run.run_id,
            signal_key="signal:1",
            decision_key="decision-key:1",
            signal_key_version="1",
            decision_stage="engine",
            strategy_id=run.strategy_id,
            strategy_version=run.strategy_version,
            engine_version=variant.engine_version,
            config_hash=run.config_hash,
            decision_time_utc="2026-07-15T09:40:00Z",
            symbol="ALV",
            primary_rule=version.rule_id,
            side="long",
            track="repair",
            occurrence=0,
            status="accepted",
            trade_action="open_long",
            setup_state="ready",
            primary_reason="test",
            context={},
            raw_record={},
            rule_id=version.rule_id,
            rule_version_id=version.rule_version_id,
        )
        self.store.upsert_decision(decision)

        self.assertEqual(self.store.get_run(run.run_id)["variant_id"], variant.variant_id)
        self.assertEqual(
            self.store.get_decision(run.run_id, "signal:1")["rule_version_id"],
            version.rule_version_id,
        )
        with self.assertRaises(IdentityConflictError):
            self.store.start_run(replace(run, variant_id=None))
        with self.assertRaises(ValueError):
            self.store.upsert_decision(replace(decision, rule_id=None))

    def test_v1_database_is_upgraded_without_data_loss(self) -> None:
        self.store.close()
        cleanup(UPGRADE_DB)
        connection = sqlite3.connect(UPGRADE_DB)
        try:
            connection.execute(
                "CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, applied_at_utc TEXT NOT NULL)"
            )
            for statement in _MIGRATION_1:
                connection.execute(statement)
            connection.execute(
                "INSERT INTO schema_migrations VALUES (1, '2026-07-15T00:00:00Z')"
            )
            connection.execute(
                """
                INSERT INTO strategy_runs VALUES (
                    'legacy-run', 'legacy-strategy', 'v1', 'legacy-config',
                    'backtest', 'test', '2026-07-14T00:00:00Z', NULL,
                    'completed', '{}', '{}'
                )
                """
            )
            connection.commit()
        finally:
            connection.close()

        upgraded = JournalStore(UPGRADE_DB)
        try:
            self.assertEqual(upgraded.schema_version, 2)
            self.assertEqual(upgraded.get_run("legacy-run")["strategy_id"], "legacy-strategy")
            self.assertIsNone(upgraded.get_run("legacy-run")["variant_id"])
            self.assertEqual(upgraded.count("strategy_variants"), 0)
            self.assertEqual(upgraded.pragma("foreign_keys"), 1)
        finally:
            upgraded.close()
            cleanup(UPGRADE_DB)
        self.store = JournalStore(TEST_DB)


if __name__ == "__main__":
    unittest.main()
