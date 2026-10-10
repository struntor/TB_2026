from __future__ import annotations

from types import SimpleNamespace
import unittest

import pandas as pd

import decision_records as records
import live_manual_regime_tws_bot as live
import manual_regime_fresh_stop_v3_manifest as manifest
from manual_regime_journal_registry import register_fresh_stop_v3_journal_manifest
from manual_regime_failed_countermove_fresh_stop import (
    POLICY_VERSION,
    TARGET_RULE,
    apply_fresh_structure_stop,
    calculate_fresh_stop,
    extract_structure_references,
)
from src.journal.store import JournalStore


class FailedCountermoveFreshStopTests(unittest.TestCase):
    day = pd.Timestamp("2026-08-13")

    @staticmethod
    def amzn_bars() -> pd.DataFrame:
        return pd.DataFrame(
            [
                {"date": "2026-08-12 12:00", "high": 268.80},
                {"date": "2026-08-12 15:55", "high": 269.45},
                {"date": "2026-08-13 09:30", "high": 268.23},
                {"date": "2026-08-13 09:35", "high": 268.10},
            ]
        )

    @staticmethod
    def engine_record() -> records.DecisionRecord:
        trade = SimpleNamespace(
            track="baseline",
            rule=TARGET_RULE,
            side="short",
            entry_date=pd.Timestamp("2026-08-13 09:35"),
            entry_price=268.04,
            stop=269.3396428571429,
            target=262.63,
            size=1.0,
            regime_at_entry="bearish_confirmed",
            labels="",
            entry_labels="",
        )
        return records.build_decision_records(
            symbol="AMZN",
            decision_time="2026-08-13 09:35",
            strategy_version="test-v1",
            config_hash=records.stable_config_hash({"test": True}),
            checks={TARGET_RULE: "trade"},
            explanation={
                "trade_action": "trade",
                "setup_state": "trade_opened",
                "primary_reason": TARGET_RULE,
                "market_phase": "test",
                "volume_price_phase": "test",
                "volume_price_detail": "test",
            },
            context={
                "day": "2026-08-13",
                "decision_price": 268.04,
                "atr14": 9.792857142857136,
                "atr_tolerance": 0.4896428571428568,
                "failed_countermove_stop": 269.3396428571429,
                "failed_countermove_target": 262.63,
            },
            opened_trades=[trade],
        )[0]

    def test_amzn_fresh_stop_matches_preregistered_backtest_variant(self) -> None:
        decision = calculate_fresh_stop(
            entry=268.04,
            original_stop=269.3396428571429,
            target=262.63,
            tolerance=0.4896428571428568,
            prior_afternoon_high=269.45,
            opening_high=268.23,
        )

        self.assertTrue(decision.accepted)
        self.assertAlmostEqual(269.93964285714287, decision.stop)
        self.assertAlmostEqual(2.847903741304779, decision.crv)
        self.assertEqual(52, int(100.0 // decision.risk))

    def test_structure_references_use_only_prior_afternoon_and_two_opening_bars(self) -> None:
        prior_high, opening_high = extract_structure_references(
            self.amzn_bars(),
            self.day,
        )
        self.assertEqual(269.45, prior_high)
        self.assertEqual(268.23, opening_high)

    def test_crv_below_one_vetoes_trade(self) -> None:
        decision = calculate_fresh_stop(
            entry=100.0,
            original_stop=101.0,
            target=99.0,
            tolerance=0.5,
            prior_afternoon_high=102.0,
            opening_high=100.5,
        )
        self.assertFalse(decision.accepted)
        self.assertLess(decision.crv, 1.0)

    def test_apply_updates_stop_and_audit_record(self) -> None:
        record = self.engine_record()
        trades = records.attach_record_keys_to_trades(
            pd.DataFrame(
                [
                    {
                        "symbol": "AMZN",
                        "track": "baseline",
                        "rule": TARGET_RULE,
                        "side": "short",
                        "entry": 268.04,
                        "stop": 269.3396428571429,
                        "target": 262.63,
                        "size": 1.0,
                    }
                ]
            ),
            [record],
        )

        adjusted, updated = apply_fresh_structure_stop(
            trades,
            [record],
            self.day,
            {"AMZN": self.amzn_bars()},
        )

        self.assertEqual(1, len(adjusted))
        self.assertAlmostEqual(269.93964285714287, adjusted.iloc[0]["stop"])
        self.assertAlmostEqual(
            269.3396428571429,
            adjusted.iloc[0]["legacy_v2_stop"],
        )
        self.assertEqual(POLICY_VERSION, adjusted.iloc[0]["fresh_stop_policy_version"])
        self.assertEqual("accepted", updated[0].status)
        self.assertEqual(
            "failed_countermove_fresh_structure_stop_v1",
            updated[0].factors[-1].factor_code,
        )
        self.assertAlmostEqual(
            269.93964285714287,
            float(updated[0].context["failed_countermove_stop"]),
        )

    def test_v3_manifest_hashes_are_current(self) -> None:
        manifest.verify_code_hashes()
        self.assertEqual("1.1.0", manifest.RULE_VERSIONS[TARGET_RULE])

    def test_v3_journal_manifest_registration_is_idempotent(self) -> None:
        with JournalStore(":memory:") as store:
            first = register_fresh_stop_v3_journal_manifest(store)
            second = register_fresh_stop_v3_journal_manifest(store)
        self.assertEqual(manifest.STRATEGY_VARIANT_ID, first)
        self.assertEqual(first, second)

    def test_v2_parent_shadow_keeps_old_stop_and_v2_size(self) -> None:
        row = pd.Series(
            {
                "entry": 268.04,
                "stop": 269.93964285714287,
                "legacy_v2_stop": 269.3396428571429,
                "size": 0.5,
                "legacy_shadow_size": 1.0,
            }
        )
        self.assertEqual(26, live.quantity_for_trade(row, 100.0))
        self.assertEqual(38, live.parent_v2_shadow_quantity_for_trade(row, 100.0))
        self.assertEqual(76, live.legacy_shadow_quantity_for_trade(row, 100.0))


if __name__ == "__main__":
    unittest.main()
