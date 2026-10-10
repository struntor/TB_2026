from __future__ import annotations

from dataclasses import replace
import unittest

import pandas as pd

from manual_regime_position_sizing import (
    POLICY_VERSION,
    PositionSizingContext,
    apply_position_sizing,
    risk_multiplier,
)


def context(
    score: int,
    *,
    volume_risk: bool = False,
    structure_available: bool = True,
    volume_available: bool = True,
) -> PositionSizingContext:
    return PositionSizingContext(
        trading_day="2026-08-03",
        structure_available=structure_available,
        structure_session="2026-07-31" if structure_available else None,
        structure_score=score,
        structure_phase={0: "directional_or_unknown", 40: "trend_break_warning", 70: "range_forming", 100: "sideways_confirmed"}.get(score, "test"),
        structure_error=None if structure_available else "missing",
        volume_available=volume_available,
        volume_session="2026-07-31" if volume_available else None,
        spy_value_area_low=740.0 if volume_available else None,
        spy_highest_hvn=760.0 if volume_available else None,
        spy_close=745.0 if volume_available else None,
        spy_atr14=10.0 if volume_available else None,
        prior_lower_rejection=volume_risk,
        prior_upper_hvn_distance_atr=1.5 if volume_risk else 0.5,
        vp_overhead_magnet_short_risk=volume_risk,
        volume_error=None if volume_available else "missing",
    )


class ManualRegimePositionSizingTests(unittest.TestCase):
    vulnerable_rule = "bearish_gap_breakdown_continuation_short"

    def test_structure_score_is_graded_only_for_vulnerable_rules(self) -> None:
        self.assertEqual(
            0.75,
            risk_multiplier(
                symbol="TEST",
                rule=self.vulnerable_rule,
                side="short",
                context=context(70),
            )[0],
        )
        self.assertEqual(
            0.50,
            risk_multiplier(
                symbol="TEST",
                rule=self.vulnerable_rule,
                side="short",
                context=context(100),
            )[0],
        )
        self.assertEqual(
            1.0,
            risk_multiplier(
                symbol="TEST",
                rule="bullish_add_on_higher_low",
                side="long",
                context=context(100),
            )[0],
        )

    def test_volume_cap_applies_to_cluster_shorts_without_compounding(self) -> None:
        multiplier, reasons = risk_multiplier(
            symbol="SPY",
            rule=self.vulnerable_rule,
            side="short",
            context=context(70, volume_risk=True),
        )
        self.assertEqual(0.50, multiplier)
        self.assertIn("range_forming_score_70", reasons)
        self.assertIn("overhead_volume_magnet_after_lower_rejection", reasons)

        unaffected, _ = risk_multiplier(
            symbol="TEST",
            rule="bearish_lower_high_failure_short",
            side="short",
            context=context(100, volume_risk=True),
        )
        self.assertEqual(1.0, unaffected)

    def test_missing_required_context_falls_back_to_half_risk(self) -> None:
        structure_missing = replace(
            context(0),
            structure_available=False,
            structure_session=None,
            structure_error="missing",
        )
        self.assertEqual(
            0.50,
            risk_multiplier(
                symbol="TEST",
                rule=self.vulnerable_rule,
                side="short",
                context=structure_missing,
            )[0],
        )
        volume_missing = replace(
            context(0),
            volume_available=False,
            volume_session=None,
            volume_error="missing",
        )
        self.assertEqual(
            0.50,
            risk_multiplier(
                symbol="OEF",
                rule="bearish_lower_high_failure_short",
                side="short",
                context=volume_missing,
            )[0],
        )

    def test_apply_sizing_preserves_legacy_shadow_size(self) -> None:
        trades = pd.DataFrame(
            [
                {
                    "symbol": "SPY",
                    "rule": self.vulnerable_rule,
                    "side": "short",
                    "size": 0.80,
                }
            ]
        )
        sized = apply_position_sizing(trades, context(70, volume_risk=True))

        self.assertEqual(0.80, sized.iloc[0]["legacy_shadow_size"])
        self.assertEqual(0.50, sized.iloc[0]["regime_volume_risk_multiplier"])
        self.assertEqual(0.40, sized.iloc[0]["size"])
        self.assertEqual(POLICY_VERSION, sized.iloc[0]["position_sizing_policy_version"])


if __name__ == "__main__":
    unittest.main()
