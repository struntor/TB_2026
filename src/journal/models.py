from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
from typing import Any, Mapping


JsonMapping = Mapping[str, Any]
UtcTimestamp = str | datetime


def deterministic_id(kind: str, *identity_parts: object) -> str:
    """Return a compact, stable external id from immutable identity fields."""
    normalized_kind = kind.strip().lower()
    if not normalized_kind:
        raise ValueError("kind must not be empty")
    if not identity_parts or any(str(part).strip() == "" for part in identity_parts):
        raise ValueError("identity parts must not be empty")
    identity_json = canonical_json([str(part) for part in identity_parts])
    digest = hashlib.sha256(identity_json.encode("utf-8")).hexdigest()[:32]
    return f"{normalized_kind}:{digest}"


def utc_iso(value: UtcTimestamp) -> str:
    """Normalize an aware timestamp to a sortable ISO-8601 UTC string."""
    if isinstance(value, str):
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError as exc:
            raise ValueError(f"invalid ISO timestamp: {value!r}") from exc
    elif isinstance(value, datetime):
        parsed = value
    else:
        raise TypeError("timestamp must be a datetime or ISO-8601 string")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include a timezone")
    normalized = parsed.astimezone(timezone.utc)
    timespec = "microseconds" if normalized.microsecond else "seconds"
    return normalized.isoformat(timespec=timespec).replace("+00:00", "Z")


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("JSON values must be finite")
        return value
    if isinstance(value, datetime):
        return utc_iso(value)
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    raise TypeError(f"unsupported JSON value type: {type(value).__name__}")


def canonical_json(value: Any) -> str:
    return json.dumps(
        _json_value(value),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


@dataclass(frozen=True, slots=True)
class StrategyRunRecord:
    run_id: str
    strategy_id: str
    strategy_version: str
    config_hash: str
    run_type: str
    environment: str
    started_at_utc: UtcTimestamp
    ended_at_utc: UtcTimestamp | None
    status: str
    parameters: JsonMapping
    metadata: JsonMapping
    variant_id: str | None = None


@dataclass(frozen=True, slots=True)
class SignalDecisionRecord:
    decision_id: str
    run_id: str
    signal_key: str
    decision_key: str
    signal_key_version: str
    decision_stage: str
    strategy_id: str
    strategy_version: str
    engine_version: str
    config_hash: str
    decision_time_utc: UtcTimestamp
    symbol: str
    primary_rule: str
    side: str
    track: str
    occurrence: int
    status: str
    trade_action: str
    setup_state: str
    primary_reason: str
    context: JsonMapping
    raw_record: JsonMapping
    rule_id: str | None = None
    rule_version_id: str | None = None


@dataclass(frozen=True, slots=True)
class RuleDefinitionRecord:
    """Stable business identity of a rule, independent of its implementation."""

    rule_id: str
    name: str
    description: str
    metadata: JsonMapping


@dataclass(frozen=True, slots=True)
class RuleVersionRecord:
    """Immutable implementation snapshot of one stable rule identity."""

    rule_version_id: str
    rule_id: str
    semver: str
    logic_hash: str
    code_hash: str
    engine_version: str
    created_at_utc: UtcTimestamp
    parameters: JsonMapping
    metadata: JsonMapping


@dataclass(frozen=True, slots=True)
class StrategyVariantRecord:
    """Immutable strategy composition and reproducibility provenance."""

    variant_id: str
    strategy_id: str
    variant_version: str
    name: str
    engine_version: str
    money_management_version: str
    config_hash: str
    data_revision: str
    fee_model: str
    slippage_model: str
    created_at_utc: UtcTimestamp
    metadata: JsonMapping


@dataclass(frozen=True, slots=True)
class StrategyVariantRuleRecord:
    """Immutable ordered membership of one rule version in a strategy variant."""

    variant_id: str
    rule_id: str
    rule_version_id: str
    position: int
    role: str
    enabled: bool
    parameters: JsonMapping


@dataclass(frozen=True, slots=True)
class DecisionFactorRecord:
    factor_id: str
    decision_id: str
    position: int
    factor_code: str
    category: str
    observed_value: Any
    operator: str
    threshold: Any
    passed: bool | None
    is_veto: bool
    reason: str
    as_of_time_utc: UtcTimestamp
    calculation_version: str


@dataclass(frozen=True, slots=True)
class TradeRecord:
    trade_id: str
    run_id: str
    signal_key: str | None
    decision_id: str | None
    asset_class: str
    symbol: str
    side: str
    status: str
    opened_at_utc: UtcTimestamp | None
    closed_at_utc: UtcTimestamp | None
    quantity: float | None
    currency: str
    entry_price: float | None
    exit_price: float | None
    realized_pnl: float | None
    metadata: JsonMapping


@dataclass(frozen=True, slots=True)
class TradeLegRecord:
    leg_id: str
    trade_id: str
    leg_index: int
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
    close_price: float | None
    metadata: JsonMapping


@dataclass(frozen=True, slots=True)
class OrderRecord:
    order_id: str
    run_id: str
    trade_id: str | None
    signal_key: str | None
    parent_order_id: str | None
    broker: str
    broker_order_id: str | None
    order_ref: str | None
    order_type: str
    action: str
    quantity: float
    limit_price: float | None
    stop_price: float | None
    time_in_force: str
    transmit: bool
    status: str
    submitted_at_utc: UtcTimestamp | None
    updated_at_utc: UtcTimestamp
    metadata: JsonMapping


@dataclass(frozen=True, slots=True)
class OrderBinding:
    order_id: str
    run_id: str
    trade_id: str | None
    signal_key: str | None


@dataclass(frozen=True, slots=True)
class OrderLegRecord:
    order_leg_id: str
    order_id: str
    leg_index: int
    trade_leg_id: str | None
    con_id: int | None
    symbol: str
    action: str
    ratio: int
    exchange: str | None
    open_close: str | None
    metadata: JsonMapping


@dataclass(frozen=True, slots=True)
class OrderStatusEventRecord:
    event_id: str
    order_id: str
    status: str
    occurred_at_utc: UtcTimestamp
    filled_quantity: float | None
    remaining_quantity: float | None
    average_fill_price: float | None
    broker_message: str | None
    payload: JsonMapping


@dataclass(frozen=True, slots=True)
class ExecutionRecord:
    execution_id: str
    order_id: str
    trade_id: str | None
    broker_execution_id: str
    executed_at_utc: UtcTimestamp
    side: str
    quantity: float
    price: float
    exchange: str | None
    liquidity: str | None
    metadata: JsonMapping


@dataclass(frozen=True, slots=True)
class CommissionRecord:
    commission_id: str
    execution_id: str
    currency: str
    amount: float
    realized_pnl: float | None
    reported_at_utc: UtcTimestamp
    metadata: JsonMapping


@dataclass(frozen=True, slots=True)
class TradeEventRecord:
    event_id: str
    run_id: str
    trade_id: str | None
    order_id: str | None
    signal_key: str | None
    event_type: str
    occurred_at_utc: UtcTimestamp
    payload: JsonMapping


@dataclass(frozen=True, slots=True)
class RiskEventRecord:
    risk_event_id: str
    run_id: str
    trade_id: str | None
    decision_id: str | None
    signal_key: str | None
    event_type: str
    status: str
    occurred_at_utc: UtcTimestamp
    risk_amount: float | None
    risk_currency: str | None
    payload: JsonMapping


@dataclass(frozen=True, slots=True)
class ReconciliationRecord:
    reconciliation_id: str
    live_trade_id: str
    reference_trade_id: str
    signal_key: str
    status: str
    matched_at_utc: UtcTimestamp
    price_slippage: float | None
    quantity_difference: float | None
    pnl_difference: float | None
    notes: str | None
    details: JsonMapping
