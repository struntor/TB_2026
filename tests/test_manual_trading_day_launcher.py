from __future__ import annotations

from datetime import date
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
import sys
import unittest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from run_trading_day import parse_manual_args


class ManualTradingDayLauncherTests(unittest.TestCase):
    def test_forwards_all_safety_and_strategy_switches_to_one_day_pipeline(self) -> None:
        source = [
            "--port", "7496",
            "--place-orders",
            "--enable-spxw-15dte-put-credit",
            "--enable-spxw-0dte-bull-put-iu30",
            "--spxw-75dte-bwb-initial-entry-work-minutes", "5",
            "--spxw-75dte-bwb-entry-work-minutes", "5",
            "--spxw-75dte-bwb-final-entry-work-minutes", "5",
            "--options-max-risk-fraction", "0.10",
        ]
        observed = parse_manual_args(source, date(2026, 7, 30))

        self.assertEqual(7496, observed.port)
        self.assertTrue(observed.place_orders)
        self.assertTrue(observed.enable_spxw_15dte_put_credit)
        self.assertTrue(observed.enable_spxw_0dte_bull_put_iu30)
        self.assertEqual(5, observed.spxw_75dte_bwb_initial_entry_work_minutes)
        self.assertEqual(5, observed.spxw_75dte_bwb_entry_work_minutes)
        self.assertEqual(5, observed.spxw_75dte_bwb_final_entry_work_minutes)
        self.assertEqual(0.10, observed.options_max_risk_fraction)
        self.assertEqual("2026-07-30", observed.once_day)

    def test_rejects_conflicting_once_day_argument(self) -> None:
        with redirect_stderr(StringIO()):
            with self.assertRaises(SystemExit):
                parse_manual_args(
                    ["--once-day", "2026-07-29"], date(2026, 7, 30)
                )


if __name__ == "__main__":
    unittest.main()
