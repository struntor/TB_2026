from __future__ import annotations

import argparse
import csv
from dataclasses import asdict
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sys
import time
from typing import Any

import pandas as pd
from ib_insync import IB, LimitOrder


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.execution.spxw_put_bwb import (  # noqa: E402
    SpxwPutBwbSelection,
    build_profit_target_order,
    require_paper_account,
    select_spxw_put_bwb,
    selection_for_identity,
)
from src.execution.spxw_put_credit import round_credit_down  # noqa: E402


DONE_STATUSES = frozenset({"Filled", "Cancelled", "ApiCancelled", "Inactive"})
ACTIVE_STATUSES = frozenset({"PendingSubmit", "PreSubmitted", "Submitted", "PendingCancel"})
DEFAULT_OUTPUT_ROOT = Path(r"D:\Trading\research\spxw_75dte_bwb_fill_test")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def log(message: str) -> None:
    stamp = pd.Timestamp.now(tz="Europe/Berlin").strftime("%Y-%m-%d %H:%M:%S %Z")
    print(f"[{stamp}] {message}", flush=True)


def finite(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def order_status(trade: Any) -> str:
    return str(getattr(getattr(trade, "orderStatus", None), "status", "") or "")


def average_fill_credit(trade: Any) -> float | None:
    value = finite(getattr(getattr(trade, "orderStatus", None), "avgFillPrice", None))
    return abs(value) if value not in (None, 0.0) else None


def direct_combo_quote(ib: IB, selection: SpxwPutBwbSelection, timeout: float = 6.0) -> dict[str, float]:
    ticker = ib.reqMktData(selection.combo_contract, "", False, False)
    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline:
            ib.sleep(0.25)
            bid, ask = finite(ticker.bid), finite(ticker.ask)
            if bid is not None and ask is not None and bid < 0 and ask < 0 and bid <= ask:
                return {
                    "bag_bid": bid,
                    "bag_ask": ask,
                    "bag_mid": (bid + ask) / 2.0,
                    "bid_credit": -bid,
                    "ask_credit": -ask,
                }
    finally:
        ib.cancelMktData(selection.combo_contract)
    raise RuntimeError("no valid direct negative SPXW BAG bid/ask quote")


def ladder_credit(
    *,
    initial_credit: float,
    attempt: int,
    decrement: float,
    ask_credit: float,
    min_tick: float,
) -> float:
    if attempt < 1:
        raise ValueError("attempt must be at least one")
    if min(initial_credit, decrement, ask_credit, min_tick) <= 0:
        raise ValueError("credits, decrement and min_tick must be positive")
    passive_ladder = initial_credit - (attempt - 1) * decrement
    one_tick_through_ask = max(min_tick, ask_credit - min_tick)
    return round_credit_down(max(passive_ladder, one_tick_through_ask), min_tick)


def maximum_loss_dollars(selection: SpxwPutBwbSelection, credit: float) -> float:
    downside = selection.lower_strike - 2.0 * selection.body_strike + selection.upper_strike
    return max(0.0, -(downside + credit)) * selection.multiplier


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def append_attempt(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.exists()
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(row))
        if not exists:
            writer.writeheader()
        writer.writerow(row)


def all_trades(ib: IB) -> tuple[Any, ...]:
    trades = list(ib.trades())
    try:
        trades.extend(ib.reqCompletedOrders(False))
    except Exception as exc:
        log(f"completed-order lookup unavailable: {type(exc).__name__}: {exc}")
    unique: dict[tuple[int, str], Any] = {}
    for trade in trades:
        order = getattr(trade, "order", None)
        key = (int(getattr(order, "orderId", 0) or 0), str(getattr(order, "orderRef", "") or ""))
        unique[key] = trade
    return tuple(unique.values())


def assert_no_conflicting_exposure(ib: IB, selection: SpxwPutBwbSelection, prefix: str) -> None:
    ib.reqAllOpenOrders()
    ib.sleep(1.0)
    for trade in all_trades(ib):
        ref = str(getattr(getattr(trade, "order", None), "orderRef", "") or "")
        if (ref.startswith("SPX75-") or ref.startswith(prefix)) and order_status(trade) not in DONE_STATUSES:
            raise RuntimeError(f"active SPXW 75DTE/BWB order already exists: {ref} {order_status(trade)}")
    selected_ids = {int(leg.contract.conId) for leg in selection.legs}
    exposures = [
        position
        for position in ib.positions()
        if int(getattr(position.contract, "conId", 0) or 0) in selected_ids
        and float(getattr(position, "position", 0.0) or 0.0) != 0.0
    ]
    if exposures:
        raise RuntimeError("selected BWB legs already have broker positions; refusing a duplicate test")


def wait_for_fill_or_timeout(ib: IB, trade: Any, seconds: float) -> str:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        ib.sleep(min(1.0, max(0.1, deadline - time.monotonic())))
        status = order_status(trade)
        if status in DONE_STATUSES:
            return status
    return order_status(trade)


def cancel_and_confirm(ib: IB, trade: Any, timeout: float = 15.0) -> str:
    if order_status(trade) in DONE_STATUSES:
        return order_status(trade)
    ib.cancelOrder(trade.order)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        ib.sleep(0.5)
        if order_status(trade) in DONE_STATUSES:
            return order_status(trade)
    raise RuntimeError(f"entry cancellation was not acknowledged; status={order_status(trade)}")


def refreshed_selection(ib: IB, original: SpxwPutBwbSelection) -> SpxwPutBwbSelection:
    return selection_for_identity(
        ib,
        expiry=original.expiry,
        upper_strike=original.upper_strike,
        body_strike=original.body_strike,
        lower_strike=original.lower_strike,
        opening_vix=original.opening_vix,
        entry_slippage_points=original.entry_slippage_points,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Isolated DU-paper fill test for the SPXW 75DTE put BWB")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7496)
    parser.add_argument("--client-id", type=int, default=93)
    parser.add_argument("--account")
    parser.add_argument("--wait-minutes", type=float, default=5.0)
    parser.add_argument("--credit-step", type=float, default=0.20)
    parser.add_argument("--max-attempts", type=int, default=60)
    parser.add_argument("--stop-new-orders-et", default="15:50")
    parser.add_argument("--max-risk-usd", type=float, default=2_000.0)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--confirm-paper-order", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.confirm_paper_order:
        raise RuntimeError("--confirm-paper-order is required; this test submits a real DU paper BAG order")
    if args.wait_minutes <= 0 or args.credit_step <= 0 or args.max_attempts <= 0:
        raise ValueError("wait-minutes, credit-step and max-attempts must be positive")

    day = pd.Timestamp.now(tz="America/New_York")
    day_text, compact_day = day.strftime("%Y-%m-%d"), day.strftime("%Y%m%d")
    output_dir = args.output_root / compact_day
    state_path, attempts_path = output_dir / "state.json", output_dir / "attempts.csv"
    prefix = f"SPX75-TEST-{day.strftime('%m%d')}"
    state: dict[str, Any] = {
        "schema_version": 1,
        "test_id": prefix,
        "day": day_text,
        "status": "starting",
        "created_at_utc": utc_now(),
        "vix_filter_bypassed": True,
        "attempts": [],
    }
    atomic_write_json(state_path, state)

    ib = IB()
    active_trade: Any | None = None
    filled_trade: Any | None = None
    try:
        ib.connect(args.host, args.port, clientId=args.client_id, timeout=8)
        accounts = ib.managedAccounts()
        account = args.account or (accounts[0] if len(accounts) == 1 else None)
        if not account or account not in accounts:
            raise RuntimeError("a unique managed --account is required")
        require_paper_account(account)
        log(f"connected to TWS paper account=*****{account[-4:]} test={prefix}")

        selection = select_spxw_put_bwb(
            ib,
            decision_day=pd.Timestamp(day_text),
            opening_vix=0.0,
            entry_slippage_points=0.20,
        )
        assert_no_conflicting_exposure(ib, selection, prefix)
        initial_credit = selection.entry_limit_credit
        state.update(
            {
                "status": "testing_entry",
                "account_suffix": account[-4:],
                "expiry": selection.expiry,
                "spot": selection.spot,
                "strikes": [selection.upper_strike, selection.body_strike, selection.lower_strike],
                "con_ids": [int(leg.contract.conId) for leg in selection.legs],
                "initial_synthetic_mid_credit": selection.mid_credit,
                "initial_synthetic_natural_credit": selection.natural_credit,
                "initial_configured_credit": initial_credit,
                "min_tick": selection.min_tick,
            }
        )
        atomic_write_json(state_path, state)

        for attempt in range(1, args.max_attempts + 1):
            now_et = pd.Timestamp.now(tz="America/New_York")
            if now_et.strftime("%H:%M") >= args.stop_new_orders_et:
                state.update({"status": "stopped_without_fill", "stop_reason": "stop_new_orders_et", "ended_at_utc": utc_now()})
                atomic_write_json(state_path, state)
                log(f"stopping without fill at {now_et:%H:%M:%S %Z}; no new orders after {args.stop_new_orders_et} ET")
                return

            current = refreshed_selection(ib, selection)
            bag = direct_combo_quote(ib, current)
            credit = ladder_credit(
                initial_credit=initial_credit,
                attempt=attempt,
                decrement=args.credit_step,
                ask_credit=bag["ask_credit"],
                min_tick=current.min_tick,
            )
            risk = maximum_loss_dollars(current, credit)
            if risk > args.max_risk_usd:
                raise RuntimeError(f"test limit would exceed max risk: ${risk:.2f} > ${args.max_risk_usd:.2f}")

            order_ref = f"{prefix}-E{attempt:02d}"[:45]
            order = LimitOrder("BUY", 1, -credit)
            order.account, order.orderRef, order.tif = account, order_ref, "DAY"
            order.outsideRth, order.transmit = False, True
            active_trade = ib.placeOrder(current.combo_contract, order)
            ib.sleep(1.0)
            submitted_at = utc_now()
            row = {
                "attempt": attempt,
                "submitted_at_utc": submitted_at,
                "order_ref": order_ref,
                "order_id": int(getattr(active_trade.order, "orderId", 0) or 0),
                "limit_credit": credit,
                "limit_price": -credit,
                "synthetic_mid_credit": current.mid_credit,
                "synthetic_natural_credit": current.natural_credit,
                **bag,
                "maximum_loss_usd": risk,
                "terminal_status": "",
                "fill_credit": "",
            }
            log(
                f"attempt={attempt} ref={order_ref} limit_credit={credit:.2f} "
                f"BAG bid/ask={bag['bag_bid']:.2f}/{bag['bag_ask']:.2f} "
                f"synthetic mid/natural={current.mid_credit:.2f}/{current.natural_credit:.2f}"
            )
            status = wait_for_fill_or_timeout(ib, active_trade, args.wait_minutes * 60.0)
            if status != "Filled":
                status = cancel_and_confirm(ib, active_trade)
            fill_credit = average_fill_credit(active_trade) if status == "Filled" else None
            row["terminal_status"], row["fill_credit"] = status, fill_credit or ""
            append_attempt(attempts_path, row)
            state["attempts"].append(row)
            atomic_write_json(state_path, state)

            if status == "Filled":
                filled_trade = active_trade
                active_trade = None
                actual_credit = float(fill_credit or credit)
                target_ref = f"{prefix}-X"[:45]
                target = build_profit_target_order(
                    current,
                    entry_credit=actual_credit,
                    profit_target_fraction=0.40,
                    account=account,
                    order_ref=target_ref,
                )
                target_trade = ib.placeOrder(current.inverse_combo_contract, target)
                ib.sleep(2.0)
                target_status = order_status(target_trade)
                if target_status not in ACTIVE_STATUSES and target_status != "Filled":
                    raise RuntimeError(f"entry filled but GTC target was not accepted: {target_status}")
                state.update(
                    {
                        "status": "filled_target_active" if target_status != "Filled" else "filled_target_filled",
                        "filled_at_utc": utc_now(),
                        "fill_attempt": attempt,
                        "fill_credit": actual_credit,
                        "fill_vs_initial_credit": actual_credit - initial_credit,
                        "fill_vs_attempt_bag_ask_credit": actual_credit - bag["ask_credit"],
                        "actual_maximum_loss_usd": maximum_loss_dollars(current, actual_credit),
                        "target_order_ref": target_ref,
                        "target_order_id": int(getattr(target_trade.order, "orderId", 0) or 0),
                        "target_debit": float(target.lmtPrice),
                        "target_status": target_status,
                        "ended_at_utc": utc_now(),
                    }
                )
                atomic_write_json(state_path, state)
                log(
                    f"FILLED attempt={attempt} credit={actual_credit:.2f}; "
                    f"inverse GTC 40% target accepted debit={target.lmtPrice:.2f} status={target_status}"
                )
                return
            active_trade = None

        state.update({"status": "stopped_without_fill", "stop_reason": "max_attempts", "ended_at_utc": utc_now()})
        atomic_write_json(state_path, state)
    except BaseException as exc:
        state.update({"status": "error", "error": f"{type(exc).__name__}: {exc}", "ended_at_utc": utc_now()})
        atomic_write_json(state_path, state)
        raise
    finally:
        if active_trade is not None and order_status(active_trade) not in DONE_STATUSES:
            try:
                cancel_and_confirm(ib, active_trade)
                log(f"cancelled active test entry during shutdown ref={active_trade.order.orderRef}")
            except Exception as exc:
                log(f"WARNING: could not confirm shutdown cancellation: {type(exc).__name__}: {exc}")
        if ib.isConnected():
            ib.disconnect()


if __name__ == "__main__":
    main()
