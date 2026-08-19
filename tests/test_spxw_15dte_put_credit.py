from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

import pandas as pd
from ib_insync import Contract

from src.bot.spxw_15dte_put_credit_monitor import (
    MonitorConfig,
    MonitorState,
    PositionState,
    Spxw15DtePutCreditMonitor,
    atomic_write_state,
    friction_gap_dollars,
    load_state,
    order_work_window_elapsed,
)
from src.journal.decision_ingest import DecisionJournalIngestor
from src.journal.execution_ingest import ExecutionJournalIngestor
from src.journal.store import JournalStore
from src.strategies.spxw_15dte_journal import register_manifest
from src.execution.spxw_put_credit import (
    OptionRiskQuote,
    PutCreditSelection,
    build_entry_order,
    build_exit_order,
    build_vertical_combo,
    require_paper_account,
)
from src.strategies.spxw_15dte_put_credit import (
    Spxw15DteConfig,
    apply_quote_gates,
    evaluate_entry,
)


RAW_SPY = Path(r"D:\Trading\Rohdaten\Rohdaten_SPY.csv")
CAUSAL_TRADES = Path(
    r"D:\Trading\research\spxw_15dte_causal_1330_signal_v1\causal_trades.csv"
)


def contract(con_id: int) -> Contract:
    return Contract(
        conId=con_id,
        symbol="SPX",
        secType="OPT",
        exchange="SMART",
        currency="USD",
        tradingClass="SPXW",
        localSymbol=f"SPXW-{con_id}",
    )


def selection() -> PutCreditSelection:
    short, long = contract(101), contract(102)
    short_quote = OptionRiskQuote(5.0, 5.2, -0.25, 0.01, -2.0, 8.0, 0.18)
    long_quote = OptionRiskQuote(3.2, 3.4, -0.20, 0.009, -1.8, 7.0, 0.19)
    return PutCreditSelection(
        "SPXW", "20260805", 6500.0, 6490.0, short, long,
        short_quote, long_quote,
        build_vertical_combo(short, long),
        build_vertical_combo(short, long, inverse=True),
        0.05, 100,
    )


class StrategyParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not RAW_SPY.exists() or not CAUSAL_TRADES.exists():
            raise unittest.SkipTest("causal research artifacts are unavailable")
        cls.bars = pd.read_csv(RAW_SPY, parse_dates=["date"])
        cls.trades = pd.read_csv(CAUSAL_TRADES, low_memory=False)

    def test_every_corrected_trade_passes_live_entry_and_quote_gates(self) -> None:
        failures = []
        for row in self.trades.itertuples(index=False):
            evaluation = evaluate_entry(self.bars, row.trade_date)
            final = apply_quote_gates(
                evaluation,
                conservative_credit=float(row.entry_credit_cons),
                short_iv=float(row.short_iv),
            )
            if not final.accepted:
                failures.append((row.trade_date, final.reason))
        self.assertEqual([], failures)

    def test_signal_does_not_read_1335_or_later_bars(self) -> None:
        day = str(self.trades.iloc[0]["trade_date"])
        baseline = evaluate_entry(self.bars, day)
        changed = self.bars.copy()
        stamps = pd.to_datetime(changed["date"])
        mask = stamps.dt.strftime("%Y-%m-%d").eq(day) & stamps.dt.strftime("%H:%M:%S").ge("13:35:00")
        changed.loc[mask, ["open", "high", "low", "close"]] = 1.0
        observed = evaluate_entry(changed, day)
        self.assertEqual(baseline.status, observed.status)
        self.assertEqual(baseline.reason, observed.reason)
        self.assertEqual(baseline.signal_close, observed.signal_close)
        self.assertEqual(baseline.dist_low10, observed.dist_low10)

    def test_reentry_requires_three_trading_days_and_distance(self) -> None:
        day = str(self.trades.iloc[10]["trade_date"])
        without = evaluate_entry(self.bars, day)
        if not without.accepted:
            self.skipTest("chosen historical day is not a raw accepted setup")
        same_day = evaluate_entry(self.bars, day, reentry_activation_day=day)
        self.assertFalse(same_day.accepted)
        self.assertEqual("reentry_cooldown_trading_days", same_day.reason)

    def test_accepted_dry_run_writes_decision_bag_legs_and_blocker(self) -> None:
        row = self.trades.iloc[0]
        config = Spxw15DteConfig()
        evaluation = apply_quote_gates(
            evaluate_entry(self.bars, str(row["trade_date"]), config),
            conservative_credit=selection().conservative_credit,
            short_iv=selection().short_quote.implied_vol,
            config=config,
        )
        self.assertTrue(evaluation.accepted)
        store = JournalStore(":memory:")
        try:
            register_manifest(store, config)
            monitor = Spxw15DtePutCreditMonitor.__new__(Spxw15DtePutCreditMonitor)
            monitor.monitor_config = MonitorConfig(
                day=pd.Timestamp(row["trade_date"]),
                account="DU123",
                client_id=62,
                place_orders=False,
                journal_path=Path(":memory:"),
            )
            monitor.strategy_config = config
            monitor.ib = SimpleNamespace(
                trades=lambda: (), reqCompletedOrders=lambda _: ()
            )
            monitor.store = store
            monitor.decisions = DecisionJournalIngestor(store)
            monitor.executions = ExecutionJournalIngestor(store)
            monitor.log = lambda _: None
            position = monitor._new_position(evaluation, selection())
            monitor._submit_entry(position, selection())
            trade = store.get_trade(position.trade_id)
            legs = store.list_trade_legs(position.trade_id)
            blocker = store._connection.execute(
                "SELECT status, event_type FROM risk_events WHERE trade_id=?",
                (position.trade_id,),
            ).fetchone()
            run = store.get_run(position.run_id)
            self.assertEqual("dry_run", position.status)
            self.assertEqual("BAG", trade["asset_class"])
            self.assertEqual(["SELL", "BUY"], [leg["side"] for leg in legs])
            self.assertEqual(("execution_blocked", "dry_run_no_order"), tuple(blocker))
            self.assertEqual("completed", run["status"])
            self.assertIsNotNone(run["ended_at_utc"])
        finally:
            store.close()


class ExecutionAndStateTests(unittest.TestCase):
    def test_paper_guard_is_not_bypassable(self) -> None:
        require_paper_account("DU123")
        with self.assertRaisesRegex(RuntimeError, "DU paper"):
            require_paper_account("U123")

    def test_credit_entry_and_debit_exit_are_combo_limits(self) -> None:
        chosen = selection()
        entry = build_entry_order(chosen, account="DU123", order_ref="SPX15-E-test")
        exit_order = build_exit_order(chosen, account="DU123", order_ref="SPX15-X-test")
        self.assertEqual("BUY", entry.action)
        self.assertLess(entry.lmtPrice, 0.0)
        self.assertEqual(-1.60, entry.lmtPrice)
        self.assertEqual("BUY", exit_order.action)
        self.assertEqual(2.0, exit_order.lmtPrice)
        self.assertTrue(entry.transmit)
        self.assertEqual("DU123", entry.account)

    def test_combo_legs_reverse_for_exit(self) -> None:
        chosen = selection()
        self.assertEqual(["SELL", "BUY"], [leg.action for leg in chosen.combo_contract.comboLegs])
        self.assertEqual(["BUY", "SELL"], [leg.action for leg in chosen.inverse_combo_contract.comboLegs])

    def test_friction_gap_matches_historical_pnl_difference(self) -> None:
        gap = friction_gap_dollars(
            entry_credit_mid=2.0,
            entry_credit_cons=1.7,
            exit_debit_mid=0.5,
            exit_debit_cons=1.1,
        )
        self.assertAlmostEqual(90.0, gap)

    def test_state_roundtrip_preserves_multiple_positions_and_reentry(self) -> None:
        position = PositionState(
            "2026-07-21", "20260805", 6500, 6490, 101, 102, 1.6, 1.8,
            "run", "signal", "decision", "trade", "SPX15-E-test", status="open",
        )
        state = MonitorState(
            evaluated_days=["2026-07-21"],
            reentry_activation_day="2026-07-20",
            positions=[position, replace(position, signal_day="2026-07-22", entry_order_ref="SPX15-E-test2")],
        )
        with tempfile.TemporaryDirectory(dir=r"D:\Trading") as directory:
            path = Path(directory) / "state.json"
            atomic_write_state(path, state)
            observed = load_state(path)
        self.assertEqual(2, len(observed.positions))
        self.assertEqual("2026-07-20", observed.reentry_activation_day)
        self.assertEqual("open", observed.positions[0].status)

    def test_entry_work_window_is_utc_safe(self) -> None:
        submitted = "2026-07-21T17:35:00Z"
        self.assertFalse(
            order_work_window_elapsed(submitted, pd.Timestamp("2026-07-21 17:39:59Z"), 5)
        )
        self.assertTrue(
            order_work_window_elapsed(submitted, pd.Timestamp("2026-07-21 17:40:00Z"), 5)
        )


if __name__ == "__main__":
    unittest.main()
