from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass, replace
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Iterable, Literal, Mapping, Sequence

import pandas as pd

import manual_regime_version_manifest as version_manifest


SCHEMA_VERSION = "1.2"
SIGNAL_KEY_VERSION = "manual-regime-v1"
FACTOR_CALCULATION_VERSION = "manual-regime-factors-v1"
DECISION_TIMEZONE = "America/New_York"
STRATEGY_ID = version_manifest.STRATEGY_ID
STRATEGY_VERSION = version_manifest.STRATEGY_VERSION
STRATEGY_VARIANT_ID = version_manifest.STRATEGY_VARIANT_ID
ENGINE_VERSION = version_manifest.ENGINE_VERSION
MONEY_MANAGEMENT_VERSION = version_manifest.MONEY_MANAGEMENT_VERSION

DecisionStatus = Literal[
    "accepted",
    "vetoed",
    "blocked",
    "no_setup",
    "blocked_risk",
    "duplicate_exposure",
    "missing_data",
    "execution_blocked",
]
FactorCategory = Literal[
    "entry_rule",
    "regime",
    "filter",
    "veto",
    "risk",
    "context",
]


@dataclass(frozen=True)
class DecisionVersionContext:
    strategy_variant_id: str
    engine_version: str
    money_management_version: str
    rule_versions: Mapping[str, str]
    manifest_hash: str

    def version_for(self, primary_rule: str) -> str:
        rule_id = version_manifest.canonical_rule_id(primary_rule)
        return str(self.rule_versions.get(rule_id) or version_manifest.rule_version(rule_id))


BASELINE_VERSION_CONTEXT = DecisionVersionContext(
    strategy_variant_id=STRATEGY_VARIANT_ID,
    engine_version=ENGINE_VERSION,
    money_management_version=MONEY_MANAGEMENT_VERSION,
    rule_versions={rule.rule_id: rule.version for rule in version_manifest.RULES},
    manifest_hash=version_manifest.manifest_hash(),
)


@dataclass(frozen=True)
class DecisionFactor:
    factor_code: str
    category: FactorCategory
    observed_value: Any
    operator: str
    threshold: Any
    passed: bool | None
    is_veto: bool
    reason: str
    as_of_time_utc: str
    calculation_version: str = FACTOR_CALCULATION_VERSION

    def to_dict(self) -> dict[str, Any]:
        return to_jsonable(asdict(self))


@dataclass(frozen=True)
class DecisionRecord:
    schema_version: str
    decision_key: str
    signal_key: str
    signal_key_version: str
    strategy_id: str
    strategy_version: str
    config_hash: str
    engine_version: str
    decision_time_utc: str
    symbol: str
    primary_rule: str
    side: str
    track: str
    occurrence: int
    status: DecisionStatus
    trade_action: str
    setup_state: str
    primary_reason: str
    factors: tuple[DecisionFactor, ...]
    context: Mapping[str, Any]
    decision_stage: str = "engine"
    rule_id: str | None = None
    rule_version: str | None = None
    strategy_variant_id: str | None = None
    money_management_version: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return to_jsonable(asdict(self))

    def to_json(self) -> str:
        return canonical_json(self.to_dict())

    @classmethod
    def from_dict(cls, source: Mapping[str, Any]) -> "DecisionRecord":
        """Read both the current schema and pre-versioning JSON payloads."""
        payload = dict(source)
        payload.setdefault("rule_id", None)
        payload.setdefault("rule_version", None)
        payload.setdefault("strategy_variant_id", None)
        payload.setdefault("money_management_version", None)
        payload["factors"] = tuple(
            factor
            if isinstance(factor, DecisionFactor)
            else DecisionFactor(**dict(factor))
            for factor in payload.get("factors", ())
        )
        return cls(**payload)

    @classmethod
    def from_json(cls, source: str) -> "DecisionRecord":
        payload = json.loads(source)
        if not isinstance(payload, Mapping):
            raise TypeError("DecisionRecord JSON must contain an object")
        return cls.from_dict(payload)

    @property
    def context_json(self) -> str:
        return canonical_json(self.context)


def to_jsonable(value: Any) -> Any:
    """Normalize pandas/numpy-rich values to strict JSON values."""
    if value is None or value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, Enum):
        return to_jsonable(value.value)
    if is_dataclass(value):
        return to_jsonable(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (set, frozenset)):
        return [to_jsonable(item) for item in sorted(value, key=str)]
    if isinstance(value, (list, tuple)):
        return [to_jsonable(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, pd.Timestamp):
        return None if pd.isna(value) else value.isoformat()
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        return value
    item = getattr(value, "item", None)
    if callable(item):
        try:
            return to_jsonable(item())
        except (TypeError, ValueError):
            pass
    return str(value)


def canonical_json(value: Any) -> str:
    return json.dumps(
        to_jsonable(value),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def canonical_decision_time(value: Any, source_timezone: str) -> str:
    timestamp = pd.Timestamp(value)
    if pd.isna(timestamp):
        raise ValueError("decision_time must not be missing")
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize(
            source_timezone,
            ambiguous="raise",
            nonexistent="raise",
        )
    return timestamp.tz_convert("UTC").isoformat().replace("+00:00", "Z")


def stable_config_hash(config: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(config).encode("utf-8")).hexdigest()


def build_signal_key(
    *,
    strategy_id: str,
    decision_time: Any,
    source_timezone: str,
    symbol: str,
    primary_rule: str,
    side: str,
    track: str,
    occurrence: int,
    key_version: str = SIGNAL_KEY_VERSION,
) -> tuple[str, str]:
    """Build a deterministic key from causal signal-identity fields only."""
    if occurrence < 0:
        raise ValueError("occurrence must be non-negative")
    decision_time_utc = canonical_decision_time(decision_time, source_timezone)
    identity = {
        "key_version": key_version,
        "strategy_id": strategy_id.strip().lower(),
        "decision_time_utc": decision_time_utc,
        "symbol": symbol.strip().upper(),
        # The existing technical rule name is the stable rule_id.  Versions
        # deliberately do not participate, so candidate variants remain
        # directly comparable with the baseline.
        "primary_rule": version_manifest.canonical_rule_id(primary_rule),
        "side": side.strip().lower(),
        "track": track.strip().lower(),
        "occurrence": int(occurrence),
    }
    digest = hashlib.sha256(canonical_json(identity).encode("utf-8")).hexdigest()[:32]
    return f"{key_version}:{digest}", decision_time_utc


def _trade_value(trade: Any, name: str) -> Any:
    return trade[name] if isinstance(trade, Mapping) else getattr(trade, name)


def _check_factor(code: str, state: str, as_of: str) -> DecisionFactor:
    passed = True if state == "trade" else False if state == "blocked" else None
    category: FactorCategory
    if state == "blocked" or "veto" in code:
        category = "veto"
    elif code in {"post_break_midrange", "structural_reset"}:
        category = "filter"
    else:
        category = "entry_rule"
    return DecisionFactor(
        factor_code=code,
        category=category,
        observed_value=state,
        operator="state_equals",
        threshold="trade",
        passed=passed,
        is_veto=state == "blocked",
        reason=f"engine check {code} ended in state {state}",
        as_of_time_utc=as_of,
    )


def _decision_status(explanation: Mapping[str, Any]) -> DecisionStatus:
    setup = str(explanation.get("setup_state", "no_setup"))
    reason = str(explanation.get("primary_reason", ""))
    if explanation.get("trade_action") == "trade":
        return "accepted"
    if setup == "warmup" or reason == "insufficient_history":
        return "missing_data"
    if setup in {"blocked", "position_open"}:
        return "blocked"
    if setup.startswith("cooldown"):
        return "vetoed"
    return "no_setup"


def _trade_snapshot(trade: Any) -> dict[str, Any]:
    names = (
        "track",
        "rule",
        "side",
        "entry_date",
        "entry_price",
        "stop",
        "target",
        "size",
        "regime_at_entry",
        "labels",
        "entry_labels",
    )
    return {name: _trade_value(trade, name) for name in names}


def _version_provenance(
    primary_rule: str,
    version_context: DecisionVersionContext,
) -> dict[str, str]:
    rule_id = version_manifest.canonical_rule_id(primary_rule)
    return {
        "strategy_variant_id": version_context.strategy_variant_id,
        "engine_version": version_context.engine_version,
        "money_management_version": version_context.money_management_version,
        "rule_id": rule_id,
        "rule_version": version_context.version_for(rule_id),
        "manifest_hash": version_context.manifest_hash,
    }


def build_decision_records(
    *,
    symbol: str,
    decision_time: Any,
    strategy_version: str,
    config_hash: str,
    checks: Mapping[str, str],
    explanation: Mapping[str, Any],
    context: Mapping[str, Any],
    opened_trades: Sequence[Any],
    source_timezone: str = DECISION_TIMEZONE,
    version_context: DecisionVersionContext = BASELINE_VERSION_CONTEXT,
) -> tuple[DecisionRecord, ...]:
    """Create one accepted record per opened track, otherwise one daily record."""
    decision_key, decision_time_utc = build_signal_key(
        strategy_id=STRATEGY_ID,
        decision_time=decision_time,
        source_timezone=source_timezone,
        symbol=symbol,
        primary_rule="__decision__",
        side="flat",
        track="decision",
        occurrence=0,
    )
    raw_context = to_jsonable(
        {
            **dict(context),
            "decision_checks": dict(checks),
            "decision_explanation": dict(explanation),
            "version_provenance": _version_provenance("__decision__", version_context),
        }
    )
    common_factors = (
        DecisionFactor(
            factor_code="regime",
            category="regime",
            observed_value=context.get("regime", "unknown"),
            operator="classified_as",
            threshold=None,
            passed=True,
            is_veto=False,
            reason="causal regime classification at decision time",
            as_of_time_utc=decision_time_utc,
        ),
        *tuple(_check_factor(code, str(state), decision_time_utc) for code, state in checks.items()),
        DecisionFactor(
            factor_code="market_phase",
            category="context",
            observed_value=explanation.get("market_phase"),
            operator="classified_as",
            threshold=None,
            passed=None,
            is_veto=False,
            reason="market phase at decision time",
            as_of_time_utc=decision_time_utc,
        ),
        DecisionFactor(
            factor_code="volume_price_phase",
            category="context",
            observed_value=explanation.get("volume_price_phase"),
            operator="classified_as",
            threshold=None,
            passed=None,
            is_veto=False,
            reason=str(explanation.get("volume_price_detail", "volume context at decision time")),
            as_of_time_utc=decision_time_utc,
        ),
    )

    if not opened_trades:
        trade_without_opening = str(explanation.get("trade_action", "no_trade")) == "trade"
        no_open_factors = common_factors
        if trade_without_opening:
            no_open_factors = common_factors + (
                DecisionFactor(
                    factor_code="decision_trade_invariant",
                    category="veto",
                    observed_value="no_opened_trade",
                    operator="requires",
                    threshold="opened_trade",
                    passed=False,
                    is_veto=True,
                    reason=(
                        "a rule reported trade but the engine created no new trade "
                        "object (for example because the model track is already open)"
                    ),
                    as_of_time_utc=decision_time_utc,
                ),
            )
        return (
            DecisionRecord(
                schema_version=SCHEMA_VERSION,
                decision_key=decision_key,
                signal_key=decision_key,
                signal_key_version=SIGNAL_KEY_VERSION,
                strategy_id=STRATEGY_ID,
                strategy_version=strategy_version,
                config_hash=config_hash,
                engine_version=version_context.engine_version,
                decision_time_utc=decision_time_utc,
                symbol=symbol.upper(),
                primary_rule="__decision__",
                side="flat",
                track="decision",
                occurrence=0,
                status="blocked" if trade_without_opening else _decision_status(explanation),
                trade_action=(
                    "no_trade"
                    if trade_without_opening
                    else str(explanation.get("trade_action", "no_trade"))
                ),
                setup_state=(
                    "blocked_no_trade_object"
                    if trade_without_opening
                    else str(explanation.get("setup_state", "no_setup"))
                ),
                primary_reason=(
                    "trade_signal_without_opened_trade"
                    if trade_without_opening
                    else str(explanation.get("primary_reason", "no_rule_triggered"))
                ),
                factors=no_open_factors,
                context=raw_context,
                rule_id="__decision__",
                rule_version=version_context.version_for("__decision__"),
                strategy_variant_id=version_context.strategy_variant_id,
                money_management_version=version_context.money_management_version,
            ),
        )

    records: list[DecisionRecord] = []
    occurrences: dict[tuple[str, str, str], int] = {}
    for trade in opened_trades:
        track = str(_trade_value(trade, "track"))
        rule = str(_trade_value(trade, "rule"))
        side = str(_trade_value(trade, "side"))
        identity = (track, rule, side)
        occurrence = occurrences.get(identity, 0)
        occurrences[identity] = occurrence + 1
        signal_key, record_time_utc = build_signal_key(
            strategy_id=STRATEGY_ID,
            decision_time=decision_time,
            source_timezone=source_timezone,
            symbol=symbol,
            primary_rule=rule,
            side=side,
            track=track,
            occurrence=occurrence,
        )
        entry = float(_trade_value(trade, "entry_price"))
        stop = float(_trade_value(trade, "stop"))
        target = float(_trade_value(trade, "target"))
        size = float(_trade_value(trade, "size"))
        factors = common_factors + (
            DecisionFactor(
                factor_code=rule,
                category="entry_rule",
                observed_value="trade",
                operator="state_equals",
                threshold="trade",
                passed=True,
                is_veto=False,
                reason=f"{rule} opened the {track} {side} trade",
                as_of_time_utc=record_time_utc,
            ),
            DecisionFactor(
                factor_code="planned_stop_distance",
                category="risk",
                observed_value=abs(entry - stop),
                operator=">",
                threshold=0.0,
                passed=abs(entry - stop) > 0.0,
                is_veto=False,
                reason="planned entry and stop define positive price risk",
                as_of_time_utc=record_time_utc,
            ),
            DecisionFactor(
                factor_code="planned_target_distance",
                category="risk",
                observed_value=abs(target - entry),
                operator=">",
                threshold=0.0,
                passed=abs(target - entry) > 0.0,
                is_veto=False,
                reason="planned entry and target define positive reward",
                as_of_time_utc=record_time_utc,
            ),
            DecisionFactor(
                factor_code="planned_size",
                category="risk",
                observed_value=size,
                operator=">",
                threshold=0.0,
                passed=size > 0.0,
                is_veto=False,
                reason="strategy-provided participation size",
                as_of_time_utc=record_time_utc,
            ),
        )
        records.append(
            DecisionRecord(
                schema_version=SCHEMA_VERSION,
                decision_key=decision_key,
                signal_key=signal_key,
                signal_key_version=SIGNAL_KEY_VERSION,
                strategy_id=STRATEGY_ID,
                strategy_version=strategy_version,
                config_hash=config_hash,
                engine_version=version_context.engine_version,
                decision_time_utc=record_time_utc,
                symbol=symbol.upper(),
                primary_rule=rule,
                side=side,
                track=track,
                occurrence=occurrence,
                status="accepted",
                trade_action="trade",
                setup_state="trade_opened",
                primary_reason=rule,
                factors=factors,
                context={
                    **raw_context,
                    "trade": to_jsonable(_trade_snapshot(trade)),
                    "version_provenance": _version_provenance(rule, version_context),
                },
                rule_id=version_manifest.canonical_rule_id(rule),
                rule_version=version_context.version_for(rule),
                strategy_variant_id=version_context.strategy_variant_id,
                money_management_version=version_context.money_management_version,
            )
        )
    return tuple(records)


def build_missing_data_record(
    *,
    symbol: str,
    decision_day: Any,
    primary_reason: str,
    source: str,
    available_bar_count: int | None,
    context: Mapping[str, Any] | None = None,
    decision_time_hhmm: str = "09:35",
    source_timezone: str = DECISION_TIMEZONE,
    version_context: DecisionVersionContext = BASELINE_VERSION_CONTEXT,
) -> DecisionRecord:
    """Create an auditable final record when a live/API decision lacks two bars."""
    day = pd.Timestamp(decision_day).normalize()
    decision_time = day + pd.Timedelta(
        hours=int(decision_time_hhmm[:2]),
        minutes=int(decision_time_hhmm[3:]),
    )
    decision_key, decision_time_utc = build_signal_key(
        strategy_id=STRATEGY_ID,
        decision_time=decision_time,
        source_timezone=source_timezone,
        symbol=symbol,
        primary_rule="__market_data__",
        side="flat",
        track="decision",
        occurrence=0,
    )
    factor = DecisionFactor(
        factor_code="current_day_bar_count",
        category="context",
        observed_value=available_bar_count,
        operator=">=",
        threshold=2,
        passed=False,
        is_veto=True,
        reason=primary_reason,
        as_of_time_utc=decision_time_utc,
    )
    return DecisionRecord(
        schema_version=SCHEMA_VERSION,
        decision_key=decision_key,
        signal_key=decision_key,
        signal_key_version=SIGNAL_KEY_VERSION,
        strategy_id=STRATEGY_ID,
        strategy_version=STRATEGY_VERSION,
        config_hash=stable_config_hash({"source": source}),
        engine_version=version_context.engine_version,
        decision_time_utc=decision_time_utc,
        symbol=symbol.upper(),
        primary_rule="__market_data__",
        side="flat",
        track="decision",
        occurrence=0,
        status="missing_data",
        trade_action="no_trade",
        setup_state="missing_data",
        primary_reason=primary_reason,
        factors=(factor,),
        context=to_jsonable(
            {
                "day": day.date().isoformat(),
                "source": source,
                "available_bar_count": available_bar_count,
                **dict(context or {}),
                "version_provenance": _version_provenance(
                    "__market_data__", version_context
                ),
            }
        ),
        decision_stage="live_final" if source.startswith("live") else "api_validation",
        rule_id="__market_data__",
        rule_version=version_context.version_for("__market_data__"),
        strategy_variant_id=version_context.strategy_variant_id,
        money_management_version=version_context.money_management_version,
    )


def attach_record_keys_to_trades(
    trades: pd.DataFrame,
    decision_records: Sequence[DecisionRecord],
) -> pd.DataFrame:
    """Attach deterministic keys by signal identity, never by incidental row order."""
    if trades.empty:
        out = trades.copy()
        out["signal_key"] = pd.Series(dtype="object")
        out["decision_key"] = pd.Series(dtype="object")
        return out
    required = {"track", "rule", "side"}
    missing = required.difference(trades.columns)
    if missing:
        raise ValueError(f"Trade rows lack identity columns: {sorted(missing)}")
    accepted: dict[tuple[str, str, str], list[DecisionRecord]] = {}
    for record in decision_records:
        if record.status == "accepted":
            accepted.setdefault(
                (record.track, record.primary_rule, record.side),
                [],
            ).append(record)
    for candidates in accepted.values():
        candidates.sort(key=lambda item: item.occurrence)

    def candidate_score(row: Any, record: DecisionRecord) -> int:
        snapshot = dict(record.context.get("trade", {}))
        comparisons = (
            ("entry_date", "entry_date"),
            ("entry", "entry_price"),
            ("stop", "stop"),
            ("target", "target"),
            ("size", "size"),
        )
        mismatches = 0
        for row_name, snapshot_name in comparisons:
            if not hasattr(row, row_name) or snapshot_name not in snapshot:
                continue
            row_value = getattr(row, row_name)
            snapshot_value = snapshot[snapshot_name]
            if row_name == "entry_date":
                if pd.Timestamp(row_value) != pd.Timestamp(snapshot_value):
                    mismatches += 1
            elif not math.isclose(
                float(row_value),
                float(snapshot_value),
                rel_tol=1e-12,
                abs_tol=1e-12,
            ):
                mismatches += 1
        return mismatches

    out = trades.copy()
    used_keys: set[str] = set()
    signal_keys: list[str] = []
    decision_keys: list[str] = []
    for row in out.itertuples(index=False):
        identity = (str(row.track), str(row.rule), str(row.side))
        candidates = [
            record
            for record in accepted.get(identity, [])
            if record.signal_key not in used_keys
        ]
        if not candidates:
            raise RuntimeError(
                "No accepted DecisionRecord matches trade identity "
                f"{identity}."
            )
        record = min(
            candidates,
            key=lambda item: (candidate_score(row, item), item.occurrence),
        )
        used_keys.add(record.signal_key)
        signal_keys.append(record.signal_key)
        decision_keys.append(record.decision_key)
    out["signal_key"] = signal_keys
    out["decision_key"] = decision_keys
    return out


def finalize_live_record(
    record: DecisionRecord,
    *,
    steps: Sequence[Mapping[str, Any]],
    accepted: bool,
    primary_reason: str,
) -> DecisionRecord:
    """Replace an engine intent with its single final live-pipeline decision."""
    if record.status != "accepted":
        return replace(
            record,
            context={**dict(record.context), "live_pipeline": {"steps": [], "applicable": False}},
            decision_stage="live_final",
        )
    factors = list(record.factors)
    normalized_steps = []
    for step in steps:
        passed = bool(step["passed"])
        code = str(step["factor_code"])
        reason = str(step["reason"])
        factors.append(
            DecisionFactor(
                factor_code=code,
                category="filter" if passed else "veto",
                observed_value=step.get("observed_value"),
                operator=str(step.get("operator", "equals")),
                threshold=step.get("threshold"),
                passed=passed,
                is_veto=not passed,
                reason=reason,
                as_of_time_utc=record.decision_time_utc,
                calculation_version="manual-regime-live-pipeline-v1",
            )
        )
        normalized_steps.append(to_jsonable(dict(step)))
    return replace(
        record,
        status="accepted" if accepted else "vetoed",
        trade_action="trade" if accepted else "no_trade",
        setup_state="live_pipeline_accepted" if accepted else "live_pipeline_vetoed",
        primary_reason=primary_reason,
        factors=tuple(factors),
        context={
            **dict(record.context),
            "live_pipeline": {
                "steps": normalized_steps,
                "applicable": True,
                "final_status": "accepted" if accepted else "vetoed",
            },
        },
        decision_stage="live_final",
    )


def format_log_line(
    records: Sequence[DecisionRecord],
    *,
    closed_tracks: Iterable[str],
    runtime: bool,
) -> str:
    """Render the existing text format from structured records."""
    if not records:
        raise ValueError("at least one DecisionRecord is required")
    first = records[0]
    context = first.context
    explanation = context["decision_explanation"]
    checks = context["decision_checks"]
    opened_rules = list(
        dict.fromkeys(record.primary_rule for record in records if record.status == "accepted")
    )
    day = str(context.get("day", first.decision_time_utc[:10]))[:10]
    parts = [
        f"ts={day}" if runtime else f"day={day}",
        f"asset={first.symbol}" if runtime else f"symbol={first.symbol}",
    ]
    if runtime:
        parts.append("phase=decision")
    parts.extend(
        [
            f"regime={context.get('regime', '-')}",
            f"open={float(context.get('open', 0.0)):.2f}",
            f"decision={float(context.get('decision_price', 0.0)):.2f}",
            f"atr14={float(context.get('atr14', 0.0) or 0.0):.2f}",
            f"opened={','.join(opened_rules) if opened_rules else '-'}",
            f"closed={','.join(sorted(str(item) for item in closed_tracks)) if closed_tracks else '-'}",
            ("rules=" if runtime else "checks=")
            + "|".join(f"{code}:{state}" for code, state in checks.items()),
            f"action={explanation.get('trade_action', first.trade_action)}",
            f"setup={explanation.get('setup_state', first.setup_state)}",
            f"reason={explanation.get('primary_reason', first.primary_reason)}",
            f"market_phase={explanation.get('market_phase', '-')}",
            f"volume_phase={explanation.get('volume_price_phase', '-')}",
            f"risk_note={explanation.get('risk_note', '-')}",
            f"detail={explanation.get('setup_detail', '-')}",
        ]
    )
    return " ".join(parts)


def write_jsonl(path: str | Path, records: Sequence[DecisionRecord]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(
        "".join(record.to_json() + "\n" for record in records),
        encoding="utf-8",
    )
    temporary.replace(output)
