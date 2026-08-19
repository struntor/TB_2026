from __future__ import annotations

from pathlib import Path
import unittest

import pandas as pd
from ib_insync import Contract

from src.execution.spxw_0dte_iu30 import (
    ComboLegSelection,
    Spxw0DteSelection,
    build_combo,
    build_entry_order,
)
from src.execution.spxw_put_credit import OptionRiskQuote
from src.journal.decision_ingest import DecisionIngestContext, map_decision_record
from src.strategies.spxw_0dte_bull_put_iu30 import Spxw0DteConfig, evaluate_entry
from src.strategies.spxw_0dte_bull_put_iu30_journal import decision_payload


def _quote(bid: float, ask: float) -> OptionRiskQuote:
    return OptionRiskQuote(bid, ask, -0.2, 0.01, -1.0, 2.0, 0.2)


class Spxw0DteStrategyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.raw_path = Path(r"D:\Trading\journal\spxw_0dte_strategy_test_spx.csv")
        self.raw_path.parent.mkdir(parents=True, exist_ok=True)
        self.raw_path.unlink(missing_ok=True)

    def tearDown(self) -> None:
        self.raw_path.unlink(missing_ok=True)

    def test_0940_context_accepts_breadth_and_strict_overlay(self) -> None:
        days = pd.bdate_range("2025-12-01", periods=100)
        raw = pd.DataFrame(
            {
                "date": days + pd.Timedelta(hours=16),
                "high": 6002.0,
                "low": 5998.0,
                "close": 6000.0,
            }
        )
        raw.to_csv(self.raw_path, index=False)
        target = days[-1] + pd.offsets.BDay(1)
        records = [
                {
                    "symbol": "AAA",
                    "track": "decision",
                    "primary_rule": "__decision__",
                    "context": {
                        "regime": "bearish_countermove",
                        "big_picture_regime": "bearish_primary",
                        "daily_structure_phase": "trend_or_unclassified",
                        "profile_price_location": "above_value",
                        "volume_price_phase": "normal_volume_trend",
                    },
                },
                {
                    "symbol": "SPX",
                    "track": "decision",
                    "primary_rule": "__decision__",
                    "context": {
                        "decision_price": 6005.0,
                        "regime": "bullish_tier3",
                        "big_picture_regime": "bullish_primary",
                        "profile_price_location": "inside_value_upper",
                    },
                },
            ]
        result = evaluate_entry(records, ["AAA"], target, self.raw_path)
        self.assertTrue(result.accepted)
        self.assertEqual(("AAA",), result.breadth_symbols)
        self.assertTrue(result.iu30_overlay)
        self.assertAlmostEqual(4.0, result.prior_atr14)
        record, factors = map_decision_record(
            decision_payload(result, Spxw0DteConfig()),
            DecisionIngestContext("test-run", "paper"),
        )
        self.assertEqual("accepted", record.status)
        self.assertEqual(6, len(factors))

        aware_result = evaluate_entry(
            records,
            ["AAA"],
            target.tz_localize("America/New_York"),
            self.raw_path,
        )
        self.assertTrue(aware_result.accepted)

    def test_missing_breadth_is_no_setup(self) -> None:
        days = pd.bdate_range("2025-12-01", periods=100)
        pd.DataFrame(
            {"date": days, "high": 6002.0, "low": 5998.0, "close": 6000.0}
        ).to_csv(self.raw_path, index=False)
        records = [{
            "symbol": "SPX", "track": "decision", "primary_rule": "__decision__",
            "context": {"decision_price": 6000.0, "regime": "neutral", "big_picture_regime": "neutral"},
        }]
        result = evaluate_entry(records, ["AAA"], days[-1] + pd.offsets.BDay(1), self.raw_path)
        self.assertFalse(result.accepted)
        self.assertEqual("no_setup", result.status)
        self.assertEqual("top50_breadth_context", result.reason)


class Spxw0DteExecutionTests(unittest.TestCase):
    def _selection(self) -> Spxw0DteSelection:
        descriptors = (
            ("short_put", "SELL", 1, "P", 5900.0, _quote(2.0, 2.2)),
            ("long_put", "BUY", 1, "P", 5880.0, _quote(0.4, 0.5)),
            ("fly_lower", "BUY", 1, "C", 5940.0, _quote(3.8, 4.0)),
            ("fly_body", "SELL", 2, "C", 5970.0, _quote(2.0, 2.2)),
            ("fly_upper", "BUY", 1, "C", 6000.0, _quote(0.8, 1.0)),
        )
        legs = tuple(
            ComboLegSelection(role, action, ratio, right, strike, Contract(conId=index + 1), quote)
            for index, (role, action, ratio, right, strike, quote) in enumerate(descriptors)
        )
        return Spxw0DteSelection(
            "20260729", 6000.0, 100.0, 5900.0, 5880.0,
            5940.0, 5970.0, 6000.0, legs, build_combo(legs), 0.05, 100,
        )

    def test_atomic_combo_and_signed_credit_order(self) -> None:
        selection = self._selection()
        self.assertEqual(["SELL", "BUY", "BUY", "SELL", "BUY"], [leg.action for leg in selection.combo_contract.comboLegs])
        self.assertEqual([1, 1, 1, 2, 1], [leg.ratio for leg in selection.combo_contract.comboLegs])
        self.assertAlmostEqual(-0.5, selection.conservative_net_debit)
        self.assertAlmostEqual(1950.0, selection.maximum_loss_dollars)
        order = build_entry_order(selection, account="DU123", order_ref="SPX0-test")
        self.assertEqual("BUY", order.action)
        self.assertAlmostEqual(-0.5, order.lmtPrice)

    def test_live_account_is_rejected(self) -> None:
        with self.assertRaises(RuntimeError):
            build_entry_order(self._selection(), account="U123", order_ref="SPX0-test")


if __name__ == "__main__":
    unittest.main()
