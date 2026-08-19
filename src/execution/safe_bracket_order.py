from __future__ import annotations

from dataclasses import dataclass
import math
import time
from typing import Any

from ib_insync import IB, LimitOrder, MarketOrder, OrderStatus, StopOrder


DONE_STATES = set(getattr(OrderStatus, "DoneStates", {"Filled", "Cancelled", "ApiCancelled"}))
PENDING_ACK_STATES = {"", "ApiPending", "PendingSubmit"}
ACCEPTED_STATES = {"PreSubmitted", "Submitted", "Filled"}


class SafeBracketError(RuntimeError):
    """Raised when a bracket cannot be submitted or verified safely."""


@dataclass(frozen=True)
class BracketOrderSpec:
    action: str
    quantity: float
    reference_entry_price: float
    take_profit_price: float
    stop_loss_price: float
    entry_order_type: str
    limit_price: float | None
    parent_tif: str
    exit_tif: str
    outside_rth: bool
    order_ref: str
    tick_size: float


@dataclass(frozen=True)
class BracketLegState:
    role: str
    order_id: int
    perm_id: int
    parent_id: int
    status: str
    filled: float
    remaining: float
    transmit: bool
    order_ref: str
    errors: tuple[str, ...]


@dataclass
class SafeBracketSubmission:
    contract: Any
    spec: BracketOrderSpec
    parent_trade: Any
    take_profit_trade: Any
    stop_loss_trade: Any

    @property
    def trades(self) -> tuple[Any, Any, Any]:
        return self.parent_trade, self.take_profit_trade, self.stop_loss_trade

    @property
    def order_ids(self) -> tuple[int, int, int]:
        return tuple(int(trade.order.orderId) for trade in self.trades)


class SafeBracketOrderManager:
    """Submit and verify an attached three-leg bracket through TWS.

    Parent and take-profit are staged with ``transmit=False``. Sending the
    stop-loss with ``transmit=True`` releases the complete attached bracket.
    """

    def __init__(
        self,
        ib: IB,
        *,
        account: str,
        require_paper_account: bool,
        ack_timeout_seconds: float,
    ) -> None:
        if not account:
            raise ValueError("account is required")
        if ack_timeout_seconds <= 0:
            raise ValueError("ack_timeout_seconds must be positive")
        self.ib = ib
        self.account = account
        self.require_paper_account = require_paper_account
        self.ack_timeout_seconds = float(ack_timeout_seconds)

    def validate_connection(self) -> None:
        if not self.ib.isConnected():
            raise SafeBracketError("TWS is not connected")
        managed_accounts = set(self.ib.managedAccounts())
        if self.account not in managed_accounts:
            raise SafeBracketError("configured account is not managed by this TWS session")
        if self.require_paper_account and not self.account.upper().startswith("DU"):
            raise SafeBracketError("paper-account guard rejected the connected account")

    def build_orders(self, spec: BracketOrderSpec) -> tuple[Any, Any, Any]:
        self._validate_spec(spec)
        action = spec.action.upper()
        exit_action = "SELL" if action == "BUY" else "BUY"
        quantity = float(spec.quantity)
        reference_entry = self._round_to_tick(spec.reference_entry_price, spec.tick_size)
        target = self._round_to_tick(spec.take_profit_price, spec.tick_size)
        stop = self._round_to_tick(spec.stop_loss_price, spec.tick_size)
        entry_order_type = spec.entry_order_type.upper()
        if action == "BUY" and not stop < reference_entry < target:
            raise ValueError("rounded BUY levels must satisfy stop < reference entry < target")
        if action == "SELL" and not target < reference_entry < stop:
            raise ValueError("rounded SELL levels must satisfy target < reference entry < stop")

        parent_id = self.ib.client.getReqId()
        take_profit_id = self.ib.client.getReqId()
        stop_loss_id = self.ib.client.getReqId()

        if entry_order_type == "MKT":
            parent = MarketOrder(action, quantity)
        else:
            limit_price = self._round_to_tick(float(spec.limit_price), spec.tick_size)
            if action == "BUY" and not stop < limit_price < target:
                raise ValueError("rounded BUY levels must satisfy stop < limit < target")
            if action == "SELL" and not target < limit_price < stop:
                raise ValueError("rounded SELL levels must satisfy target < limit < stop")
            parent = LimitOrder(action, quantity, limit_price)

        take_profit = LimitOrder(exit_action, quantity, target)
        stop_loss = StopOrder(exit_action, quantity, stop)

        parent.orderId = parent_id
        parent.transmit = False
        parent.tif = spec.parent_tif

        take_profit.orderId = take_profit_id
        take_profit.parentId = parent_id
        take_profit.transmit = False
        take_profit.tif = spec.exit_tif

        stop_loss.orderId = stop_loss_id
        stop_loss.parentId = parent_id
        stop_loss.transmit = True
        stop_loss.tif = spec.exit_tif

        for order in (parent, take_profit, stop_loss):
            order.account = self.account
            order.orderRef = spec.order_ref
            order.outsideRth = spec.outside_rth

        return parent, take_profit, stop_loss

    def submit(self, contract: Any, spec: BracketOrderSpec) -> SafeBracketSubmission:
        self.validate_connection()
        self._assert_unique_active_order_ref(spec.order_ref)
        parent, take_profit, stop_loss = self.build_orders(spec)
        placed_trades: list[Any] = []
        try:
            parent_trade = self.ib.placeOrder(contract, parent)
            placed_trades.append(parent_trade)
            take_profit_trade = self.ib.placeOrder(contract, take_profit)
            placed_trades.append(take_profit_trade)
            stop_loss_trade = self.ib.placeOrder(contract, stop_loss)
            placed_trades.append(stop_loss_trade)
        except Exception as exc:
            self._cancel_trades(placed_trades)
            raise SafeBracketError(f"failed while staging bracket: {type(exc).__name__}: {exc}") from exc

        submission = SafeBracketSubmission(
            contract=contract,
            spec=spec,
            parent_trade=parent_trade,
            take_profit_trade=take_profit_trade,
            stop_loss_trade=stop_loss_trade,
        )
        self.wait_for_ack(submission)
        states = self.states(submission)
        errors = [error for state in states for error in state.errors]
        invalid = [state for state in states if state.status not in ACCEPTED_STATES]
        if errors or invalid:
            self.cancel(submission)
            detail = "; ".join(errors) or ", ".join(f"{state.role}={state.status}" for state in invalid)
            raise SafeBracketError(f"bracket was not acknowledged safely: {detail}")
        return submission

    def wait_for_ack(self, submission: SafeBracketSubmission) -> tuple[BracketLegState, ...]:
        deadline = time.monotonic() + self.ack_timeout_seconds
        while time.monotonic() < deadline:
            states = self.states(submission)
            if all(state.status not in PENDING_ACK_STATES for state in states):
                return states
            self.ib.waitOnUpdate(timeout=0.25)
        return self.states(submission)

    def cancel(self, submission: SafeBracketSubmission) -> tuple[BracketLegState, ...]:
        self._cancel_trades(list(reversed(submission.trades)))
        deadline = time.monotonic() + self.ack_timeout_seconds
        while time.monotonic() < deadline:
            states = self.states(submission)
            if all(state.status in DONE_STATES for state in states):
                return states
            self.ib.waitOnUpdate(timeout=0.25)
        return self.states(submission)

    def states(self, submission: SafeBracketSubmission) -> tuple[BracketLegState, ...]:
        roles = ("parent", "take_profit", "stop_loss")
        return tuple(self._state(role, trade) for role, trade in zip(roles, submission.trades, strict=True))

    def _assert_unique_active_order_ref(self, order_ref: str) -> None:
        self.ib.reqAllOpenOrders()
        self.ib.sleep(0.2)
        duplicates = [
            trade
            for trade in self.ib.openTrades()
            if str(getattr(trade.order, "account", "") or "") == self.account
            and str(getattr(trade.order, "orderRef", "") or "") == order_ref
            and str(getattr(trade.orderStatus, "status", "") or "") not in DONE_STATES
        ]
        if duplicates:
            order_ids = sorted(int(trade.order.orderId) for trade in duplicates)
            raise SafeBracketError(f"active orderRef already exists: {order_ref} order_ids={order_ids}")

    def _cancel_trades(self, trades: list[Any]) -> None:
        for trade in trades:
            status = str(getattr(trade.orderStatus, "status", "") or "")
            if status in DONE_STATES:
                continue
            try:
                self.ib.cancelOrder(trade.order)
            except Exception:
                continue

    @staticmethod
    def _state(role: str, trade: Any) -> BracketLegState:
        order = trade.order
        order_status = trade.orderStatus
        errors: list[str] = []
        for entry in getattr(trade, "log", []) or []:
            error_code = int(getattr(entry, "errorCode", 0) or 0)
            if error_code:
                errors.append(f"{error_code}:{str(getattr(entry, 'message', '') or '')}")
        advanced_error = str(getattr(trade, "advancedError", "") or "")
        if advanced_error:
            errors.append(f"advanced:{advanced_error}")
        return BracketLegState(
            role=role,
            order_id=int(getattr(order, "orderId", 0) or 0),
            perm_id=int(getattr(order, "permId", 0) or 0),
            parent_id=int(getattr(order, "parentId", 0) or 0),
            status=str(getattr(order_status, "status", "") or ""),
            filled=float(getattr(order_status, "filled", 0.0) or 0.0),
            remaining=float(getattr(order_status, "remaining", 0.0) or 0.0),
            transmit=bool(getattr(order, "transmit", False)),
            order_ref=str(getattr(order, "orderRef", "") or ""),
            errors=tuple(errors),
        )

    @staticmethod
    def _validate_spec(spec: BracketOrderSpec) -> None:
        action = spec.action.upper()
        if action not in {"BUY", "SELL"}:
            raise ValueError("action must be BUY or SELL")
        if not math.isfinite(float(spec.quantity)) or float(spec.quantity) <= 0:
            raise ValueError("quantity must be positive")
        if not spec.order_ref or len(spec.order_ref) > 45:
            raise ValueError("order_ref must contain 1 to 45 characters")
        if not math.isfinite(float(spec.tick_size)) or float(spec.tick_size) <= 0:
            raise ValueError("tick_size must be positive")
        entry_order_type = spec.entry_order_type.upper()
        if entry_order_type not in {"MKT", "LMT"}:
            raise ValueError("entry_order_type must be MKT or LMT")
        if entry_order_type == "LMT" and spec.limit_price is None:
            raise ValueError("limit_price is required for LMT entry")

        reference = float(spec.reference_entry_price)
        target = float(spec.take_profit_price)
        stop = float(spec.stop_loss_price)
        if not all(math.isfinite(value) and value > 0 for value in (reference, target, stop)):
            raise ValueError("reference, target and stop prices must be positive finite values")
        if action == "BUY" and not stop < reference < target:
            raise ValueError("BUY bracket requires stop < reference entry < target")
        if action == "SELL" and not target < reference < stop:
            raise ValueError("SELL bracket requires target < reference entry < stop")
        if entry_order_type == "LMT":
            limit_price = float(spec.limit_price)
            if not math.isfinite(limit_price) or limit_price <= 0:
                raise ValueError("limit_price must be a positive finite value")
            if action == "BUY" and not stop < limit_price < target:
                raise ValueError("BUY bracket requires stop < limit < target")
            if action == "SELL" and not target < limit_price < stop:
                raise ValueError("SELL bracket requires target < limit < stop")

    @staticmethod
    def _round_to_tick(price: float, tick_size: float) -> float:
        steps = round(float(price) / float(tick_size))
        decimals = max(0, min(8, len(str(float(tick_size)).split(".")[-1].rstrip("0"))))
        return round(steps * float(tick_size), decimals)
