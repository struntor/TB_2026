from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from functools import wraps
import math
from typing import Any, ContextManager, Mapping, Protocol, Sequence

from .models import (
    CommissionRecord,
    ExecutionRecord,
    OrderBinding,
    OrderLegRecord,
    OrderRecord,
    OrderStatusEventRecord,
    RiskEventRecord,
    TradeEventRecord,
    deterministic_id,
    utc_iso,
)


def _value(obj: Any, name: str, fallback: Any = None) -> Any:
    if obj is None:
        return fallback
    if isinstance(obj, Mapping):
        return obj.get(name, fallback)
    return getattr(obj, name, fallback)


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _integer(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    # ibapi uses near-max-double sentinels for unset prices and commissions.
    return result if math.isfinite(result) and abs(result) < 1e100 else None


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) and abs(value) < 1e100 else None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_value(item) for item in value]
    if hasattr(value, "item"):
        try:
            return _json_value(value.item())
        except (TypeError, ValueError):
            pass
    return str(value)


@dataclass(frozen=True, slots=True)
class ExecutionContext:
    run_id: str
    signal_key: str | None
    trade_id: str | None
    decision_id: str | None
    account: str
    client_id: int
    observed_at_utc: str | datetime

    def __post_init__(self) -> None:
        if not self.run_id:
            raise ValueError("run_id is required")
        if not self.account:
            raise ValueError("account is required")
        utc_iso(self.observed_at_utc)


class ExecutionJournalStore(Protocol):
    """The subset of :class:`JournalStore` used by the TWS adapter."""

    def upsert_order(
        self,
        record: OrderRecord,
        legs: Sequence[OrderLegRecord] | None = None,
    ) -> None: ...

    def add_order_status(self, record: OrderStatusEventRecord) -> None: ...

    def upsert_execution(self, record: ExecutionRecord) -> None: ...

    def upsert_commission(self, record: CommissionRecord) -> None: ...

    def refresh_order_filled_from_executions(self, order_id: str) -> str: ...

    def add_event(self, record: TradeEventRecord) -> None: ...

    def add_risk_event(self, record: RiskEventRecord) -> None: ...

    def find_order_binding(
        self,
        *,
        run_id: str,
        broker: str,
        account: str,
        perm_id: int | None,
        client_id: int | None,
        client_order_id: int | None,
    ) -> OrderBinding | None: ...

    def transaction(self) -> ContextManager[None]: ...


def _atomic_store_write(method: Any) -> Any:
    @wraps(method)
    def wrapped(self: "ExecutionJournalIngestor", *args: Any, **kwargs: Any) -> Any:
        with self.store.transaction():
            return method(self, *args, **kwargs)

    return wrapped


@dataclass(frozen=True, slots=True)
class IngestCounts:
    orders: int = 0
    order_legs: int = 0
    statuses: int = 0
    executions: int = 0
    commissions: int = 0
    terminal_events: int = 0

    def __add__(self, other: "IngestCounts") -> "IngestCounts":
        return IngestCounts(
            orders=self.orders + other.orders,
            order_legs=self.order_legs + other.order_legs,
            statuses=self.statuses + other.statuses,
            executions=self.executions + other.executions,
            commissions=self.commissions + other.commissions,
            terminal_events=self.terminal_events + other.terminal_events,
        )


@dataclass(frozen=True, slots=True)
class _BoundOrderView:
    """Minimal order data needed when TWS returns a historical fill only."""

    order_id: str
    trade_id: str | None
    order_ref: str | None


class ExecutionJournalIngestor:
    """Convert ib_insync-like Trade objects into normalized journal records.

    Production ``ib_insync`` objects and small test fakes use the same attribute-only
    mapping. Stable IDs make replaying an unchanged snapshot idempotent at the store.
    No broker calls are made here.
    """

    def __init__(self, store: ExecutionJournalStore) -> None:
        self.store = store
        self._known_order_bindings: dict[tuple[str, int, int], OrderBinding] = {}

    @_atomic_store_write
    def ingest_bracket_submission(
        self,
        submission: Any,
        context: ExecutionContext,
    ) -> IngestCounts:
        roles = ("parent", "take_profit", "stop_loss")
        trades = tuple(_value(submission, "trades", ()))
        if len(trades) != 3:
            raise ValueError("bracket submission must contain exactly three trades")

        parent_keys: dict[int, str] = {}
        for trade in trades:
            order = _value(trade, "order")
            if order is None:
                raise ValueError("trade.order is required")
            order_id = _integer(_value(order, "orderId"))
            parent_keys[order_id] = self._order_binding(order, context).order_id

        result = IngestCounts()
        for role, trade in zip(roles, trades, strict=True):
            result += self.ingest_trade(
                trade,
                context,
                role=role,
                parent_order_ids=parent_keys,
            )
        return result

    @_atomic_store_write
    def ingest_trade(
        self,
        trade: Any,
        context: ExecutionContext,
        *,
        role: str,
        parent_order_ids: Mapping[int, str] | None = None,
    ) -> IngestCounts:
        order = _value(trade, "order")
        contract = _value(trade, "contract")
        if order is None:
            raise ValueError("trade.order is required")
        order_record = self._order_record(
            trade,
            order,
            contract,
            context,
            role,
            parent_order_ids or {},
        )
        legs = self._order_legs(order_record.order_id, contract)
        self.store.upsert_order(order_record, legs)

        statuses = self._status_records(trade, order_record, context)
        terminal_events = 0
        for status, kind in statuses:
            self.store.add_order_status(status)
            if kind in {"cancelled", "rejected"}:
                self.store.add_event(
                    TradeEventRecord(
                        event_id=deterministic_id(
                            "event", order_record.order_id, status.event_id, kind
                        ),
                        run_id=order_record.run_id,
                        trade_id=order_record.trade_id,
                        order_id=order_record.order_id,
                        signal_key=order_record.signal_key,
                        event_type=f"order_{kind}",
                        occurred_at_utc=status.occurred_at_utc,
                        payload={
                            "status": status.status,
                            "broker_message": status.broker_message,
                            "status_event_id": status.event_id,
                        },
                    )
                )
                terminal_events += 1

        execution_count = 0
        commission_count = 0
        for fill in tuple(_value(trade, "fills", ()) or ()):
            execution = self._execution_record(fill, order_record, context)
            self.store.upsert_execution(execution)
            execution_count += 1
            commission = self._commission_record(fill, execution, context)
            if commission is not None:
                self.store.upsert_commission(commission)
                commission_count += 1

        refresh_trade = getattr(
            self.store, "refresh_trade_open_from_executions", None
        )
        if (
            order_record.trade_id is not None
            and role == "parent"
            and callable(refresh_trade)
        ):
            refresh_trade(order_record.trade_id)

        return IngestCounts(
            orders=1,
            order_legs=len(legs),
            statuses=len(statuses),
            executions=execution_count,
            commissions=commission_count,
            terminal_events=terminal_events,
        )

    @_atomic_store_write
    def ingest_historical_fill(
        self,
        fill: Any,
        context: ExecutionContext,
        *,
        binding: OrderBinding | None = None,
    ) -> IngestCounts:
        """Journal a fill returned by ``reqExecutions`` without a Trade snapshot.

        Historical executions must already match a known journal order.  Unknown
        broker fills are deliberately left unbound instead of fabricating a trade.
        """
        execution = _value(fill, "execution")
        if execution is None:
            raise ValueError("fill.execution is required")
        actual_account = _text(_value(execution, "acctNumber")) or context.account
        actual_client_id = _integer(_value(execution, "clientId"))
        if binding is None:
            binding = self.store.find_order_binding(
                run_id=context.run_id,
                broker="IBKR",
                account=actual_account,
                perm_id=_integer(_value(execution, "permId")) or None,
                client_id=(actual_client_id if actual_client_id > 0 else context.client_id),
                client_order_id=_integer(_value(execution, "orderId")) or None,
            )
        if binding is None:
            return IngestCounts()

        actual_context = ExecutionContext(
            run_id=context.run_id,
            signal_key=binding.signal_key,
            trade_id=binding.trade_id,
            decision_id=context.decision_id,
            account=actual_account,
            client_id=(actual_client_id if actual_client_id > 0 else context.client_id),
            observed_at_utc=context.observed_at_utc,
        )
        order = _BoundOrderView(
            order_id=binding.order_id,
            trade_id=binding.trade_id,
            order_ref=_text(_value(execution, "orderRef")) or None,
        )
        record = self._execution_record(fill, order, actual_context)
        self.store.upsert_execution(record)
        commission = self._commission_record(fill, record, actual_context)
        if commission is not None:
            self.store.upsert_commission(commission)
        refresh_order = getattr(
            self.store, "refresh_order_filled_from_executions", None
        )
        if callable(refresh_order):
            refresh_order(binding.order_id)
        return IngestCounts(executions=1, commissions=int(commission is not None))

    def record_blocker(
        self,
        context: ExecutionContext,
        *,
        status: str,
        reason_code: str,
        reason: str,
        requested_quantity: float | None,
        occurred_at: str | datetime,
        details: Mapping[str, Any],
    ) -> RiskEventRecord:
        allowed = {
            "blocked_risk",
            "duplicate_exposure",
            "execution_blocked",
            "execution_rejected",
        }
        if status not in allowed:
            raise ValueError(f"unsupported execution decision status: {status}")
        if not context.signal_key:
            raise ValueError("signal_key is required for an execution blocker")
        occurred_at_utc = utc_iso(occurred_at)
        payload = {
            "reason_code": reason_code,
            "reason": reason,
            "requested_quantity": _number(requested_quantity),
            "account": context.account,
            "client_id": context.client_id,
            "details": _json_value(details),
        }
        record = RiskEventRecord(
            risk_event_id=deterministic_id(
                "risk-event",
                context.run_id,
                context.signal_key,
                status,
                reason_code,
                occurred_at_utc,
            ),
            run_id=context.run_id,
            trade_id=context.trade_id,
            decision_id=context.decision_id,
            signal_key=context.signal_key,
            event_type=reason_code,
            status=status,
            occurred_at_utc=occurred_at_utc,
            risk_amount=_number(details.get("risk_amount")),
            risk_currency=_text(details.get("risk_currency")) or None,
            payload=payload,
        )
        self.store.add_risk_event(record)
        return record

    @staticmethod
    def _actual_account(order: Any, context: ExecutionContext) -> str:
        return _text(_value(order, "account")) or context.account

    @staticmethod
    def _actual_client_id(order: Any, context: ExecutionContext) -> int:
        value = _value(order, "clientId")
        return _integer(value) if value is not None else context.client_id

    def _order_binding(self, order: Any, context: ExecutionContext) -> OrderBinding:
        account = self._actual_account(order, context)
        order_id = _integer(_value(order, "orderId"))
        client_id = self._actual_client_id(order, context)
        perm_id = _integer(_value(order, "permId"))
        cache_key = (account, client_id, order_id)
        cached = self._known_order_bindings.get(cache_key)
        if cached is not None:
            return cached
        resolved = self.store.find_order_binding(
            run_id=context.run_id,
            broker="IBKR",
            account=account,
            perm_id=perm_id if perm_id > 0 else None,
            client_id=client_id,
            client_order_id=order_id if order_id > 0 else None,
        )
        if resolved is not None:
            if order_id > 0:
                self._known_order_bindings[cache_key] = resolved
            return resolved
        if order_id > 0:
            journal_id = deterministic_id(
                "order", "IBKR", account, "client", client_id, "order", order_id
            )
            binding = OrderBinding(
                order_id=journal_id,
                run_id=context.run_id,
                trade_id=context.trade_id,
                signal_key=context.signal_key,
            )
            self._known_order_bindings[cache_key] = binding
            return binding
        if perm_id <= 0:
            raise ValueError("positive orderId or permId is required for journal ingestion")
        return OrderBinding(
            order_id=deterministic_id("order", "IBKR", account, "perm", perm_id),
            run_id=context.run_id,
            trade_id=context.trade_id,
            signal_key=context.signal_key,
        )

    def _journal_order_id(self, order: Any, context: ExecutionContext) -> str:
        return self._order_binding(order, context).order_id

    def _order_record(
        self,
        trade: Any,
        order: Any,
        contract: Any,
        context: ExecutionContext,
        role: str,
        parent_order_ids: Mapping[int, str],
    ) -> OrderRecord:
        order_id = _integer(_value(order, "orderId"))
        perm_id = _integer(_value(order, "permId"))
        parent_id = _integer(_value(order, "parentId"))
        status_obj = _value(trade, "orderStatus")
        quantity = _number(_value(order, "totalQuantity"))
        if quantity is None:
            raise ValueError("finite order totalQuantity is required")
        parent_key = None
        if parent_id > 0:
            cache_key = (
                self._actual_account(order, context),
                self._actual_client_id(order, context),
                parent_id,
            )
            cached_parent = self._known_order_bindings.get(cache_key)
            parent_key = parent_order_ids.get(parent_id) or (
                cached_parent.order_id if cached_parent else None
            )
        binding = self._order_binding(order, context)
        metadata = {
            "role": role,
            "account": self._actual_account(order, context),
            "client_id": self._actual_client_id(order, context),
            "order_id": order_id,
            "perm_id": perm_id,
            "parent_broker_order_id": parent_id,
            "outside_rth": bool(_value(order, "outsideRth", False)),
            "oca_group": _text(_value(order, "ocaGroup")),
            "oca_type": _integer(_value(order, "ocaType")),
            "contract": self._contract(contract),
        }
        return OrderRecord(
            order_id=binding.order_id,
            run_id=binding.run_id,
            trade_id=binding.trade_id,
            signal_key=binding.signal_key,
            parent_order_id=parent_key,
            broker="IBKR",
            broker_order_id=(
                f"permId:{perm_id}" if perm_id > 0 else f"clientId:{self._actual_client_id(order, context)}:orderId:{order_id}"
            ),
            order_ref=_text(_value(order, "orderRef")) or None,
            order_type=_text(_value(order, "orderType")),
            action=_text(_value(order, "action")),
            quantity=quantity,
            limit_price=_number(_value(order, "lmtPrice")),
            stop_price=_number(_value(order, "auxPrice")),
            time_in_force=_text(_value(order, "tif")),
            transmit=bool(_value(order, "transmit", False)),
            status=_text(_value(status_obj, "status")) or "PendingAck",
            submitted_at_utc=self._submitted_at(trade),
            updated_at_utc=context.observed_at_utc,
            metadata=metadata,
        )

    @staticmethod
    def _contract(contract: Any) -> dict[str, Any]:
        expiry = _text(_value(contract, "lastTradeDateOrContractMonth")) or None
        return {
            "conId": _integer(_value(contract, "conId")) or None,
            "secType": _text(_value(contract, "secType")),
            "symbol": _text(_value(contract, "symbol")),
            "localSymbol": _text(_value(contract, "localSymbol")),
            "expiry": expiry,
            "lastTradeDateOrContractMonth": expiry,
            "strike": _number(_value(contract, "strike")),
            "right": _text(_value(contract, "right")) or None,
            "multiplier": _text(_value(contract, "multiplier")) or None,
            "currency": _text(_value(contract, "currency")),
            "exchange": _text(_value(contract, "exchange")),
            "primaryExchange": _text(_value(contract, "primaryExchange")) or None,
            "tradingClass": _text(_value(contract, "tradingClass")) or None,
        }

    def _order_legs(
        self,
        journal_order_id: str,
        contract: Any,
    ) -> tuple[OrderLegRecord, ...]:
        result = []
        contract_symbol = _text(_value(contract, "symbol"))
        for index, leg in enumerate(tuple(_value(contract, "comboLegs", ()) or ())):
            con_id = _integer(_value(leg, "conId")) or None
            result.append(
                OrderLegRecord(
                    order_leg_id=deterministic_id(
                        "order-leg", journal_order_id, index
                    ),
                    order_id=journal_order_id,
                    leg_index=index,
                    trade_leg_id=None,
                    con_id=con_id,
                    symbol=contract_symbol,
                    action=_text(_value(leg, "action")),
                    ratio=_integer(_value(leg, "ratio")),
                    exchange=_text(_value(leg, "exchange")) or None,
                    open_close=(
                        str(_integer(_value(leg, "openClose")))
                        if _value(leg, "openClose") is not None
                        else None
                    ),
                    metadata={
                        "short_sale_slot": _integer(_value(leg, "shortSaleSlot")),
                        "designated_location": _text(_value(leg, "designatedLocation")),
                        "exempt_code": _integer(_value(leg, "exemptCode")),
                    },
                )
            )
        return tuple(result)

    def _status_records(
        self,
        trade: Any,
        order: OrderRecord,
        context: ExecutionContext,
    ) -> tuple[tuple[OrderStatusEventRecord, str], ...]:
        result = []
        for entry in tuple(_value(trade, "log", ()) or ()):
            result.append(
                self._status_record(
                    order,
                    source="trade_log",
                    occurred_at=_value(entry, "time") or context.observed_at_utc,
                    status=_text(_value(entry, "status")) or "PendingAck",
                    status_obj=None,
                    error_code=_integer(_value(entry, "errorCode")),
                    message=_text(_value(entry, "message")),
                )
            )
        status_obj = _value(trade, "orderStatus")
        if status_obj is not None:
            result.append(
                self._status_record(
                    order,
                    source="snapshot",
                    occurred_at=context.observed_at_utc,
                    status=_text(_value(status_obj, "status")) or "PendingAck",
                    status_obj=status_obj,
                    error_code=0,
                    message=_text(_value(trade, "advancedError")),
                )
            )
        return tuple(result)

    def _status_record(
        self,
        order: OrderRecord,
        *,
        source: str,
        occurred_at: str | datetime,
        status: str,
        status_obj: Any,
        error_code: int,
        message: str,
    ) -> tuple[OrderStatusEventRecord, str]:
        occurred_at_utc = utc_iso(occurred_at)
        filled = _number(_value(status_obj, "filled"))
        remaining = _number(_value(status_obj, "remaining"))
        avg_price = _number(_value(status_obj, "avgFillPrice"))
        last_price = _number(_value(status_obj, "lastFillPrice"))
        identity = (
            order.order_id,
            source,
            status,
            filled,
            remaining,
            avg_price,
            last_price,
            error_code,
            message,
        )
        if source == "trade_log":
            identity = (*identity, occurred_at_utc)
        identity = tuple("<empty>" if value == "" else value for value in identity)
        record = OrderStatusEventRecord(
            event_id=deterministic_id("order-status", *identity),
            order_id=order.order_id,
            status=status,
            occurred_at_utc=occurred_at_utc,
            filled_quantity=filled,
            remaining_quantity=remaining,
            average_fill_price=avg_price,
            broker_message=message or None,
            payload={
                "source": source,
                "last_fill_price": last_price,
                "why_held": _text(_value(status_obj, "whyHeld")),
                "mkt_cap_price": _number(_value(status_obj, "mktCapPrice")),
                "error_code": error_code,
            },
        )
        return record, self._status_kind(status, error_code, message)

    def _execution_record(
        self,
        fill: Any,
        order: OrderRecord | _BoundOrderView,
        context: ExecutionContext,
    ) -> ExecutionRecord:
        execution = _value(fill, "execution")
        if execution is None:
            raise ValueError("fill.execution is required")
        execution_time = _value(execution, "time") or _value(fill, "time") or context.observed_at_utc
        executed_at_utc = utc_iso(execution_time)
        quantity = _number(_value(execution, "shares"))
        price = _number(_value(execution, "price"))
        if quantity is None or price is None:
            raise ValueError("finite execution shares and price are required")
        broker_execution_id = _text(_value(execution, "execId"))
        if not broker_execution_id:
            broker_execution_id = deterministic_id(
                "ib-exec-fallback",
                order.order_id,
                executed_at_utc,
                _text(_value(execution, "side")),
                quantity,
                price,
                _number(_value(execution, "cumQty")),
            )
        execution_id = deterministic_id(
            "execution", "IBKR", context.account, broker_execution_id
        )
        return ExecutionRecord(
            execution_id=execution_id,
            order_id=order.order_id,
            trade_id=order.trade_id,
            broker_execution_id=broker_execution_id,
            executed_at_utc=executed_at_utc,
            side=_text(_value(execution, "side")),
            quantity=quantity,
            price=price,
            exchange=_text(_value(execution, "exchange")) or None,
            liquidity=_text(_value(execution, "lastLiquidity")) or None,
            metadata={
                "account": _text(_value(execution, "acctNumber")) or context.account,
                "client_id": _integer(_value(execution, "clientId")),
                "broker_order_id": _integer(_value(execution, "orderId")),
                "perm_id": _integer(_value(execution, "permId")),
                "cum_quantity": _number(_value(execution, "cumQty")),
                "average_price": _number(_value(execution, "avgPrice")),
                "liquidation": _integer(_value(execution, "liquidation")),
                "order_ref": _text(_value(execution, "orderRef")) or order.order_ref,
                "contract": self._contract(_value(fill, "contract")),
            },
        )

    def _commission_record(
        self,
        fill: Any,
        execution: ExecutionRecord,
        context: ExecutionContext,
    ) -> CommissionRecord | None:
        report = _value(fill, "commissionReport")
        if report is None:
            return None
        amount = _number(_value(report, "commission"))
        if amount is None:
            return None
        return CommissionRecord(
            commission_id=deterministic_id("commission", execution.execution_id),
            execution_id=execution.execution_id,
            currency=(
                _text(_value(report, "currency"))
                or _text(_value(_value(fill, "contract"), "currency"))
                or "UNKNOWN"
            ),
            amount=amount,
            realized_pnl=_number(_value(report, "realizedPNL")),
            reported_at_utc=context.observed_at_utc,
            metadata={
                "broker_execution_id": _text(_value(report, "execId"))
                or execution.broker_execution_id,
                "yield": _number(_value(report, "yield_")),
                "yield_redemption_date": _integer(_value(report, "yieldRedemptionDate")),
            },
        )

    @staticmethod
    def _submitted_at(trade: Any) -> str | datetime | None:
        times = [
            _value(entry, "time")
            for entry in tuple(_value(trade, "log", ()) or ())
            if _value(entry, "time") not in {None, ""}
        ]
        if not times:
            return None
        normalized = sorted(utc_iso(value) for value in times)
        return normalized[0]

    @staticmethod
    def _status_kind(status: str, error_code: int, message: str) -> str:
        normalized = status.replace("_", "").replace(" ", "").lower()
        message_lower = message.lower()
        if normalized in {"cancelled", "canceled", "apicancelled"} or "cancel" in message_lower:
            return "cancelled"
        if "reject" in normalized or "reject" in message_lower:
            return "rejected"
        if normalized == "inactive" and error_code:
            return "rejected"
        if error_code:
            return "broker_error"
        if normalized == "filled":
            return "filled"
        if normalized == "inactive":
            return "inactive"
        return "status"
