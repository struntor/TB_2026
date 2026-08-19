from __future__ import annotations

import sqlite3
import unittest
from dataclasses import replace
from pathlib import Path
import uuid

from src.journal.models import (
    CommissionRecord,
    DecisionFactorRecord,
    ExecutionRecord,
    OrderLegRecord,
    OrderRecord,
    OrderStatusEventRecord,
    ReconciliationRecord,
    RiskEventRecord,
    SignalDecisionRecord,
    StrategyRunRecord,
    TradeEventRecord,
    TradeLegRecord,
    TradeRecord,
    deterministic_id,
)
from src.journal.store import JournalStore


NOW = "2026-07-15T09:35:00Z"


def run_record(run_id: str, run_type: str) -> StrategyRunRecord:
    return StrategyRunRecord(
        run_id=run_id,
        strategy_id="manual-regime",
        strategy_version="v1",
        config_hash="abc123",
        run_type=run_type,
        environment="paper" if run_type != "backtest" else "historical",
        started_at_utc=NOW,
        ended_at_utc=None,
        status="running",
        parameters={"risk": 0.01},
        metadata={"owner": "test"},
    )


def decision_record(run_id: str, signal_key: str, status: str = "accepted") -> SignalDecisionRecord:
    return SignalDecisionRecord(
        decision_id=deterministic_id("decision", run_id, signal_key),
        run_id=run_id,
        signal_key=signal_key,
        decision_key="decision:key",
        signal_key_version="v1",
        decision_stage="live_final",
        strategy_id="manual-regime",
        strategy_version="v1",
        engine_version="engine-v1",
        config_hash="abc123",
        decision_time_utc=NOW,
        symbol="SPY",
        primary_rule="breakout",
        side="long",
        track="baseline",
        occurrence=0,
        status=status,
        trade_action="trade" if status == "accepted" else "no_trade",
        setup_state="ready",
        primary_reason="breakout",
        context={"price": 600.0},
        raw_record={"signal_key": signal_key, "status": status},
    )


def factor_record(decision_id: str) -> DecisionFactorRecord:
    return DecisionFactorRecord(
        factor_id=deterministic_id("factor", decision_id, 0),
        decision_id=decision_id,
        position=0,
        factor_code="close_above_level",
        category="entry_rule",
        observed_value=601.0,
        operator=">",
        threshold=600.0,
        passed=True,
        is_veto=False,
        reason="breakout confirmed",
        as_of_time_utc=NOW,
        calculation_version="factor-v1",
    )


def trade_record(run_id: str, trade_id: str, signal_key: str, decision_id: str) -> TradeRecord:
    return TradeRecord(
        trade_id=trade_id,
        run_id=run_id,
        signal_key=signal_key,
        decision_id=decision_id,
        asset_class="BAG",
        symbol="SPX",
        side="long",
        status="planned",
        opened_at_utc=None,
        closed_at_utc=None,
        quantity=1.0,
        currency="USD",
        entry_price=None,
        exit_price=None,
        realized_pnl=None,
        metadata={"structure": "vertical"},
    )


class TradingJournalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.path = Path(__file__).parent / f".journal-{uuid.uuid4().hex}.sqlite"
        self.store = JournalStore(self.path, busy_timeout_ms=2_000)

    def tearDown(self) -> None:
        self.store.close()
        for suffix in ("", "-wal", "-shm"):
            Path(str(self.path) + suffix).unlink(missing_ok=True)

    def test_schema_and_connection_safety_pragmas(self) -> None:
        self.assertEqual(self.store.schema_version, 2)
        self.assertEqual(self.store.pragma("foreign_keys"), 1)
        self.assertEqual(str(self.store.pragma("journal_mode")).lower(), "wal")
        self.assertEqual(self.store.pragma("busy_timeout"), 2_000)

        reopened = JournalStore(self.path)
        try:
            self.assertEqual(reopened.schema_version, 2)
        finally:
            reopened.close()

    def test_decision_upsert_is_idempotent_and_scoped_to_run(self) -> None:
        self.store.start_run(run_record("backtest-1", "backtest"))
        self.store.start_run(run_record("paper-1", "paper"))
        for run_id in ("backtest-1", "paper-1"):
            decision = decision_record(run_id, "signal:same")
            factor = factor_record(decision.decision_id)
            self.store.upsert_decision(decision, (factor,))
            self.store.upsert_decision(decision, (factor,))

        self.assertEqual(self.store.count("signal_decisions"), 2)
        self.assertEqual(self.store.count("decision_factors"), 2)
        self.assertIsNotNone(self.store.get_decision("backtest-1", "signal:same"))
        self.assertIsNotNone(self.store.get_decision("paper-1", "signal:same"))

    def test_run_identity_cannot_be_silently_reassigned(self) -> None:
        original = run_record("stable-run", "paper")
        self.store.start_run(original)
        conflicts = (
            replace(original, run_type="backtest"),
            replace(original, strategy_version="v2"),
            replace(original, config_hash="different"),
            replace(original, started_at_utc="2026-07-15T09:36:00Z"),
        )
        for conflicting in conflicts:
            with self.subTest(conflicting=conflicting):
                with self.assertRaisesRegex(RuntimeError, "cannot be reassigned"):
                    self.store.start_run(conflicting)
        self.store.start_run(
            replace(
                original,
                status="completed",
                ended_at_utc="2026-07-15T20:00:00Z",
                metadata={"result": "ok"},
            )
        )
        self.assertEqual(self.store.count("strategy_runs"), 1)

    def test_decision_and_factor_write_rolls_back_together(self) -> None:
        self.store.start_run(run_record("paper-1", "paper"))
        decision = decision_record("paper-1", "signal:rollback")
        wrong_factor = factor_record("another-decision")
        with self.assertRaisesRegex(ValueError, "does not match"):
            self.store.upsert_decision(decision, (wrong_factor,))
        self.assertEqual(self.store.count("signal_decisions"), 0)
        self.assertEqual(self.store.count("decision_factors"), 0)

    def test_live_final_decision_cannot_be_downgraded_by_engine_replay(self) -> None:
        run_id = "paper-final"
        signal_key = "signal:final"
        self.store.start_run(run_record(run_id, "paper"))
        final = decision_record(run_id, signal_key)
        self.store.upsert_decision(final, (factor_record(final.decision_id),))
        stale_engine = replace(
            final,
            decision_stage="engine",
            status="accepted",
            primary_reason="stale engine replay",
        )
        self.store.upsert_decision(stale_engine)
        saved = self.store.get_decision(run_id, signal_key)
        self.assertEqual(saved["decision_stage"], "live_final")
        self.assertEqual(saved["primary_reason"], "breakout")
        self.assertEqual(self.store.count("decision_factors"), 1)

    def test_foreign_keys_and_outer_transaction_rollback(self) -> None:
        missing_parent_trade = trade_record(
            "missing-run", "trade:missing", "signal:missing", "decision:missing"
        )
        with self.assertRaises((sqlite3.IntegrityError, ValueError)):
            self.store.create_trade(missing_parent_trade)

        with self.assertRaises((sqlite3.IntegrityError, ValueError)):
            with self.store.transaction():
                self.store.start_run(run_record("atomic-run", "paper"))
                self.store.create_trade(missing_parent_trade)
        self.assertEqual(self.store.count("strategy_runs"), 0)

    def test_option_combo_legs_are_normalized_and_idempotent(self) -> None:
        run_id = "paper-options"
        signal_key = "signal:options"
        self.store.start_run(run_record(run_id, "paper"))
        decision = decision_record(run_id, signal_key)
        self.store.upsert_decision(decision)
        trade_id = deterministic_id("trade", run_id, signal_key)
        trade = trade_record(run_id, trade_id, signal_key, decision.decision_id)
        legs = (
            TradeLegRecord(
                leg_id=deterministic_id("leg", trade_id, 0),
                trade_id=trade_id,
                leg_index=0,
                asset_class="OPT",
                symbol="SPX",
                con_id=1001,
                local_symbol="SPX  260717C06000000",
                exchange="SMART",
                currency="USD",
                side="BUY",
                ratio=1,
                quantity=1.0,
                expiry="2026-07-17",
                strike=6000.0,
                option_right="C",
                multiplier=100.0,
                open_price=20.0,
                close_price=None,
                metadata={"delta": 0.55},
            ),
            TradeLegRecord(
                leg_id=deterministic_id("leg", trade_id, 1),
                trade_id=trade_id,
                leg_index=1,
                asset_class="OPT",
                symbol="SPX",
                con_id=1002,
                local_symbol="SPX  260717C06050000",
                exchange="SMART",
                currency="USD",
                side="SELL",
                ratio=1,
                quantity=1.0,
                expiry="2026-07-17",
                strike=6050.0,
                option_right="C",
                multiplier=100.0,
                open_price=8.0,
                close_price=None,
                metadata={"delta": 0.30},
            ),
        )
        self.store.create_trade(trade, legs)
        self.store.create_trade(trade, legs)
        saved = self.store.list_trade_legs(trade_id)
        self.assertEqual([row["leg_index"] for row in saved], [0, 1])
        self.assertEqual([row["option_right"] for row in saved], ["C", "C"])
        self.assertEqual(self.store.count("trade_legs"), 2)
        self.store.create_trade(replace(trade, status="open"))
        self.assertEqual(self.store.count("trade_legs"), 2)
        self.store.create_trade(replace(trade, status="closed"), ())
        self.assertEqual(self.store.count("trade_legs"), 0)

    def test_reconciliation_links_live_and_backtest_by_signal(self) -> None:
        signal_key = "signal:reconcile"
        trade_ids: dict[str, str] = {}
        for run_id, run_type in (("backtest-run", "backtest"), ("live-run", "live")):
            self.store.start_run(run_record(run_id, run_type))
            decision = decision_record(run_id, signal_key)
            self.store.upsert_decision(decision)
            trade_id = deterministic_id("trade", run_id, signal_key)
            trade_ids[run_type] = trade_id
            self.store.create_trade(
                trade_record(run_id, trade_id, signal_key, decision.decision_id)
            )

        record = ReconciliationRecord(
            reconciliation_id=deterministic_id(
                "reconciliation", trade_ids["live"], trade_ids["backtest"]
            ),
            live_trade_id=trade_ids["live"],
            reference_trade_id=trade_ids["backtest"],
            signal_key=signal_key,
            status="matched",
            matched_at_utc=NOW,
            price_slippage=0.25,
            quantity_difference=0.0,
            pnl_difference=None,
            notes=None,
            details={"method": "exact_signal_key"},
        )
        self.store.upsert_reconciliation(record)
        self.store.upsert_reconciliation(record)
        self.assertEqual(self.store.count("reconciliation"), 1)
        with self.assertRaisesRegex(ValueError, "paper/live to backtest"):
            self.store.upsert_reconciliation(
                replace(
                    record,
                    reconciliation_id="reconciliation:reverse",
                    live_trade_id=record.reference_trade_id,
                    reference_trade_id=record.live_trade_id,
                )
            )

    def test_execution_lifecycle_and_broker_keys_are_idempotent(self) -> None:
        run_id = "paper-execution"
        signal_key = "signal:execution"
        self.store.start_run(run_record(run_id, "paper"))
        decision = decision_record(run_id, signal_key)
        self.store.upsert_decision(decision)
        trade_id = deterministic_id("trade", run_id, signal_key)
        self.store.create_trade(
            trade_record(run_id, trade_id, signal_key, decision.decision_id)
        )
        order_id = deterministic_id("order", "IBKR", "DU123", 7, 101)
        order = OrderRecord(
            order_id=order_id,
            run_id=run_id,
            trade_id=trade_id,
            signal_key=signal_key,
            parent_order_id=None,
            broker="IBKR",
            broker_order_id="clientId:7:orderId:101",
            order_ref="journal-test",
            order_type="LMT",
            action="BUY",
            quantity=1.0,
            limit_price=600.0,
            stop_price=None,
            time_in_force="DAY",
            transmit=True,
            status="Submitted",
            submitted_at_utc=NOW,
            updated_at_utc=NOW,
            metadata={
                "account": "DU123",
                "client_id": 7,
                "order_id": 101,
                "perm_id": 9001,
            },
        )
        status = OrderStatusEventRecord(
            event_id=deterministic_id("order-status", order_id, "submitted"),
            order_id=order_id,
            status="Submitted",
            occurred_at_utc=NOW,
            filled_quantity=0.0,
            remaining_quantity=1.0,
            average_fill_price=None,
            broker_message=None,
            payload={"source": "snapshot"},
        )
        execution = ExecutionRecord(
            execution_id=deterministic_id("execution", "IBKR", "E1"),
            order_id=order_id,
            trade_id=trade_id,
            broker_execution_id="E1",
            executed_at_utc=NOW,
            side="BOT",
            quantity=1.0,
            price=599.9,
            exchange="CBOE",
            liquidity="1",
            metadata={"account": "DU123"},
        )
        commission = CommissionRecord(
            commission_id=deterministic_id("commission", execution.execution_id),
            execution_id=execution.execution_id,
            currency="USD",
            amount=1.25,
            realized_pnl=None,
            reported_at_utc=NOW,
            metadata={},
        )
        event = TradeEventRecord(
            event_id=deterministic_id("event", order_id, "submitted"),
            run_id=run_id,
            trade_id=trade_id,
            order_id=order_id,
            signal_key=signal_key,
            event_type="order_submitted",
            occurred_at_utc=NOW,
            payload={},
        )
        risk = RiskEventRecord(
            risk_event_id=deterministic_id("risk", trade_id, "approved"),
            run_id=run_id,
            trade_id=trade_id,
            decision_id=decision.decision_id,
            signal_key=signal_key,
            event_type="pre_trade_risk",
            status="approved",
            occurred_at_utc=NOW,
            risk_amount=100.0,
            risk_currency="USD",
            payload={"limit": 200.0},
        )
        for _ in range(2):
            self.store.upsert_order(order)
            self.store.add_order_status(status)
            self.store.upsert_execution(execution)
            self.store.upsert_commission(commission)
            self.store.add_event(event)
            self.store.add_risk_event(risk)
        for table in (
            "orders", "order_status_events", "executions", "commissions",
            "trade_events", "risk_events",
        ):
            self.assertEqual(self.store.count(table), 1, table)

        later_commission = replace(
            commission,
            reported_at_utc="2026-07-15T10:00:00Z",
            amount=1.30,
        )
        self.store.upsert_commission(later_commission)
        saved_report = self.store._connection.execute(
            "SELECT reported_at_utc, amount FROM commissions WHERE commission_id=?",
            (commission.commission_id,),
        ).fetchone()
        self.assertEqual(saved_report["reported_at_utc"], NOW)
        self.assertEqual(saved_report["amount"], 1.30)

        order_leg = OrderLegRecord(
            order_leg_id="order-leg:one",
            order_id=order_id,
            leg_index=0,
            trade_leg_id=None,
            con_id=123,
            symbol="SPX",
            action="BUY",
            ratio=1,
            exchange="SMART",
            open_close="O",
            metadata={},
        )
        self.store.upsert_order(order, (order_leg,))
        self.store.upsert_order(replace(order, status="Filled"))
        self.assertEqual(self.store.count("order_legs"), 1)
        self.store.upsert_order(replace(order, status="Filled"), ())
        self.assertEqual(self.store.count("order_legs"), 0)

        conflicting_order = replace(order, order_id="order:conflicting-local-id")
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.upsert_order(conflicting_order)

    def test_strict_json_and_aware_utc_are_required(self) -> None:
        invalid_json = run_record("invalid-json", "paper")
        object.__setattr__(invalid_json, "metadata", {"bad": float("nan")})
        with self.assertRaisesRegex(ValueError, "finite"):
            self.store.start_run(invalid_json)

        invalid_time = run_record("invalid-time", "paper")
        object.__setattr__(invalid_time, "started_at_utc", "2026-07-15T09:35:00")
        with self.assertRaisesRegex(ValueError, "timezone"):
            self.store.start_run(invalid_time)

        valid_run = run_record("finite-values", "paper")
        self.store.start_run(valid_run)
        invalid_order = OrderRecord(
            order_id="order:infinite",
            run_id=valid_run.run_id,
            trade_id=None,
            signal_key=None,
            parent_order_id=None,
            broker="IBKR",
            broker_order_id=None,
            order_ref=None,
            order_type="MKT",
            action="BUY",
            quantity=float("inf"),
            limit_price=None,
            stop_price=None,
            time_in_force="DAY",
            transmit=True,
            status="PendingSubmit",
            submitted_at_utc=None,
            updated_at_utc=NOW,
            metadata={},
        )
        with self.assertRaisesRegex(ValueError, "finite"):
            self.store.upsert_order(invalid_order)


if __name__ == "__main__":
    unittest.main()
