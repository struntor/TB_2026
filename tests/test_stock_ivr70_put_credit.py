from __future__ import annotations

from dataclasses import asdict, replace
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import pandas as pd
from ib_insync import Contract

from src.bot.stock_ivr70_put_credit_monitor import MonitorState, PositionState
from src.bot.stock_ivr70_put_credit_monitor import (
    MonitorConfig,
    StockIvr70PutCreditMonitor,
)
from src.execution.stock_put_credit import (
    OptionQuote,
    StockPutCreditSelectionError,
    StockPutCreditSelection,
    _expiry,
    _select_quoted_vertical,
    build_entry_order,
    build_exit_order,
    build_vertical_combo,
    require_paper_account,
)
from src.risk.options_risk_budget import OptionsRiskBudget
from src.journal.decision_ingest import DecisionJournalIngestor
from src.journal.execution_ingest import ExecutionJournalIngestor
from src.journal.models import RuleDefinitionRecord
from src.journal.store import JournalStore
from src.strategies.stock_ivr70_journal import register_manifest
from src.strategies.stock_ivr70_put_credit import (
    StockIvr70Config,
    apply_expiry_and_earnings_gate,
    apply_quote_and_expiry_gates,
    evaluate_candidate,
)


def option(con_id: int, strike: float) -> Contract:
    return Contract(
        conId=con_id,
        symbol="MSFT",
        secType="OPT",
        exchange="SMART",
        currency="USD",
        tradingClass="MSFT",
        localSymbol=f"MSFT-{con_id}",
        strike=strike,
        right="P",
    )


def selection() -> StockPutCreditSelection:
    short, long = option(101, 500.0), option(102, 490.0)
    short_quote = OptionQuote(4.20, 4.40, -0.19, 0.01, -0.05, 0.10, 0.31)
    long_quote = OptionQuote(2.00, 2.20, -0.14, 0.008, -0.04, 0.08, 0.32)
    return StockPutCreditSelection(
        "MSFT",
        "20260904",
        500.0,
        490.0,
        short,
        long,
        short_quote,
        long_quote,
        build_vertical_combo("MSFT", short, long),
        build_vertical_combo("MSFT", short, long, inverse=True),
        0.01,
        100,
        550.0,
    )


class StrategyTests(unittest.TestCase):
    def test_manifest_keeps_the_registered_rule_definition_stable(self) -> None:
        store = JournalStore(":memory:")
        try:
            store.register_rule(
                RuleDefinitionRecord(
                    "stock-ivr70-causal-cross-entry",
                    "stock-ivr70-causal-cross-entry",
                    "Causal modeled-IVR cross above 70 with earnings and live quote gates",
                    {
                        "asset": "US single-stock options",
                        "structure": "42DTE 10-point put credit",
                    },
                )
            )
            register_manifest(store, StockIvr70Config())
        finally:
            store.close()

    def candidate(self, **changes: object) -> dict[str, object]:
        row: dict[str, object] = {
            "symbol": "MSFT",
            "signal_date": "2026-07-20",
            "entry_date": "2026-07-21",
            "modeled_iv_rank": 72.0,
            "previous_modeled_iv_rank": 69.0,
            "median_dollar_volume60": 2_000_000_000.0,
            "predicted_earnings": "2026-10-30",
        }
        row.update(changes)
        return row

    def test_requires_a_causal_cross_not_merely_high_ivr(self) -> None:
        config = StockIvr70Config()
        self.assertTrue(evaluate_candidate(self.candidate(), config).accepted)
        no_cross = evaluate_candidate(
            self.candidate(previous_modeled_iv_rank=71.0), config
        )
        self.assertFalse(no_cross.accepted)
        self.assertEqual("modeled_ivr_cross_above_70", no_cross.reason)

    def test_actual_expiry_controls_the_earnings_veto(self) -> None:
        config = StockIvr70Config()
        evaluation = evaluate_candidate(self.candidate(), config)
        accepted = apply_quote_and_expiry_gates(
            evaluation,
            expiry="20260904",
            conservative_credit=2.0,
            maximum_loss_dollars=800.0,
            config=config,
        )
        self.assertTrue(accepted.accepted)
        vetoed = apply_quote_and_expiry_gates(
            replace(evaluation, predicted_earnings="2026-09-08"),
            expiry="20260904",
            conservative_credit=2.0,
            maximum_loss_dollars=800.0,
            config=config,
        )
        self.assertFalse(vetoed.accepted)
        self.assertEqual("earnings_after_expiry_buffer", vetoed.reason)

    def test_earnings_gate_can_run_before_option_quotes(self) -> None:
        evaluation = evaluate_candidate(self.candidate(), StockIvr70Config())
        result = apply_expiry_and_earnings_gate(
            replace(evaluation, predicted_earnings="2026-08-01"),
            expiry="20260904",
            config=StockIvr70Config(),
        )
        self.assertFalse(result.accepted)
        self.assertEqual("earnings_after_expiry_buffer", result.reason)


class ExecutionAndRiskTests(unittest.TestCase):
    def test_no_expiration_has_a_stable_reason_code(self) -> None:
        with self.assertRaises(StockPutCreditSelectionError) as raised:
            _expiry(
                ["20260821", "20260918"],
                pd.Timestamp("2026-07-23"),
                42,
                35,
                50,
            )
        self.assertEqual("no_expiration_in_dte_window", raised.exception.reason_code)

    def test_pair_selection_does_not_escape_to_a_far_delta_for_positive_credit(self) -> None:
        quotes = {
            100.0: (option(201, 100.0), OptionQuote(1.0, 1.2, -0.19, None, None, None, 0.3)),
            90.0: (option(202, 90.0), OptionQuote(1.8, 2.0, -0.10, None, None, None, 0.3)),
            95.0: (option(203, 95.0), OptionQuote(2.0, 2.2, -0.25, None, None, None, 0.3)),
            85.0: (option(204, 85.0), OptionQuote(0.8, 1.0, -0.08, None, None, None, 0.3)),
        }
        with self.assertRaises(StockPutCreditSelectionError) as raised:
            _select_quoted_vertical(
                quotes,
                target_delta=-0.19,
                minimum_abs_delta=0.16,
                maximum_abs_delta=0.22,
                width=10.0,
            )
        self.assertEqual(
            "no_positive_conservative_credit",
            raised.exception.reason_code,
        )

    def test_pair_selection_accepts_delta_nearest_pair_with_positive_credit(self) -> None:
        quotes = {
            100.0: (option(201, 100.0), OptionQuote(2.2, 2.4, -0.19, None, None, None, 0.3)),
            90.0: (option(202, 90.0), OptionQuote(0.8, 1.0, -0.10, None, None, None, 0.3)),
            95.0: (option(203, 95.0), OptionQuote(2.0, 2.2, -0.25, None, None, None, 0.3)),
            85.0: (option(204, 85.0), OptionQuote(0.8, 1.0, -0.08, None, None, None, 0.3)),
        }
        chosen = _select_quoted_vertical(
            quotes,
            target_delta=-0.19,
            minimum_abs_delta=0.16,
            maximum_abs_delta=0.22,
            width=10.0,
        )
        self.assertEqual((100.0, 90.0), (chosen[0], chosen[3]))
        self.assertAlmostEqual(1.2, chosen[2].bid - chosen[5].ask)

    def test_pair_selection_rejects_when_all_conservative_credits_are_nonpositive(self) -> None:
        quotes = {
            100.0: (option(201, 100.0), OptionQuote(1.0, 1.2, -0.19, None, None, None, 0.3)),
            90.0: (option(202, 90.0), OptionQuote(1.8, 2.0, -0.10, None, None, None, 0.3)),
        }
        with self.assertRaises(StockPutCreditSelectionError) as raised:
            _select_quoted_vertical(
                quotes,
                target_delta=-0.19,
                minimum_abs_delta=0.16,
                maximum_abs_delta=0.22,
                width=10.0,
            )
        self.assertEqual(
            "no_positive_conservative_credit",
            raised.exception.reason_code,
        )

    def test_orders_are_paper_only_combo_limits(self) -> None:
        chosen = selection()
        entry = build_entry_order(chosen, account="DU123", order_ref="IVR70-E-test")
        exit_order = build_exit_order(chosen, account="DU123", order_ref="IVR70-X-test")
        self.assertEqual(-2.0, entry.lmtPrice)
        self.assertEqual(2.4, exit_order.lmtPrice)
        self.assertEqual(["SELL", "BUY"], [leg.action for leg in chosen.combo_contract.comboLegs])
        self.assertEqual(["BUY", "SELL"], [leg.action for leg in chosen.inverse_combo_contract.comboLegs])
        with self.assertRaisesRegex(RuntimeError, "DU paper"):
            require_paper_account("U123")

    def test_shared_risk_budget_sums_independent_strategies(self) -> None:
        budget = OptionsRiskBudget(2_500.0)
        budget.register("spxw", lambda: 900.0)
        budget.register("stock-ivr70", lambda: 804.0)
        self.assertEqual(1_704.0, budget.open_risk_usd)
        self.assertEqual(796.0, budget.available_risk_usd)
        self.assertFalse(budget.can_allocate(800.0))

    def test_accepted_dry_run_writes_bag_legs_and_explicit_blocker(self) -> None:
        config = StockIvr70Config()
        evaluation = apply_quote_and_expiry_gates(
            evaluate_candidate(StrategyTests().candidate(), config),
            expiry=selection().expiry,
            conservative_credit=selection().conservative_credit,
            maximum_loss_dollars=selection().maximum_loss_dollars,
            config=config,
        )
        store = JournalStore(":memory:")
        try:
            register_manifest(store, config)
            monitor = StockIvr70PutCreditMonitor.__new__(StockIvr70PutCreditMonitor)
            monitor.monitor_config = MonitorConfig(
                day=pd.Timestamp("2026-07-21"),
                account="DU123",
                client_id=62,
                place_orders=False,
                journal_path=Path(":memory:"),
                candidates_path=Path("unused.csv"),
                state_path=Path("unused.json"),
                poll_seconds=15.0,
                entry_work_minutes=5,
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
            self.assertEqual("dry_run", position.status)
            self.assertEqual("BAG", trade["asset_class"])
            self.assertEqual(["SELL", "BUY"], [leg["side"] for leg in legs])
            self.assertEqual(("execution_blocked", "dry_run_no_order"), tuple(blocker))
            self.assertEqual(804.0, position.maximum_loss_dollars)
        finally:
            store.close()

    def test_state_roundtrip_keeps_daily_exit_throttle_and_assignment_risk(self) -> None:
        position = PositionState(
            "MSFT", "2026-07-20", "2026-07-21", "2026-10-30", "20260904",
            500.0, 490.0, 101, 102, 2.0, 2.2, 804.0,
            "run", "signal", "decision", "trade", "IVR70-E-test",
            status="assignment_attention",
        )
        state = MonitorState(
            evaluated_days=["2026-07-21"],
            exit_evaluated_days={"trade": ["2026-07-22"]},
            positions=[position],
        )
        payload = json.loads(json.dumps(asdict(state)))
        payload["positions"] = [PositionState(**item) for item in payload["positions"]]
        observed = MonitorState(**payload)
        self.assertEqual(["2026-07-22"], observed.exit_evaluated_days["trade"])
        self.assertEqual("assignment_attention", observed.positions[0].status)

    def test_cancelled_exit_keeps_position_open_and_risk_active(self) -> None:
        position = PositionState(
            "MSFT", "2026-07-20", "2026-07-21", "2026-10-30", "20260904",
            500.0, 490.0, 101, 102, 2.0, 2.2, 804.0,
            "run", "signal", "decision", "trade", "IVR70-E-test",
            status="exit_submitted", exit_order_ref="IVR70-X-test", exit_order_id=77,
        )
        broker_trade = SimpleNamespace(
            order=SimpleNamespace(orderId=77, orderRef="IVR70-X-test"),
            orderStatus=SimpleNamespace(status="Cancelled", avgFillPrice=0.0),
        )
        monitor = StockIvr70PutCreditMonitor.__new__(StockIvr70PutCreditMonitor)
        monitor.monitor_config = MonitorConfig(
            day=pd.Timestamp("2026-07-22"), account="DU123", client_id=62,
            place_orders=True, journal_path=Path(":memory:"),
            candidates_path=Path("unused.csv"), state_path=Path("unused.json"),
            poll_seconds=15.0, entry_work_minutes=5,
        )
        monitor.ib = SimpleNamespace(
            trades=lambda: (broker_trade,), reqCompletedOrders=lambda _: ()
        )
        monitor.executions = SimpleNamespace(ingest_trade=lambda *args, **kwargs: None)
        monitor._finish_run = lambda *args, **kwargs: None
        monitor._service_orders(MonitorState(positions=[position]))
        self.assertEqual("open", position.status)
        self.assertIn("Cancelled", position.last_error)

    def test_expected_selection_rejection_is_journaled_with_precise_reason(self) -> None:
        config = StockIvr70Config()
        store = JournalStore(":memory:")
        try:
            register_manifest(store, config)
            monitor = StockIvr70PutCreditMonitor.__new__(StockIvr70PutCreditMonitor)
            monitor.monitor_config = MonitorConfig(
                day=pd.Timestamp("2026-07-23"), account="DU123", client_id=62,
                place_orders=False, journal_path=Path(":memory:"),
                candidates_path=Path("unused.csv"), state_path=Path("unused.json"),
                poll_seconds=15.0, entry_work_minutes=5,
            )
            monitor.strategy_config = config
            monitor.options_risk_budget = OptionsRiskBudget(2_500.0)
            monitor.ib = SimpleNamespace()
            monitor.store = store
            monitor.decisions = DecisionJournalIngestor(store)
            monitor.executions = ExecutionJournalIngestor(store)
            monitor.log = lambda _: None
            monitor._load_candidates = lambda _: pd.DataFrame(
                [StrategyTests().candidate(entry_date="2026-07-23")]
            )
            with patch(
                "src.bot.stock_ivr70_put_credit_monitor.select_stock_put_expiry",
                side_effect=StockPutCreditSelectionError(
                    "no_expiration_in_dte_window", "no eligible expiry"
                ),
            ):
                monitor._evaluate_entries(
                    MonitorState(), pd.Timestamp("2026-07-23 09:40", tz="America/New_York")
                )
            row = store._connection.execute(
                "SELECT status, primary_reason FROM signal_decisions"
            ).fetchone()
            self.assertEqual(
                ("vetoed", "no_expiration_in_dte_window"),
                tuple(row),
            )
        finally:
            store.close()


if __name__ == "__main__":
    unittest.main()
