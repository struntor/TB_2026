from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import math
from pathlib import Path
from typing import Any, Callable, Iterable

from ib_insync import ExecutionFilter, IB

from .execution_ingest import ExecutionContext, ExecutionJournalIngestor, IngestCounts
from .models import OrderBinding, StrategyRunRecord, deterministic_id
from .store import DEFAULT_JOURNAL_PATH, JournalStore


SYNC_STRATEGY_ID = "ibkr-broker-sync"
SYNC_STRATEGY_VERSION = "ibkr-read-only-sync-v2"


@dataclass(frozen=True, slots=True)
class SyncResult:
    run_id: str
    unique_trades: int
    skipped_zero_quantity_orders: int
    refreshed_trade_lifecycles: int
    counts: IngestCounts


def _value(obj: Any, name: str, fallback: Any = None) -> Any:
    return getattr(obj, name, fallback) if obj is not None else fallback


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
    return result if math.isfinite(result) and abs(result) < 1e100 else None


def _has_zero_quantity(trade: Any) -> bool:
    quantity = _number(_value(_value(trade, "order"), "totalQuantity"))
    return quantity is not None and quantity == 0.0


def _account(trade: Any, fallback: str) -> str:
    return str(_value(_value(trade, "order"), "account", "") or fallback)


def _client_id(trade: Any, fallback: int) -> int:
    value = _value(_value(trade, "order"), "clientId")
    return _integer(value) if value is not None else fallback


def _trade_identity(trade: Any, account: str, fallback_client_id: int) -> tuple[Any, ...]:
    order = _value(trade, "order")
    perm_id = _integer(_value(order, "permId"))
    if perm_id > 0:
        return "perm", _account(trade, account), perm_id
    order_id = _integer(_value(order, "orderId"))
    if order_id > 0:
        return "order", _account(trade, account), _client_id(trade, fallback_client_id), order_id
    return "object", id(trade)


def _role(trade: Any) -> str:
    order = _value(trade, "order")
    if _integer(_value(order, "parentId")) <= 0:
        return "parent"
    order_type = str(_value(order, "orderType", "") or "").upper()
    if order_type in {"STP", "STP LMT", "TRAIL", "TRAIL LIMIT"}:
        return "stop_loss"
    if order_type == "LMT":
        return "take_profit"
    return "child"


def _deduplicate_trades(
    collections: Iterable[Iterable[Any]],
    *,
    account: str,
    client_id: int,
) -> list[Any]:
    unique: dict[tuple[Any, ...], Any] = {}
    for collection in collections:
        for trade in collection or ():
            trade_account = _account(trade, account)
            if trade_account and trade_account != account:
                continue
            unique[_trade_identity(trade, account, client_id)] = trade
    return sorted(
        unique.values(),
        key=lambda trade: (
            _integer(_value(_value(trade, "order"), "parentId")) > 0,
            _integer(_value(_value(trade, "order"), "orderId")),
        ),
    )


def _binding_for(
    store: JournalStore,
    trade: Any,
    *,
    sync_run_id: str,
    account: str,
    client_id: int,
) -> OrderBinding | None:
    order = _value(trade, "order")
    return store.find_order_binding(
        run_id=sync_run_id,
        broker="IBKR",
        account=_account(trade, account),
        perm_id=_integer(_value(order, "permId")) or None,
        client_id=_client_id(trade, client_id),
        client_order_id=_integer(_value(order, "orderId")) or None,
    )


def _fill_binding(
    store: JournalStore,
    fill: Any,
    *,
    sync_run_id: str,
    account: str,
    client_id: int,
) -> OrderBinding | None:
    execution = _value(fill, "execution")
    actual_account = str(_value(execution, "acctNumber", "") or account)
    actual_client = _integer(_value(execution, "clientId")) or client_id
    return store.find_order_binding(
        run_id=sync_run_id,
        broker="IBKR",
        account=actual_account,
        perm_id=_integer(_value(execution, "permId")) or None,
        client_id=actual_client,
        client_order_id=_integer(_value(execution, "orderId")) or None,
    )


def _fill_identity(fill: Any) -> tuple[Any, ...]:
    execution = _value(fill, "execution")
    exec_id = str(_value(execution, "execId", "") or "")
    if exec_id:
        return "exec", exec_id
    return "object", id(fill)


def _new_order_id(trade: Any, *, account: str, client_id: int) -> str | None:
    order = _value(trade, "order")
    order_id = _integer(_value(order, "orderId"))
    actual_account = _account(trade, account)
    if order_id > 0:
        return deterministic_id(
            "order", "IBKR", actual_account, "client",
            _client_id(trade, client_id), "order", order_id,
        )
    perm_id = _integer(_value(order, "permId"))
    if perm_id > 0:
        return deterministic_id("order", "IBKR", actual_account, "perm", perm_id)
    return None


def _parent_ids(
    store: JournalStore,
    trades: list[Any],
    bindings: dict[int, OrderBinding | None],
    *,
    sync_run_id: str,
    account: str,
    client_id: int,
) -> dict[int, dict[int, str]]:
    current: dict[tuple[str, int, int], str] = {}
    for trade in trades:
        order = _value(trade, "order")
        order_id = _integer(_value(order, "orderId"))
        if order_id <= 0:
            continue
        binding = bindings[id(trade)]
        journal_id = binding.order_id if binding else _new_order_id(
            trade, account=account, client_id=client_id
        )
        if journal_id:
            current[(_account(trade, account), _client_id(trade, client_id), order_id)] = journal_id

    result: dict[int, dict[int, str]] = {}
    for trade in trades:
        order = _value(trade, "order")
        parent_id = _integer(_value(order, "parentId"))
        if parent_id <= 0:
            continue
        actual_account = _account(trade, account)
        actual_client = _client_id(trade, client_id)
        parent = current.get((actual_account, actual_client, parent_id))
        if parent is None:
            child_binding = bindings[id(trade)]
            parent_binding = store.find_order_binding(
                run_id=(child_binding.run_id if child_binding else sync_run_id),
                broker="IBKR",
                account=actual_account,
                perm_id=None,
                client_id=actual_client,
                client_order_id=parent_id,
            )
            parent = parent_binding.order_id if parent_binding else None
        if parent is not None:
            result[id(trade)] = {parent_id: parent}
    return result


def sync_connected_ib(
    ib: Any,
    store: JournalStore,
    *,
    account: str,
    client_id: int,
    run_mode: str,
    sync_day: date,
    commission_wait_seconds: float,
    execution_start_day: date | None = None,
    now_utc: Callable[[], datetime] | None = None,
) -> SyncResult:
    """Read broker state from an already connected IB and journal it.

    This function never places, modifies, or cancels an order.
    """
    if run_mode not in {"paper", "live"}:
        raise ValueError("run_mode must be paper or live")
    if not account:
        raise ValueError("account is required")
    if commission_wait_seconds < 0:
        raise ValueError("commission_wait_seconds must be non-negative")
    execution_start = execution_start_day or (sync_day - timedelta(days=1))
    if execution_start > sync_day:
        raise ValueError("execution_start_day must not be after sync_day")
    clock = now_utc or (lambda: datetime.now(timezone.utc))
    run_id = deterministic_id(
        "run",
        SYNC_STRATEGY_ID,
        sync_day.isoformat(),
        run_mode,
        account,
        client_id,
        execution_start.isoformat(),
    )
    existing_run = store.get_run(run_id)
    started = (
        str(existing_run["started_at_utc"])
        if existing_run is not None
        else clock()
    )
    base_run = dict(
        run_id=run_id,
        strategy_id=SYNC_STRATEGY_ID,
        strategy_version=SYNC_STRATEGY_VERSION,
        config_hash=deterministic_id(
            "config", run_mode, account, client_id, execution_start.isoformat()
        ),
        run_type=run_mode,
        environment=run_mode,
        started_at_utc=started,
        parameters={
            "account": account,
            "client_id": client_id,
            "commission_wait_seconds": commission_wait_seconds,
            "execution_start_day": execution_start.isoformat(),
        },
        metadata={"source": "TWS", "read_only": True},
    )
    store.start_run(StrategyRunRecord(**base_run, ended_at_utc=None, status="running"))
    try:
        open_trades = ib.reqAllOpenOrders() or []
        completed_trades = ib.reqCompletedOrders(apiOnly=False) or []
        execution_filter = ExecutionFilter(
            time=f"{execution_start:%Y%m%d} 00:00:00",
            acctCode=account,
        )
        try:
            execution_fills = ib.reqExecutions(execution_filter) or []
        except TypeError:
            # Keep small IB test doubles and older compatible adapters usable.
            execution_fills = ib.reqExecutions() or []
        ib.sleep(commission_wait_seconds)
        observed_trades = _deduplicate_trades(
            (open_trades, completed_trades, ib.trades() or []),
            account=account,
            client_id=client_id,
        )
        skipped_zero_quantity_orders = sum(
            _has_zero_quantity(trade) for trade in observed_trades
        )
        trades = [
            trade for trade in observed_trades if not _has_zero_quantity(trade)
        ]
        bindings = {
            id(trade): _binding_for(
                store,
                trade,
                sync_run_id=run_id,
                account=account,
                client_id=client_id,
            )
            for trade in trades
        }
        parent_ids = _parent_ids(
            store,
            trades,
            bindings,
            sync_run_id=run_id,
            account=account,
            client_id=client_id,
        )
        ingestor = ExecutionJournalIngestor(store)
        counts = IngestCounts()
        observed_at = clock()
        for trade in trades:
            context = ExecutionContext(
                run_id=run_id,
                signal_key=None,
                trade_id=None,
                decision_id=None,
                account=account,
                client_id=client_id,
                observed_at_utc=observed_at,
            )
            # ExecutionJournalIngestor resolves a known permId back to the original
            # run/trade/signal. The sync context is used only for genuinely unknown orders.
            counts += ingestor.ingest_trade(
                trade,
                context,
                role=_role(trade),
                parent_order_ids=parent_ids.get(id(trade), {}),
            )
        attached_fill_ids = {
            _fill_identity(fill)
            for trade in trades
            for fill in tuple(_value(trade, "fills", ()) or ())
        }
        historical_bindings: list[OrderBinding] = []
        unbound_execution_fills = 0
        for fill in execution_fills:
            if _fill_identity(fill) in attached_fill_ids:
                continue
            binding = _fill_binding(
                store,
                fill,
                sync_run_id=run_id,
                account=account,
                client_id=client_id,
            )
            if binding is None:
                unbound_execution_fills += 1
                continue
            historical_bindings.append(binding)
            counts += ingestor.ingest_historical_fill(
                fill,
                ExecutionContext(
                    run_id=run_id,
                    signal_key=binding.signal_key,
                    trade_id=binding.trade_id,
                    decision_id=None,
                    account=account,
                    client_id=client_id,
                    observed_at_utc=observed_at,
                ),
                binding=binding,
            )
        bound_trade_ids = sorted(
            {
                binding.trade_id
                for binding in (*bindings.values(), *historical_bindings)
                if binding is not None and binding.trade_id is not None
            }
        )
        refreshed_trade_lifecycles = sum(
            store.refresh_trade_lifecycle_from_executions(trade_id) in {"open", "closed"}
            for trade_id in bound_trade_ids
        )
        store.start_run(
            StrategyRunRecord(
                **{
                    **base_run,
                    "metadata": {
                        **base_run["metadata"],
                        "observed_unique_orders": len(observed_trades),
                        "ingested_unique_orders": len(trades),
                        "skipped_zero_quantity_orders": skipped_zero_quantity_orders,
                        "refreshed_trade_lifecycles": refreshed_trade_lifecycles,
                        "observed_execution_fills": len(execution_fills),
                        "unbound_execution_fills": unbound_execution_fills,
                        "execution_start_day": execution_start.isoformat(),
                    },
                },
                ended_at_utc=clock(),
                status="completed",
            )
        )
        return SyncResult(
            run_id=run_id,
            unique_trades=len(trades),
            skipped_zero_quantity_orders=skipped_zero_quantity_orders,
            refreshed_trade_lifecycles=refreshed_trade_lifecycles,
            counts=counts,
        )
    except BaseException as exc:
        store.start_run(
            StrategyRunRecord(
                **{
                    **base_run,
                    "metadata": {
                        **base_run["metadata"],
                        "error": f"{type(exc).__name__}: {exc}",
                    },
                },
                ended_at_utc=clock(),
                status="failed",
            )
        )
        raise


def resolve_account(ib: Any, requested_account: str | None) -> str:
    accounts = list(ib.managedAccounts())
    if requested_account:
        if requested_account not in accounts:
            raise RuntimeError("requested account is not managed by this TWS session")
        return requested_account
    if len(accounts) != 1:
        raise RuntimeError("--account is required when TWS exposes zero or multiple accounts")
    return str(accounts[0])


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only TWS to SQLite journal sync")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7497)
    parser.add_argument("--client-id", type=int, default=63)
    parser.add_argument("--account")
    parser.add_argument("--journal-path", default=str(DEFAULT_JOURNAL_PATH))
    parser.add_argument("--allow-live-account", action="store_true")
    parser.add_argument("--commission-wait-seconds", type=float, default=2.0)
    parser.add_argument("--execution-start-date")
    parser.add_argument("--sync-day")
    args = parser.parse_args()

    ib = IB()
    try:
        ib.connect(args.host, args.port, clientId=args.client_id, timeout=10)
        account = resolve_account(ib, args.account)
        if not args.allow_live_account and not account.upper().startswith("DU"):
            raise RuntimeError("paper-account guard rejected the connected account")
        run_mode = "paper" if account.upper().startswith("DU") else "live"
        with JournalStore(Path(args.journal_path)) as store:
            result = sync_connected_ib(
                ib,
                store,
                account=account,
                client_id=args.client_id,
                run_mode=run_mode,
                sync_day=(
                    date.fromisoformat(args.sync_day)
                    if args.sync_day
                    else datetime.now(timezone.utc).date()
                ),
                commission_wait_seconds=args.commission_wait_seconds,
                execution_start_day=(
                    date.fromisoformat(args.execution_start_date)
                    if args.execution_start_date
                    else None
                ),
            )
        print(
            f"run_id={result.run_id} unique_trades={result.unique_trades} "
            f"skipped_zero_quantity={result.skipped_zero_quantity_orders} "
            f"refreshed_trade_lifecycles={result.refreshed_trade_lifecycles} "
            f"orders={result.counts.orders} executions={result.counts.executions} "
            f"commissions={result.counts.commissions}"
        )
    finally:
        if ib.isConnected():
            ib.disconnect()


if __name__ == "__main__":
    main()
