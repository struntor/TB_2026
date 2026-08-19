from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys
import time

from ib_insync import IB, MarketOrder, Stock


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.execution.safe_bracket_order import BracketOrderSpec, SafeBracketOrderManager


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fill, verify, cancel and flatten one Paper-TWS bracket.")
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--client-id", required=True, type=int)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--quantity", required=True, type=float)
    parser.add_argument("--reference-entry", required=True, type=float)
    parser.add_argument("--take-profit", required=True, type=float)
    parser.add_argument("--stop-loss", required=True, type=float)
    parser.add_argument("--ack-timeout-seconds", required=True, type=float)
    parser.add_argument("--fill-timeout-seconds", required=True, type=float)
    return parser.parse_args()


def account_position(ib: IB, account: str, symbol: str) -> float:
    normalized = symbol.upper()
    return sum(
        float(position.position)
        for position in ib.positions(account=account)
        if str(position.contract.symbol).upper() == normalized
    )


def wait_for(ib: IB, predicate, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        ib.waitOnUpdate(timeout=0.25)
    return bool(predicate())


def flatten_test_position(ib: IB, account: str, contract, symbol: str, timeout: float) -> dict[str, object]:
    quantity = account_position(ib, account, symbol)
    if quantity == 0:
        return {"needed": False, "status": "no_position"}
    action = "SELL" if quantity > 0 else "BUY"
    order = MarketOrder(action, abs(quantity), tif="DAY")
    order.account = account
    order.orderRef = f"CODEX_BRACKET_CLEANUP_{int(time.time())}"[:45]
    trade = ib.placeOrder(contract, order)
    filled = wait_for(
        ib,
        lambda: str(trade.orderStatus.status or "") in {"Filled", "Cancelled", "ApiCancelled", "Inactive"},
        timeout,
    )
    ib.sleep(0.5)
    return {
        "needed": True,
        "wait_completed": filled,
        "order_id": int(trade.order.orderId),
        "perm_id": int(trade.order.permId or 0),
        "status": str(trade.orderStatus.status or ""),
        "filled": float(trade.orderStatus.filled or 0.0),
        "avg_fill_price": float(trade.orderStatus.avgFillPrice or 0.0),
    }


def main() -> int:
    args = parse_args()
    ib = IB()
    manager = None
    submission = None
    contract = None
    account = ""
    cleanup: dict[str, object] = {"needed": False, "status": "not_run"}
    try:
        ib.connect(args.host, args.port, clientId=args.client_id, timeout=8, readonly=False)
        accounts = ib.managedAccounts()
        if len(accounts) != 1 or not accounts[0].upper().startswith("DU"):
            raise RuntimeError("exactly one Paper account is required")
        account = accounts[0]
        symbol = args.symbol.upper()
        if account_position(ib, account, symbol) != 0:
            raise RuntimeError("test symbol already has a position")
        ib.reqAllOpenOrders()
        ib.sleep(0.2)
        if any(str(trade.contract.symbol).upper() == symbol for trade in ib.openTrades()):
            raise RuntimeError("test symbol already has an open order")

        [contract] = ib.qualifyContracts(Stock(symbol, "SMART", "USD"))
        details = ib.reqContractDetails(contract)
        if not details:
            raise RuntimeError("no contract details returned")
        tick_size = float(details[0].minTick or 0.01)
        order_ref = f"CODEX_SAFE_FILL_{int(time.time())}"[:45]
        manager = SafeBracketOrderManager(
            ib,
            account=account,
            require_paper_account=True,
            ack_timeout_seconds=args.ack_timeout_seconds,
        )
        spec = BracketOrderSpec(
            action="BUY",
            quantity=args.quantity,
            reference_entry_price=args.reference_entry,
            take_profit_price=args.take_profit,
            stop_loss_price=args.stop_loss,
            entry_order_type="MKT",
            limit_price=None,
            parent_tif="DAY",
            exit_tif="GTC",
            outside_rth=False,
            order_ref=order_ref,
            tick_size=tick_size,
        )
        submission = manager.submit(contract, spec)
        parent_filled = wait_for(
            ib,
            lambda: str(submission.parent_trade.orderStatus.status or "") == "Filled",
            args.fill_timeout_seconds,
        )
        post_fill_states = manager.states(submission)
        print(
            json.dumps(
                {
                    "phase": "post_fill",
                    "paper_account": True,
                    "symbol": symbol,
                    "order_ref": order_ref,
                    "parent_filled": parent_filled,
                    "parent_avg_fill_price": float(submission.parent_trade.orderStatus.avgFillPrice or 0.0),
                    "states": [asdict(state) for state in post_fill_states],
                },
                sort_keys=True,
            ),
            flush=True,
        )
        if not parent_filled:
            raise RuntimeError("market parent did not fill in time")
        if any(state.errors for state in post_fill_states):
            raise RuntimeError("TWS reported an error on a bracket leg")
        for state in post_fill_states[1:]:
            if state.status not in {"PreSubmitted", "Submitted"}:
                raise RuntimeError(f"protective leg is not active: {state.role}={state.status}")

        cancelled_states = manager.cancel(submission)
        cleanup = flatten_test_position(ib, account, contract, symbol, args.fill_timeout_seconds)
        ib.sleep(0.5)
        remaining_position = account_position(ib, account, symbol)
        ib.reqAllOpenOrders()
        ib.sleep(0.2)
        remaining_orders = [
            int(trade.order.orderId)
            for trade in ib.openTrades()
            if str(trade.contract.symbol).upper() == symbol
        ]
        print(
            json.dumps(
                {
                    "phase": "cleanup",
                    "cancelled_states": [asdict(state) for state in cancelled_states],
                    "flatten": cleanup,
                    "remaining_position": remaining_position,
                    "remaining_orders": remaining_orders,
                },
                sort_keys=True,
            ),
            flush=True,
        )
        if remaining_position != 0 or remaining_orders:
            raise RuntimeError("test cleanup left position or orders behind")
        if cleanup.get("status") != "Filled":
            raise RuntimeError(f"cleanup order did not fill: {cleanup}")
        return 0
    finally:
        if submission is not None and manager is not None:
            manager.cancel(submission)
        if contract is not None and account and ib.isConnected():
            residual = account_position(ib, account, str(contract.symbol))
            if residual != 0:
                emergency = flatten_test_position(
                    ib,
                    account,
                    contract,
                    str(contract.symbol),
                    args.fill_timeout_seconds,
                )
                print(json.dumps({"phase": "final_emergency_cleanup", "flatten": emergency}, sort_keys=True), flush=True)
        if ib.isConnected():
            ib.disconnect()


if __name__ == "__main__":
    raise SystemExit(main())
