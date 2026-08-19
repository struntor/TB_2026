from __future__ import annotations

from datetime import datetime, timezone
from contextlib import contextmanager
from types import SimpleNamespace as NS
import unittest

from src.journal.execution_ingest import (
    ExecutionContext,
    ExecutionJournalIngestor,
)
from src.journal.models import OrderBinding, StrategyRunRecord, TradeRecord
from src.journal.store import JournalStore


class _Store:
    def __init__(self) -> None:
        self.orders = {}
        self.order_legs = {}
        self.statuses = {}
        self.executions = {}
        self.commissions = {}
        self.decisions = {}

    def upsert_order(self, record, legs=()) -> None:
        self.orders[record.order_id] = record
        self.order_legs[record.order_id] = tuple(legs)

    def add_order_status(self, record) -> None:
        self.statuses[record.event_id] = record

    def upsert_execution(self, record) -> None:
        self.executions[record.execution_id] = record

    def upsert_commission(self, record) -> None:
        self.commissions[record.commission_id] = record

    def add_event(self, record) -> None:
        self.decisions[record.event_id] = record

    def add_risk_event(self, record) -> None:
        self.decisions[record.risk_event_id] = record

    def find_order_binding(
        self,
        *,
        run_id,
        broker,
        account,
        perm_id,
        client_id,
        client_order_id,
    ):
        for order in self.orders.values():
            metadata = order.metadata
            if (
                perm_id is not None
                and order.broker == broker
                and metadata.get("account") == account
                and metadata.get("perm_id") == perm_id
            ):
                return OrderBinding(
                    order_id=order.order_id,
                    run_id=order.run_id,
                    trade_id=order.trade_id,
                    signal_key=order.signal_key,
                )
        for order in self.orders.values():
            metadata = order.metadata
            if (
                order.run_id == run_id
                and order.broker == broker
                and metadata.get("account") == account
                and metadata.get("client_id") == client_id
                and metadata.get("order_id") == client_order_id
            ):
                return OrderBinding(
                    order_id=order.order_id,
                    run_id=order.run_id,
                    trade_id=order.trade_id,
                    signal_key=order.signal_key,
                )
        return None

    @contextmanager
    def transaction(self):
        yield


def _context() -> ExecutionContext:
    return ExecutionContext(
        run_id="run-paper-1",
        signal_key="signal-abc",
        trade_id="trade-abc",
        decision_id="decision-abc",
        account="DU12345",
        client_id=62,
        observed_at_utc="2026-07-15T13:45:00Z",
    )


def _contract(**overrides):
    values = {
        "secType": "STK",
        "conId": 123,
        "symbol": "IBM",
        "localSymbol": "IBM",
        "exchange": "SMART",
        "currency": "USD",
        "comboLegs": [],
    }
    values.update(overrides)
    return NS(**values)


def _trade(order_id=100, parent_id=0, status="Submitted", fills=(), log=()):
    order = NS(
        orderId=order_id,
        clientId=62,
        permId=9000 + order_id,
        parentId=parent_id,
        account="DU12345",
        orderRef="MRB:20260715:IBM:rule",
        action="BUY" if parent_id == 0 else "SELL",
        orderType="MKT" if parent_id == 0 else "LMT",
        totalQuantity=4,
        lmtPrice=105 if parent_id else 0,
        auxPrice=0,
        tif="DAY",
        transmit=parent_id != 0,
        ocaGroup="",
    )
    order_status = NS(
        status=status,
        filled=sum(float(fill.execution.shares) for fill in fills),
        remaining=4 - sum(float(fill.execution.shares) for fill in fills),
        avgFillPrice=100.25 if fills else 0,
        lastFillPrice=100.5 if fills else 0,
        whyHeld="",
        mktCapPrice=0,
    )
    return NS(
        order=order,
        contract=_contract(),
        orderStatus=order_status,
        fills=list(fills),
        log=list(log),
        advancedError="",
    )


def _fill(exec_id, shares, price, commission):
    execution = NS(
        execId=exec_id,
        time=datetime(2026, 7, 15, 13, 40, tzinfo=timezone.utc),
        acctNumber="DU12345",
        exchange="NYSE",
        side="BOT",
        shares=shares,
        price=price,
        permId=9100,
        clientId=62,
        orderId=100,
        liquidation=0,
        cumQty=shares,
        avgPrice=price,
        orderRef="MRB:20260715:IBM:rule",
    )
    report = NS(
        execId=exec_id,
        commission=commission,
        currency="USD",
        realizedPNL=0,
        yield_=0,
        yieldRedemptionDate=0,
    )
    return NS(
        execution=execution,
        contract=_contract(),
        commissionReport=report,
        time=execution.time,
    )


class ExecutionIngestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = _Store()
        self.ingestor = ExecutionJournalIngestor(self.store)

    def test_bracket_hierarchy_and_identifiers_are_preserved(self) -> None:
        submission = NS(
            trades=(
                _trade(100),
                _trade(101, parent_id=100),
                _trade(102, parent_id=100),
            )
        )

        counts = self.ingestor.ingest_bracket_submission(submission, _context())

        self.assertEqual(counts.orders, 3)
        self.assertEqual(len(self.store.orders), 3)
        child = next(order for order in self.store.orders.values() if order.metadata["order_id"] == 101)
        parent = next(order for order in self.store.orders.values() if order.metadata["order_id"] == 100)
        self.assertEqual(child.parent_order_id, parent.order_id)
        self.assertEqual(child.metadata["perm_id"], 9101)
        self.assertEqual(child.order_ref, "MRB:20260715:IBM:rule")

    def test_combo_contract_legs_are_stored_in_order(self) -> None:
        combo = _contract(
            secType="BAG",
            conId=0,
            symbol="SPX",
            comboLegs=[
                NS(conId=11, ratio=1, action="BUY", exchange="SMART", openClose=1,
                   shortSaleSlot=0, designatedLocation="", exemptCode=-1),
                NS(conId=12, ratio=2, action="SELL", exchange="SMART", openClose=1,
                   shortSaleSlot=0, designatedLocation="", exemptCode=-1),
            ],
        )
        trade = _trade()
        trade.contract = combo

        self.ingestor.ingest_trade(trade, _context(), role="parent")

        order = next(iter(self.store.orders.values()))
        self.assertEqual(order.metadata["contract"]["secType"], "BAG")
        legs = self.store.order_legs[order.order_id]
        self.assertEqual([(leg.con_id, leg.ratio, leg.action) for leg in legs], [
            (11, 1, "BUY"), (12, 2, "SELL")
        ])

    def test_partial_fills_commissions_and_status_log_are_idempotent(self) -> None:
        fills = (_fill("E1", 1, 100.0, 0.35), _fill("E2", 3, 100.5, 0.65))
        logs = (
            NS(time=datetime(2026, 7, 15, 13, 39, tzinfo=timezone.utc),
               status="Submitted", errorCode=0, message=""),
            NS(time=datetime(2026, 7, 15, 13, 40, tzinfo=timezone.utc),
               status="Filled", errorCode=0, message=""),
        )
        trade = _trade(status="Filled", fills=fills, log=logs)

        first = self.ingestor.ingest_trade(trade, _context(), role="parent")
        self.ingestor.ingest_trade(trade, _context(), role="parent")

        self.assertEqual((first.executions, first.commissions, first.statuses), (2, 2, 3))
        self.assertEqual(len(self.store.executions), 2)
        self.assertEqual(len(self.store.commissions), 2)
        self.assertEqual(len(self.store.statuses), 3)
        self.assertEqual(sum(e.quantity for e in self.store.executions.values()), 4)

    def test_equal_status_messages_at_different_times_remain_distinct(self) -> None:
        logs = (
            NS(time="2026-07-15T13:39:00Z", status="Submitted", errorCode=0, message="working"),
            NS(time="2026-07-15T13:40:00Z", status="Submitted", errorCode=0, message="working"),
        )
        trade = _trade(log=logs)

        self.ingestor.ingest_trade(trade, _context(), role="parent")
        self.ingestor.ingest_trade(trade, _context(), role="parent")

        self.assertEqual(len(self.store.statuses), 3)  # two logs plus one snapshot

    def test_pre_and_post_ack_keep_same_journal_order_id(self) -> None:
        before_ack = _trade(status="PendingSubmit")
        before_ack.order.permId = 0
        after_ack = _trade(status="Submitted")
        after_ack.order.permId = 99100

        self.ingestor.ingest_trade(before_ack, _context(), role="parent")
        first_id = next(iter(self.store.orders))
        self.ingestor.ingest_trade(after_ack, _context(), role="parent")

        self.assertEqual(len(self.store.orders), 1)
        self.assertEqual(next(iter(self.store.orders)), first_id)
        self.assertEqual(next(iter(self.store.orders.values())).metadata["perm_id"], 99100)

    def test_reconnect_resolves_perm_id_across_changed_client_and_order_ids(self) -> None:
        original = _trade(order_id=100)
        original.order.permId = 99100
        self.ingestor.ingest_trade(original, _context(), role="parent")
        original_id = next(iter(self.store.orders))

        reconnected = _trade(order_id=777)
        reconnected.order.clientId = 88
        reconnected.order.permId = 99100
        reconnect_context = ExecutionContext(
            run_id="run-paper-1",
            signal_key="signal-abc",
            trade_id="trade-abc",
            decision_id="decision-abc",
            account="DU12345",
            client_id=88,
            observed_at_utc="2026-07-15T14:00:00Z",
        )
        self.ingestor.ingest_trade(reconnected, reconnect_context, role="parent")

        self.assertEqual(len(self.store.orders), 1)
        self.assertEqual(next(iter(self.store.orders)), original_id)

    def test_unchanged_snapshot_at_later_observation_keeps_one_status(self) -> None:
        trade = _trade(status="Submitted")
        self.ingestor.ingest_trade(trade, _context(), role="parent")
        later = ExecutionContext(
            run_id="run-paper-1",
            signal_key="signal-abc",
            trade_id="trade-abc",
            decision_id="decision-abc",
            account="DU12345",
            client_id=62,
            observed_at_utc="2026-07-15T14:05:00Z",
        )
        self.ingestor.ingest_trade(trade, later, role="parent")
        self.assertEqual(len(self.store.statuses), 1)

    def test_unmatched_manual_broker_order_does_not_require_signal_or_trade(self) -> None:
        context = ExecutionContext(
            run_id="broker-sync-run",
            signal_key=None,
            trade_id=None,
            decision_id=None,
            account="DU12345",
            client_id=62,
            observed_at_utc="2026-07-15T13:45:00Z",
        )

        self.ingestor.ingest_trade(_trade(), context, role="unmatched")

        order = next(iter(self.store.orders.values()))
        self.assertIsNone(order.signal_key)
        self.assertIsNone(order.trade_id)

    def test_option_contract_snapshot_contains_reconciliation_fields(self) -> None:
        option = _contract(
            secType="OPT",
            conId=456,
            symbol="SPY",
            localSymbol="SPY   260717C00600000",
            lastTradeDateOrContractMonth="20260717",
            strike=600.0,
            right="C",
            multiplier="100",
            tradingClass="SPY",
        )
        trade = _trade()
        trade.contract = option

        self.ingestor.ingest_trade(trade, _context(), role="parent")

        snapshot = next(iter(self.store.orders.values())).metadata["contract"]
        self.assertEqual(
            (snapshot["secType"], snapshot["expiry"], snapshot["strike"], snapshot["right"], snapshot["multiplier"]),
            ("OPT", "20260717", 600.0, "C", "100"),
        )

    def test_real_sqlite_store_replay_is_idempotent(self) -> None:
        context = ExecutionContext(
            run_id="broker-sync-run",
            signal_key=None,
            trade_id=None,
            decision_id=None,
            account="DU12345",
            client_id=62,
            observed_at_utc="2026-07-15T13:45:00Z",
        )
        fills = (_fill("E1", 1, 100.0, 0.35), _fill("E2", 3, 100.5, 0.65))
        trade = _trade(status="Filled", fills=fills)
        with JournalStore(":memory:") as store:
            store.start_run(
                StrategyRunRecord(
                    run_id=context.run_id,
                    strategy_id="broker_sync",
                    strategy_version="1",
                    config_hash="none",
                    run_type="paper",
                    environment="test",
                    started_at_utc="2026-07-15T13:00:00Z",
                    ended_at_utc=None,
                    status="running",
                    parameters={},
                    metadata={},
                )
            )
            ingestor = ExecutionJournalIngestor(store)

            ingestor.ingest_trade(trade, context, role="unmatched")
            ingestor.ingest_trade(trade, context, role="unmatched")
            later_context = ExecutionContext(
                run_id=context.run_id,
                signal_key=None,
                trade_id=None,
                decision_id=None,
                account=context.account,
                client_id=context.client_id,
                observed_at_utc="2026-07-15T14:45:00Z",
            )
            ingestor.ingest_trade(trade, later_context, role="unmatched")

            counts = {
                table: store._connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in ("orders", "order_status_events", "executions", "commissions")
            }
            first_status_time = store._connection.execute(
                "SELECT occurred_at_utc FROM order_status_events"
            ).fetchone()[0]
            commission_times = {
                row[0]
                for row in store._connection.execute(
                    "SELECT reported_at_utc FROM commissions"
                ).fetchall()
            }
        self.assertEqual(counts, {
            "orders": 1,
            "order_status_events": 1,
            "executions": 2,
            "commissions": 2,
        })
        self.assertEqual(first_status_time, "2026-07-15T13:45:00Z")
        self.assertEqual(commission_times, {"2026-07-15T13:45:00Z"})

    def test_bound_partial_entry_fills_promote_planned_trade_to_open(self) -> None:
        context = _context()
        fills = (_fill("OPEN-1", 1, 100.0, 0.35), _fill("OPEN-2", 3, 100.5, 0.65))
        trade = _trade(status="Filled", fills=fills)
        with JournalStore(":memory:") as store:
            store.start_run(
                StrategyRunRecord(
                    run_id=context.run_id,
                    strategy_id="manual-regime",
                    strategy_version="v1",
                    config_hash="cfg",
                    run_type="paper",
                    environment="paper",
                    started_at_utc="2026-07-15T13:00:00Z",
                    ended_at_utc=None,
                    status="running",
                    parameters={},
                    metadata={},
                )
            )
            store.create_trade(
                TradeRecord(
                    trade_id=context.trade_id,
                    run_id=context.run_id,
                    signal_key=context.signal_key,
                    decision_id=None,
                    asset_class="STK",
                    symbol="IBM",
                    side="long",
                    status="planned",
                    opened_at_utc="2026-07-15T13:35:00Z",
                    closed_at_utc=None,
                    quantity=4,
                    currency="USD",
                    entry_price=99.0,
                    exit_price=None,
                    realized_pnl=None,
                    metadata={"planned": True},
                )
            )

            ingestor = ExecutionJournalIngestor(store)
            ingestor.ingest_trade(trade, context, role="parent")
            ingestor.ingest_trade(trade, context, role="parent")
            opened = store.get_trade(context.trade_id)

        self.assertEqual("open", opened["status"])
        self.assertEqual(4.0, opened["quantity"])
        self.assertAlmostEqual(100.375, opened["entry_price"])
        self.assertEqual("2026-07-15T13:40:00Z", opened["opened_at_utc"])
        metadata = __import__("json").loads(opened["metadata_json"])
        self.assertEqual(2, metadata["execution_lifecycle"]["opening_execution_count"])
        self.assertEqual(4.0, metadata["execution_lifecycle"]["net_open_quantity"])

    def test_cross_day_gtc_resync_keeps_original_run_provenance(self) -> None:
        old_context = ExecutionContext(
            run_id="paper-2026-07-15",
            signal_key=None,
            trade_id=None,
            decision_id=None,
            account="DU12345",
            client_id=62,
            observed_at_utc="2026-07-15T13:45:00Z",
        )
        new_context = ExecutionContext(
            run_id="paper-2026-07-16",
            signal_key=None,
            trade_id=None,
            decision_id=None,
            account="DU12345",
            client_id=88,
            observed_at_utc="2026-07-16T13:45:00Z",
        )
        with JournalStore(":memory:") as store:
            for run_id, started in (
                (old_context.run_id, "2026-07-15T13:00:00Z"),
                (new_context.run_id, "2026-07-16T13:00:00Z"),
            ):
                store.start_run(
                    StrategyRunRecord(
                        run_id=run_id,
                        strategy_id="broker_sync",
                        strategy_version="1",
                        config_hash="none",
                        run_type="paper",
                        environment="test",
                        started_at_utc=started,
                        ended_at_utc=None,
                        status="running",
                        parameters={},
                        metadata={},
                    )
                )
            original = _trade(order_id=100, status="Submitted")
            original.order.permId = 99100
            ExecutionJournalIngestor(store).ingest_trade(
                original, old_context, role="gtc"
            )

            resynced = _trade(
                order_id=777,
                status="Filled",
                fills=(_fill("GTC-E1", 4, 100.5, 0.75),),
            )
            resynced.order.clientId = 88
            resynced.order.permId = 99100
            ExecutionJournalIngestor(store).ingest_trade(
                resynced, new_context, role="gtc_resync"
            )

            order = store._connection.execute(
                "SELECT * FROM orders"
            ).fetchone()
            execution = store._connection.execute(
                "SELECT * FROM executions"
            ).fetchone()
            self.assertEqual(store.count("orders"), 1)
            self.assertEqual(order["run_id"], old_context.run_id)
            self.assertIsNone(order["trade_id"])
            self.assertIsNone(execution["trade_id"])
            self.assertEqual(store.count("order_status_events"), 2)
            self.assertEqual(store.count("executions"), 1)
            self.assertEqual(store.count("commissions"), 1)

    def test_ingest_trade_rolls_back_order_and_status_on_bad_fill(self) -> None:
        context = ExecutionContext(
            run_id="atomic-broker-sync",
            signal_key=None,
            trade_id=None,
            decision_id=None,
            account="DU12345",
            client_id=62,
            observed_at_utc="2026-07-15T13:45:00Z",
        )
        bad_fill = _fill("BAD", 1, 100.0, 0.35)
        bad_fill.execution.shares = float("nan")
        trade = _trade(status="Filled", fills=(bad_fill,))
        with JournalStore(":memory:") as store:
            store.start_run(
                StrategyRunRecord(
                    run_id=context.run_id,
                    strategy_id="broker_sync",
                    strategy_version="1",
                    config_hash="none",
                    run_type="paper",
                    environment="test",
                    started_at_utc="2026-07-15T13:00:00Z",
                    ended_at_utc=None,
                    status="running",
                    parameters={},
                    metadata={},
                )
            )
            with self.assertRaisesRegex(ValueError, "finite execution"):
                ExecutionJournalIngestor(store).ingest_trade(
                    trade, context, role="unmatched"
                )
            self.assertEqual(store.count("orders"), 0)
            self.assertEqual(store.count("order_status_events"), 0)

    def test_cancel_and_reject_are_classified(self) -> None:
        cancel = _trade(status="Cancelled")
        reject = _trade(
            order_id=200,
            status="Inactive",
            log=(NS(time="2026-07-15T13:40:00+00:00", status="Inactive",
                    errorCode=201, message="Order rejected"),),
        )

        self.ingestor.ingest_trade(cancel, _context(), role="parent")
        self.ingestor.ingest_trade(reject, _context(), role="parent")

        terminal_types = {event.event_type for event in self.store.decisions.values()}
        self.assertIn("order_cancelled", terminal_types)
        self.assertIn("order_rejected", terminal_types)

    def test_nonterminal_broker_error_is_not_mislabeled_as_rejection(self) -> None:
        warning = _trade(
            status="Submitted",
            log=(NS(time="2026-07-15T13:40:00Z", status="Submitted",
                    errorCode=399, message="Order warning"),),
        )

        counts = self.ingestor.ingest_trade(warning, _context(), role="parent")

        self.assertEqual(counts.terminal_events, 0)
        self.assertEqual(len(self.store.decisions), 0)

    def test_risk_and_duplicate_blockers_are_idempotent(self) -> None:
        arguments = {
            "status": "blocked_risk",
            "reason_code": "qty_zero",
            "reason": "quantity computed as zero",
            "requested_quantity": 0,
            "occurred_at": "2026-07-15T13:40:00+00:00",
            "details": {"risk_per_trade": 100, "stop_distance": 0},
        }
        first = self.ingestor.record_blocker(_context(), **arguments)
        second = self.ingestor.record_blocker(_context(), **arguments)

        self.assertEqual(first.risk_event_id, second.risk_event_id)
        self.assertEqual(len(self.store.decisions), 1)
        self.assertEqual(first.status, "blocked_risk")

        duplicate = self.ingestor.record_blocker(
            _context(),
            status="duplicate_exposure",
            reason_code="existing_position_or_open_order",
            reason="existing position/open order found",
            requested_quantity=4,
            occurred_at="2026-07-15T13:41:00Z",
            details={"symbol": "IBM", "risk_amount": 100, "risk_currency": "USD"},
        )
        rejected = self.ingestor.record_blocker(
            _context(),
            status="execution_rejected",
            reason_code="safe_bracket_rejected",
            reason="broker rejected bracket",
            requested_quantity=4,
            occurred_at="2026-07-15T13:42:00Z",
            details={"error": "201: rejected"},
        )
        self.assertEqual((duplicate.status, rejected.status), (
            "duplicate_exposure", "execution_rejected"
        ))
        self.assertEqual(duplicate.risk_amount, 100)
        self.assertEqual(duplicate.risk_currency, "USD")


if __name__ == "__main__":
    unittest.main()
