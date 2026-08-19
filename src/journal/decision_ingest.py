from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
import math
from typing import Any, ContextManager, Iterable, Literal, Mapping, Protocol, Sequence

from src.journal.models import (
    DecisionFactorRecord,
    SignalDecisionRecord,
    TradeLegRecord,
    TradeRecord,
    canonical_json,
    deterministic_id,
)


RunMode = Literal["backtest", "paper", "live"]
FINAL_STATUSES = frozenset(
    {
        "accepted",
        "vetoed",
        "blocked",
        "no_setup",
        "blocked_risk",
        "duplicate_exposure",
        "missing_data",
        "execution_blocked",
    }
)


def _require_finite(
    value: float | None,
    field: str,
    *,
    positive: bool = False,
    non_negative: bool = False,
) -> None:
    if value is None:
        return
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a finite number")
    try:
        normalized = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a finite number") from exc
    if not math.isfinite(normalized):
        raise ValueError(f"{field} must be finite")
    if positive and normalized <= 0:
        raise ValueError(f"{field} must be positive")
    if non_negative and normalized < 0:
        raise ValueError(f"{field} must be non-negative")


class DecisionJournalStore(Protocol):
    """Small persistence boundary implemented by ``JournalStore``.

    ``upsert_decision`` must be atomic and replace the factor set for the
    deterministic decision id. ``create_trade`` must be idempotent by trade id.
    """

    def transaction(self) -> ContextManager[None]: ...

    def upsert_decision(
        self,
        record: SignalDecisionRecord,
        factors: Sequence[DecisionFactorRecord] = (),
    ) -> None: ...

    def create_trade(
        self,
        record: TradeRecord,
        legs: Sequence[TradeLegRecord] = (),
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class DecisionIngestContext:
    run_id: str
    run_mode: RunMode

    def __post_init__(self) -> None:
        if not self.run_id.strip():
            raise ValueError("run_id is required")
        if self.run_mode not in {"backtest", "paper", "live"}:
            raise ValueError(f"unsupported run_mode: {self.run_mode!r}")


@dataclass(frozen=True, slots=True)
class TradeIntentLeg:
    asset_class: str
    symbol: str
    con_id: int | None
    local_symbol: str | None
    exchange: str | None
    currency: str
    side: str
    ratio: int
    quantity: float | None
    expiry: str | None
    strike: float | None
    option_right: str | None
    multiplier: float | None
    open_price: float | None
    metadata: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not self.asset_class.strip():
            raise ValueError("leg asset_class is required")
        if not self.symbol.strip():
            raise ValueError("leg symbol is required")
        if not self.currency.strip():
            raise ValueError("leg currency is required")
        if not self.side.strip():
            raise ValueError("leg side is required")
        if self.ratio <= 0:
            raise ValueError("leg ratio must be positive")
        _require_finite(self.quantity, "leg quantity", positive=True)
        _require_finite(self.strike, "leg strike", non_negative=True)
        _require_finite(self.multiplier, "leg multiplier", positive=True)
        _require_finite(self.open_price, "leg open_price")
        if self.con_id is not None and self.con_id <= 0:
            raise ValueError("leg con_id must be positive when supplied")
        if self.asset_class.upper() == "OPT":
            if not self.expiry or not self.expiry.strip():
                raise ValueError("an option leg requires expiry")
            if self.strike is None:
                raise ValueError("an option leg requires strike")
            if not self.option_right or self.option_right.strip().upper() not in {
                "C",
                "P",
                "CALL",
                "PUT",
            }:
                raise ValueError("an option leg requires option_right C/P/CALL/PUT")
            if self.multiplier is None:
                raise ValueError("an option leg requires multiplier")
            if self.con_id is None and not (self.local_symbol and self.local_symbol.strip()):
                raise ValueError("an option leg requires con_id or local_symbol identity")


@dataclass(frozen=True, slots=True)
class TradeIntent:
    """Broker-neutral accepted intent, valid for assets and option structures."""

    asset_class: str
    symbol: str
    side: str
    status: str
    opened_at_utc: str | None
    quantity: float | None
    currency: str
    entry_price: float | None
    stop_price: float | None
    target_price: float | None
    exchange: str | None
    legs: tuple[TradeIntentLeg, ...]
    metadata: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not self.asset_class.strip():
            raise ValueError("asset_class is required")
        if not self.symbol.strip():
            raise ValueError("symbol is required")
        if not self.side.strip():
            raise ValueError("side is required")
        if not self.currency.strip():
            raise ValueError("currency is required")
        _require_finite(self.quantity, "quantity", positive=True)
        _require_finite(self.entry_price, "entry_price")
        _require_finite(self.stop_price, "stop_price")
        _require_finite(self.target_price, "target_price")
        asset_class = self.asset_class.upper()
        if asset_class == "OPT":
            if not self.legs:
                raise ValueError("an OPT trade intent requires an explicit option leg")
            if any(leg.asset_class.upper() != "OPT" for leg in self.legs):
                raise ValueError("all legs of an OPT trade intent must be OPT")
        if asset_class == "BAG" and len(self.legs) < 2:
            raise ValueError("a BAG option structure requires at least two legs")


@dataclass(frozen=True, slots=True)
class DecisionIngestResult:
    decision_id: str
    signal_key: str
    status: str
    factor_count: int


@dataclass(frozen=True, slots=True)
class ComparableSignal:
    signal_key: str
    left: Any
    right: Any


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        result = to_dict()
        if not isinstance(result, Mapping):
            raise TypeError("to_dict() must return a mapping")
        return dict(result)
    if is_dataclass(value):
        return asdict(value)
    raise TypeError(f"expected a DecisionRecord-like object, got {type(value).__name__}")


def _required(payload: Mapping[str, Any], field: str) -> Any:
    if field not in payload or payload[field] is None:
        raise ValueError(f"DecisionRecord field {field!r} is required")
    return payload[field]


def _validate_json_mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field} must be a mapping")
    result = dict(value)
    canonical_json(result)
    return result


def map_decision_record(
    source: Any,
    context: DecisionIngestContext,
) -> tuple[SignalDecisionRecord, tuple[DecisionFactorRecord, ...]]:
    """Losslessly map an engine DecisionRecord into normalized journal DTOs."""
    raw = _mapping(source)
    signal_key = str(_required(raw, "signal_key"))
    decision_id = deterministic_id("decision", context.run_id, signal_key)
    status = str(_required(raw, "status"))
    if status not in FINAL_STATUSES:
        raise ValueError(f"unsupported decision status: {status!r}")
    decision_time = str(_required(raw, "decision_time_utc"))
    raw_record = _validate_json_mapping(raw, "raw_record")
    raw_rule_id = raw.get("rule_id")
    raw_rule_version = raw.get("rule_version")
    if (raw_rule_id is None) != (raw_rule_version is None):
        raise ValueError("DecisionRecord rule_id and rule_version must be supplied together")
    rule_id = str(raw_rule_id) if raw_rule_id is not None else None
    rule_version_id = (
        deterministic_id("rule-version", rule_id, str(raw_rule_version))
        if rule_id is not None
        else None
    )
    record = SignalDecisionRecord(
        decision_id=decision_id,
        run_id=context.run_id,
        signal_key=signal_key,
        decision_key=str(_required(raw, "decision_key")),
        signal_key_version=str(_required(raw, "signal_key_version")),
        decision_stage=str(raw.get("decision_stage", "engine")),
        strategy_id=str(_required(raw, "strategy_id")),
        strategy_version=str(_required(raw, "strategy_version")),
        engine_version=str(_required(raw, "engine_version")),
        config_hash=str(_required(raw, "config_hash")),
        decision_time_utc=decision_time,
        symbol=str(_required(raw, "symbol")),
        primary_rule=str(_required(raw, "primary_rule")),
        side=str(_required(raw, "side")),
        track=str(_required(raw, "track")),
        occurrence=int(_required(raw, "occurrence")),
        status=status,
        trade_action=str(_required(raw, "trade_action")),
        setup_state=str(_required(raw, "setup_state")),
        primary_reason=str(_required(raw, "primary_reason")),
        context=_validate_json_mapping(_required(raw, "context"), "context"),
        raw_record=raw_record,
        rule_id=rule_id,
        rule_version_id=rule_version_id,
    )

    source_factors = raw.get("factors", ())
    if not isinstance(source_factors, (list, tuple)):
        raise TypeError("DecisionRecord factors must be a sequence")
    factors: list[DecisionFactorRecord] = []
    for position, source_factor in enumerate(source_factors):
        factor = _mapping(source_factor)
        factors.append(
            DecisionFactorRecord(
                factor_id=deterministic_id("factor", decision_id, position),
                decision_id=decision_id,
                position=position,
                factor_code=str(_required(factor, "factor_code")),
                category=str(_required(factor, "category")),
                observed_value=factor.get("observed_value"),
                operator=str(_required(factor, "operator")),
                threshold=factor.get("threshold"),
                passed=factor.get("passed"),
                is_veto=bool(_required(factor, "is_veto")),
                reason=str(_required(factor, "reason")),
                as_of_time_utc=str(_required(factor, "as_of_time_utc")),
                calculation_version=str(_required(factor, "calculation_version")),
            )
        )
    # Validate scalar factor payloads before a transaction reaches SQLite.
    canonical_json([asdict(factor) for factor in factors])
    return record, tuple(factors)


class DecisionJournalIngestor:
    def __init__(self, store: DecisionJournalStore) -> None:
        self.store = store

    def ingest(
        self,
        source: Any,
        context: DecisionIngestContext,
    ) -> DecisionIngestResult:
        record, factors = map_decision_record(source, context)
        self.store.upsert_decision(record, factors)
        return DecisionIngestResult(
            decision_id=record.decision_id,
            signal_key=record.signal_key,
            status=record.status,
            factor_count=len(factors),
        )

    def ingest_many(
        self,
        sources: Iterable[Any],
        context: DecisionIngestContext,
    ) -> tuple[DecisionIngestResult, ...]:
        return tuple(self.ingest(source, context) for source in sources)

    def create_trade_intent(
        self,
        source: Any,
        context: DecisionIngestContext,
        intent: TradeIntent,
    ) -> str:
        decision, factors = map_decision_record(source, context)
        if decision.status != "accepted" or decision.trade_action != "trade":
            raise ValueError(
                f"cannot create a trade intent from {decision.status!r}/{decision.trade_action!r}"
            )
        trade_id = deterministic_id("trade", context.run_id, decision.signal_key)
        metadata = {
            **dict(intent.metadata),
            "run_mode": context.run_mode,
            "planned_stop_price": intent.stop_price,
            "planned_target_price": intent.target_price,
            "exchange": intent.exchange,
        }
        trade = TradeRecord(
            trade_id=trade_id,
            run_id=context.run_id,
            signal_key=decision.signal_key,
            decision_id=decision.decision_id,
            asset_class=intent.asset_class,
            symbol=intent.symbol,
            side=intent.side,
            status=intent.status,
            opened_at_utc=intent.opened_at_utc,
            closed_at_utc=None,
            quantity=intent.quantity,
            currency=intent.currency,
            entry_price=intent.entry_price,
            exit_price=None,
            realized_pnl=None,
            metadata=metadata,
        )
        intent_legs = intent.legs or (
            TradeIntentLeg(
                asset_class=intent.asset_class,
                symbol=intent.symbol,
                con_id=None,
                local_symbol=None,
                exchange=intent.exchange,
                currency=intent.currency,
                side=intent.side,
                ratio=1,
                quantity=intent.quantity,
                expiry=None,
                strike=None,
                option_right=None,
                multiplier=None,
                open_price=intent.entry_price,
                metadata={"role": "single_asset"},
            ),
        )
        legs = tuple(
            TradeLegRecord(
                leg_id=deterministic_id("trade-leg", trade_id, leg_index),
                trade_id=trade_id,
                leg_index=leg_index,
                asset_class=leg.asset_class,
                symbol=leg.symbol,
                con_id=leg.con_id,
                local_symbol=leg.local_symbol,
                exchange=leg.exchange,
                currency=leg.currency,
                side=leg.side,
                ratio=leg.ratio,
                quantity=leg.quantity,
                expiry=leg.expiry,
                strike=leg.strike,
                option_right=leg.option_right,
                multiplier=leg.multiplier,
                open_price=leg.open_price,
                close_price=None,
                metadata=dict(leg.metadata),
            )
            for leg_index, leg in enumerate(intent_legs)
        )
        with self.store.transaction():
            self.store.upsert_decision(decision, factors)
            self.store.create_trade(trade, legs)
        return trade_id


def correlate_by_signal_key(
    left: Iterable[Any],
    right: Iterable[Any],
) -> tuple[ComparableSignal, ...]:
    """Pair backtest/paper/live records by causal key, never by trade id."""

    def keyed(items: Iterable[Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for item in items:
            payload = _mapping(item)
            key = str(_required(payload, "signal_key"))
            if key in result:
                raise ValueError(f"duplicate signal_key in comparison side: {key}")
            result[key] = item
        return result

    left_by_key = keyed(left)
    right_by_key = keyed(right)
    return tuple(
        ComparableSignal(signal_key=key, left=left_by_key[key], right=right_by_key[key])
        for key in sorted(left_by_key.keys() & right_by_key.keys())
    )
