from __future__ import annotations

import csv
from pathlib import Path
import unittest
import uuid

from src.journal.models import StrategyRunRecord, TradeRecord
from src.journal.report import JournalReporter, ReportSelection, parse_period
from src.journal.store import JournalStore


def _run(run_id: str, run_type: str, started: str) -> StrategyRunRecord:
    return StrategyRunRecord(
        run_id=run_id,
        strategy_id="strategy-a",
        strategy_version="1.0.0",
        config_hash=f"cfg-{run_id}",
        run_type=run_type,
        environment="test",
        started_at_utc=started,
        ended_at_utc="2026-02-02T00:00:00Z",
        status="completed",
        parameters={"starting_capital": 10_000},
        metadata={},
    )


def _trade(
    trade_id: str,
    run_id: str,
    signal_key: str,
    closed_at: str,
    pnl: float,
    *,
    entry: float = 100,
    exit_price: float = 101,
) -> TradeRecord:
    return TradeRecord(
        trade_id=trade_id,
        run_id=run_id,
        signal_key=signal_key,
        decision_id=None,
        asset_class="STK",
        symbol="TEST",
        side="long",
        status="closed",
        opened_at_utc="2026-01-01T15:00:00Z",
        closed_at_utc=closed_at,
        quantity=10,
        currency="USD",
        entry_price=entry,
        exit_price=exit_price,
        realized_pnl=pnl,
        metadata={},
    )


class JournalReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(r"D:\Trading\journal\report_test_runtime") / uuid.uuid4().hex
        self.root.mkdir(parents=True, exist_ok=True)
        self.database = self.root / "journal.sqlite"
        with JournalStore(self.database) as store:
            store.start_run(_run("backtest-run", "backtest", "2026-02-01T00:00:00Z"))
            store.start_run(_run("paper-run", "paper", "2026-01-01T00:00:00Z"))
            store.create_trade(
                _trade("bt-1", "backtest-run", "signal-1", "2026-01-10T20:00:00Z", 100)
            )
            store.create_trade(
                _trade("bt-2", "backtest-run", "signal-2", "2026-01-20T20:00:00Z", -50)
            )
            store.create_trade(
                _trade("bt-3", "backtest-run", "signal-3", "2026-02-02T20:00:00Z", 200)
            )
            store.create_trade(
                _trade(
                    "paper-1", "paper-run", "signal-1", "2026-01-10T20:01:00Z", 80,
                    entry=100.2, exit_price=100.8,
                )
            )
            store.create_trade(
                _trade("paper-4", "paper-run", "signal-4", "2026-01-25T20:00:00Z", 40)
            )

    def selection(self, period: str = "2026-01") -> ReportSelection:
        return ReportSelection(
            variant_id=None,
            strategy_id="strategy-a",
            period=parse_period(period),
            actual_run_types=("paper", "live"),
            backtest_run_id=None,
            starting_capital=None,
        )

    def test_period_parser_supports_month_and_year(self) -> None:
        month = parse_period("2026-01")
        year = parse_period("2026")

        self.assertEqual("2026-02-01", month.end_exclusive.date().isoformat())
        self.assertEqual("2027-01-01", year.end_exclusive.date().isoformat())
        with self.assertRaises(ValueError):
            parse_period("2026-13")

    def test_report_builds_equity_and_signal_differences(self) -> None:
        output = self.root / "report"
        summary = JournalReporter(self.database).create_report(self.selection(), output)

        self.assertEqual(50.0, summary["backtest_metrics"]["net_pnl"])
        self.assertEqual(120.0, summary["actual_metrics"]["net_pnl"])
        self.assertEqual(1, summary["comparison"]["matched"])
        self.assertEqual(1, summary["comparison"]["backtest_only"])
        self.assertEqual(1, summary["comparison"]["actual_only"])
        self.assertEqual(-20.0, summary["comparison"]["matched_pnl_difference"])
        for name in (
            "backtest_trades.csv", "actual_trades.csv", "backtest_equity.csv",
            "actual_equity.csv", "backtest_vs_actual.csv", "summary.json",
            "matched_pnl_difference.csv", "equity.png", "report.html",
        ):
            self.assertTrue((output / name).exists(), name)
        with (output / "matched_pnl_difference.csv").open(
            newline="", encoding="utf-8"
        ) as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(1, len(rows))
        self.assertEqual(-20.0, float(rows[0]["cumulative_pnl_difference"]))

    def test_year_report_includes_later_backtest_trade(self) -> None:
        summary = JournalReporter(self.database).create_report(
            self.selection("2026"), self.root / "year-report"
        )

        self.assertEqual(250.0, summary["backtest_metrics"]["net_pnl"])
        self.assertEqual(3, summary["backtest_metrics"]["trades"])


if __name__ == "__main__":
    unittest.main()
