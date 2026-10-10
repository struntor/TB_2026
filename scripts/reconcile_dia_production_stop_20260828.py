"""Reconcile the production DIA stop fill visible in the TWS trade history.

The production sync client could not recover the historical execution through
``reqExecutions``.  This one-off repair is deliberately narrow and replay-safe:
it validates the expected live trade and stop order, inserts one deterministic
manual-recovery execution plus its TWS commission, and derives the trade
lifecycle from the complete bound execution set.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from src.journal.models import (
    CommissionRecord,
    ExecutionRecord,
    OrderStatusEventRecord,
    TradeEventRecord,
    deterministic_id,
)
from src.journal.store import JournalStore


JOURNAL = Path(r"D:\Trading\journal\trading_journal.sqlite")
BACKUP = JOURNAL.with_name("trading_journal.pre_dia_stop_recovery_20260831.sqlite")

TRADE_ID = "trade:d0d07ff287def2b37fc8654b03696126"
STOP_ORDER_ID = "order:f1bf98b46911c6ffc1db46ee36c0bc99"
TARGET_ORDER_ID = "order:9d1f1aa3aefb7b6bc3a5d05a2fbb0d14"
ACCOUNT = "U7492180"
PERM_ID = 224386470
CLIENT_ID = 72
BROKER_ORDER_ID = 52

EXECUTED_AT_UTC = "2026-08-28T15:00:11Z"  # TWS: 17:00:11 Europe/Berlin
QUANTITY = 44.0
PRICE = 537.19
EXCHANGE = "DARK"
COMMISSION = 2.0

BROKER_EXECUTION_ID = (
    f"manual-tws-recovery:{ACCOUNT}:{PERM_ID}:{EXECUTED_AT_UTC}:{QUANTITY}:{PRICE}"
)
EXECUTION_ID = deterministic_id("execution", "IBKR", BROKER_EXECUTION_ID)
COMMISSION_ID = deterministic_id("commission", EXECUTION_ID)


def _backup_once() -> None:
    if BACKUP.exists():
        return
    source = sqlite3.connect(JOURNAL)
    destination = sqlite3.connect(BACKUP)
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()


def main() -> None:
    _backup_once()
    with JournalStore(JOURNAL) as store:
        trade = store._connection.execute(
            "SELECT * FROM trades WHERE trade_id=?", (TRADE_ID,)
        ).fetchone()
        if trade is None:
            raise RuntimeError(f"expected production DIA trade not found: {TRADE_ID}")
        if (
            str(trade["symbol"]) != "DIA"
            or str(trade["side"]) != "short"
            or float(trade["entry_price"]) != 534.52
            or float(trade["quantity"]) != QUANTITY
        ):
            raise RuntimeError("DIA trade identity no longer matches the audited fill")

        stop = store._connection.execute(
            "SELECT * FROM orders WHERE order_id=?", (STOP_ORDER_ID,)
        ).fetchone()
        if stop is None:
            raise RuntimeError(f"expected DIA stop order not found: {STOP_ORDER_ID}")
        if (
            str(stop["trade_id"]) != TRADE_ID
            or str(stop["broker_account"]) != ACCOUNT
            or int(stop["broker_perm_id"]) != PERM_ID
            or int(stop["broker_client_id"]) != CLIENT_ID
            or int(stop["broker_client_order_id"]) != BROKER_ORDER_ID
            or str(stop["action"]) != "BUY"
            or float(stop["quantity"]) != QUANTITY
        ):
            raise RuntimeError("DIA stop order identity no longer matches the audited fill")

        real_closes = store._connection.execute(
            """
            SELECT execution_id, broker_execution_id, executed_at_utc, quantity, price
            FROM executions
            WHERE trade_id=? AND upper(side) IN ('BOT', 'BUY')
              AND execution_id<>?
            """,
            (TRADE_ID, EXECUTION_ID),
        ).fetchall()
        if real_closes:
            raise RuntimeError(
                "a different closing execution already exists; refusing a duplicate: "
                + json.dumps([dict(row) for row in real_closes], sort_keys=True)
            )

        evidence = {
            "source": "production_tws_trade_history_manual_recovery",
            "reason": "reqExecutions_returned_no_historical_production_fills",
            "tws_display_time": "2026-08-28 17:00:11 Europe/Berlin",
            "captured_fields": {
                "symbol": "DIA",
                "side": "BOT",
                "quantity": QUANTITY,
                "price": PRICE,
                "exchange": EXCHANGE,
                "commission_usd": COMMISSION,
            },
            "broker_execution_id_available": False,
            "synthetic_broker_execution_id": BROKER_EXECUTION_ID,
            "screenshot": r"D:\Trading\tws_production_after_trades_click.png",
        }

        with store.transaction():
            store.upsert_execution(
                ExecutionRecord(
                    execution_id=EXECUTION_ID,
                    order_id=STOP_ORDER_ID,
                    trade_id=TRADE_ID,
                    broker_execution_id=BROKER_EXECUTION_ID,
                    executed_at_utc=EXECUTED_AT_UTC,
                    side="BOT",
                    quantity=QUANTITY,
                    price=PRICE,
                    exchange=EXCHANGE,
                    liquidity=None,
                    metadata={
                        **evidence,
                        "account": ACCOUNT,
                        "client_id": CLIENT_ID,
                        "broker_order_id": BROKER_ORDER_ID,
                        "perm_id": PERM_ID,
                        "order_ref": stop["order_ref"],
                    },
                )
            )
            store.upsert_commission(
                CommissionRecord(
                    commission_id=COMMISSION_ID,
                    execution_id=EXECUTION_ID,
                    currency="USD",
                    amount=COMMISSION,
                    realized_pnl=None,
                    reported_at_utc=EXECUTED_AT_UTC,
                    metadata=evidence,
                )
            )
            store.add_order_status(
                OrderStatusEventRecord(
                    event_id=deterministic_id(
                        "order-status", STOP_ORDER_ID, "Filled", EXECUTED_AT_UTC
                    ),
                    order_id=STOP_ORDER_ID,
                    status="Filled",
                    occurred_at_utc=EXECUTED_AT_UTC,
                    filled_quantity=QUANTITY,
                    remaining_quantity=0.0,
                    average_fill_price=PRICE,
                    broker_message="Recovered from visible production TWS trade history",
                    payload=evidence,
                )
            )
            store.refresh_order_filled_from_executions(STOP_ORDER_ID)
            store.add_order_status(
                OrderStatusEventRecord(
                    event_id=deterministic_id(
                        "order-status", TARGET_ORDER_ID, "Cancelled", EXECUTED_AT_UTC
                    ),
                    order_id=TARGET_ORDER_ID,
                    status="Cancelled",
                    occurred_at_utc=EXECUTED_AT_UTC,
                    filled_quantity=0.0,
                    remaining_quantity=0.0,
                    average_fill_price=None,
                    broker_message="OCA sibling absent from production TWS open orders after stop fill",
                    payload={
                        **evidence,
                        "reason": "oca_sibling_cancelled_after_stop_fill",
                        "filled_stop_order_id": STOP_ORDER_ID,
                    },
                )
            )
            store._connection.execute(
                """
                UPDATE orders
                SET status='Cancelled', updated_at_utc=?
                WHERE order_id=? AND status NOT IN ('Filled', 'Cancelled')
                """,
                (EXECUTED_AT_UTC, TARGET_ORDER_ID),
            )
            status = store.refresh_trade_lifecycle_from_executions(TRADE_ID)
            store.add_event(
                TradeEventRecord(
                    event_id=deterministic_id(
                        "trade-event", TRADE_ID, "manual_tws_fill_recovered", EXECUTED_AT_UTC
                    ),
                    run_id=str(trade["run_id"]),
                    trade_id=TRADE_ID,
                    order_id=STOP_ORDER_ID,
                    signal_key=trade["signal_key"],
                    event_type="manual_tws_fill_recovered",
                    occurred_at_utc=EXECUTED_AT_UTC,
                    payload={**evidence, "target_order_id": TARGET_ORDER_ID},
                )
            )

        result = store._connection.execute(
            """
            SELECT status, opened_at_utc, closed_at_utc, quantity, entry_price,
                   exit_price, realized_pnl
            FROM trades WHERE trade_id=?
            """,
            (TRADE_ID,),
        ).fetchone()
        stop_result = store._connection.execute(
            "SELECT status, updated_at_utc FROM orders WHERE order_id=?",
            (STOP_ORDER_ID,),
        ).fetchone()
        print(
            json.dumps(
                {
                    "trade": dict(result),
                    "stop_order": dict(stop_result),
                    "execution_id": EXECUTION_ID,
                    "commission_id": COMMISSION_ID,
                    "derived_status": status,
                    "backup": str(BACKUP),
                },
                sort_keys=True,
            )
        )


if __name__ == "__main__":
    main()
