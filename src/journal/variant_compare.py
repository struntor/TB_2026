from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sqlite3
from typing import Any, Mapping, Sequence


class IncompatibleVariantError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class EquityPoint:
    closed_at_utc: str
    trade_id: str
    signal_key: str
    realized_pnl: float
    equity: float
    drawdown: float


@dataclass(frozen=True, slots=True)
class RunMetrics:
    trade_count: int
    total_pnl: float
    ending_equity: float
    return_pct: float
    max_drawdown: float
    max_drawdown_pct: float
    win_rate: float | None


@dataclass(frozen=True, slots=True)
class OptionalMetric:
    available: bool
    value: Mapping[str, float] | None
    reason: str | None


@dataclass(frozen=True, slots=True)
class TradeDelta:
    signal_key: str
    membership: str
    old_trade_id: str | None
    new_trade_id: str | None
    old_entry_price: float | None
    new_entry_price: float | None
    entry_price_delta: float | None
    old_exit_price: float | None
    new_exit_price: float | None
    exit_price_delta: float | None
    old_pnl: float | None
    new_pnl: float | None
    pnl_delta: float | None
    old_quantity: float | None
    new_quantity: float | None
    quantity_delta: float | None


@dataclass(frozen=True, slots=True)
class RuleContribution:
    rule_id: str
    rule_version_id: str
    old_trade_count: int
    new_trade_count: int
    old_pnl: float
    new_pnl: float
    pnl_delta: float


@dataclass(frozen=True, slots=True)
class RunComparison:
    old_run_id: str
    new_run_id: str
    old_variant_id: str | None
    new_variant_id: str | None
    provenance: Mapping[str, Any]
    compatibility_overridden: bool
    compatibility_issues: tuple[str, ...]
    old_metrics: RunMetrics
    new_metrics: RunMetrics
    metric_delta: Mapping[str, float]
    old_equity_curve: tuple[EquityPoint, ...]
    new_equity_curve: tuple[EquityPoint, ...]
    trade_deltas: tuple[TradeDelta, ...]
    rule_contributions: tuple[RuleContribution, ...]
    old_turnover: OptionalMetric
    new_turnover: OptionalMetric
    old_exposure: OptionalMetric
    new_exposure: OptionalMetric

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_PROVENANCE_ALIASES = {
    "period_start": ("period_start", "period_start_utc", "backtest_start", "start"),
    "period_end": ("period_end", "period_end_utc", "backtest_end", "end"),
    "starting_capital": ("starting_capital", "initial_capital", "start_capital"),
    "capital_currency": ("capital_currency", "base_currency", "currency"),
    "data_revision": ("data_revision", "dataset_revision"),
    "fee_model": ("fee_model", "cost_model", "commission_model"),
    "slippage_model": ("slippage_model",),
    "money_management_version": (
        "money_management_version", "money_management", "mm_version"
    ),
}


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    if not _table_exists(connection, table):
        return set()
    return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}


def _json_object(value: Any, field: str) -> dict[str, Any]:
    if value is None or value == "":
        return {}
    try:
        parsed = json.loads(str(value))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{field} is not valid JSON") from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"{field} must contain a JSON object")
    return parsed


def _load_run(connection: sqlite3.Connection, run_id: str) -> dict[str, Any]:
    row = connection.execute("SELECT * FROM strategy_runs WHERE run_id=?", (run_id,)).fetchone()
    if row is None:
        raise ValueError(f"strategy run not found: {run_id}")
    payload = dict(row)
    if payload.get("run_type") != "backtest":
        raise ValueError(f"variant comparison requires a backtest run: {run_id}")
    payload["parameters"] = _json_object(payload.get("parameters_json"), "parameters_json")
    payload["metadata"] = _json_object(payload.get("metadata_json"), "metadata_json")
    return payload


def _load_variant(connection: sqlite3.Connection, variant_id: str | None) -> dict[str, Any]:
    if not variant_id or not _table_exists(connection, "strategy_variants"):
        return {}
    row = connection.execute(
        "SELECT * FROM strategy_variants WHERE variant_id=?", (variant_id,)
    ).fetchone()
    if row is None:
        return {}
    payload = dict(row)
    if "metadata_json" in payload:
        payload["metadata"] = _json_object(payload.get("metadata_json"), "variant.metadata_json")
    return payload


def _first(sources: Sequence[Mapping[str, Any]], aliases: Sequence[str]) -> Any:
    for source in sources:
        for alias in aliases:
            value = source.get(alias)
            if value is not None and value != "":
                return value
    return None


def _provenance(run: Mapping[str, Any], variant: Mapping[str, Any]) -> dict[str, Any]:
    parameters = dict(run["parameters"])
    metadata = dict(run["metadata"])
    nested = []
    for source in (parameters, metadata):
        value = source.get("comparison_provenance")
        if isinstance(value, Mapping):
            nested.append(dict(value))
    variant_metadata = variant.get("metadata", {})
    sources = [*nested, parameters, metadata, variant, variant_metadata]
    return {
        key: _first(sources, aliases)
        for key, aliases in _PROVENANCE_ALIASES.items()
    }


def _compatible_provenance(
    old: Mapping[str, Any],
    new: Mapping[str, Any],
) -> tuple[dict[str, Any], tuple[str, ...]]:
    issues = []
    agreed = {}
    for field in _PROVENANCE_ALIASES:
        old_value = old.get(field)
        new_value = new.get(field)
        if old_value is None or old_value == "" or new_value is None or new_value == "":
            issues.append(f"missing required provenance: {field}")
            continue
        if field == "starting_capital":
            try:
                old_value = float(old_value)
                new_value = float(new_value)
            except (TypeError, ValueError) as exc:
                raise ValueError("starting_capital must be numeric") from exc
            if not math.isfinite(old_value) or old_value <= 0 or not math.isfinite(new_value):
                raise ValueError("starting_capital must be positive and finite")
        if old_value != new_value:
            issues.append(f"incompatible {field}: old={old_value!r} new={new_value!r}")
        else:
            agreed[field] = old_value
    return agreed, tuple(issues)


def _finite_or_none(value: Any) -> float | None:
    if value is None:
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _load_closed_trades(connection: sqlite3.Connection, run_id: str) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT * FROM trades
        WHERE run_id=? AND closed_at_utc IS NOT NULL
        ORDER BY closed_at_utc, trade_id
        """,
        (run_id,),
    ).fetchall()
    trades = []
    seen_signals = set()
    for row in rows:
        trade = dict(row)
        signal_key = trade.get("signal_key")
        if signal_key in {None, ""}:
            raise ValueError(f"closed trade lacks signal_key: {trade['trade_id']}")
        if signal_key in seen_signals:
            raise ValueError(f"duplicate closed-trade signal_key in run {run_id}: {signal_key}")
        seen_signals.add(signal_key)
        trade["metadata"] = _json_object(trade.get("metadata_json"), "trade.metadata_json")
        for field in ("entry_price", "exit_price", "realized_pnl", "quantity"):
            trade[field] = _finite_or_none(trade.get(field))
        if trade["realized_pnl"] is None:
            raise ValueError(
                f"closed trade lacks finite realized_pnl: {trade['trade_id']}"
            )
        trades.append(trade)
    return trades


def _equity(
    trades: Sequence[Mapping[str, Any]],
    starting_capital: float,
) -> tuple[tuple[EquityPoint, ...], RunMetrics]:
    equity = starting_capital
    peak = starting_capital
    max_drawdown = 0.0
    max_drawdown_pct = 0.0
    points = []
    wins = 0
    for trade in trades:
        pnl = float(trade["realized_pnl"])
        equity += pnl
        peak = max(peak, equity)
        drawdown = equity - peak
        max_drawdown = min(max_drawdown, drawdown)
        max_drawdown_pct = min(max_drawdown_pct, (drawdown / peak) * 100.0)
        wins += int(pnl > 0)
        points.append(
            EquityPoint(
                closed_at_utc=str(trade["closed_at_utc"]),
                trade_id=str(trade["trade_id"]),
                signal_key=str(trade["signal_key"]),
                realized_pnl=pnl,
                equity=equity,
                drawdown=drawdown,
            )
        )
    total = equity - starting_capital
    metrics = RunMetrics(
        trade_count=len(trades),
        total_pnl=total,
        ending_equity=equity,
        return_pct=(total / starting_capital) * 100.0,
        max_drawdown=max_drawdown,
        max_drawdown_pct=max_drawdown_pct,
        win_rate=(wins / len(trades)) if trades else None,
    )
    return tuple(points), metrics


def _difference(old: float | None, new: float | None) -> float | None:
    return None if old is None or new is None else new - old


def _trade_deltas(
    old_trades: Sequence[Mapping[str, Any]],
    new_trades: Sequence[Mapping[str, Any]],
) -> tuple[TradeDelta, ...]:
    old = {str(trade["signal_key"]): trade for trade in old_trades}
    new = {str(trade["signal_key"]): trade for trade in new_trades}
    result = []
    for key in sorted(old.keys() | new.keys()):
        left = old.get(key)
        right = new.get(key)
        membership = "common" if left and right else "old_only" if left else "new_only"
        old_entry = left.get("entry_price") if left else None
        new_entry = right.get("entry_price") if right else None
        old_exit = left.get("exit_price") if left else None
        new_exit = right.get("exit_price") if right else None
        old_pnl = left.get("realized_pnl") if left else None
        new_pnl = right.get("realized_pnl") if right else None
        old_qty = left.get("quantity") if left else None
        new_qty = right.get("quantity") if right else None
        result.append(
            TradeDelta(
                signal_key=key,
                membership=membership,
                old_trade_id=str(left["trade_id"]) if left else None,
                new_trade_id=str(right["trade_id"]) if right else None,
                old_entry_price=old_entry,
                new_entry_price=new_entry,
                entry_price_delta=_difference(old_entry, new_entry),
                old_exit_price=old_exit,
                new_exit_price=new_exit,
                exit_price_delta=_difference(old_exit, new_exit),
                old_pnl=old_pnl,
                new_pnl=new_pnl,
                pnl_delta=_difference(old_pnl, new_pnl),
                old_quantity=old_qty,
                new_quantity=new_qty,
                quantity_delta=_difference(old_qty, new_qty),
            )
        )
    return tuple(result)


def _decision_rules(connection: sqlite3.Connection, run_id: str) -> dict[str, tuple[str, str]]:
    columns = _columns(connection, "signal_decisions")
    if not columns:
        return {}
    rule_expr = "COALESCE(rule_id, primary_rule)" if "rule_id" in columns else "primary_rule"
    version_expr = (
        "COALESCE(rule_version_id, strategy_version)"
        if "rule_version_id" in columns else "strategy_version"
    )
    rows = connection.execute(
        f"SELECT signal_key, {rule_expr} AS rule_id, {version_expr} AS rule_version_id "
        "FROM signal_decisions WHERE run_id=?",
        (run_id,),
    ).fetchall()
    return {
        str(row["signal_key"]): (
            str(row["rule_id"] or "__unattributed__"),
            str(row["rule_version_id"] or "__unknown__"),
        )
        for row in rows
    }


def _rule_contributions(
    connection: sqlite3.Connection,
    old_run_id: str,
    new_run_id: str,
    old_trades: Sequence[Mapping[str, Any]],
    new_trades: Sequence[Mapping[str, Any]],
) -> tuple[RuleContribution, ...]:
    old_rules = _decision_rules(connection, old_run_id)
    new_rules = _decision_rules(connection, new_run_id)

    def aggregate(trades: Sequence[Mapping[str, Any]], rules: Mapping[str, tuple[str, str]]):
        result: dict[tuple[str, str], list[float]] = {}
        for trade in trades:
            key = rules.get(str(trade["signal_key"]), ("__unattributed__", "__unknown__"))
            bucket = result.setdefault(key, [0.0, 0.0])
            bucket[0] += 1
            bucket[1] += float(trade["realized_pnl"])
        return result

    old = aggregate(old_trades, old_rules)
    new = aggregate(new_trades, new_rules)
    return tuple(
        RuleContribution(
            rule_id=key[0],
            rule_version_id=key[1],
            old_trade_count=int(old.get(key, [0, 0])[0]),
            new_trade_count=int(new.get(key, [0, 0])[0]),
            old_pnl=float(old.get(key, [0, 0])[1]),
            new_pnl=float(new.get(key, [0, 0])[1]),
            pnl_delta=float(new.get(key, [0, 0])[1] - old.get(key, [0, 0])[1]),
        )
        for key in sorted(old.keys() | new.keys())
    )


def _multiplier(trade: Mapping[str, Any]) -> float | None:
    if str(trade.get("asset_class", "")).upper() in {"STK", "ETF", "INDEX"}:
        return 1.0
    metadata = trade.get("metadata", {})
    value = metadata.get("contract_multiplier", metadata.get("multiplier"))
    return _finite_or_none(value)


def _turnover(trades: Sequence[Mapping[str, Any]], starting_capital: float) -> OptionalMetric:
    total = 0.0
    for trade in trades:
        multiplier = _multiplier(trade)
        if any(trade.get(field) is None for field in ("entry_price", "exit_price", "quantity")) or multiplier is None:
            return OptionalMetric(False, None, f"turnover unavailable: incomplete notional data for {trade['trade_id']}")
        total += (
            abs(float(trade["entry_price"])) + abs(float(trade["exit_price"]))
        ) * abs(float(trade["quantity"])) * multiplier
    return OptionalMetric(True, {"gross_notional": total, "capital_turnover": total / starting_capital}, None)


def _parse_time(value: Any) -> datetime:
    text = str(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _exposure(
    trades: Sequence[Mapping[str, Any]],
    starting_capital: float,
    period_start: Any,
    period_end: Any,
) -> OptionalMetric:
    start = _parse_time(period_start)
    end = _parse_time(period_end)
    if end <= start:
        raise ValueError("period_end must be after period_start")
    event_deltas: dict[datetime, float] = {}
    for trade in trades:
        multiplier = _multiplier(trade)
        if any(trade.get(field) is None for field in ("opened_at_utc", "closed_at_utc", "entry_price", "quantity")) or multiplier is None:
            return OptionalMetric(False, None, f"exposure unavailable: incomplete interval/notional data for {trade['trade_id']}")
        opened = max(start, _parse_time(trade["opened_at_utc"]))
        closed = min(end, _parse_time(trade["closed_at_utc"]))
        if closed < opened:
            continue
        notional = abs(float(trade["entry_price"]) * float(trade["quantity"]) * multiplier)
        event_deltas[opened] = event_deltas.get(opened, 0.0) + notional
        event_deltas[closed] = event_deltas.get(closed, 0.0) - notional
    current = 0.0
    maximum = 0.0
    area = 0.0
    previous = start
    for timestamp, delta in sorted(event_deltas.items()):
        area += current * (timestamp - previous).total_seconds()
        current += delta
        maximum = max(maximum, current)
        previous = timestamp
    area += current * (end - previous).total_seconds()
    average = area / (end - start).total_seconds()
    return OptionalMetric(
        True,
        {
            "average_entry_notional": average,
            "maximum_entry_notional": maximum,
            "average_capital_exposure": average / starting_capital,
            "maximum_capital_exposure": maximum / starting_capital,
        },
        None,
    )


def _validate_trade_period(
    trades: Sequence[Mapping[str, Any]],
    period_start: Any,
    period_end: Any,
) -> None:
    start = _parse_time(period_start)
    end = _parse_time(period_end)
    if end <= start:
        raise ValueError("period_end must be after period_start")
    for trade in trades:
        closed = _parse_time(trade["closed_at_utc"])
        if closed < start or closed > end:
            raise IncompatibleVariantError(
                "closed trade falls outside comparison period: "
                f"trade={trade['trade_id']} closed_at_utc={trade['closed_at_utc']}"
            )


def compare_runs(
    connection: sqlite3.Connection,
    old_run_id: str,
    new_run_id: str,
    *,
    allow_incompatible: bool,
) -> RunComparison:
    connection.row_factory = sqlite3.Row
    old_run = _load_run(connection, old_run_id)
    new_run = _load_run(connection, new_run_id)
    run_columns = _columns(connection, "strategy_runs")
    old_variant_id = old_run.get("variant_id") if "variant_id" in run_columns else None
    new_variant_id = new_run.get("variant_id") if "variant_id" in run_columns else None
    old_provenance = _provenance(old_run, _load_variant(connection, old_variant_id))
    new_provenance = _provenance(new_run, _load_variant(connection, new_variant_id))
    provenance, issues = _compatible_provenance(old_provenance, new_provenance)
    if old_run.get("strategy_id") != new_run.get("strategy_id"):
        issues = (
            *issues,
            "incompatible strategy_id: "
            f"old={old_run.get('strategy_id')!r} new={new_run.get('strategy_id')!r}",
        )
    if issues and not allow_incompatible:
        raise IncompatibleVariantError("; ".join(issues))
    mandatory_common = {
        "period_start", "period_end", "starting_capital", "capital_currency"
    }
    if not mandatory_common.issubset(provenance):
        # An override may permit investigation, but equity still needs one explicit
        # common capital base; never silently invent it.
        raise IncompatibleVariantError(
            "no compatible period/starting_capital/capital_currency is available"
        )
    starting_capital = float(provenance["starting_capital"])
    old_trades = _load_closed_trades(connection, old_run_id)
    new_trades = _load_closed_trades(connection, new_run_id)
    capital_currency = str(provenance["capital_currency"])
    for trade in (*old_trades, *new_trades):
        if str(trade.get("currency")) != capital_currency:
            raise IncompatibleVariantError(
                "trade currency does not match capital_currency: "
                f"trade={trade['trade_id']} currency={trade.get('currency')!r} "
                f"capital_currency={capital_currency!r}"
            )
    _validate_trade_period(
        (*old_trades, *new_trades),
        provenance["period_start"],
        provenance["period_end"],
    )
    old_curve, old_metrics = _equity(old_trades, starting_capital)
    new_curve, new_metrics = _equity(new_trades, starting_capital)
    metric_delta = {
        "trade_count": float(new_metrics.trade_count - old_metrics.trade_count),
        "total_pnl": new_metrics.total_pnl - old_metrics.total_pnl,
        "ending_equity": new_metrics.ending_equity - old_metrics.ending_equity,
        "return_pct": new_metrics.return_pct - old_metrics.return_pct,
        "max_drawdown": new_metrics.max_drawdown - old_metrics.max_drawdown,
        "max_drawdown_pct": new_metrics.max_drawdown_pct - old_metrics.max_drawdown_pct,
    }
    period_start = provenance.get("period_start")
    period_end = provenance.get("period_end")
    old_exposure = (
        _exposure(old_trades, starting_capital, period_start, period_end)
        if period_start is not None and period_end is not None
        else OptionalMetric(False, None, "exposure unavailable: compatible period is missing")
    )
    new_exposure = (
        _exposure(new_trades, starting_capital, period_start, period_end)
        if period_start is not None and period_end is not None
        else OptionalMetric(False, None, "exposure unavailable: compatible period is missing")
    )
    return RunComparison(
        old_run_id=old_run_id,
        new_run_id=new_run_id,
        old_variant_id=old_variant_id,
        new_variant_id=new_variant_id,
        provenance=provenance,
        compatibility_overridden=bool(issues and allow_incompatible),
        compatibility_issues=issues,
        old_metrics=old_metrics,
        new_metrics=new_metrics,
        metric_delta=metric_delta,
        old_equity_curve=old_curve,
        new_equity_curve=new_curve,
        trade_deltas=_trade_deltas(old_trades, new_trades),
        rule_contributions=_rule_contributions(
            connection, old_run_id, new_run_id, old_trades, new_trades
        ),
        old_turnover=_turnover(old_trades, starting_capital),
        new_turnover=_turnover(new_trades, starting_capital),
        old_exposure=old_exposure,
        new_exposure=new_exposure,
    )


def _read_only_connection(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare two journaled backtest variants")
    parser.add_argument("--database", required=True)
    parser.add_argument("--old-run-id", required=True)
    parser.add_argument("--new-run-id", required=True)
    parser.add_argument("--allow-incompatible", action="store_true")
    parser.add_argument("--output")
    args = parser.parse_args()
    with _read_only_connection(Path(args.database)) as connection:
        result = compare_runs(
            connection,
            args.old_run_id,
            args.new_run_id,
            allow_incompatible=args.allow_incompatible,
        )
    payload = json.dumps(result.to_dict(), ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        Path(args.output).write_text(payload + "\n", encoding="utf-8")
    else:
        print(payload)


if __name__ == "__main__":
    main()
