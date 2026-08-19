from __future__ import annotations

import json
import sqlite3
import unittest

from src.journal.variant_compare import IncompatibleVariantError, compare_runs


class VariantCompareTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(
            """
            CREATE TABLE strategy_runs (
                run_id TEXT PRIMARY KEY,
                strategy_id TEXT,
                strategy_version TEXT,
                config_hash TEXT,
                run_type TEXT,
                environment TEXT,
                started_at_utc TEXT,
                ended_at_utc TEXT,
                status TEXT,
                parameters_json TEXT,
                metadata_json TEXT,
                variant_id TEXT
            );
            CREATE TABLE strategy_variants (
                variant_id TEXT PRIMARY KEY,
                strategy_id TEXT,
                variant_version TEXT,
                name TEXT,
                engine_version TEXT,
                money_management_version TEXT,
                config_hash TEXT,
                data_revision TEXT,
                fee_model TEXT,
                slippage_model TEXT,
                created_at_utc TEXT,
                metadata_json TEXT
            );
            CREATE TABLE trades (
                trade_id TEXT PRIMARY KEY,
                run_id TEXT,
                signal_key TEXT,
                decision_id TEXT,
                asset_class TEXT,
                symbol TEXT,
                side TEXT,
                status TEXT,
                opened_at_utc TEXT,
                closed_at_utc TEXT,
                quantity REAL,
                currency TEXT,
                entry_price REAL,
                exit_price REAL,
                realized_pnl REAL,
                metadata_json TEXT
            );
            CREATE TABLE signal_decisions (
                decision_id TEXT PRIMARY KEY,
                run_id TEXT,
                signal_key TEXT,
                primary_rule TEXT,
                strategy_version TEXT,
                rule_id TEXT,
                rule_version_id TEXT
            );
            """
        )
        self._variant("old-variant", data_revision="bars-v1")
        self._variant("new-variant", data_revision="bars-v1")
        self._run("old-run", "old-variant")
        self._run("new-run", "new-variant")

    def tearDown(self) -> None:
        self.connection.close()

    @staticmethod
    def _provenance(starting_capital: float = 10_000) -> dict:
        return {
            "comparison_provenance": {
                "period_start": "2025-01-01T00:00:00Z",
                "period_end": "2025-02-01T00:00:00Z",
                "starting_capital": starting_capital,
                "capital_currency": "USD",
            }
        }

    def _variant(
        self,
        variant_id: str,
        *,
        data_revision: str,
        fee_model: str = "ibkr-fixed-v1",
        slippage_model: str = "close-5bp-v1",
        money_management: str = "fixed-risk-v1",
    ) -> None:
        self.connection.execute(
            "INSERT INTO strategy_variants VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                variant_id, "strategy", "1", variant_id, "engine-v1",
                money_management, f"cfg-{variant_id}", data_revision,
                fee_model, slippage_model, "2025-01-01T00:00:00Z", "{}",
            ),
        )

    def _run(
        self,
        run_id: str,
        variant_id: str,
        *,
        starting_capital: float = 10_000,
    ) -> None:
        self.connection.execute(
            "INSERT INTO strategy_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run_id, "strategy", "1", f"cfg-{run_id}", "backtest",
                "historical", "2025-01-01T00:00:00Z", "2025-02-01T00:00:00Z",
                "completed", json.dumps(self._provenance(starting_capital)), "{}",
                variant_id,
            ),
        )

    def _trade(
        self,
        run_id: str,
        signal_key: str,
        *,
        pnl: float,
        opened: str,
        closed: str,
        entry: float | None = 100,
        exit_price: float | None = 101,
        quantity: float | None = 10,
        asset_class: str = "STK",
        metadata: dict | None = None,
        rule_id: str = "rule-a",
        rule_version_id: str = "rule-a-v1",
    ) -> None:
        trade_id = f"{run_id}:{signal_key}"
        decision_id = f"decision:{run_id}:{signal_key}"
        self.connection.execute(
            "INSERT INTO trades VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                trade_id, run_id, signal_key, decision_id, asset_class, "TEST", "long",
                "closed", opened, closed, quantity, "USD", entry, exit_price, pnl,
                json.dumps(metadata or {}),
            ),
        )
        self.connection.execute(
            "INSERT INTO signal_decisions VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                decision_id, run_id, signal_key, rule_id, "strategy-v1",
                rule_id, rule_version_id,
            ),
        )

    def _populate_comparable(self) -> None:
        self._trade(
            "old-run", "common", pnl=100,
            opened="2025-01-02T00:00:00Z", closed="2025-01-05T00:00:00Z",
            entry=100, exit_price=110, quantity=10,
            rule_id="rule-a", rule_version_id="rule-a-v1",
        )
        self._trade(
            "old-run", "old-only", pnl=-50,
            opened="2025-01-06T00:00:00Z", closed="2025-01-10T00:00:00Z",
            rule_id="rule-b", rule_version_id="rule-b-v1",
        )
        self._trade(
            "new-run", "common", pnl=120,
            opened="2025-01-02T00:00:00Z", closed="2025-01-05T00:00:00Z",
            entry=101, exit_price=112, quantity=12,
            rule_id="rule-a", rule_version_id="rule-a-v2",
        )
        self._trade(
            "new-run", "new-only", pnl=80,
            opened="2025-01-12T00:00:00Z", closed="2025-01-15T00:00:00Z",
            rule_id="rule-c", rule_version_id="rule-c-v1",
        )

    def test_equity_trade_deltas_rules_drawdown_turnover_and_exposure(self) -> None:
        self._populate_comparable()

        result = compare_runs(
            self.connection, "old-run", "new-run", allow_incompatible=False
        )

        self.assertEqual((result.old_metrics.total_pnl, result.new_metrics.total_pnl), (50, 200))
        self.assertEqual(result.old_metrics.max_drawdown, -50)
        self.assertEqual(result.metric_delta["total_pnl"], 150)
        self.assertEqual(
            {delta.signal_key: delta.membership for delta in result.trade_deltas},
            {"common": "common", "old-only": "old_only", "new-only": "new_only"},
        )
        common = next(delta for delta in result.trade_deltas if delta.signal_key == "common")
        self.assertEqual((common.entry_price_delta, common.pnl_delta, common.quantity_delta), (1, 20, 2))
        contributions = {
            (item.rule_id, item.rule_version_id): (item.old_pnl, item.new_pnl)
            for item in result.rule_contributions
        }
        self.assertEqual(contributions[("rule-a", "rule-a-v1")], (100, 0))
        self.assertEqual(contributions[("rule-a", "rule-a-v2")], (0, 120))
        self.assertTrue(result.old_turnover.available)
        self.assertTrue(result.new_exposure.available)

    def test_data_revision_mismatch_fails_closed_and_override_is_audited(self) -> None:
        self.connection.execute(
            "UPDATE strategy_variants SET data_revision='bars-v2' WHERE variant_id='new-variant'"
        )

        with self.assertRaisesRegex(IncompatibleVariantError, "data_revision"):
            compare_runs(self.connection, "old-run", "new-run", allow_incompatible=False)
        overridden = compare_runs(
            self.connection, "old-run", "new-run", allow_incompatible=True
        )
        self.assertTrue(overridden.compatibility_overridden)
        self.assertTrue(any("data_revision" in issue for issue in overridden.compatibility_issues))

    def test_cost_slippage_and_money_management_mismatches_fail_closed(self) -> None:
        for field, value in (
            ("fee_model", "other-fees"),
            ("slippage_model", "other-slippage"),
            ("money_management_version", "other-mm"),
        ):
            with self.subTest(field=field):
                self.connection.execute(
                    f"UPDATE strategy_variants SET {field}=? WHERE variant_id='new-variant'",
                    (value,),
                )
                with self.assertRaisesRegex(IncompatibleVariantError, field):
                    compare_runs(
                        self.connection, "old-run", "new-run", allow_incompatible=False
                    )
                self.connection.execute(
                    f"UPDATE strategy_variants SET {field}=(SELECT {field} FROM strategy_variants WHERE variant_id='old-variant') WHERE variant_id='new-variant'"
                )

    def test_starting_capital_must_match_even_with_override(self) -> None:
        self.connection.execute("DELETE FROM strategy_runs WHERE run_id='new-run'")
        self._run("new-run", "new-variant", starting_capital=20_000)

        with self.assertRaisesRegex(IncompatibleVariantError, "starting_capital"):
            compare_runs(self.connection, "old-run", "new-run", allow_incompatible=True)

    def test_turnover_and_exposure_are_explicitly_unavailable(self) -> None:
        self._trade(
            "old-run", "option-old", pnl=10,
            opened="2025-01-02T00:00:00Z", closed="2025-01-03T00:00:00Z",
            asset_class="OPT", entry=2, exit_price=3, quantity=1, metadata={},
        )
        self._trade(
            "new-run", "option-new", pnl=20,
            opened="2025-01-02T00:00:00Z", closed="2025-01-03T00:00:00Z",
            asset_class="OPT", entry=2, exit_price=4, quantity=1, metadata={},
        )

        result = compare_runs(
            self.connection, "old-run", "new-run", allow_incompatible=False
        )

        self.assertFalse(result.old_turnover.available)
        self.assertIn("incomplete notional", result.old_turnover.reason)
        self.assertFalse(result.new_exposure.available)
        self.assertIn("incomplete interval/notional", result.new_exposure.reason)

    def test_closed_trade_without_realized_pnl_fails_closed(self) -> None:
        self._trade(
            "old-run", "missing-pnl", pnl=None,
            opened="2025-01-02T00:00:00Z", closed="2025-01-03T00:00:00Z",
        )

        with self.assertRaisesRegex(ValueError, "missing-pnl"):
            compare_runs(self.connection, "old-run", "new-run", allow_incompatible=False)

    def test_trade_outside_declared_period_fails_closed(self) -> None:
        self._trade(
            "old-run", "outside", pnl=10,
            opened="2025-02-02T00:00:00Z", closed="2025-02-03T00:00:00Z",
        )

        with self.assertRaisesRegex(IncompatibleVariantError, "outside comparison period"):
            compare_runs(self.connection, "old-run", "new-run", allow_incompatible=False)


if __name__ == "__main__":
    unittest.main()
