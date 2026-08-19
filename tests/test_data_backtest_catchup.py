from __future__ import annotations

from datetime import date
import json
from pathlib import Path
import unittest

from scripts.run_data_backtest_catchup import (
    default_window,
    manifest_path,
    missing_sessions,
    session_days,
)


class DataBacktestCatchupTests(unittest.TestCase):
    def test_sunday_window_covers_previous_completed_week(self) -> None:
        self.assertEqual(
            (date(2026, 8, 1), date(2026, 8, 7)),
            default_window(date(2026, 8, 9)),
        )
        self.assertEqual(
            [date(2026, 8, day) for day in range(3, 8)],
            session_days(date(2026, 8, 1), date(2026, 8, 7)),
        )

    def test_only_valid_manifests_are_treated_as_completed(self) -> None:
        root = Path(r"D:\Trading\journal\catchup_test_runtime")
        completed = manifest_path(root, date(2026, 8, 3))
        completed.parent.mkdir(parents=True, exist_ok=True)
        completed.write_text(json.dumps({"run_id": "completed-run"}), encoding="utf-8")
        invalid = manifest_path(root, date(2026, 8, 4))
        invalid.parent.mkdir(parents=True, exist_ok=True)
        invalid.write_text("{}", encoding="utf-8")

        self.assertEqual(
            [date(2026, 8, 4), date(2026, 8, 5)],
            missing_sessions(root, date(2026, 8, 3), date(2026, 8, 5)),
        )


if __name__ == "__main__":
    unittest.main()
