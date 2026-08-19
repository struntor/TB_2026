from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys
import time

from ib_insync import IB, Stock


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.execution.safe_bracket_order import BracketOrderSpec, SafeBracketOrderManager


def active_symbol_exposure(ib: IB, account: str, symbol: str) -> list[str]:
    normalized = symbol.upper()
    findings: list[str] = []
    for position in ib.positions(account=account):
        if str(position.contract.symbol).upper() == normalized and float(position.position) != 0:
            findings.append(f"position={float(position.position)}")
    ib.reqAllOpenOrders()
    ib.sleep(0.2)
    for trade in ib.openTrades():
        if str(getattr(trade.order, "account", "") or "") != account:
            continue
        if str(trade.contract.symbol).upper() == normalized:
            findings.append(f"open_order={int(trade.order.orderId)}")
    return findings


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Submit, verify and cancel one non-marketable Paper-TWS bracket.")
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--client-id", required=True, type=int)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--side", required=True, choices=("long", "short"))
    parser.add_argument("--quantity", required=True, type=float)
    parser.add_argument("--entry-limit", required=True, type=float)
    parser.add_argument("--take-profit", required=True, type=float)
    parser.add_argument("--stop-loss", required=True, type=float)
    parser.add_argument("--cancel-after-seconds", required=True, type=float)
    parser.add_argument("--ack-timeout-seconds", required=True, type=float)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    ib = IB()
    submission = None
    manager = None
    try:
        ib.connect(args.host, args.port, clientId=args.client_id, timeout=8, readonly=False)
        accounts = ib.managedAccounts()
        if len(accounts) != 1:
            raise RuntimeError(f"expected exactly one managed account, got {len(accounts)}")
        account = accounts[0]
        if not account.upper().startswith("DU"):
            raise RuntimeError("paper-account guard rejected the connected account")

        symbol = args.symbol.upper()
        existing = active_symbol_exposure(ib, account, symbol)
        if existing:
            raise RuntimeError(f"test symbol already has exposure: {existing}")

        [contract] = ib.qualifyContracts(Stock(symbol, "SMART", "USD"))
        details = ib.reqContractDetails(contract)
        if not details:
            raise RuntimeError("no contract details returned")
        tick_size = float(details[0].minTick or 0.01)
        order_ref = f"CODEX_SAFE_BRACKET_{int(time.time())}"[:45]
        action = "BUY" if args.side == "long" else "SELL"
        spec = BracketOrderSpec(
            action=action,
            quantity=args.quantity,
            reference_entry_price=args.entry_limit,
            take_profit_price=args.take_profit,
            stop_loss_price=args.stop_loss,
            entry_order_type="LMT",
            limit_price=args.entry_limit,
            parent_tif="DAY",
            exit_tif="GTC",
            outside_rth=False,
            order_ref=order_ref,
            tick_size=tick_size,
        )
        manager = SafeBracketOrderManager(
            ib,
            account=account,
            require_paper_account=True,
            ack_timeout_seconds=args.ack_timeout_seconds,
        )
        submission = manager.submit(contract, spec)
        print(
            json.dumps(
                {
                    "phase": "submitted",
                    "paper_account": True,
                    "symbol": symbol,
                    "order_ref": order_ref,
                    "states": [asdict(state) for state in manager.states(submission)],
                },
                sort_keys=True,
            ),
            flush=True,
        )

        ib.sleep(args.cancel_after_seconds)
        cancelled_states = manager.cancel(submission)
        ib.reqAllOpenOrders()
        ib.sleep(0.5)
        remaining = active_symbol_exposure(ib, account, symbol)
        print(
            json.dumps(
                {
                    "phase": "cancelled",
                    "symbol": symbol,
                    "states": [asdict(state) for state in cancelled_states],
                    "remaining_exposure": remaining,
                },
                sort_keys=True,
            ),
            flush=True,
        )
        if remaining:
            raise RuntimeError(f"cleanup failed: {remaining}")
        if not all(state.status in {"Cancelled", "ApiCancelled"} for state in cancelled_states):
            raise RuntimeError("not every bracket leg reached a cancelled state")
        return 0
    finally:
        if submission is not None and manager is not None:
            manager.cancel(submission)
        if ib.isConnected():
            ib.disconnect()


if __name__ == "__main__":
    raise SystemExit(main())
