from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import pandas as pd
from ib_insync import Contract

from src.bot.spxw_75dte_put_bwb_monitor import (
    MonitorConfig,
    MonitorState,
    PositionState,
    Spxw75DtePutBwbMonitor,
)
from src.execution.spxw_put_bwb import (
    BwbLegSelection,
    SpxwPutBwbSelection,
    _expiration_floor,
    build_combo,
    build_entry_order,
    build_profit_target_order,
    require_paper_account,
)
from src.execution.spxw_put_credit import OptionRiskQuote
from src.journal.decision_ingest import DecisionJournalIngestor
from src.journal.execution_ingest import ExecutionJournalIngestor
from src.journal.store import JournalStore
from src.strategies.spxw_75dte_put_bwb import (
    Spxw75DteBwbConfig,
    evaluate_entry,
    maximum_concurrent_positions_for_day,
    paper_weekday_experiment_active,
)
from src.strategies.spxw_75dte_put_bwb_journal import register_manifest
import src.strategies.spxw_75dte_put_bwb_journal as bwb_manifest


def contract(con_id: int, strike: float) -> Contract:
    return Contract(
        conId=con_id, symbol="SPX", secType="OPT", exchange="SMART", currency="USD",
        tradingClass="SPXW", localSymbol=f"SPXW-{strike:g}", strike=strike,
        right="P", lastTradeDateOrContractMonth="20261016", multiplier="100",
    )


def selection() -> SpxwPutBwbSelection:
    descriptors = (
        ("upper", "BUY", 1, 6935.0, 101, OptionRiskQuote(20.9, 21.1, -0.45, None, None, None, 0.20)),
        ("body", "SELL", 2, 6900.0, 102, OptionRiskQuote(15.9, 16.1, -0.38, None, None, None, 0.20)),
        ("lower", "BUY", 1, 6845.0, 103, OptionRiskQuote(8.9, 9.1, -0.25, None, None, None, 0.20)),
    )
    legs = tuple(BwbLegSelection(role, action, ratio, strike, contract(con_id, strike), quote) for role, action, ratio, strike, con_id, quote in descriptors)
    return SpxwPutBwbSelection(
        "20261016", 6969.70, 18.2, 6935.0, 6900.0, 6845.0, legs,
        build_combo(legs), build_combo(legs, inverse=True), 0.05, 100, 0.20,
    )


class StrategyAndExecutionTests(unittest.TestCase):
    def test_entry_order_default_work_windows_are_five_minutes_each(self) -> None:
        observed = MonitorConfig(
            pd.Timestamp("2026-08-13"), "DU123", 62, False, Path(":memory:")
        )
        self.assertEqual(5, observed.initial_entry_work_minutes)
        self.assertEqual(5, observed.entry_work_minutes)
        self.assertEqual(5, observed.final_entry_work_minutes)
        self.assertEqual(0.65, Spxw75DteBwbConfig().final_entry_slippage_points)

    def test_only_thursday_with_vix_at_least_15_is_accepted(self) -> None:
        accepted = evaluate_entry("2026-08-13", opening_vix=15.0)
        self.assertTrue(accepted.accepted)
        self.assertEqual("thursday_vix15_entry_accepted", accepted.reason)
        self.assertEqual("no_setup", evaluate_entry("2026-08-12", opening_vix=20.0).status)
        self.assertEqual("no_setup", evaluate_entry("2026-08-13", opening_vix=14.99).status)
        self.assertEqual("missing_data", evaluate_entry("2026-08-13", opening_vix=None).status)

    def test_two_week_paper_experiment_accepts_weekdays_then_expires_automatically(self) -> None:
        config = Spxw75DteBwbConfig()
        for day in ("2026-08-14", "2026-08-17", "2026-08-18", "2026-08-19", "2026-08-20", "2026-08-21", "2026-08-27"):
            observed = evaluate_entry(day, opening_vix=15.0, config=config)
            self.assertTrue(observed.accepted, day)
            self.assertEqual("weekday_paper_experiment_vix15_entry_accepted", observed.reason)
            self.assertTrue(paper_weekday_experiment_active(day, config))
            self.assertEqual(20, maximum_concurrent_positions_for_day(day, config))

        self.assertEqual("no_setup", evaluate_entry("2026-08-22", opening_vix=20.0, config=config).status)
        self.assertEqual("no_setup", evaluate_entry("2026-08-28", opening_vix=20.0, config=config).status)
        self.assertEqual("no_setup", evaluate_entry("2026-08-31", opening_vix=20.0, config=config).status)
        self.assertTrue(evaluate_entry("2026-09-03", opening_vix=15.0, config=config).accepted)
        self.assertFalse(paper_weekday_experiment_active("2026-08-31", config))
        self.assertEqual(7, maximum_concurrent_positions_for_day("2026-08-31", config))

    def test_expiration_is_first_listed_at_or_above_75_dte(self) -> None:
        observed = _expiration_floor(["20261009", "20261016", "20261023"], pd.Timestamp("2026-07-28"), 75)
        self.assertEqual("20261016", observed)
        self.assertEqual(80, (pd.Timestamp(observed) - pd.Timestamp("2026-07-28")).days)

    def test_daily_fill_test_keeps_normal_risk_limits_and_vix_filter(self) -> None:
        config = Spxw75DteBwbConfig()
        for day in ("2026-09-03", "2026-09-04", "2026-09-08", "2026-09-09", "2026-09-14", "2026-10-02"):
            observed = evaluate_entry(day, opening_vix=15.0, config=config)
            self.assertTrue(observed.accepted, day)
            self.assertEqual("daily_paper_fill_test_vix15_entry_accepted", observed.reason)
            self.assertTrue(observed.factors[0]["paper_daily_fill_test_active"])
            self.assertFalse(paper_weekday_experiment_active(day, config))
            self.assertEqual(7, maximum_concurrent_positions_for_day(day, config))
            self.assertFalse(evaluate_entry(day, opening_vix=14.99, config=config).accepted)
            self.assertEqual("missing_data", evaluate_entry(day, opening_vix=None, config=config).status)
        for day in ("2026-09-02", "2026-09-05", "2026-09-06"):
            self.assertFalse(evaluate_entry(day, opening_vix=20.0, config=config).accepted)

    def test_combo_geometry_credit_risk_and_orders(self) -> None:
        chosen = selection()
        self.assertEqual(["BUY", "SELL", "BUY"], [leg.action for leg in chosen.combo_contract.comboLegs])
        self.assertEqual([1, 2, 1], [leg.ratio for leg in chosen.combo_contract.comboLegs])
        self.assertEqual(["SELL", "BUY", "SELL"], [leg.action for leg in chosen.inverse_combo_contract.comboLegs])
        self.assertAlmostEqual(2.0, chosen.mid_credit)
        self.assertAlmostEqual(1.8, chosen.entry_limit_credit)
        self.assertAlmostEqual(1820.0, chosen.maximum_loss_dollars)
        entry = build_entry_order(chosen, account="DU123", order_ref="SPX75-E-test")
        repriced_entry = build_entry_order(
            chosen, account="DU123", order_ref="SPX75-E-test", limit_credit=1.6
        )
        target = build_profit_target_order(
            chosen, entry_credit=1.8, profit_target_fraction=0.40,
            account="DU123", order_ref="SPX75-X-test",
        )
        self.assertEqual(("BUY", -1.8, "DAY"), (entry.action, entry.lmtPrice, entry.tif))
        self.assertEqual(("BUY", -1.6, "DAY"), (repriced_entry.action, repriced_entry.lmtPrice, repriced_entry.tif))
        self.assertEqual(("BUY", 1.05, "GTC"), (target.action, target.lmtPrice, target.tif))

    def test_daily_test_does_not_re_evaluate_a_processed_day(self) -> None:
        monitor = Spxw75DtePutBwbMonitor.__new__(Spxw75DtePutBwbMonitor)
        monitor.strategy_config = Spxw75DteBwbConfig()
        # No broker connection exists: any attempt past the guard would fail.
        monitor._evaluate_entry(
            MonitorState(evaluated_days=["2026-09-04"]),
            pd.Timestamp("2026-09-04 09:32:00"),
        )

    def test_daily_manifest_coexists_with_prior_version_and_is_idempotent(self) -> None:
        store = JournalStore(":memory:")
        try:
            old_variant = f"{bwb_manifest.STRATEGY_ID}@1.5.0-paper-weekday-fill-experiment"
            with patch.multiple(
                bwb_manifest,
                STRATEGY_VERSION="1.5.0-paper-weekday-fill-experiment",
                STRATEGY_VARIANT_ID=old_variant,
                ENGINE_VERSION="spxw-75dte-put-bwb-live@1.5.0",
                ENTRY_RULE_VERSION="1.5.0",
                EXIT_RULE_VERSION="1.0.3",
                CREATED_AT_UTC="2026-08-13T19:55:00Z",
            ):
                register_manifest(store, Spxw75DteBwbConfig())
            before = [tuple(row) for row in store._connection.execute("SELECT * FROM rule_versions")]
            register_manifest(store, Spxw75DteBwbConfig())
            register_manifest(store, Spxw75DteBwbConfig())
            after = [tuple(row) for row in store._connection.execute("SELECT * FROM rule_versions")]
            self.assertEqual(4, len(after))
            self.assertTrue(all(row in after for row in before))
        finally:
            store.close()

    def test_paper_guard_cannot_be_bypassed(self) -> None:
        require_paper_account("DU123")
        with self.assertRaisesRegex(RuntimeError, "DU paper"):
            require_paper_account("U123")

    def test_accepted_dry_run_creates_auditable_three_leg_bag(self) -> None:
        config = Spxw75DteBwbConfig()
        evaluation = evaluate_entry("2026-08-13", opening_vix=18.2, config=config)
        store = JournalStore(":memory:")
        try:
            register_manifest(store, config)
            monitor = Spxw75DtePutBwbMonitor.__new__(Spxw75DtePutBwbMonitor)
            monitor.monitor_config = MonitorConfig(
                pd.Timestamp("2026-08-13"), "DU123", 62, False, Path(":memory:")
            )
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
            blocker = store._connection.execute(
                "SELECT status, event_type FROM risk_events WHERE trade_id=?", (position.trade_id,)
            ).fetchone()
            self.assertEqual("dry_run", position.status)
            self.assertEqual("BAG", trade["asset_class"])
            self.assertEqual(["BUY", "SELL", "BUY"], [leg["side"] for leg in legs])
            self.assertEqual([1, 2, 1], [leg["ratio"] for leg in legs])
            self.assertEqual(("execution_blocked", "dry_run_no_order"), tuple(blocker))
        finally:
            store.close()

    def test_entry_fill_automatically_submits_inverse_gtc_target(self) -> None:
        chosen = selection()
        position = PositionState(
            "2026-07-28", chosen.expiry, 18.2, chosen.spot,
            chosen.upper_strike, chosen.body_strike, chosen.lower_strike,
            101, 102, 103, chosen.entry_limit_credit, chosen.maximum_loss_dollars,
            "run", "signal", "decision", "trade", "SPX75-E-test", "SPX75-X-test",
            status="entry_submitted", entry_order_id=11,
        )
        entry_order = SimpleNamespace(orderId=11, orderRef="SPX75-E-test")
        entry_trade = SimpleNamespace(
            order=entry_order,
            orderStatus=SimpleNamespace(status="Filled", avgFillPrice=-1.8),
            fills=(SimpleNamespace(execution=SimpleNamespace(time=pd.Timestamp("2026-08-13T14:17:56Z"))),),
        )
        placed = []

        def place_order(combo, order):
            placed.append((combo, order))
            order.orderId = 12
            return SimpleNamespace(order=order, orderStatus=SimpleNamespace(status="Submitted", avgFillPrice=0.0))

        monitor = Spxw75DtePutBwbMonitor.__new__(Spxw75DtePutBwbMonitor)
        monitor.monitor_config = MonitorConfig(pd.Timestamp("2026-08-13"), "DU123", 62, True, Path(":memory:"))
        monitor.strategy_config = Spxw75DteBwbConfig()
        monitor.ib = SimpleNamespace(placeOrder=place_order, sleep=lambda _: None)
        monitor.executions = SimpleNamespace(ingest_trade=lambda *args, **kwargs: None)
        monitor._execution_context = lambda _: None
        monitor._finish_run = lambda *args: None
        monitor._selection = lambda _: chosen
        monitor._find_trade = lambda ref, oid: entry_trade if ref == "SPX75-E-test" else None
        monitor.log = lambda _: None
        monitor._service_orders(MonitorState(positions=[position]))
        self.assertEqual("target_submitted", position.status)
        self.assertEqual(1.8, position.entry_fill_credit)
        self.assertEqual("2026-08-13T14:17:56Z", position.entry_filled_at_utc)
        self.assertEqual(1.05, position.target_debit)
        self.assertEqual(12, position.target_order_id)
        self.assertEqual("GTC", placed[0][1].tif)
        self.assertEqual(["SELL", "BUY", "SELL"], [leg.action for leg in placed[0][0].comboLegs])

    def test_unfilled_entry_runs_three_five_minute_price_stages_then_cancels(self) -> None:
        chosen = selection()
        position = PositionState(
            "2026-08-13", chosen.expiry, 18.2, chosen.spot,
            chosen.upper_strike, chosen.body_strike, chosen.lower_strike,
            101, 102, 103, chosen.entry_limit_credit, chosen.maximum_loss_dollars,
            "run", "signal", "decision", "trade", "SPX75-E-test", "SPX75-X-test",
            status="entry_submitted", entry_order_id=11,
            entry_submitted_at_utc="2026-08-13T14:00:00Z",
            entry_stage_started_at_utc="2026-08-13T14:00:00Z",
        )
        order = SimpleNamespace(orderId=11, orderRef="SPX75-E-test", lmtPrice=-1.8)
        trade = SimpleNamespace(
            order=order,
            orderStatus=SimpleNamespace(status="Submitted", avgFillPrice=0.0),
            fills=(),
        )
        placed = []
        cancelled = []

        def place_order(combo, modified_order):
            placed.append((combo, modified_order.orderId, modified_order.orderRef, modified_order.lmtPrice))
            return trade

        monitor = Spxw75DtePutBwbMonitor.__new__(Spxw75DtePutBwbMonitor)
        monitor.monitor_config = MonitorConfig(pd.Timestamp("2026-08-13"), "DU123", 62, True, Path(":memory:"))
        monitor.strategy_config = Spxw75DteBwbConfig()
        monitor.options_risk_budget = None
        monitor.ib = SimpleNamespace(
            placeOrder=place_order,
            cancelOrder=lambda observed: cancelled.append(observed.orderId),
            sleep=lambda _: None,
        )
        monitor.executions = SimpleNamespace(ingest_trade=lambda *args, **kwargs: None)
        monitor._execution_context = lambda _: None
        monitor._finish_run = lambda *args: None
        monitor._selection = lambda _: chosen
        monitor._find_trade = lambda ref, oid: trade
        monitor.log = lambda _: None

        monitor._service_orders(MonitorState(positions=[position]))

        self.assertEqual(2, position.entry_price_stage)
        self.assertAlmostEqual(1.6, position.planned_credit)
        self.assertEqual([(11, "SPX75-E-test", -1.6)], [(item[1], item[2], item[3]) for item in placed])
        self.assertEqual([], cancelled)

        position.entry_stage_started_at_utc = "2026-08-13T14:00:00Z"
        monitor._service_orders(MonitorState(positions=[position]))

        self.assertEqual(3, position.entry_price_stage)
        self.assertAlmostEqual(1.35, position.planned_credit)
        self.assertAlmostEqual(2.0, position.final_mid_credit)
        self.assertEqual(
            [(11, "SPX75-E-test", -1.6), (11, "SPX75-E-test", -1.35)],
            [(item[1], item[2], item[3]) for item in placed],
        )
        self.assertEqual([], cancelled)

        position.entry_stage_started_at_utc = "2026-08-13T14:00:00Z"
        monitor._service_orders(MonitorState(positions=[position]))

        self.assertEqual([11], cancelled)
        self.assertEqual("entry_cancel_requested", position.status)

        trade.orderStatus.status = "Cancelled"
        monitor._service_orders(MonitorState(positions=[position]))
        self.assertEqual("entry_not_filled", position.status)

    def test_only_named_isolated_test_exposure_is_allowed_during_weekday_experiment(self) -> None:
        chosen = selection()
        test_trade = SimpleNamespace(
            order=SimpleNamespace(orderId=4633, orderRef="SPX75-TEST-0813-X"),
            orderStatus=SimpleNamespace(status="Submitted"),
            contract=SimpleNamespace(comboLegs=chosen.inverse_combo_contract.comboLegs),
        )
        test_position = SimpleNamespace(
            account="DU123",
            contract=SimpleNamespace(conId=101, tradingClass="SPXW"),
            position=1.0,
        )
        monitor = Spxw75DtePutBwbMonitor.__new__(Spxw75DtePutBwbMonitor)
        monitor.monitor_config = MonitorConfig(pd.Timestamp("2026-08-17"), "DU123", 62, True, Path(":memory:"))
        monitor.strategy_config = Spxw75DteBwbConfig()
        monitor.ib = SimpleNamespace(positions=lambda: (test_position,))
        monitor._all_trades = lambda: (test_trade,)

        self.assertFalse(
            monitor._unknown_exposure_exists(
                MonitorState(), chosen, signal_day="2026-08-17"
            )
        )
        self.assertTrue(
            monitor._unknown_exposure_exists(
                MonitorState(), chosen, signal_day="2026-09-03"
            )
        )

        test_trade.order.orderRef = "SPX75-UNKNOWN-X"
        self.assertTrue(
            monitor._unknown_exposure_exists(
                MonitorState(), chosen, signal_day="2026-08-17"
            )
        )


if __name__ == "__main__":
    unittest.main()
