from __future__ import annotations

import unittest

from manual_regime_gap_breakdown_quarter_sizing import (
    RISK_MULTIPLIER,
    TARGET_RULE,
    quarter_size_decision,
    spx_direction_bucket,
)


class GapBreakdownQuarterSizingTests(unittest.TestCase):
    def decision(
        self,
        volume_phase: str,
        direction: str,
        relative_strength: float | None,
        *,
        rule: str = TARGET_RULE,
    ) -> tuple[float, tuple[str, ...]]:
        return quarter_size_decision(
            rule=rule,
            volume_price_phase=volume_phase,
            direction_bucket=direction,
            relative_strength_20_pct=relative_strength,
        )

    def test_direction_bucket_matches_backtest_boundaries(self) -> None:
        self.assertEqual("strong_bear", spx_direction_bucket(-2.0))
        self.assertEqual("mild_bear", spx_direction_bucket(0.0))
        self.assertEqual("mild_bull", spx_direction_bucket(2.0))
        self.assertEqual("strong_bull", spx_direction_bucket(2.01))

    def test_high_volume_breakdown_is_quarter_size_outside_strong_bull(self) -> None:
        multiplier, reasons = self.decision(
            "high_volume_breakdown", "mild_bull", -3.0
        )
        self.assertEqual(RISK_MULTIPLIER, multiplier)
        self.assertIn("adverse_volume_phase_for_gap_breakdown_short", reasons)

    def test_high_volume_breakout_is_always_quarter_size(self) -> None:
        multiplier, _ = self.decision(
            "high_volume_breakout", "strong_bull", -3.0
        )
        self.assertEqual(RISK_MULTIPLIER, multiplier)

    def test_strong_bull_requires_nonnegative_relative_strength(self) -> None:
        reduced, reasons = self.decision(
            "normal_volume_trend", "strong_bull", 0.0
        )
        full, _ = self.decision(
            "high_volume_breakdown", "strong_bull", -0.01
        )
        self.assertEqual(RISK_MULTIPLIER, reduced)
        self.assertIn(
            "nonnegative_relative_strength_in_strong_bull_spx",
            reasons,
        )
        self.assertEqual(1.0, full)

    def test_other_rules_are_unchanged(self) -> None:
        multiplier, reasons = self.decision(
            "high_volume_breakout",
            "strong_bull",
            5.0,
            rule="bearish_lower_high_failure_short",
        )
        self.assertEqual(1.0, multiplier)
        self.assertEqual(("rule_not_affected",), reasons)


if __name__ == "__main__":
    unittest.main()

