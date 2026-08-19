from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace as NS
import unittest

from src.journal.models import ExecutionRecord, OrderRecord, StrategyRunRecord, TradeRecord
from src.journal.store import JournalStore
from src.journal.tws_sync import sync_connected_ib


NOW = "2026-07-15T13:00:00Z"


def _run(run_id: str) -> StrategyRunRecord:
    return StrategyRunRecord(
        run_id=run_id,
        strategy_id="manual-regime",
        strategy_version="v1",
        config_hash="cfg",
        run_type="paper",
        environment="paper",
        started_at_utc=NOW,
        ended_at_utc=None,
        status="running",
        parameters={},
        metadata={},
    )


def _order(
    journal_id: str,
    *,
    run_id: str,
    trade_id: str,
    signal_key: str,
    broker_order_id: int,
    perm_id: int,
    parent_order_id: str | None,
) -> OrderRecord:
    return OrderRecord(
        order_id=journal_id,
        run_id=run_id,
        trade_id=trade_id,
        signal_key=signal_key,
        parent_order_id=parent_order_id,
        broker="IBKR",
        broker_order_id=f"permId:{perm_id}",
        order_ref="MRB:20260714:IBM:rule",
        order_type="MKT" if parent_order_id is None else "LMT",
        action="BUY" if parent_order_id is None else "SELL",
        quantity=2,
        limit_price=None if parent_order_id is None else 105,
        stop_price=None,
        time_in_force="GTC",
        transmit=True,
        status="Submitted",
        submitted_at_utc=NOW,
        updated_at_utc=NOW,
        metadata={
            "account": "DU123",
            "client_id": 62,
            "order_id": broker_order_id,
            "perm_id": perm_id,
            "contract": {"secType": "STK", "symbol": "IBM"},
        },
    )


def _contract():
    return NS(
        secType="STK", conId=123, symbol="IBM", localSymbol="IBM",
        exchange="SMART", currency="USD", comboLegs=[],
    )


def _broker_trade(
    *,
    order_id: int,
    client_id: int,
    perm_id: int,
    parent_id: int,
    order_ref: str,
    status: str,
):
    return NS(
        order=NS(
            orderId=order_id,
            clientId=client_id,
            permId=perm_id,
            parentId=parent_id,
            account="DU123",
            orderRef=order_ref,
            action="SELL" if parent_id else "BUY",
            orderType="LMT" if parent_id else "MKT",
            totalQuantity=2,
            lmtPrice=105 if parent_id else 0,
            auxPrice=0,
            tif="GTC",
            transmit=True,
            ocaGroup="",
        ),
        contract=_contract(),
        orderStatus=NS(
            status=status,
            filled=2 if status == "Filled" else 0,
            remaining=0 if status == "Filled" else 2,
            avgFillPrice=105 if status == "Filled" else 0,
            lastFillPrice=105 if status == "Filled" else 0,
            whyHeld="",
            mktCapPrice=0,
        ),
        fills=[],
        log=[],
        advancedError="",
    )


class _FakeIB:
    def __init__(self, completed, manual) -> None:
        self.completed = completed
        self.manual = manual
        self.calls = []
        self.last_execution_filter = None

    def reqAllOpenOrders(self):
        self.calls.append("open")
        return [self.manual]

    def reqCompletedOrders(self, apiOnly):
        self.calls.append(("completed", apiOnly))
        return [self.completed]

    def reqExecutions(self, execution_filter=None):
        self.calls.append("executions")
        self.last_execution_filter = execution_filter
        if not self.completed.fills:
            execution_time = datetime(2026, 7, 15, 20, 1, tzinfo=timezone.utc)
            self.completed.fills.append(
                NS(
                    execution=NS(
                        execId="EXIT-1", time=execution_time, acctNumber="DU123",
                        exchange="NYSE", side="SLD", shares=2, price=105,
                        permId=9101, clientId=99, orderId=501, liquidation=0,
                        cumQty=2, avgPrice=105, orderRef=self.completed.order.orderRef,
                    ),
                    contract=self.completed.contract,
                    commissionReport=NS(
                        execId="EXIT-1", commission=1.7976931348623157e308,
                        currency="USD", realizedPNL=0, yield_=0,
                        yieldRedemptionDate=0,
                    ),
                    time=execution_time,
                )
            )
        return self.completed.fills

    def sleep(self, seconds):
        self.calls.append(("sleep", seconds))
        self.completed.fills[0].commissionReport.commission = 0.75

    def trades(self):
        self.calls.append("trades")
        return [self.completed, self.manual]

    def placeOrder(self, *args, **kwargs):
        raise AssertionError("read-only sync must never place orders")

    def cancelOrder(self, *args, **kwargs):
        raise AssertionError("read-only sync must never cancel orders")


class _HistoricalFillOnlyIB(_FakeIB):
    """Model TWS no longer returning yesterday's order as a Trade object."""

    def reqAllOpenOrders(self):
        self.calls.append("open")
        return []

    def reqCompletedOrders(self, apiOnly):
        self.calls.append(("completed", apiOnly))
        return []

    def reqExecutions(self, execution_filter=None):
        self.calls.append("executions")
        execution_time = datetime(2026, 7, 15, 20, 1, tzinfo=timezone.utc)
        return [
            NS(
                execution=NS(
                    execId="EXIT-ONLY-1", time=execution_time,
                    acctNumber="DU123", exchange="NYSE", side="SLD",
                    shares=2, price=105, permId=9101, clientId=99,
                    orderId=501, liquidation=0, cumQty=2, avgPrice=105,
                    orderRef="MRB:20260714:IBM:rule",
                ),
                contract=_contract(),
                commissionReport=NS(
                    execId="EXIT-ONLY-1", commission=0.75, currency="USD",
                    realizedPNL=10, yield_=0, yieldRedemptionDate=0,
                ),
                time=execution_time,
            )
        ]

    def sleep(self, seconds):
        self.calls.append(("sleep", seconds))

    def trades(self):
        self.calls.append("trades")
        return []


class TwsJournalSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = JournalStore(":memory:")
        self.original_run = "original-run"
        self.trade_id = "original-trade"
        self.signal_key = "original-signal"
        self.parent_journal_id = "original-parent-order"
        self.exit_journal_id = "original-tp-order"
        self.store.start_run(_run(self.original_run))
        self.store.create_trade(
            TradeRecord(
                trade_id=self.trade_id,
                run_id=self.original_run,
                signal_key=self.signal_key,
                decision_id=None,
                asset_class="STK",
                symbol="IBM",
                side="long",
                status="open",
                opened_at_utc=NOW,
                closed_at_utc=None,
                quantity=2,
                currency="USD",
                entry_price=100,
                exit_price=None,
                realized_pnl=None,
                metadata={},
            )
        )
        self.store.upsert_order(
            _order(
                self.parent_journal_id,
                run_id=self.original_run,
                trade_id=self.trade_id,
                signal_key=self.signal_key,
                broker_order_id=100,
                perm_id=9100,
                parent_order_id=None,
            )
        )
        self.store.upsert_order(
            _order(
                self.exit_journal_id,
                run_id=self.original_run,
                trade_id=self.trade_id,
                signal_key=self.signal_key,
                broker_order_id=101,
                perm_id=9101,
                parent_order_id=self.parent_journal_id,
            )
        )
        self.completed = _broker_trade(
            order_id=501,
            client_id=99,
            perm_id=9101,
            parent_id=500,
            order_ref="MRB:20260714:IBM:rule",
            status="Filled",
        )
        self.manual = _broker_trade(
            order_id=700,
            client_id=0,
            perm_id=9700,
            parent_id=0,
            order_ref="manual",
            status="Submitted",
        )
        self.ib = _FakeIB(self.completed, self.manual)
        self.now = lambda: datetime(2026, 7, 15, 20, 5, tzinfo=timezone.utc)

    def tearDown(self) -> None:
        self.store.close()

    def sync(self, client_id: int = 63, execution_start_day: date | None = None):
        return sync_connected_ib(
            self.ib,
            self.store,
            account="DU123",
            client_id=client_id,
            run_mode="paper",
            sync_day=date(2026, 7, 15),
            commission_wait_seconds=1.5,
            execution_start_day=execution_start_day,
            now_utc=self.now,
        )

    def test_cross_day_gtc_fill_keeps_original_binding_and_commission(self) -> None:
        result = self.sync()

        exit_order = self.store._connection.execute(
            "SELECT run_id, trade_id, signal_key, parent_order_id FROM orders WHERE order_id=?",
            (self.exit_journal_id,),
        ).fetchone()
        execution = self.store._connection.execute(
            "SELECT order_id, trade_id FROM executions WHERE broker_execution_id='EXIT-1'"
        ).fetchone()
        commission = self.store._connection.execute(
            "SELECT amount FROM commissions"
        ).fetchone()
        sync_run = self.store._connection.execute(
            "SELECT status FROM strategy_runs WHERE run_id=?", (result.run_id,)
        ).fetchone()
        self.assertEqual(tuple(exit_order), (
            self.original_run,
            self.trade_id,
            self.signal_key,
            self.parent_journal_id,
        ))
        self.assertEqual(tuple(execution), (self.exit_journal_id, self.trade_id))
        self.assertEqual(commission["amount"], 0.75)
        self.assertEqual(sync_run["status"], "completed")

    def test_historical_fill_without_trade_snapshot_closes_bound_trade(self) -> None:
        self.store.upsert_execution(
            ExecutionRecord(
                execution_id="entry-execution",
                order_id=self.parent_journal_id,
                trade_id=self.trade_id,
                broker_execution_id="ENTRY-1",
                executed_at_utc=NOW,
                side="BOT",
                quantity=2,
                price=100,
                exchange="NYSE",
                liquidity=None,
                metadata={},
            )
        )
        self.ib = _HistoricalFillOnlyIB(self.completed, self.manual)

        result = self.sync()

        execution = self.store._connection.execute(
            "SELECT order_id, trade_id FROM executions "
            "WHERE broker_execution_id='EXIT-ONLY-1'"
        ).fetchone()
        trade = self.store.get_trade(self.trade_id)
        order_status = self.store._connection.execute(
            "SELECT status FROM orders WHERE order_id=?", (self.exit_journal_id,)
        ).fetchone()[0]
        self.assertEqual(tuple(execution), (self.exit_journal_id, self.trade_id))
        self.assertEqual(trade["status"], "closed")
        self.assertEqual(order_status, "Filled")
        self.assertEqual(trade["exit_price"], 105)
        self.assertAlmostEqual(trade["realized_pnl"], 9.25)
        self.assertEqual(result.counts.executions, 1)

    def test_manual_order_uses_daily_broker_sync_run(self) -> None:
        result = self.sync()

        manual = self.store._connection.execute(
            "SELECT run_id, trade_id, signal_key FROM orders WHERE broker_perm_id=9700"
        ).fetchone()
        self.assertEqual(tuple(manual), (result.run_id, None, None))

    def test_zero_quantity_broker_order_is_skipped_and_diagnosed(self) -> None:
        self.manual.order.totalQuantity = 0

        result = self.sync()

        self.assertEqual(1, result.skipped_zero_quantity_orders)
        self.assertEqual(1, result.unique_trades)
        self.assertIsNone(
            self.store._connection.execute(
                "SELECT order_id FROM orders WHERE broker_perm_id=9700"
            ).fetchone()
        )
        metadata = self.store._connection.execute(
            "SELECT metadata_json FROM strategy_runs WHERE run_id=?",
            (result.run_id,),
        ).fetchone()[0]
        payload = __import__("json").loads(metadata)
        self.assertEqual(1, payload["skipped_zero_quantity_orders"])
        self.assertEqual(2, payload["observed_unique_orders"])
        self.assertEqual(1, payload["ingested_unique_orders"])

    def test_completed_orders_are_unrestricted_and_read_sequence_is_exact(self) -> None:
        self.sync()

        self.assertEqual(self.ib.calls, [
            "open",
            ("completed", False),
            "executions",
            ("sleep", 1.5),
            "trades",
        ])

    def test_execution_filter_can_start_at_previous_market_session(self) -> None:
        self.sync(execution_start_day=date(2026, 7, 10))

        self.assertEqual(self.ib.last_execution_filter.time, "20260710 00:00:00")
        self.assertEqual(self.ib.last_execution_filter.acctCode, "DU123")

    def test_replay_is_idempotent(self) -> None:
        first = self.sync()
        self.ib.calls.clear()
        self.now = lambda: datetime(2026, 7, 15, 21, 5, tzinfo=timezone.utc)
        second = self.sync()

        counts = {
            table: self.store._connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("orders", "executions", "commissions", "order_status_events")
        }
        self.assertEqual(first.run_id, second.run_id)
        self.assertEqual(counts, {
            "orders": 3,
            "executions": 1,
            "commissions": 1,
            "order_status_events": 2,
        })

    def test_different_client_ids_get_distinct_daily_sync_runs(self) -> None:
        first = self.sync(client_id=63)
        second = self.sync(client_id=64)

        self.assertNotEqual(first.run_id, second.run_id)


if __name__ == "__main__":
    unittest.main()
