from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import unittest

import pandas as pd
from ib_insync import Contract

from src.bot.xsp_vix_pullback_monitor import MonitorConfig, MonitorState, PositionState, XspVixPullbackMonitor
from src.execution.spxw_put_credit import OptionRiskQuote
from src.execution.xsp_put_credit import (
    XspPutCreditSelection,
    build_combo,
    build_entry_order,
    build_profit_target_order,
    expiration_floor,
    quote_quality_passes,
    require_paper_account,
)
from src.journal.decision_ingest import DecisionJournalIngestor
from src.journal.execution_ingest import ExecutionJournalIngestor
from src.journal.store import JournalStore
from src.strategies.xsp_vix_pullback import (
    XspVixPullbackConfig,
    compute_reversal_signals,
    evaluate_entry,
)
from src.strategies.xsp_vix_pullback_journal import register_manifest


def history(values: list[tuple[str, float]], column: str) -> pd.DataFrame:
    rows = []
    for day, value in values:
        rows.append(
            {
                "date": pd.Timestamp(f"{day} 13:35:00"),
                "trade_date": pd.Timestamp(day),
                column: value,
            }
        )
    return pd.DataFrame(rows)


def option(con_id: int, strike: float) -> Contract:
    return Contract(
        conId=con_id, symbol="XSP", secType="OPT", exchange="SMART", currency="USD",
        tradingClass="XSP", localSymbol=f"XSP-{strike:g}", strike=strike, right="P",
        lastTradeDateOrContractMonth="20261218", multiplier="100",
    )


def selection() -> XspPutCreditSelection:
    short, long = option(101, 660.0), option(102, 650.0)
    return XspPutCreditSelection(
        "20261218", 700.0, 660.0, 650.0, short, long,
        OptionRiskQuote(1.80, 1.90, -0.201, 0.01, -0.02, 0.20, 0.25),
        OptionRiskQuote(0.70, 0.80, -0.14, 0.01, -0.01, 0.15, 0.27),
        build_combo(short, long), build_combo(short, long, inverse=True), 0.05, 100,
    )


def position_state(*, status: str) -> PositionState:
    chosen = selection()
    return PositionState(
        "2026-06-03", chosen.expiry, 18.4, 19.5, 0.05, chosen.spot,
        chosen.short_strike, chosen.long_strike, 101, 102,
        chosen.conservative_credit, chosen.maximum_loss_dollars,
        "run", "signal", "decision", "trade",
        "XSPVX-E-test", "XSPVX-TP-test", "XSPVX-T60-test",
        status=status,
    )


class SignalTests(unittest.TestCase):
    def test_local_cboe_bar_is_sampled_at_1235_ct_for_1335_et_decision(self) -> None:
        self.assertEqual("12:35:00", XspVixPullbackConfig().raw_cboe_entry_time_ct)

    def test_frozen_state_machine_and_momentum_accept_the_candidate(self) -> None:
        vix = history(
            [("2026-05-29", 17.0), ("2026-06-01", 19.5), ("2026-06-02", 19.2)],
            "vix",
        )
        spx_days = pd.bdate_range("2026-04-20", periods=30)
        spx = history([(day.strftime("%Y-%m-%d"), 100.0 + index) for index, day in enumerate(spx_days)], "spx")
        observed = evaluate_entry(
            "2026-06-03", vix_frame=vix, spx_frame=spx,
            current_vix=18.4, current_spx=140.0,
        )
        self.assertTrue(observed.accepted)
        self.assertEqual("2026-06-01", observed.peak_day)
        self.assertAlmostEqual(1.1, observed.vix_drop)
        self.assertGreater(observed.momentum_20_sessions, 0)

    def test_momentum_is_a_veto_not_a_second_strategy(self) -> None:
        vix = history([("2026-06-01", 19.5), ("2026-06-02", 19.2)], "vix")
        spx_days = pd.bdate_range("2026-04-20", periods=30)
        spx = history([(day.strftime("%Y-%m-%d"), 120.0) for day in spx_days], "spx")
        observed = evaluate_entry(
            "2026-06-03", vix_frame=vix, spx_frame=spx,
            current_vix=18.4, current_spx=110.0,
        )
        self.assertEqual("no_setup", observed.status)
        self.assertEqual("spx_momentum_20_sessions", observed.reason)

    def test_cooldown_blocks_a_second_signal_for_25_calendar_days(self) -> None:
        frame = history(
            [
                ("2026-06-01", 19.5), ("2026-06-02", 18.4),
                ("2026-06-10", 20.0), ("2026-06-11", 18.8),
                ("2026-06-29", 20.0), ("2026-06-30", 18.8),
            ],
            "vix",
        )
        signals = compute_reversal_signals(frame)
        self.assertEqual(["2026-06-02", "2026-06-30"], signals["entry_day"].tolist())


class StructureAndJournalTests(unittest.TestCase):
    def test_first_standard_monthly_expiry_at_or_above_100_dte(self) -> None:
        observed = expiration_floor(
            ["20261106", "20261120", "20261218"], pd.Timestamp("2026-08-11"), 100
        )
        self.assertEqual("20261120", observed)

    def test_holiday_shifted_monthly_uses_thursday_but_not_an_arbitrary_thursday(self) -> None:
        observed = expiration_floor(
            ["20250410", "20250417", "20250516"], pd.Timestamp("2025-01-07"), 100
        )
        self.assertEqual("20250417", observed)

    def test_defined_risk_entry_and_tp60_orders(self) -> None:
        chosen = selection()
        self.assertAlmostEqual(1.0, chosen.conservative_credit)
        self.assertAlmostEqual(903.30, chosen.maximum_loss_dollars)
        entry = build_entry_order(chosen, account="DU123", order_ref="XSPVX-E-test")
        target = build_profit_target_order(
            chosen, entry_credit=1.0, profit_target_fraction=0.60,
            account="DU123", order_ref="XSPVX-TP-test",
        )
        self.assertEqual(("BUY", -1.0, "DAY"), (entry.action, entry.lmtPrice, entry.tif))
        self.assertEqual(("BUY", 0.4, "GTC"), (target.action, target.lmtPrice, target.tif))
        self.assertEqual(["SELL", "BUY"], [leg.action for leg in chosen.combo_contract.comboLegs])
        self.assertEqual(["BUY", "SELL"], [leg.action for leg in chosen.inverse_combo_contract.comboLegs])

    def test_paper_account_guard(self) -> None:
        require_paper_account("DU123")
        with self.assertRaisesRegex(RuntimeError, "DU paper"):
            require_paper_account("U123")

    def test_entry_quote_quality_requires_both_legs_within_limit(self) -> None:
        chosen = selection()
        self.assertTrue(quote_quality_passes(chosen, 0.20))
        wide_long = XspPutCreditSelection(
            chosen.expiry, chosen.spot, chosen.short_strike, chosen.long_strike,
            chosen.short_contract, chosen.long_contract, chosen.short_quote,
            OptionRiskQuote(0.10, 0.90, -0.14, 0.01, -0.01, 0.15, 0.27),
            chosen.combo_contract, chosen.inverse_combo_contract, chosen.min_tick,
            chosen.multiplier,
        )
        self.assertFalse(quote_quality_passes(wide_long, 0.20))

    def test_accepted_dry_run_creates_two_leg_journal_trade(self) -> None:
        config = XspVixPullbackConfig()
        vix = history([("2026-06-01", 19.5), ("2026-06-02", 19.2)], "vix")
        spx_days = pd.bdate_range("2026-04-20", periods=30)
        spx = history([(day.strftime("%Y-%m-%d"), 100.0 + i) for i, day in enumerate(spx_days)], "spx")
        evaluation = evaluate_entry(
            "2026-06-03", vix_frame=vix, spx_frame=spx,
            current_vix=18.4, current_spx=140.0, config=config,
        )
        store = JournalStore(":memory:")
        try:
            register_manifest(store, config)
            monitor = XspVixPullbackMonitor.__new__(XspVixPullbackMonitor)
            monitor.monitor_config = MonitorConfig(pd.Timestamp("2026-06-03"), "DU123", 62, False, Path(":memory:"))
            monitor.strategy_config = config
            monitor.ib = SimpleNamespace(trades=lambda: (), reqCompletedOrders=lambda _: ())
            monitor.store = store
            monitor.decisions = DecisionJournalIngestor(store)
            monitor.executions = ExecutionJournalIngestor(store)
            monitor.log = lambda _: None
            position = monitor._new_position(evaluation, selection())
            monitor._submit_entry(position, selection())
            trade = store.get_trade(position.trade_id)
            legs = store.list_trade_legs(position.trade_id)
            self.assertEqual("dry_run", position.status)
            self.assertEqual("BAG", trade["asset_class"])
            self.assertEqual(["SELL", "BUY"], [leg["side"] for leg in legs])
        finally:
            store.close()

    def test_entry_fill_immediately_submits_inverse_gtc_tp60(self) -> None:
        chosen = selection()
        position = position_state(status="entry_submitted")
        position.entry_order_id = 11
        entry_trade = SimpleNamespace(
            order=SimpleNamespace(orderId=11, orderRef=position.entry_order_ref),
            orderStatus=SimpleNamespace(status="Filled", avgFillPrice=-1.0),
        )
        placed = []

        def place_order(combo, order):
            placed.append((combo, order))
            order.orderId = 12
            return SimpleNamespace(order=order, orderStatus=SimpleNamespace(status="Submitted", avgFillPrice=0.0))

        monitor = XspVixPullbackMonitor.__new__(XspVixPullbackMonitor)
        monitor.monitor_config = MonitorConfig(pd.Timestamp("2026-06-03"), "DU123", 62, True, Path(":memory:"))
        monitor.strategy_config = XspVixPullbackConfig()
        monitor.ib = SimpleNamespace(placeOrder=place_order, sleep=lambda _: None)
        monitor.executions = SimpleNamespace(ingest_trade=lambda *args, **kwargs: None)
        monitor._execution_context = lambda _: None
        monitor._finish_run = lambda *args: None
        monitor._selection = lambda _: chosen
        monitor._find_trade = lambda ref, oid: entry_trade if ref == position.entry_order_ref else None
        monitor.log = lambda _: None
        monitor._service_orders(MonitorState(positions=[position]))
        self.assertEqual("target_submitted", position.status)
        self.assertEqual(0.4, position.target_debit)
        self.assertEqual("GTC", placed[0][1].tif)
        self.assertEqual(["BUY", "SELL"], [leg.action for leg in placed[0][0].comboLegs])

    def test_time60_cancels_target_then_submits_quality_gated_day_exit(self) -> None:
        chosen = selection()
        position = position_state(status="target_submitted")
        position.target_order_id = 12
        target = SimpleNamespace(
            order=SimpleNamespace(orderId=12, orderRef=position.target_order_ref),
            orderStatus=SimpleNamespace(status="Submitted", avgFillPrice=0.0),
        )
        placed = []

        def cancel_order(_):
            target.orderStatus.status = "Cancelled"

        def place_order(combo, order):
            placed.append((combo, order))
            order.orderId = 13
            return SimpleNamespace(order=order, orderStatus=SimpleNamespace(status="Submitted", avgFillPrice=0.0))

        monitor = XspVixPullbackMonitor.__new__(XspVixPullbackMonitor)
        monitor.monitor_config = MonitorConfig(pd.Timestamp("2026-10-20"), "DU123", 62, True, Path(":memory:"))
        monitor.strategy_config = XspVixPullbackConfig()
        monitor.ib = SimpleNamespace(cancelOrder=cancel_order, placeOrder=place_order, sleep=lambda _: None)
        monitor.executions = SimpleNamespace(ingest_trade=lambda *args, **kwargs: None)
        monitor._execution_context = lambda _: None
        monitor._finish_run = lambda *args: None
        monitor._selection = lambda _: chosen
        monitor._find_trade = lambda ref, oid: target if ref == position.target_order_ref else None
        monitor.log = lambda _: None
        monitor._service_time_exits(
            MonitorState(positions=[position]), pd.Timestamp("2026-10-20 10:00", tz="America/New_York")
        )
        self.assertEqual("time_exit_submitted", position.status)
        self.assertEqual("Cancelled", target.orderStatus.status)
        self.assertEqual(("BUY", "DAY"), (placed[0][1].action, placed[0][1].tif))


if __name__ == "__main__":
    unittest.main()
