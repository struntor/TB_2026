from __future__ import annotations

import math
import unittest

import pandas as pd
from ib_insync import Contract

from src.execution.spy_poc_fly import (
    FlyStructureQuote,
    ZebraOptionQuote,
    build_entry_order,
    build_put_fly_combo,
    round_down_to_tick,
    round_up_to_tick,
)
from src.strategies.failed_auction_poc_fly import evaluate_day


def quote(bid: float, ask: float) -> ZebraOptionQuote:
    return ZebraOptionQuote(bid, ask, None, None, None)


class FailedAuctionPocFlyTests(unittest.TestCase):
    def test_structure_marks_use_correct_sides(self) -> None:
        structure = FlyStructureQuote(
            upper=quote(8.0, 8.1),
            body=quote(1.0, 1.1),
            lower=quote(0.05, 0.10),
        )
        self.assertTrue(math.isclose(structure.mid, 6.025))
        self.assertTrue(math.isclose(structure.entry_conservative, 6.20))
        self.assertTrue(math.isclose(structure.exit_conservative, 5.85))

    def test_combo_has_frozen_1_2_1_put_fly_ratios(self) -> None:
        legs = []
        for con_id in (1, 2, 3):
            contract = Contract(conId=con_id, exchange="SMART")
            legs.append(contract)
        combo = build_put_fly_combo(*legs)
        self.assertEqual(
            [(leg.action, leg.ratio) for leg in combo.comboLegs],
            [("BUY", 1), ("SELL", 2), ("BUY", 1)],
        )

    def test_tick_rounding_is_directional(self) -> None:
        self.assertEqual(round_up_to_tick(1.231, 0.05), 1.25)
        self.assertEqual(round_down_to_tick(1.249, 0.05), 1.20)

    def test_entry_order_is_transmitted_paper_candidate(self) -> None:
        class Selection:
            entry_marketable_limit = 1.25

        order = build_entry_order(
            Selection(), account="DU123", order_ref="FA:test", quantity=1
        )
        self.assertEqual(order.action, "BUY")
        self.assertEqual(order.totalQuantity, 1)
        self.assertEqual(order.lmtPrice, 1.25)
        self.assertTrue(order.transmit)

    def test_historical_live_evaluator_matches_frozen_candidate_day(self) -> None:
        bars = pd.read_csv(r"D:\Trading\Rohdaten\Rohdaten_SPY.csv")
        result = evaluate_day(bars, "2025-10-09")
        self.assertEqual(result.status, "accepted")
        self.assertEqual(result.body_strike, 669.0)
        self.assertGreaterEqual(result.rvol, 1.0)
        self.assertLess(result.rvol, 1.2)


if __name__ == "__main__":
    unittest.main()
