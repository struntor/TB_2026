from __future__ import annotations

import unittest

from lhf_cluster_rank_score import LhfScoreInput, rank_cluster_choices, score_cluster


def candidate(
    symbol: str,
    *,
    signal_regime: str = "bearish_confirmed",
    big_picture_regime: str = "bearish_primary",
    big_picture_state: str = "weekly_lower_high_lower_low",
    daily_structure_phase: str = "post_break_range_lower",
    countermove: str = "bearish_countermove_weak_retest",
    bars: int = 2,
    distance: float = 3.0,
    net_atr: float = -1.5,
    crv: float = 1.5,
) -> LhfScoreInput:
    entry = 100.0
    stop = 102.0
    return LhfScoreInput(
        signal_key=f"signal:{symbol}",
        symbol=symbol,
        signal_regime=signal_regime,
        big_picture_regime=big_picture_regime,
        big_picture_state=big_picture_state,
        daily_structure_phase=daily_structure_phase,
        bearish_countermove_subregime=countermove,
        volume_price_phase="low_volume_pullback",
        bars_since_support_break=bars,
        distance_from_recent_high_atr=distance,
        net_atr=net_atr,
        entry=entry,
        stop=stop,
        target=entry - crv * (stop - entry),
    )


class LhfClusterRankScoreTest(unittest.TestCase):
    def test_structurally_aligned_fresh_signal_beats_bullish_stale_signal(self) -> None:
        strong = candidate("SPY")
        weak = candidate(
            "DIA",
            signal_regime="bearish_countermove",
            big_picture_regime="bullish_primary",
            big_picture_state="weekly_higher_high_higher_low",
            daily_structure_phase="trend_or_unclassified",
            countermove="bearish_countermove_repair_rebound",
            bars=8,
            distance=1.5,
            net_atr=-0.8,
            crv=1.2,
        )
        scores = score_cluster([weak, strong])
        self.assertEqual(scores[0].symbol, "SPY")
        self.assertGreater(scores[0].score, scores[1].score)

    def test_relative_weakness_prefers_more_damaged_and_more_bearish_candidate(self) -> None:
        weaker = candidate("OEF", distance=3.8, net_atr=-1.9)
        stronger = candidate("IVV", distance=2.4, net_atr=-1.1)
        scores = {result.symbol: result for result in score_cluster([weaker, stronger])}
        self.assertEqual(scores["OEF"].relative_distance_rank, 1.0)
        self.assertEqual(scores["OEF"].relative_impulse_rank, 1.0)
        self.assertEqual(scores["IVV"].relative_distance_rank, 0.0)
        self.assertEqual(scores["IVV"].relative_impulse_rank, 0.0)

    def test_duplicate_signal_keys_are_rejected(self) -> None:
        first = candidate("SPY")
        duplicate = LhfScoreInput(**{**first.__dict__, "symbol": "IVV"})
        with self.assertRaisesRegex(ValueError, "duplicate signal keys"):
            score_cluster([first, duplicate])

    def test_cluster_choice_prefers_highest_planned_crv(self) -> None:
        high_crv = candidate("OEF", crv=1.8)
        stronger_setup = candidate(
            "SPY",
            crv=1.5,
            distance=3.0,
            net_atr=-2.0,
        )
        ranked = rank_cluster_choices(score_cluster([stronger_setup, high_crv]))
        self.assertEqual(ranked[0].symbol, "OEF")
        self.assertAlmostEqual(ranked[0].choice_score or 0.0, 1.8)


if __name__ == "__main__":
    unittest.main()
