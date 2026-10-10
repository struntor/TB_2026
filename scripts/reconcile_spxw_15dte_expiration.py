"""Reconcile the 2026-08-19 SPXW 15DTE paper expiration.

The SPXW index options expired with zero-value leg executions that have no
broker order id/order ref.  This one-off, idempotent reconciliation binds the
two TWS expiration fills to the already-journaled trade and closes it as an
OTM expiry.  It also leaves synthetic expiration orders so a later TWS sync
can resolve the broker permanent ids instead of counting the fills as
unbound.
"""

from __future__ import annotations

import json
from pathlib import Path

from src.journal.models import (
    ExecutionRecord,
    OrderLegRecord,
    OrderRecord,
    TradeEventRecord,
    TradeLegRecord,
    TradeRecord,
    deterministic_id,
)
from src.journal.store import JournalStore


JOURNAL = Path(r"D:\Trading\journal\trading_journal.sqlite")
TRADE_ID = "trade:c2183003f3a2a9f3646eb2a822119b91"
ACCOUNT = "DU9372630"
CLIENT_ID = 187  # read-only inspection client used against the Paper TWS
EXPIRY_AT = "2026-08-20T01:15:00Z"  # 2026-08-19 21:15 ET
EXPIRY_DAY = "2026-08-19"
ENTRY_AT = "2026-08-04T15:35:34Z"
ENTRY_CREDIT = 1.50
MULTIPLIER = 100.0
ENTRY_COMMISSIONS = 3.26056

EXPIRATION_LEGS = (
    {
        "perm_id": 1528547379,
        "exec_id": "000261b6.6a853b51.01.01",
        "con_id": 907870914,
        "local_symbol": "SPXW  260819P07595000",
        "leg_index": 1,
        "action": "SELL",
        "side": "SLD",
        "trade_leg_id": "trade-leg:17d9a0c35fb2170c9bb711f1d89d1035",
    },
    {
        "perm_id": 1528547380,
        "exec_id": "000261b6.6a853b52.01.01",
        "con_id": 907870925,
        "local_symbol": "SPXW  260819P07605000",
        "leg_index": 0,
        "action": "BUY",
        "side": "BOT",
        "trade_leg_id": "trade-leg:7f4df81457a52e18d96f50193e9efdc7",
    },
)


def main() -> None:
    with JournalStore(JOURNAL) as store:
        trade = store._connection.execute(
            "SELECT * FROM trades WHERE trade_id=?", (TRADE_ID,)
        ).fetchone()
        if trade is None:
            raise RuntimeError(f"trade not found: {TRADE_ID}")
        if str(trade["run_id"]) != "run:9f7138a4066d4c95e7d5649f88f28913":
            raise RuntimeError("trade is not the expected paper SPXW 15DTE trade")

        legs = store.list_trade_legs(TRADE_ID)
        if len(legs) != 2:
            raise RuntimeError(f"expected two option legs, found {len(legs)}")

        metadata = json.loads(str(trade["metadata_json"]))
        gross_pnl = ENTRY_CREDIT * MULTIPLIER * float(trade["quantity"])
        net_pnl = gross_pnl - ENTRY_COMMISSIONS
        metadata.update(
            {
                "contract_multiplier": MULTIPLIER,
                "lifecycle_source": "paper_tws_expiration_reconciliation",
                "expiration_day": EXPIRY_DAY,
                "expiration_settlement": "worthless",
                "expiration_settlement_value": 0.0,
                "expiration_execution_at_utc": EXPIRY_AT,
                "gross_realized_pnl": gross_pnl,
                "entry_commissions_usd": ENTRY_COMMISSIONS,
                "close_commissions_usd": 0.0,
                "net_realized_pnl": net_pnl,
                "successful_expiry": True,
            }
        )

        updated_legs = [
            TradeLegRecord(
                leg_id=str(leg["leg_id"]),
                trade_id=TRADE_ID,
                leg_index=int(leg["leg_index"]),
                asset_class=str(leg["asset_class"]),
                symbol=str(leg["symbol"]),
                con_id=int(leg["con_id"]) if leg["con_id"] is not None else None,
                local_symbol=leg["local_symbol"],
                exchange=leg["exchange"],
                currency=str(leg["currency"]),
                side=str(leg["side"]),
                ratio=int(leg["ratio"]),
                quantity=float(leg["quantity"]),
                expiry=leg["expiry"],
                strike=float(leg["strike"]),
                option_right=leg["option_right"],
                multiplier=float(leg["multiplier"]),
                open_price=float(leg["open_price"]),
                close_price=0.0,
                metadata=json.loads(str(leg["metadata_json"])),
            )
            for leg in legs
        ]
        store.create_trade(
            TradeRecord(
                trade_id=TRADE_ID,
                run_id=str(trade["run_id"]),
                signal_key=trade["signal_key"],
                decision_id=trade["decision_id"],
                asset_class="BAG",
                symbol="SPXW",
                side="short",
                status="closed",
                opened_at_utc=ENTRY_AT,
                closed_at_utc=EXPIRY_AT,
                quantity=float(trade["quantity"]),
                currency="USD",
                entry_price=ENTRY_CREDIT,
                exit_price=0.0,
                realized_pnl=net_pnl,
                metadata=metadata,
            ),
            updated_legs,
        )

        for item in EXPIRATION_LEGS:
            order_id = deterministic_id("order", "IBKR", ACCOUNT, "perm", item["perm_id"])
            store.upsert_order(
                OrderRecord(
                    order_id=order_id,
                    run_id=str(trade["run_id"]),
                    trade_id=TRADE_ID,
                    signal_key=trade["signal_key"],
                    parent_order_id=None,
                    broker="IBKR",
                    broker_order_id=f"permId:{item['perm_id']}",
                    order_ref="SPX15-EXP-20260819",
                    order_type="EXPIRATION",
                    action=item["action"],
                    quantity=1.0,
                    limit_price=None,
                    stop_price=None,
                    time_in_force="DAY",
                    transmit=True,
                    status="Filled",
                    submitted_at_utc=None,
                    updated_at_utc=EXPIRY_AT,
                    metadata={
                        "account": ACCOUNT,
                        "client_id": CLIENT_ID,
                        "perm_id": item["perm_id"],
                        "order_id": 0,
                        "role": "expiration_close",
                        "source": "Paper TWS",
                        "contract": {
                            "conId": item["con_id"],
                            "localSymbol": item["local_symbol"],
                            "secType": "OPT",
                            "exchange": "CBOE",
                            "symbol": "SPX",
                            "tradingClass": "SPXW",
                        },
                    },
                ),
                [
                    OrderLegRecord(
                        order_leg_id=deterministic_id("order-leg", order_id, 0),
                        order_id=order_id,
                        leg_index=0,
                        trade_leg_id=item["trade_leg_id"],
                        con_id=item["con_id"],
                        symbol="SPXW",
                        action=item["action"],
                        ratio=1,
                        exchange="CBOE",
                        open_close="C",
                        metadata={"source": "Paper TWS", "expiration": EXPIRY_DAY},
                    )
                ],
            )
            store.upsert_execution(
                ExecutionRecord(
                    execution_id=deterministic_id("execution", "IBKR", item["perm_id"], EXPIRY_AT),
                    order_id=order_id,
                    trade_id=TRADE_ID,
                    broker_execution_id=item["exec_id"],
                    executed_at_utc=EXPIRY_AT,
                    side=item["side"],
                    quantity=1.0,
                    price=0.0,
                    exchange="CBOE",
                    liquidity=None,
                    metadata={
                        "source": "Paper TWS",
                        "expiration": EXPIRY_DAY,
                        "settlement": "worthless",
                        "con_id": item["con_id"],
                        "local_symbol": item["local_symbol"],
                    },
                )
            )

        store.add_event(
            TradeEventRecord(
                event_id=deterministic_id("trade-event", TRADE_ID, "expiration", EXPIRY_AT),
                run_id=str(trade["run_id"]),
                trade_id=TRADE_ID,
                order_id=None,
                signal_key=trade["signal_key"],
                event_type="option_expired_worthless",
                occurred_at_utc=EXPIRY_AT,
                payload={
                    "source": "Paper TWS",
                    "expiry": EXPIRY_DAY,
                    "settlement_value": 0.0,
                    "gross_realized_pnl": gross_pnl,
                    "commissions_usd": ENTRY_COMMISSIONS,
                    "net_realized_pnl": net_pnl,
                },
            )
        )
        result = store._connection.execute(
            "SELECT status, opened_at_utc, closed_at_utc, entry_price, exit_price, realized_pnl "
            "FROM trades WHERE trade_id=?",
            (TRADE_ID,),
        ).fetchone()
        print(json.dumps(dict(result), sort_keys=True))


if __name__ == "__main__":
    main()
