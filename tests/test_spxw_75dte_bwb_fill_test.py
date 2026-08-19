from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest


SCRIPT = Path(__file__).parents[1] / "scripts" / "run_spxw_75dte_bwb_fill_test.py"
SPEC = importlib.util.spec_from_file_location("spxw_bwb_fill_test", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class PriceLadderTests(unittest.TestCase):
    def test_starts_at_existing_configured_limit(self) -> None:
        observed = MODULE.ladder_credit(
            initial_credit=4.50, attempt=1, decrement=0.20, ask_credit=3.80, min_tick=0.05
        )
        self.assertEqual(4.50, observed)

    def test_reprices_by_twenty_cents_every_attempt(self) -> None:
        observed = [
            MODULE.ladder_credit(
                initial_credit=4.50, attempt=attempt, decrement=0.20, ask_credit=3.80, min_tick=0.05
            )
            for attempt in range(1, 5)
        ]
        self.assertEqual([4.50, 4.30, 4.10, 3.90], observed)

    def test_never_moves_beyond_one_tick_through_current_ask(self) -> None:
        observed = MODULE.ladder_credit(
            initial_credit=4.50, attempt=20, decrement=0.20, ask_credit=3.80, min_tick=0.05
        )
        self.assertEqual(3.75, observed)

    def test_rejects_invalid_attempt(self) -> None:
        with self.assertRaisesRegex(ValueError, "attempt"):
            MODULE.ladder_credit(
                initial_credit=4.50, attempt=0, decrement=0.20, ask_credit=3.80, min_tick=0.05
            )


if __name__ == "__main__":
    unittest.main()
