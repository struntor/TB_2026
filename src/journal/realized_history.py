from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import json
import math
from pathlib import Path
import sqlite3
from typing import Iterable, Mapping
from zoneinfo import ZoneInfo


NEW_YORK = ZoneInfo("America/New_York")
CANONICAL_MODEL_HISTORY_ROLES = frozenset(
    {"canonical", "canonical_model_history", "model_history"}
)


class JournalHistoryDataError(RuntimeError):
    """The journal cannot prove one complete, unambiguous model history."""


@dataclass(frozen=True, slots=True)
class ModelHistoryTrade:
    signal_key: str
    run_id: str
    run_type: str
    rule_id: str
    closed_at_utc: str
    realized_pnl: float


@dataclass(frozen=True, slots=True)
class ModelOpenPosition:
    signal_key: str
    run_id: str
    run_type: str
    symbol: str
    side: str
    rule_id: str
    track: str
    opened_at_utc: str
    entry_price: float | None
    stop_price: float | None
    target_price: float | None
    quantity: float | None
    source_status: str


@dataclass(frozen=True, slots=True)
class ModelHistorySnapshot:
    database_path: str
    strategy_variant_id: str
    selected_run_id: str
    run_selection: str
    window_start_utc: str
    window_end_utc: str
    trades: tuple[ModelHistoryTrade, ...]
    source_candidate_count: int
    duplicate_signal_count: int
    run_candidate_count: int
    data_quality_status: str = "complete"
    open_positions: tuple[ModelOpenPosition, ...] = ()
    open_position_source_count: int = 0
    open_position_duplicate_count: int = 0

    @property
    def losses(self) -> tuple[ModelHistoryTrade, ...]:
        return tuple(trade for trade in self.trades if trade.realized_pnl <= 0.0)

    @property
    def loss_count(self) -> int:
        return len(self.losses)

    @property
    def loss_net(self) -> float:
        return float(sum(trade.realized_pnl for trade in self.losses))

    @property
    def open_symbols(self) -> frozenset[str]:
        return frozenset(position.symbol.upper() for position in self.open_positions)


# Compatibility names for callers that build an in-memory snapshot. The semantics
# are model/backtest-only despite the historical class name.
RealizedHistoryTrade = ModelHistoryTrade
RealizedHistorySnapshot = ModelHistorySnapshot


def _utc_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _parse_utc(value: str) -> datetime:
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise JournalHistoryDataError(f"journal timestamp is not timezone-aware: {value!r}")
    return parsed.astimezone(timezone.utc)


def _bounds(trading_day: date, lookback_calendar_days: int) -> tuple[datetime, datetime]:
    if lookback_calendar_days <= 0:
        raise ValueError("lookback_calendar_days must be positive")
    end_local = datetime.combine(trading_day, time.min, tzinfo=NEW_YORK)
    start_local = end_local - timedelta(days=lookback_calendar_days)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def _read_connection(database_path: str | Path) -> sqlite3.Connection:
    path = Path(database_path)
    if not path.is_file():
        raise JournalHistoryDataError(f"trading journal does not exist: {path}")
    connection = sqlite3.connect(str(path), timeout=5.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


def _json_object(raw: str, *, field: str, run_id: str) -> dict[str, object]:
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise JournalHistoryDataError(
            f"invalid {field} for model-history run {run_id!r}"
        ) from exc
    if not isinstance(value, dict):
        raise JournalHistoryDataError(
            f"{field} must be a JSON object for model-history run {run_id!r}"
        )
    return value


def _model_history_role(metadata: Mapping[str, object]) -> str:
    return str(metadata.get("model_history_role", "")).strip().lower()


def _end_of_data_snapshot(status: str, metadata: Mapping[str, object]) -> bool:
    if status == "open":
        return True
    exit_reason = str(metadata.get("exit_reason", "")).strip().lower()
    return status == "closed" and exit_reason in {
        "end_of_test",
        "end_of_test_mark",
        "end_of_data",
    }


def _optional_number(value: object) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _iso_date(value: object, *, field: str, run_id: str) -> date:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise JournalHistoryDataError(
            f"model-history run has invalid {field}: run_id={run_id} value={value!r}"
        ) from exc


def _validate_coverage(
    run: sqlite3.Row,
    *,
    window_start_day: date,
    trading_day: date,
) -> None:
    run_id = str(run["run_id"])
    parameters = _json_object(
        str(run["parameters_json"]), field="parameters_json", run_id=run_id
    )
    if "period_start" not in parameters or "period_end_exclusive" not in parameters:
        raise JournalHistoryDataError(
            "model-history run lacks auditable period_start/period_end_exclusive "
            f"coverage: run_id={run_id}"
        )
    period_start = _iso_date(parameters["period_start"], field="period_start", run_id=run_id)
    period_end_exclusive = _iso_date(
        parameters["period_end_exclusive"],
        field="period_end_exclusive",
        run_id=run_id,
    )
    if period_start > window_start_day:
        raise JournalHistoryDataError(
            "model history starts after the required lookback window: "
            f"run_id={run_id} period_start={period_start} required_start={window_start_day}"
        )
    if period_end_exclusive < trading_day:
        raise JournalHistoryDataError(
            "stale model history: selected run does not cover every possible exit "
            "before the trading day: "
            f"run_id={run_id} period_end_exclusive={period_end_exclusive} "
            f"required_end_exclusive={trading_day}"
        )


def _select_run(
    rows: list[sqlite3.Row],
    *,
    requested_run_id: str | None,
) -> tuple[sqlite3.Row, str]:
    if requested_run_id is not None:
        selected_id = requested_run_id.strip()
        if not selected_id:
            raise ValueError("model_run_id must be non-empty when supplied")
        matches = [row for row in rows if str(row["run_id"]) == selected_id]
        if len(matches) != 1:
            raise JournalHistoryDataError(
                "explicit model-history run is not a completed backtest/shadow run "
                f"for the strategy variant: run_id={selected_id}"
            )
        return matches[0], "explicit_run_id"

    tagged: list[sqlite3.Row] = []
    for row in rows:
        metadata = _json_object(
            str(row["metadata_json"]), field="metadata_json", run_id=str(row["run_id"])
        )
        if _model_history_role(metadata) in CANONICAL_MODEL_HISTORY_ROLES:
            tagged.append(row)
    if len(tagged) == 1:
        return tagged[0], "metadata_role"
    if len(tagged) > 1:
        raise JournalHistoryDataError(
            "multiple completed backtest/shadow runs are tagged as canonical model "
            "history; pass model_run_id explicitly: "
            + ", ".join(sorted(str(row["run_id"]) for row in tagged))
        )
    if len(rows) == 1:
        return rows[0], "sole_completed_run"
    if not rows:
        raise JournalHistoryDataError(
            "no completed backtest/shadow model-history run exists for the strategy variant"
        )
    raise JournalHistoryDataError(
        "multiple completed backtest/shadow runs exist without one canonical metadata "
        "role; pass model_run_id explicitly: "
        + ", ".join(sorted(str(row["run_id"]) for row in rows))
    )


def load_model_history(
    database_path: str | Path,
    *,
    strategy_variant_id: str,
    rule_ids: Iterable[str],
    trading_day: date,
    lookback_calendar_days: int,
    model_run_id: str | None = None,
) -> ModelHistorySnapshot:
    """Load causal strategy state from exactly one completed model run.

    Paper/live executions are intentionally never queried. Broker availability,
    missed orders, fills, slippage, and commissions cannot change strategy state.
    When more than one completed backtest/shadow run exists, selection is explicit
    by ``model_run_id`` or by one run tagged
    ``metadata.model_history_role=canonical_model_history``. Otherwise the query
    fails closed instead of mixing runs.
    """

    variant_id = strategy_variant_id.strip()
    normalized_rules = tuple(sorted({str(rule).strip() for rule in rule_ids if str(rule).strip()}))
    if not variant_id:
        raise ValueError("strategy_variant_id must not be empty")
    if not normalized_rules:
        raise ValueError("at least one rule_id is required")
    start_utc, end_utc = _bounds(trading_day, lookback_calendar_days)
    placeholders = ",".join("?" for _ in normalized_rules)

    connection = _read_connection(database_path)
    try:
        version_row = connection.execute(
            "SELECT COALESCE(MAX(version), 0) AS version FROM schema_migrations"
        ).fetchone()
        if version_row is None or int(version_row["version"]) < 2:
            raise JournalHistoryDataError("trading journal schema version 2 is required")
        run_rows = connection.execute(
            """
            SELECT run_id, run_type, started_at_utc, ended_at_utc,
                   parameters_json, metadata_json
            FROM strategy_runs
            WHERE variant_id=?
              AND status='completed'
              AND run_type IN ('backtest', 'shadow')
            ORDER BY COALESCE(ended_at_utc, started_at_utc), run_id
            """,
            (variant_id,),
        ).fetchall()
        selected_run, selection = _select_run(
            list(run_rows), requested_run_id=model_run_id
        )
        _validate_coverage(
            selected_run,
            window_start_day=trading_day - timedelta(days=lookback_calendar_days),
            trading_day=trading_day,
        )
        selected_run_id = str(selected_run["run_id"])
        rows = connection.execute(
            f"""
            SELECT t.trade_id, t.signal_key, t.status AS trade_status,
                   t.opened_at_utc, t.closed_at_utc, t.entry_price,
                   t.quantity, t.realized_pnl, t.metadata_json,
                   t.symbol, t.side, sr.run_type,
                   sd.primary_rule, sd.track
            FROM trades AS t
            JOIN strategy_runs AS sr ON sr.run_id=t.run_id
            JOIN signal_decisions AS sd
              ON sd.run_id=t.run_id AND sd.signal_key=t.signal_key
            WHERE t.run_id=?
              AND t.signal_key IS NOT NULL
            ORDER BY COALESCE(t.closed_at_utc, t.opened_at_utc),
                     t.signal_key, t.trade_id
            """,
            (selected_run_id,),
        ).fetchall()

        candidates: list[ModelHistoryTrade] = []
        open_candidates: list[ModelOpenPosition] = []
        for row in rows:
            metadata = _json_object(
                str(row["metadata_json"]),
                field="trade.metadata_json",
                run_id=selected_run_id,
            )
            status = str(row["trade_status"]).strip().lower()
            if _end_of_data_snapshot(status, metadata):
                opened_text = row["opened_at_utc"]
                if opened_text is None:
                    raise JournalHistoryDataError(
                        "open model position lacks opened_at_utc: "
                        f"run_id={selected_run_id} signal_key={row['signal_key']}"
                    )
                source_row = metadata.get("source_row")
                source = source_row if isinstance(source_row, dict) else {}
                open_candidates.append(
                    ModelOpenPosition(
                        signal_key=str(row["signal_key"]),
                        run_id=selected_run_id,
                        run_type=str(row["run_type"]),
                        symbol=str(row["symbol"]).upper(),
                        side=str(row["side"]),
                        rule_id=str(row["primary_rule"]),
                        track=str(row["track"]),
                        opened_at_utc=_utc_iso(_parse_utc(str(opened_text))),
                        entry_price=_optional_number(row["entry_price"]),
                        stop_price=_optional_number(source.get("stop")),
                        target_price=_optional_number(source.get("target")),
                        quantity=_optional_number(row["quantity"]),
                        source_status=(
                            "open"
                            if status == "open"
                            else "legacy_end_of_test"
                        ),
                    )
                )
                continue
            if status != "closed" or str(row["primary_rule"]) not in normalized_rules:
                continue
            closed_text = row["closed_at_utc"]
            if closed_text is None:
                raise JournalHistoryDataError(
                    "closed model trade lacks closed_at_utc: "
                    f"run_id={selected_run_id} signal_key={row['signal_key']}"
                )
            closed = _parse_utc(str(closed_text))
            if not start_utc <= closed < end_utc:
                continue
            pnl_raw = row["realized_pnl"]
            if pnl_raw is None or not math.isfinite(float(pnl_raw)):
                raise JournalHistoryDataError(
                    "closed model trade lacks finite realized_pnl: "
                    f"run_id={selected_run_id} signal_key={row['signal_key']}"
                )
            candidates.append(
                ModelHistoryTrade(
                    signal_key=str(row["signal_key"]),
                    run_id=selected_run_id,
                    run_type=str(row["run_type"]),
                    rule_id=str(row["primary_rule"]),
                    closed_at_utc=_utc_iso(closed),
                    realized_pnl=float(pnl_raw),
                )
            )
    except sqlite3.Error as exc:
        raise JournalHistoryDataError(f"cannot read model history from trading journal: {exc}") from exc
    finally:
        connection.close()

    selected: dict[str, ModelHistoryTrade] = {}
    duplicate_count = 0
    for candidate in candidates:
        previous = selected.get(candidate.signal_key)
        if previous is None:
            selected[candidate.signal_key] = candidate
            continue
        duplicate_count += 1
        if candidate != previous:
            raise JournalHistoryDataError(
                "conflicting duplicate signal_key in canonical model-history run: "
                f"run_id={selected_run_id} signal_key={candidate.signal_key}"
            )
    trades = tuple(sorted(selected.values(), key=lambda item: (item.closed_at_utc, item.signal_key)))
    open_selected: dict[str, ModelOpenPosition] = {}
    open_duplicate_count = 0
    for candidate in open_candidates:
        previous = open_selected.get(candidate.signal_key)
        if previous is None:
            open_selected[candidate.signal_key] = candidate
            continue
        open_duplicate_count += 1
        if candidate != previous:
            raise JournalHistoryDataError(
                "conflicting duplicate open signal_key in canonical model-history run: "
                f"run_id={selected_run_id} signal_key={candidate.signal_key}"
            )
    open_positions = tuple(
        sorted(
            open_selected.values(),
            key=lambda item: (item.symbol, item.track, item.opened_at_utc, item.signal_key),
        )
    )
    return ModelHistorySnapshot(
        database_path=str(Path(database_path)),
        strategy_variant_id=variant_id,
        selected_run_id=selected_run_id,
        run_selection=selection,
        window_start_utc=_utc_iso(start_utc),
        window_end_utc=_utc_iso(end_utc),
        trades=trades,
        source_candidate_count=len(candidates),
        duplicate_signal_count=duplicate_count,
        run_candidate_count=len(run_rows),
        data_quality_status=(
            "complete_empty_window" if not trades and not open_positions else "complete"
        ),
        open_positions=open_positions,
        open_position_source_count=len(open_candidates),
        open_position_duplicate_count=open_duplicate_count,
    )


def load_realized_history(
    database_path: str | Path,
    *,
    strategy_variant_id: str,
    rule_ids: Iterable[str],
    trading_day: date,
    lookback_calendar_days: int,
    model_run_id: str | None = None,
) -> ModelHistorySnapshot:
    """Compatibility wrapper for :func:`load_model_history`."""

    return load_model_history(
        database_path,
        strategy_variant_id=strategy_variant_id,
        rule_ids=rule_ids,
        trading_day=trading_day,
        lookback_calendar_days=lookback_calendar_days,
        model_run_id=model_run_id,
    )
