from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import math
from typing import Any, Iterable, Mapping, Protocol, Sequence
from zoneinfo import ZoneInfo

from src.journal.decision_ingest import (
    DecisionIngestContext,
    DecisionJournalIngestor,
    map_decision_record,
)
from src.journal.models import (
    DecisionFactorRecord,
    ReconciliationRecord,
    SignalDecisionRecord,
    StrategyRunRecord,
    TradeLegRecord,
    TradeRecord,
    deterministic_id,
    utc_iso,
)


class BacktestJournalStore(Protocol):
    def transaction(self): ...

    def start_run(self, record: StrategyRunRecord) -> str: ...

    def upsert_decision(
        self,
        record: SignalDecisionRecord,
        factors: Sequence[DecisionFactorRecord] = (),
    ) -> str: ...

    def create_trade(
        self,
        record: TradeRecord,
        legs: Sequence[TradeLegRecord] = (),
    ) -> str: ...

    def get_trade_by_signal(
        self,
        run_id: str,
        signal_key: str,
    ) -> dict[str, object] | None: ...

    def upsert_reconciliation(self, record: ReconciliationRecord) -> str: ...


@dataclass(frozen=True, slots=True)
class BacktestTradeDefaults:
    """Values that are run-level facts rather than columns in every trade row."""

    symbol: str
    asset_class: str
    currency: str
    exchange: str | None
    naive_timestamp_timezone: str | None

    def __post_init__(self) -> None:
        for field, value in (
            ("symbol", self.symbol),
            ("asset_class", self.asset_class),
            ("currency", self.currency),
        ):
            if not value.strip():
                raise ValueError(f"{field} is required")
        if self.naive_timestamp_timezone is not None:
            ZoneInfo(self.naive_timestamp_timezone)


@dataclass(frozen=True, slots=True)
class BacktestRunSpec:
    run_id: str
    strategy_id: str
    strategy_version: str
    strategy_variant_id: str | None
    engine_version: str
    money_management_version: str
    config_hash: str
    environment: str
    started_at_utc: str | datetime
    parameters: Mapping[str, Any]
    metadata: Mapping[str, Any]
    trade_defaults: BacktestTradeDefaults

    def __post_init__(self) -> None:
        for field, value in (
            ("run_id", self.run_id),
            ("strategy_id", self.strategy_id),
            ("strategy_version", self.strategy_version),
            ("engine_version", self.engine_version),
            ("money_management_version", self.money_management_version),
            ("config_hash", self.config_hash),
            ("environment", self.environment),
        ):
            if not value.strip():
                raise ValueError(f"{field} is required")
        if self.strategy_variant_id is not None and not self.strategy_variant_id.strip():
            raise ValueError("strategy_variant_id must be non-empty when supplied")
        utc_iso(self.started_at_utc)


@dataclass(frozen=True, slots=True)
class BacktestIngestResult:
    run_id: str
    decision_count: int
    trade_count: int
    trade_ids: tuple[str, ...]


END_OF_DATA_EXIT_REASONS = frozenset(
    {"end_of_test", "end_of_test_mark", "end_of_data"}
)


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    if type(value).__name__ in {"NAType", "NaTType"}:
        return True
    if isinstance(value, float):
        return not math.isfinite(value)
    try:
        missing = value != value
    except (TypeError, ValueError):
        return False
    try:
        return bool(missing)
    except (TypeError, ValueError):
        return False


def _native(value: Any) -> Any:
    """Convert pandas/numpy scalar values without importing either package."""
    if _is_missing(value):
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    item = getattr(value, "item", None)
    if callable(item):
        try:
            converted = item()
        except (TypeError, ValueError):
            converted = value
        if converted is not value:
            return _native(converted)
    if isinstance(value, Mapping):
        return {str(key): _native(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_native(item) for item in value]
    return value


def _is_carry_forward_row(row: Mapping[str, Any]) -> bool:
    exit_reason = str(row.get("exit_reason", "")).strip().lower()
    marked_open = row.get("marked_open")
    if isinstance(marked_open, str):
        marked_open = marked_open.strip().lower() in {"1", "true", "yes", "open"}
    return exit_reason in END_OF_DATA_EXIT_REASONS or bool(marked_open)


def _rows(frame: Any) -> list[dict[str, Any]]:
    if frame is None:
        raise TypeError("trades frame is required")
    to_dict = getattr(frame, "to_dict", None)
    if callable(to_dict):
        try:
            records = to_dict(orient="records")
        except TypeError:
            records = to_dict("records")
    elif isinstance(frame, Iterable) and not isinstance(frame, (str, bytes, Mapping)):
        records = list(frame)
    else:
        raise TypeError("trades must be a DataFrame or an iterable of mappings")
    result: list[dict[str, Any]] = []
    for index, row in enumerate(records):
        if not isinstance(row, Mapping):
            raise TypeError(f"trade row {index} is not a mapping")
        result.append({str(key): _native(value) for key, value in row.items()})
    return result


def _first(row: Mapping[str, Any], names: Sequence[str]) -> tuple[str | None, Any]:
    for name in names:
        value = row.get(name)
        if not _is_missing(value):
            return name, value
    return None, None


def _required(row: Mapping[str, Any], names: Sequence[str], label: str) -> Any:
    _, value = _first(row, names)
    if value is None or (isinstance(value, str) and not value.strip()):
        raise ValueError(f"completed trade lacks {label}; tried columns {list(names)}")
    return value


def _finite_float(value: Any, field: str, *, positive: bool = False) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be numeric") from exc
    if not math.isfinite(result):
        raise ValueError(f"{field} must be finite")
    if positive and result <= 0:
        raise ValueError(f"{field} must be positive")
    return result


def _optional_float(row: Mapping[str, Any], names: Sequence[str], field: str) -> float | None:
    _, value = _first(row, names)
    return None if value is None else _finite_float(value, field)


def _timestamp(value: Any, naive_timezone: str | None, field: str) -> str:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, datetime.min.time())
    else:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError as exc:
            raise ValueError(f"invalid {field}: {value!r}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        if naive_timezone is None:
            raise ValueError(
                f"{field} is timezone-naive; set naive_timestamp_timezone explicitly"
            )
        parsed = parsed.replace(tzinfo=ZoneInfo(naive_timezone))
    return utc_iso(parsed)


class BacktestJournalIngestor:
    """Persist one completed backtest without depending on a strategy package."""

    def __init__(self, store: BacktestJournalStore) -> None:
        self.store = store
        self.decisions = DecisionJournalIngestor(store)

    def _run_record(
        self,
        spec: BacktestRunSpec,
        *,
        status: str,
        ended_at_utc: str | datetime | None,
        metadata: Mapping[str, Any] | None = None,
    ) -> StrategyRunRecord:
        return StrategyRunRecord(
            run_id=spec.run_id,
            strategy_id=spec.strategy_id,
            strategy_version=spec.strategy_version,
            config_hash=spec.config_hash,
            run_type="backtest",
            environment=spec.environment,
            started_at_utc=spec.started_at_utc,
            ended_at_utc=ended_at_utc,
            status=status,
            parameters=_native(spec.parameters),
            metadata={
                **_native(spec.metadata),
                "engine_version": spec.engine_version,
                "money_management_version": spec.money_management_version,
                "strategy_variant_id": spec.strategy_variant_id,
                **_native(metadata or {}),
            },
            variant_id=spec.strategy_variant_id,
        )

    def start_run(self, spec: BacktestRunSpec) -> str:
        return self.store.start_run(self._run_record(spec, status="running", ended_at_utc=None))

    def finalize_run(
        self,
        spec: BacktestRunSpec,
        *,
        status: str,
        ended_at_utc: str | datetime,
        metadata: Mapping[str, Any] | None = None,
    ) -> str:
        if status not in {"completed", "failed"}:
            raise ValueError("final backtest status must be 'completed' or 'failed'")
        return self.store.start_run(
            self._run_record(
                spec,
                status=status,
                ended_at_utc=ended_at_utc,
                metadata=metadata,
            )
        )

    def ingest_run(
        self,
        spec: BacktestRunSpec,
        decision_records: Iterable[Any],
        completed_trades: Any,
        *,
        completed_at_utc: str | datetime,
    ) -> BacktestIngestResult:
        """Ingest decisions and trades atomically, then mark the run completed.

        ``completed_trades`` should normally be the result of the engine's
        ``attach_record_keys_to_trades`` helper. Matching here is exclusively
        by the attached ``signal_key`` and never by row position. Engine rows
        marked ``end_of_test`` are snapshots of still-open positions; they are
        persisted with ``status=open`` and never counted as realized exits.
        """
        utc_iso(completed_at_utc)
        self.start_run(spec)
        try:
            sources = list(decision_records)
            rows = _rows(completed_trades)
            context = DecisionIngestContext(spec.run_id, "backtest")
            accepted: dict[str, Any] = {}
            decision_keys: set[str] = set()
            for source in sources:
                decision, _ = map_decision_record(source, context)
                if decision.signal_key in decision_keys:
                    raise ValueError(
                        f"duplicate DecisionRecord signal_key: {decision.signal_key}"
                    )
                decision_keys.add(decision.signal_key)
                if decision.status == "accepted" and decision.trade_action == "trade":
                    accepted[decision.signal_key] = source
            trade_keys: set[str] = set()
            for row in rows:
                signal_key = str(_required(row, ("signal_key",), "signal_key"))
                if signal_key in trade_keys:
                    raise ValueError(f"duplicate completed trade signal_key: {signal_key}")
                trade_keys.add(signal_key)
            with self.store.transaction():
                results = self.decisions.ingest_many(sources, context)
                trade_ids = tuple(
                    self._ingest_completed_trade(spec, row, accepted)
                    for row in rows
                )
            open_trade_count = sum(_is_carry_forward_row(row) for row in rows)
            self.finalize_run(
                spec,
                status="completed",
                ended_at_utc=completed_at_utc,
                metadata={
                    "journal_decision_count": len(results),
                    "journal_trade_count": len(trade_ids),
                    "journal_open_trade_count": open_trade_count,
                    "journal_closed_trade_count": len(trade_ids) - open_trade_count,
                },
            )
        except BaseException as exc:
            self.finalize_run(
                spec,
                status="failed",
                ended_at_utc=completed_at_utc,
                metadata={
                    "journal_ingest_error_type": type(exc).__name__,
                    "journal_ingest_error": str(exc),
                },
            )
            raise
        return BacktestIngestResult(
            run_id=spec.run_id,
            decision_count=len(results),
            trade_count=len(trade_ids),
            trade_ids=trade_ids,
        )

    def _ingest_completed_trade(
        self,
        spec: BacktestRunSpec,
        row: Mapping[str, Any],
        accepted: Mapping[str, Any],
    ) -> str:
        signal_key = str(_required(row, ("signal_key",), "signal_key"))
        source = accepted.get(signal_key)
        if source is None:
            raise ValueError(
                "completed trade has no accepted DecisionRecord with signal_key "
                f"{signal_key!r}"
            )
        context = DecisionIngestContext(spec.run_id, "backtest")
        decision, _ = map_decision_record(source, context)
        defaults = spec.trade_defaults
        symbol = str(_first(row, ("symbol",))[1] or defaults.symbol)
        asset_class = str(_first(row, ("asset_class", "sec_type"))[1] or defaults.asset_class)
        currency = str(_first(row, ("currency",))[1] or defaults.currency)
        side = str(_required(row, ("side", "action"), "side"))
        entry = _finite_float(
            _required(row, ("entry_price", "entry", "open_price"), "entry price"),
            "entry price",
        )
        exit_reason = str(_required(row, ("exit_reason",), "exit_reason"))
        carry_forward = _is_carry_forward_row(row)
        marked_price = _optional_float(
            row,
            ("exit_price", "exit", "close_price"),
            "mark price" if carry_forward else "exit price",
        )
        if not carry_forward and marked_price is None:
            raise ValueError("completed trade lacks exit price")
        opened = _timestamp(
            _required(row, ("opened_at_utc", "entry_time", "entry_date"), "entry time"),
            defaults.naive_timestamp_timezone,
            "entry time",
        )
        _, exit_time_value = _first(row, ("closed_at_utc", "exit_time", "exit_date"))
        if not carry_forward and exit_time_value is None:
            raise ValueError("completed trade lacks exit time")
        marked_at = (
            _timestamp(exit_time_value, defaults.naive_timestamp_timezone, "mark time")
            if exit_time_value is not None
            else None
        )
        quantity_column, quantity_value = _first(row, ("quantity", "qty", "size"))
        quantity = (
            None
            if quantity_value is None
            else _finite_float(quantity_value, quantity_column or "quantity", positive=True)
        )
        points = _optional_float(row, ("points", "pnl_points"), "points")
        weighted_points = _optional_float(row, ("weighted_points",), "weighted_points")
        reported_pnl = _optional_float(
            row,
            ("realized_pnl", "pnl", "profit_loss"),
            "unrealized_pnl" if carry_forward else "realized_pnl",
        )
        trade_id = deterministic_id("trade", spec.run_id, signal_key)
        metadata = {
            "source": (
                "backtest_open_position_snapshot"
                if carry_forward
                else "backtest_completed_trade"
            ),
            "exit_reason": exit_reason,
            "carry_forward": carry_forward,
            "snapshot_reason": "end_of_data" if carry_forward else None,
            "marked_at_utc": marked_at if carry_forward else None,
            "mark_price": marked_price if carry_forward else None,
            "unrealized_pnl": reported_pnl if carry_forward else None,
            "points": points,
            "weighted_points": weighted_points,
            "size": _optional_float(row, ("size",), "size"),
            "quantity_source": quantity_column,
            "exchange": defaults.exchange,
            "source_row": dict(row),
        }
        trade = TradeRecord(
            trade_id=trade_id,
            run_id=spec.run_id,
            signal_key=signal_key,
            decision_id=decision.decision_id,
            asset_class=asset_class,
            symbol=symbol,
            side=side,
            status="open" if carry_forward else "closed",
            opened_at_utc=opened,
            closed_at_utc=None if carry_forward else marked_at,
            quantity=quantity,
            currency=currency,
            entry_price=entry,
            exit_price=None if carry_forward else marked_price,
            realized_pnl=None if carry_forward else reported_pnl,
            metadata=metadata,
        )
        leg = TradeLegRecord(
            leg_id=deterministic_id("trade-leg", trade_id, 0),
            trade_id=trade_id,
            leg_index=0,
            asset_class=asset_class,
            symbol=symbol,
            con_id=None,
            local_symbol=None,
            exchange=defaults.exchange,
            currency=currency,
            side=side,
            ratio=1,
            quantity=quantity,
            expiry=None,
            strike=None,
            option_right=None,
            multiplier=None,
            open_price=entry,
            close_price=None if carry_forward else marked_price,
            metadata={
                "role": "single_asset",
                "source": "backtest",
                "carry_forward": carry_forward,
            },
        )
        return self.store.create_trade(trade, (leg,))


def reconcile_backtest_to_execution(
    store: BacktestJournalStore,
    *,
    backtest_run_id: str,
    execution_run_id: str,
    signal_key: str,
    matched_at_utc: str | datetime,
    notes: str | None = None,
) -> str:
    """Explicitly compare one backtest trade to one paper/live trade by signal."""
    reference = store.get_trade_by_signal(backtest_run_id, signal_key)
    actual = store.get_trade_by_signal(execution_run_id, signal_key)
    if reference is None or actual is None:
        missing = "backtest" if reference is None else "paper/live"
        raise ValueError(f"no {missing} trade found for signal_key {signal_key!r}")

    def difference(field: str) -> float | None:
        left = actual.get(field)
        right = reference.get(field)
        if left is None or right is None:
            return None
        return float(left) - float(right)

    reconciliation_id = deterministic_id(
        "reconciliation",
        actual["trade_id"],
        reference["trade_id"],
        signal_key,
    )
    return store.upsert_reconciliation(
        ReconciliationRecord(
            reconciliation_id=reconciliation_id,
            live_trade_id=str(actual["trade_id"]),
            reference_trade_id=str(reference["trade_id"]),
            signal_key=signal_key,
            status="matched",
            matched_at_utc=matched_at_utc,
            price_slippage=difference("entry_price"),
            quantity_difference=difference("quantity"),
            pnl_difference=difference("realized_pnl"),
            notes=notes,
            details={
                "backtest_run_id": backtest_run_id,
                "execution_run_id": execution_run_id,
                "difference_semantics": "execution_minus_backtest",
            },
        )
    )
