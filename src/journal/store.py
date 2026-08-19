from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import math
from pathlib import Path
import sqlite3
from typing import Iterator, Sequence

from .models import (
    CommissionRecord,
    DecisionFactorRecord,
    ExecutionRecord,
    OrderLegRecord,
    OrderBinding,
    OrderRecord,
    OrderStatusEventRecord,
    ReconciliationRecord,
    RiskEventRecord,
    RuleDefinitionRecord,
    RuleVersionRecord,
    SignalDecisionRecord,
    StrategyVariantRecord,
    StrategyVariantRuleRecord,
    StrategyRunRecord,
    TradeEventRecord,
    TradeLegRecord,
    TradeRecord,
    canonical_json,
    utc_iso,
)


DEFAULT_JOURNAL_PATH = Path(r"D:\Trading\journal\trading_journal.sqlite")
SCHEMA_VERSION = 2


class JournalError(RuntimeError):
    pass


class IdentityConflictError(JournalError):
    pass


_MIGRATION_1 = (
    """
    CREATE TABLE strategy_runs (
        run_id TEXT PRIMARY KEY,
        strategy_id TEXT NOT NULL,
        strategy_version TEXT NOT NULL,
        config_hash TEXT NOT NULL,
        run_type TEXT NOT NULL,
        environment TEXT NOT NULL,
        started_at_utc TEXT NOT NULL,
        ended_at_utc TEXT,
        status TEXT NOT NULL,
        parameters_json TEXT NOT NULL CHECK(json_valid(parameters_json)),
        metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json)),
        CHECK(substr(started_at_utc, -1) = 'Z'),
        CHECK(ended_at_utc IS NULL OR substr(ended_at_utc, -1) = 'Z')
    )
    """,
    """
    CREATE TABLE signal_decisions (
        decision_id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        signal_key TEXT NOT NULL,
        decision_key TEXT NOT NULL,
        signal_key_version TEXT NOT NULL,
        decision_stage TEXT NOT NULL,
        strategy_id TEXT NOT NULL,
        strategy_version TEXT NOT NULL,
        engine_version TEXT NOT NULL,
        config_hash TEXT NOT NULL,
        decision_time_utc TEXT NOT NULL,
        symbol TEXT NOT NULL,
        primary_rule TEXT NOT NULL,
        side TEXT NOT NULL,
        track TEXT NOT NULL,
        occurrence INTEGER NOT NULL CHECK(occurrence >= 0),
        status TEXT NOT NULL,
        trade_action TEXT NOT NULL,
        setup_state TEXT NOT NULL,
        primary_reason TEXT NOT NULL,
        context_json TEXT NOT NULL CHECK(json_valid(context_json)),
        raw_record_json TEXT NOT NULL CHECK(json_valid(raw_record_json)),
        UNIQUE(run_id, signal_key),
        UNIQUE(decision_id, run_id),
        FOREIGN KEY(run_id) REFERENCES strategy_runs(run_id) ON DELETE CASCADE,
        CHECK(substr(decision_time_utc, -1) = 'Z')
    )
    """,
    """
    CREATE TABLE decision_factors (
        factor_id TEXT PRIMARY KEY,
        decision_id TEXT NOT NULL,
        position INTEGER NOT NULL CHECK(position >= 0),
        factor_code TEXT NOT NULL,
        category TEXT NOT NULL,
        observed_value_json TEXT NOT NULL CHECK(json_valid(observed_value_json)),
        operator TEXT NOT NULL,
        threshold_json TEXT NOT NULL CHECK(json_valid(threshold_json)),
        passed INTEGER CHECK(passed IN (0, 1) OR passed IS NULL),
        is_veto INTEGER NOT NULL CHECK(is_veto IN (0, 1)),
        reason TEXT NOT NULL,
        as_of_time_utc TEXT NOT NULL,
        calculation_version TEXT NOT NULL,
        UNIQUE(decision_id, position),
        FOREIGN KEY(decision_id) REFERENCES signal_decisions(decision_id) ON DELETE CASCADE,
        CHECK(substr(as_of_time_utc, -1) = 'Z')
    )
    """,
    """
    CREATE TABLE trades (
        trade_id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        signal_key TEXT,
        decision_id TEXT,
        asset_class TEXT NOT NULL,
        symbol TEXT NOT NULL,
        side TEXT NOT NULL,
        status TEXT NOT NULL,
        opened_at_utc TEXT,
        closed_at_utc TEXT,
        quantity REAL,
        currency TEXT NOT NULL,
        entry_price REAL,
        exit_price REAL,
        realized_pnl REAL,
        metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json)),
        UNIQUE(run_id, signal_key),
        UNIQUE(trade_id, run_id),
        FOREIGN KEY(run_id) REFERENCES strategy_runs(run_id) ON DELETE CASCADE,
        FOREIGN KEY(decision_id, run_id) REFERENCES signal_decisions(decision_id, run_id),
        CHECK(opened_at_utc IS NULL OR substr(opened_at_utc, -1) = 'Z'),
        CHECK(closed_at_utc IS NULL OR substr(closed_at_utc, -1) = 'Z')
    )
    """,
    """
    CREATE TABLE trade_legs (
        leg_id TEXT PRIMARY KEY,
        trade_id TEXT NOT NULL,
        leg_index INTEGER NOT NULL CHECK(leg_index >= 0),
        asset_class TEXT NOT NULL,
        symbol TEXT NOT NULL,
        con_id INTEGER,
        local_symbol TEXT,
        exchange TEXT,
        currency TEXT NOT NULL,
        side TEXT NOT NULL,
        ratio INTEGER NOT NULL CHECK(ratio > 0),
        quantity REAL,
        expiry TEXT,
        strike REAL,
        option_right TEXT,
        multiplier REAL,
        open_price REAL,
        close_price REAL,
        metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json)),
        UNIQUE(trade_id, leg_index),
        FOREIGN KEY(trade_id) REFERENCES trades(trade_id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE orders (
        order_id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        trade_id TEXT,
        signal_key TEXT,
        parent_order_id TEXT,
        broker TEXT NOT NULL,
        broker_account TEXT,
        broker_perm_id INTEGER,
        broker_client_id INTEGER,
        broker_client_order_id INTEGER,
        broker_order_id TEXT,
        order_ref TEXT,
        order_type TEXT NOT NULL,
        action TEXT NOT NULL,
        quantity REAL NOT NULL CHECK(quantity > 0),
        limit_price REAL,
        stop_price REAL,
        time_in_force TEXT NOT NULL,
        transmit INTEGER NOT NULL CHECK(transmit IN (0, 1)),
        status TEXT NOT NULL,
        submitted_at_utc TEXT,
        updated_at_utc TEXT NOT NULL,
        metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json)),
        UNIQUE(run_id, broker, broker_order_id),
        FOREIGN KEY(run_id) REFERENCES strategy_runs(run_id) ON DELETE CASCADE,
        FOREIGN KEY(trade_id, run_id) REFERENCES trades(trade_id, run_id),
        FOREIGN KEY(parent_order_id) REFERENCES orders(order_id),
        CHECK(submitted_at_utc IS NULL OR substr(submitted_at_utc, -1) = 'Z'),
        CHECK(substr(updated_at_utc, -1) = 'Z')
    )
    """,
    """
    CREATE TABLE order_legs (
        order_leg_id TEXT PRIMARY KEY,
        order_id TEXT NOT NULL,
        leg_index INTEGER NOT NULL CHECK(leg_index >= 0),
        trade_leg_id TEXT,
        con_id INTEGER,
        symbol TEXT NOT NULL,
        action TEXT NOT NULL,
        ratio INTEGER NOT NULL CHECK(ratio > 0),
        exchange TEXT,
        open_close TEXT,
        metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json)),
        UNIQUE(order_id, leg_index),
        FOREIGN KEY(order_id) REFERENCES orders(order_id) ON DELETE CASCADE,
        FOREIGN KEY(trade_leg_id) REFERENCES trade_legs(leg_id)
    )
    """,
    """
    CREATE TABLE order_status_events (
        event_id TEXT PRIMARY KEY,
        order_id TEXT NOT NULL,
        status TEXT NOT NULL,
        occurred_at_utc TEXT NOT NULL,
        filled_quantity REAL,
        remaining_quantity REAL,
        average_fill_price REAL,
        broker_message TEXT,
        payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
        FOREIGN KEY(order_id) REFERENCES orders(order_id) ON DELETE CASCADE,
        CHECK(substr(occurred_at_utc, -1) = 'Z')
    )
    """,
    """
    CREATE TABLE executions (
        execution_id TEXT PRIMARY KEY,
        order_id TEXT NOT NULL,
        trade_id TEXT,
        broker_execution_id TEXT NOT NULL,
        executed_at_utc TEXT NOT NULL,
        side TEXT NOT NULL,
        quantity REAL NOT NULL CHECK(quantity > 0),
        price REAL NOT NULL,
        exchange TEXT,
        liquidity TEXT,
        metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json)),
        UNIQUE(order_id, broker_execution_id),
        FOREIGN KEY(order_id) REFERENCES orders(order_id) ON DELETE CASCADE,
        FOREIGN KEY(trade_id) REFERENCES trades(trade_id),
        CHECK(substr(executed_at_utc, -1) = 'Z')
    )
    """,
    """
    CREATE TABLE commissions (
        commission_id TEXT PRIMARY KEY,
        execution_id TEXT NOT NULL,
        currency TEXT NOT NULL,
        amount REAL NOT NULL,
        realized_pnl REAL,
        reported_at_utc TEXT NOT NULL,
        metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json)),
        FOREIGN KEY(execution_id) REFERENCES executions(execution_id) ON DELETE CASCADE,
        CHECK(substr(reported_at_utc, -1) = 'Z')
    )
    """,
    """
    CREATE TABLE trade_events (
        event_id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        trade_id TEXT,
        order_id TEXT,
        signal_key TEXT,
        event_type TEXT NOT NULL,
        occurred_at_utc TEXT NOT NULL,
        payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
        FOREIGN KEY(run_id) REFERENCES strategy_runs(run_id) ON DELETE CASCADE,
        FOREIGN KEY(trade_id) REFERENCES trades(trade_id),
        FOREIGN KEY(order_id) REFERENCES orders(order_id),
        CHECK(substr(occurred_at_utc, -1) = 'Z')
    )
    """,
    """
    CREATE TABLE risk_events (
        risk_event_id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        trade_id TEXT,
        decision_id TEXT,
        signal_key TEXT,
        event_type TEXT NOT NULL,
        status TEXT NOT NULL,
        occurred_at_utc TEXT NOT NULL,
        risk_amount REAL,
        risk_currency TEXT,
        payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
        FOREIGN KEY(run_id) REFERENCES strategy_runs(run_id) ON DELETE CASCADE,
        FOREIGN KEY(trade_id) REFERENCES trades(trade_id),
        FOREIGN KEY(decision_id) REFERENCES signal_decisions(decision_id),
        CHECK(substr(occurred_at_utc, -1) = 'Z')
    )
    """,
    """
    CREATE TABLE reconciliation (
        reconciliation_id TEXT PRIMARY KEY,
        live_trade_id TEXT NOT NULL,
        reference_trade_id TEXT NOT NULL,
        signal_key TEXT NOT NULL,
        status TEXT NOT NULL,
        matched_at_utc TEXT NOT NULL,
        price_slippage REAL,
        quantity_difference REAL,
        pnl_difference REAL,
        notes TEXT,
        details_json TEXT NOT NULL CHECK(json_valid(details_json)),
        UNIQUE(live_trade_id, reference_trade_id),
        FOREIGN KEY(live_trade_id) REFERENCES trades(trade_id),
        FOREIGN KEY(reference_trade_id) REFERENCES trades(trade_id),
        CHECK(live_trade_id <> reference_trade_id),
        CHECK(substr(matched_at_utc, -1) = 'Z')
    )
    """,
    "CREATE INDEX idx_decisions_time ON signal_decisions(decision_time_utc)",
    "CREATE INDEX idx_trades_signal ON trades(run_id, signal_key)",
    "CREATE INDEX idx_orders_trade ON orders(trade_id)",
    """
    CREATE UNIQUE INDEX uq_orders_broker_perm
    ON orders(broker, broker_account, broker_perm_id)
    WHERE broker_account IS NOT NULL AND broker_perm_id IS NOT NULL AND broker_perm_id > 0
    """,
    """
    CREATE UNIQUE INDEX uq_orders_broker_session
    ON orders(run_id, broker, broker_account, broker_client_id, broker_client_order_id)
    WHERE broker_account IS NOT NULL
      AND broker_client_id IS NOT NULL
      AND broker_client_order_id IS NOT NULL
      AND broker_client_order_id > 0
    """,
    "CREATE INDEX idx_executions_trade ON executions(trade_id)",
    "CREATE INDEX idx_trade_events_time ON trade_events(run_id, occurred_at_utc)",
    "CREATE INDEX idx_risk_events_time ON risk_events(run_id, occurred_at_utc)",
)


_MIGRATION_2 = (
    """
    CREATE TABLE rules (
        rule_id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        description TEXT NOT NULL,
        metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json))
    )
    """,
    """
    CREATE TABLE rule_versions (
        rule_version_id TEXT PRIMARY KEY,
        rule_id TEXT NOT NULL,
        semver TEXT NOT NULL,
        logic_hash TEXT NOT NULL,
        code_hash TEXT NOT NULL,
        engine_version TEXT NOT NULL,
        created_at_utc TEXT NOT NULL,
        parameters_json TEXT NOT NULL CHECK(json_valid(parameters_json)),
        metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json)),
        UNIQUE(rule_id, semver),
        UNIQUE(rule_version_id, rule_id),
        FOREIGN KEY(rule_id) REFERENCES rules(rule_id),
        CHECK(substr(created_at_utc, -1) = 'Z')
    )
    """,
    """
    CREATE TABLE strategy_variants (
        variant_id TEXT PRIMARY KEY,
        strategy_id TEXT NOT NULL,
        variant_version TEXT NOT NULL,
        name TEXT NOT NULL,
        engine_version TEXT NOT NULL,
        money_management_version TEXT NOT NULL,
        config_hash TEXT NOT NULL,
        data_revision TEXT NOT NULL,
        fee_model TEXT NOT NULL,
        slippage_model TEXT NOT NULL,
        created_at_utc TEXT NOT NULL,
        composition_hash TEXT NOT NULL,
        metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json)),
        UNIQUE(strategy_id, variant_version),
        CHECK(substr(created_at_utc, -1) = 'Z')
    )
    """,
    """
    CREATE TABLE strategy_variant_rules (
        variant_id TEXT NOT NULL,
        rule_id TEXT NOT NULL,
        rule_version_id TEXT NOT NULL,
        position INTEGER NOT NULL CHECK(position >= 0),
        role TEXT NOT NULL,
        enabled INTEGER NOT NULL CHECK(enabled IN (0, 1)),
        parameters_json TEXT NOT NULL CHECK(json_valid(parameters_json)),
        PRIMARY KEY(variant_id, rule_id),
        UNIQUE(variant_id, position),
        FOREIGN KEY(variant_id) REFERENCES strategy_variants(variant_id) ON DELETE CASCADE,
        FOREIGN KEY(rule_version_id, rule_id)
            REFERENCES rule_versions(rule_version_id, rule_id)
    )
    """,
    "ALTER TABLE strategy_runs ADD COLUMN variant_id TEXT REFERENCES strategy_variants(variant_id)",
    "ALTER TABLE signal_decisions ADD COLUMN rule_id TEXT REFERENCES rules(rule_id)",
    "ALTER TABLE signal_decisions ADD COLUMN rule_version_id TEXT REFERENCES rule_versions(rule_version_id)",
    "CREATE INDEX idx_runs_variant ON strategy_runs(variant_id)",
    "CREATE INDEX idx_decisions_rule_version ON signal_decisions(rule_version_id)",
)


class JournalStore:
    def __init__(
        self,
        database_path: str | Path = DEFAULT_JOURNAL_PATH,
        *,
        busy_timeout_ms: int = 5_000,
    ) -> None:
        if busy_timeout_ms <= 0:
            raise ValueError("busy_timeout_ms must be positive")
        self.path = Path(database_path)
        if str(database_path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(
            str(database_path),
            isolation_level=None,
            timeout=busy_timeout_ms / 1000,
        )
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute(f"PRAGMA busy_timeout = {int(busy_timeout_ms)}")
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._connection.execute("PRAGMA synchronous = NORMAL")
        self._migrate()

    def __enter__(self) -> JournalStore:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def close(self) -> None:
        self._connection.close()

    @property
    def schema_version(self) -> int:
        row = self._connection.execute(
            "SELECT COALESCE(MAX(version), 0) AS version FROM schema_migrations"
        ).fetchone()
        return int(row["version"])

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Open an atomic write transaction; nested store calls join it."""
        owns_transaction = not self._connection.in_transaction
        if owns_transaction:
            self._connection.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            if owns_transaction and self._connection.in_transaction:
                self._connection.rollback()
            raise
        else:
            if owns_transaction and self._connection.in_transaction:
                self._connection.commit()

    def _migrate(self) -> None:
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at_utc TEXT NOT NULL
            )
            """
        )
        current = self.schema_version
        if current > SCHEMA_VERSION:
            raise JournalError(
                f"database schema {current} is newer than supported {SCHEMA_VERSION}"
            )
        if current < 1:
            with self.transaction():
                for statement in _MIGRATION_1:
                    self._connection.execute(statement)
                self._connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at_utc) VALUES (?, ?)",
                    (1, utc_iso("2026-07-15T00:00:00Z")),
                )
        if current < 2:
            with self.transaction():
                for statement in _MIGRATION_2:
                    self._connection.execute(statement)
                self._connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at_utc) VALUES (?, ?)",
                    (2, utc_iso("2026-07-15T00:00:00Z")),
                )

    @staticmethod
    def _required(value: str, field: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError(f"{field} must not be empty")
        return normalized

    @staticmethod
    def _finite(value: float | None, field: str) -> float | None:
        if value is None:
            return None
        normalized = float(value)
        if not math.isfinite(normalized):
            raise ValueError(f"{field} must be finite")
        return normalized

    @classmethod
    def _positive(cls, value: float, field: str) -> float:
        normalized = cls._finite(value, field)
        if normalized is None or normalized <= 0:
            raise ValueError(f"{field} must be positive")
        return normalized

    def _identity_row(self, table: str, id_column: str, external_id: str) -> sqlite3.Row | None:
        allowed = {
            ("signal_decisions", "decision_id"),
            ("trades", "trade_id"),
            ("orders", "order_id"),
        }
        if (table, id_column) not in allowed:
            raise ValueError("unsupported identity lookup")
        return self._connection.execute(
            f"SELECT * FROM {table} WHERE {id_column} = ?",
            (external_id,),
        ).fetchone()

    def register_rule(self, record: RuleDefinitionRecord) -> str:
        """Register one immutable stable business rule identity."""
        values = (
            self._required(record.rule_id, "rule_id"),
            self._required(record.name, "rule.name"),
            self._required(record.description, "rule.description"),
            canonical_json(record.metadata),
        )
        with self.transaction():
            existing = self._connection.execute(
                "SELECT rule_id, name, description, metadata_json FROM rules WHERE rule_id=?",
                (values[0],),
            ).fetchone()
            if existing is not None:
                if tuple(existing) != values:
                    raise IdentityConflictError("rule_id cannot change its definition")
                return values[0]
            self._connection.execute(
                "INSERT INTO rules(rule_id, name, description, metadata_json) VALUES (?, ?, ?, ?)",
                values,
            )
        return values[0]

    def register_rule_version(self, record: RuleVersionRecord) -> str:
        """Register one immutable implementation version of a stable rule."""
        values = (
            self._required(record.rule_version_id, "rule_version_id"),
            self._required(record.rule_id, "rule_id"),
            self._required(record.semver, "rule.semver"),
            self._required(record.logic_hash, "rule.logic_hash"),
            self._required(record.code_hash, "rule.code_hash"),
            self._required(record.engine_version, "rule.engine_version"),
            utc_iso(record.created_at_utc),
            canonical_json(record.parameters),
            canonical_json(record.metadata),
        )
        with self.transaction():
            if self.get_rule(values[1]) is None:
                raise ValueError("rule version requires a registered rule_id")
            existing = self._connection.execute(
                "SELECT * FROM rule_versions WHERE rule_version_id=?",
                (values[0],),
            ).fetchone()
            if existing is not None:
                stored = tuple(existing[column] for column in (
                    "rule_version_id", "rule_id", "semver", "logic_hash", "code_hash",
                    "engine_version", "created_at_utc", "parameters_json", "metadata_json",
                ))
                if stored != values:
                    raise IdentityConflictError(
                        "rule_version_id cannot change implementation semantics"
                    )
                return values[0]
            semantic_version = self._connection.execute(
                "SELECT rule_version_id FROM rule_versions WHERE rule_id=? AND semver=?",
                (values[1], values[2]),
            ).fetchone()
            if semantic_version is not None:
                raise IdentityConflictError(
                    "rule_id/semver already belongs to another rule_version_id"
                )
            self._connection.execute(
                """
                INSERT INTO rule_versions(
                    rule_version_id, rule_id, semver, logic_hash, code_hash,
                    engine_version, created_at_utc, parameters_json, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                values,
            )
        return values[0]

    @staticmethod
    def _variant_composition(
        variant_id: str,
        rules: Sequence[StrategyVariantRuleRecord],
    ) -> tuple[str, list[tuple[object, ...]]]:
        rows: list[tuple[object, ...]] = []
        positions: set[int] = set()
        rule_ids: set[str] = set()
        for member in rules:
            if member.variant_id.strip() != variant_id:
                raise ValueError("variant rule variant_id does not match its variant")
            position = int(member.position)
            if position < 0:
                raise ValueError("variant rule position must be non-negative")
            rule_id = member.rule_id.strip()
            rule_version_id = member.rule_version_id.strip()
            role = member.role.strip()
            if not rule_id or not rule_version_id or not role:
                raise ValueError("variant rule identity and role must not be empty")
            if position in positions or rule_id in rule_ids:
                raise ValueError("variant rules require unique positions and rule_ids")
            positions.add(position)
            rule_ids.add(rule_id)
            rows.append((
                variant_id, rule_id, rule_version_id, position, role,
                int(bool(member.enabled)), canonical_json(member.parameters),
            ))
        rows.sort(key=lambda row: int(row[3]))
        identity = [list(row[1:]) for row in rows]
        digest = hashlib.sha256(canonical_json(identity).encode("utf-8")).hexdigest()
        return digest, rows

    def register_strategy_variant(
        self,
        record: StrategyVariantRecord,
        rules: Sequence[StrategyVariantRuleRecord] = (),
    ) -> str:
        """Atomically register an immutable variant and its complete rule composition."""
        variant_id = self._required(record.variant_id, "variant_id")
        composition_hash, memberships = self._variant_composition(variant_id, rules)
        values = (
            variant_id,
            self._required(record.strategy_id, "variant.strategy_id"),
            self._required(record.variant_version, "variant.variant_version"),
            self._required(record.name, "variant.name"),
            self._required(record.engine_version, "variant.engine_version"),
            self._required(
                record.money_management_version, "variant.money_management_version"
            ),
            self._required(record.config_hash, "variant.config_hash"),
            self._required(record.data_revision, "variant.data_revision"),
            self._required(record.fee_model, "variant.fee_model"),
            self._required(record.slippage_model, "variant.slippage_model"),
            utc_iso(record.created_at_utc),
            composition_hash,
            canonical_json(record.metadata),
        )
        with self.transaction():
            for member in memberships:
                version = self._connection.execute(
                    "SELECT rule_id FROM rule_versions WHERE rule_version_id=?",
                    (member[2],),
                ).fetchone()
                if version is None or version["rule_id"] != member[1]:
                    raise ValueError("variant membership requires a matching rule version")
            existing = self._connection.execute(
                "SELECT * FROM strategy_variants WHERE variant_id=?",
                (variant_id,),
            ).fetchone()
            if existing is not None:
                stored = tuple(existing[column] for column in (
                    "variant_id", "strategy_id", "variant_version", "name",
                    "engine_version", "money_management_version", "config_hash",
                    "data_revision", "fee_model", "slippage_model", "created_at_utc",
                    "composition_hash", "metadata_json",
                ))
                if stored != values:
                    raise IdentityConflictError(
                        "variant_id cannot change strategy or provenance semantics"
                    )
                stored_memberships = self._connection.execute(
                    """
                    SELECT variant_id, rule_id, rule_version_id, position, role,
                           enabled, parameters_json
                    FROM strategy_variant_rules WHERE variant_id=? ORDER BY position
                    """,
                    (variant_id,),
                ).fetchall()
                if [tuple(row) for row in stored_memberships] != memberships:
                    raise IdentityConflictError(
                        "variant_id cannot change its rule composition"
                    )
                return variant_id
            version_owner = self._connection.execute(
                "SELECT variant_id FROM strategy_variants WHERE strategy_id=? AND variant_version=?",
                (values[1], values[2]),
            ).fetchone()
            if version_owner is not None:
                raise IdentityConflictError(
                    "strategy_id/variant_version already belongs to another variant_id"
                )
            self._connection.execute(
                """
                INSERT INTO strategy_variants(
                    variant_id, strategy_id, variant_version, name, engine_version,
                    money_management_version, config_hash, data_revision, fee_model,
                    slippage_model, created_at_utc, composition_hash, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                values,
            )
            self._connection.executemany(
                """
                INSERT INTO strategy_variant_rules(
                    variant_id, rule_id, rule_version_id, position, role,
                    enabled, parameters_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                memberships,
            )
        return variant_id

    def get_rule(self, rule_id: str) -> dict[str, object] | None:
        row = self._connection.execute(
            "SELECT * FROM rules WHERE rule_id=?", (rule_id,)
        ).fetchone()
        return dict(row) if row else None

    def get_rule_version(self, rule_version_id: str) -> dict[str, object] | None:
        row = self._connection.execute(
            "SELECT * FROM rule_versions WHERE rule_version_id=?", (rule_version_id,)
        ).fetchone()
        return dict(row) if row else None

    def get_strategy_variant(self, variant_id: str) -> dict[str, object] | None:
        row = self._connection.execute(
            "SELECT * FROM strategy_variants WHERE variant_id=?", (variant_id,)
        ).fetchone()
        return dict(row) if row else None

    def list_strategy_variant_rules(self, variant_id: str) -> list[dict[str, object]]:
        rows = self._connection.execute(
            "SELECT * FROM strategy_variant_rules WHERE variant_id=? ORDER BY position",
            (variant_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def start_run(self, record: StrategyRunRecord) -> str:
        run_id = self._required(record.run_id, "run_id")
        strategy_id = self._required(record.strategy_id, "strategy_id")
        run_type = self._required(record.run_type, "run_type")
        environment = self._required(record.environment, "environment")
        variant_id = (
            self._required(record.variant_id, "variant_id")
            if record.variant_id is not None
            else None
        )
        values = (
            run_id,
            strategy_id,
            self._required(record.strategy_version, "strategy_version"),
            self._required(record.config_hash, "config_hash"),
            run_type,
            environment,
            utc_iso(record.started_at_utc),
            utc_iso(record.ended_at_utc) if record.ended_at_utc is not None else None,
            self._required(record.status, "status"),
            canonical_json(record.parameters),
            canonical_json(record.metadata),
            variant_id,
        )
        with self.transaction():
            if variant_id is not None:
                variant = self._connection.execute(
                    "SELECT strategy_id FROM strategy_variants WHERE variant_id=?",
                    (variant_id,),
                ).fetchone()
                if variant is None:
                    raise ValueError("run variant_id must reference a registered strategy variant")
                if variant["strategy_id"] != strategy_id:
                    raise ValueError("run strategy_id must match its strategy variant")
            existing = self._connection.execute(
                """
                SELECT strategy_id, strategy_version, config_hash, run_type,
                       environment, started_at_utc, variant_id
                FROM strategy_runs WHERE run_id=?
                """,
                (run_id,),
            ).fetchone()
            if existing and (
                existing["strategy_id"],
                existing["strategy_version"],
                existing["config_hash"],
                existing["run_type"],
                existing["environment"],
                existing["started_at_utc"],
                existing["variant_id"],
            ) != (
                strategy_id,
                record.strategy_version.strip(),
                record.config_hash.strip(),
                run_type,
                environment,
                utc_iso(record.started_at_utc),
                variant_id,
            ):
                raise IdentityConflictError(
                    "run_id cannot be reassigned or change immutable strategy/config/run identity"
                )
            self._connection.execute(
                """
                INSERT INTO strategy_runs (
                    run_id, strategy_id, strategy_version, config_hash, run_type,
                    environment, started_at_utc, ended_at_utc, status,
                    parameters_json, metadata_json, variant_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    strategy_id=excluded.strategy_id,
                    strategy_version=excluded.strategy_version,
                    config_hash=excluded.config_hash,
                    run_type=excluded.run_type,
                    environment=excluded.environment,
                    started_at_utc=excluded.started_at_utc,
                    ended_at_utc=excluded.ended_at_utc,
                    status=excluded.status,
                    parameters_json=excluded.parameters_json,
                    metadata_json=excluded.metadata_json,
                    variant_id=excluded.variant_id
                """,
                values,
            )
        return run_id

    def upsert_decision(
        self,
        record: SignalDecisionRecord,
        factors: Sequence[DecisionFactorRecord] = (),
    ) -> str:
        decision_id = self._required(record.decision_id, "decision_id")
        run_id = self._required(record.run_id, "run_id")
        signal_key = self._required(record.signal_key, "signal_key")
        if (record.rule_id is None) != (record.rule_version_id is None):
            raise ValueError("decision rule_id and rule_version_id must be provided together")
        rule_id = (
            self._required(record.rule_id, "rule_id")
            if record.rule_id is not None
            else None
        )
        rule_version_id = (
            self._required(record.rule_version_id, "rule_version_id")
            if record.rule_version_id is not None
            else None
        )
        with self.transaction():
            if rule_version_id is not None:
                rule_version = self._connection.execute(
                    "SELECT rule_id FROM rule_versions WHERE rule_version_id=?",
                    (rule_version_id,),
                ).fetchone()
                if rule_version is None or rule_version["rule_id"] != rule_id:
                    raise ValueError("decision rule_id must match its registered rule version")
            existing = self._identity_row("signal_decisions", "decision_id", decision_id)
            if existing and (existing["run_id"], existing["signal_key"]) != (run_id, signal_key):
                raise IdentityConflictError("decision_id already belongs to another run/signal")
            if existing and existing["rule_version_id"] is not None and (
                existing["rule_id"], existing["rule_version_id"]
            ) != (rule_id, rule_version_id):
                raise IdentityConflictError(
                    "decision_id cannot change its registered rule identity"
                )
            if (
                existing
                and existing["decision_stage"] == "live_final"
                and record.decision_stage != "live_final"
            ):
                return decision_id
            pair = self._connection.execute(
                "SELECT decision_id FROM signal_decisions WHERE run_id=? AND signal_key=?",
                (run_id, signal_key),
            ).fetchone()
            if pair and pair["decision_id"] != decision_id:
                raise IdentityConflictError("run_id/signal_key already has another decision_id")
            self._connection.execute(
                """
                INSERT INTO signal_decisions (
                    decision_id, run_id, signal_key, decision_key, signal_key_version,
                    decision_stage, strategy_id, strategy_version, engine_version,
                    config_hash, decision_time_utc, symbol, primary_rule, side, track,
                    occurrence, status, trade_action, setup_state, primary_reason,
                    context_json, raw_record_json, rule_id, rule_version_id
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                ON CONFLICT(decision_id) DO UPDATE SET
                    decision_key=excluded.decision_key,
                    signal_key_version=excluded.signal_key_version,
                    decision_stage=excluded.decision_stage,
                    strategy_id=excluded.strategy_id,
                    strategy_version=excluded.strategy_version,
                    engine_version=excluded.engine_version,
                    config_hash=excluded.config_hash,
                    decision_time_utc=excluded.decision_time_utc,
                    symbol=excluded.symbol,
                    primary_rule=excluded.primary_rule,
                    side=excluded.side,
                    track=excluded.track,
                    occurrence=excluded.occurrence,
                    status=excluded.status,
                    trade_action=excluded.trade_action,
                    setup_state=excluded.setup_state,
                    primary_reason=excluded.primary_reason,
                    context_json=excluded.context_json,
                    raw_record_json=excluded.raw_record_json,
                    rule_id=excluded.rule_id,
                    rule_version_id=excluded.rule_version_id
                """,
                (
                    decision_id, run_id, signal_key,
                    self._required(record.decision_key, "decision_key"),
                    self._required(record.signal_key_version, "signal_key_version"),
                    self._required(record.decision_stage, "decision_stage"),
                    self._required(record.strategy_id, "strategy_id"),
                    self._required(record.strategy_version, "strategy_version"),
                    self._required(record.engine_version, "engine_version"),
                    self._required(record.config_hash, "config_hash"),
                    utc_iso(record.decision_time_utc),
                    self._required(record.symbol, "symbol"),
                    self._required(record.primary_rule, "primary_rule"),
                    self._required(record.side, "side"),
                    self._required(record.track, "track"),
                    int(record.occurrence), self._required(record.status, "status"),
                    self._required(record.trade_action, "trade_action"),
                    self._required(record.setup_state, "setup_state"),
                    self._required(record.primary_reason, "primary_reason"),
                    canonical_json(record.context), canonical_json(record.raw_record),
                    rule_id, rule_version_id,
                ),
            )
            self._connection.execute(
                "DELETE FROM decision_factors WHERE decision_id=?", (decision_id,)
            )
            for factor in factors:
                if factor.decision_id != decision_id:
                    raise ValueError("factor decision_id does not match its decision")
                self._connection.execute(
                    """
                    INSERT INTO decision_factors VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                    )
                    """,
                    (
                        self._required(factor.factor_id, "factor_id"), decision_id,
                        int(factor.position), self._required(factor.factor_code, "factor_code"),
                        self._required(factor.category, "category"),
                        canonical_json(factor.observed_value),
                        self._required(factor.operator, "operator"),
                        canonical_json(factor.threshold),
                        None if factor.passed is None else int(factor.passed),
                        int(factor.is_veto), self._required(factor.reason, "reason"),
                        utc_iso(factor.as_of_time_utc),
                        self._required(factor.calculation_version, "calculation_version"),
                    ),
                )
        return decision_id

    def create_trade(
        self,
        record: TradeRecord,
        legs: Sequence[TradeLegRecord] | None = None,
    ) -> str:
        trade_id = self._required(record.trade_id, "trade_id")
        run_id = self._required(record.run_id, "run_id")
        with self.transaction():
            existing = self._identity_row("trades", "trade_id", trade_id)
            if existing and existing["run_id"] != run_id:
                raise IdentityConflictError("trade_id already belongs to another run")
            if record.decision_id is not None and record.signal_key is None:
                raise ValueError("trade decision_id requires a signal_key")
            if record.decision_id is not None:
                decision = self._connection.execute(
                    "SELECT run_id, signal_key FROM signal_decisions WHERE decision_id=?",
                    (record.decision_id,),
                ).fetchone()
                if decision is None or (
                    decision["run_id"], decision["signal_key"]
                ) != (run_id, record.signal_key):
                    raise ValueError("trade run/signal must match its decision")
            self._connection.execute(
                """
                INSERT INTO trades VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(trade_id) DO UPDATE SET
                    signal_key=excluded.signal_key,
                    decision_id=excluded.decision_id,
                    asset_class=excluded.asset_class,
                    symbol=excluded.symbol,
                    side=excluded.side,
                    status=excluded.status,
                    opened_at_utc=excluded.opened_at_utc,
                    closed_at_utc=excluded.closed_at_utc,
                    quantity=excluded.quantity,
                    currency=excluded.currency,
                    entry_price=excluded.entry_price,
                    exit_price=excluded.exit_price,
                    realized_pnl=excluded.realized_pnl,
                    metadata_json=excluded.metadata_json
                """,
                (
                    trade_id, run_id, record.signal_key, record.decision_id,
                    self._required(record.asset_class, "asset_class"),
                    self._required(record.symbol, "symbol"),
                    self._required(record.side, "side"),
                    self._required(record.status, "status"),
                    utc_iso(record.opened_at_utc) if record.opened_at_utc is not None else None,
                    utc_iso(record.closed_at_utc) if record.closed_at_utc is not None else None,
                    self._finite(record.quantity, "trade.quantity"),
                    self._required(record.currency, "currency"),
                    self._finite(record.entry_price, "trade.entry_price"),
                    self._finite(record.exit_price, "trade.exit_price"),
                    self._finite(record.realized_pnl, "trade.realized_pnl"),
                    canonical_json(record.metadata),
                ),
            )
            if legs is None:
                return trade_id
            leg_ids: list[str] = []
            for leg in legs:
                if leg.trade_id != trade_id:
                    raise ValueError("leg trade_id does not match its trade")
                leg_id = self._required(leg.leg_id, "leg_id")
                leg_ids.append(leg_id)
                self._connection.execute(
                    """
                    INSERT INTO trade_legs VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                    )
                    ON CONFLICT(leg_id) DO UPDATE SET
                        leg_index=excluded.leg_index,
                        asset_class=excluded.asset_class,
                        symbol=excluded.symbol,
                        con_id=excluded.con_id,
                        local_symbol=excluded.local_symbol,
                        exchange=excluded.exchange,
                        currency=excluded.currency,
                        side=excluded.side,
                        ratio=excluded.ratio,
                        quantity=excluded.quantity,
                        expiry=excluded.expiry,
                        strike=excluded.strike,
                        option_right=excluded.option_right,
                        multiplier=excluded.multiplier,
                        open_price=excluded.open_price,
                        close_price=excluded.close_price,
                        metadata_json=excluded.metadata_json
                    """,
                    (
                        leg_id, trade_id, int(leg.leg_index),
                        self._required(leg.asset_class, "asset_class"),
                        self._required(leg.symbol, "symbol"), leg.con_id, leg.local_symbol,
                        leg.exchange, self._required(leg.currency, "currency"),
                        self._required(leg.side, "side"), int(leg.ratio),
                        self._finite(leg.quantity, "trade_leg.quantity"),
                        leg.expiry, self._finite(leg.strike, "trade_leg.strike"),
                        leg.option_right,
                        self._finite(leg.multiplier, "trade_leg.multiplier"),
                        self._finite(leg.open_price, "trade_leg.open_price"),
                        self._finite(leg.close_price, "trade_leg.close_price"),
                        canonical_json(leg.metadata),
                    ),
                )
            if leg_ids:
                placeholders = ",".join("?" for _ in leg_ids)
                self._connection.execute(
                    f"DELETE FROM trade_legs WHERE trade_id=? AND leg_id NOT IN ({placeholders})",
                    (trade_id, *leg_ids),
                )
            else:
                self._connection.execute("DELETE FROM trade_legs WHERE trade_id=?", (trade_id,))
        return trade_id

    def upsert_order(
        self,
        record: OrderRecord,
        legs: Sequence[OrderLegRecord] | None = None,
    ) -> str:
        order_id = self._required(record.order_id, "order_id")
        run_id = self._required(record.run_id, "run_id")
        broker_account = record.metadata.get("account")
        broker_perm_id = record.metadata.get("perm_id")
        broker_client_id = record.metadata.get("client_id")
        broker_client_order_id = record.metadata.get("order_id")
        with self.transaction():
            existing = self._identity_row("orders", "order_id", order_id)
            if existing and existing["run_id"] != run_id:
                raise IdentityConflictError("order_id already belongs to another run")
            if record.trade_id is not None:
                trade = self._connection.execute(
                    "SELECT run_id, signal_key FROM trades WHERE trade_id=?",
                    (record.trade_id,),
                ).fetchone()
                if trade is None or (trade["run_id"], trade["signal_key"]) != (
                    run_id, record.signal_key
                ):
                    raise ValueError("order run/signal must match its trade")
            self._connection.execute(
                """
                INSERT INTO orders VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                ON CONFLICT(order_id) DO UPDATE SET
                    trade_id=excluded.trade_id,
                    signal_key=excluded.signal_key,
                    parent_order_id=COALESCE(excluded.parent_order_id, orders.parent_order_id),
                    broker=excluded.broker,
                    broker_account=excluded.broker_account,
                    broker_perm_id=excluded.broker_perm_id,
                    broker_client_id=excluded.broker_client_id,
                    broker_client_order_id=excluded.broker_client_order_id,
                    broker_order_id=excluded.broker_order_id,
                    order_ref=excluded.order_ref,
                    order_type=excluded.order_type,
                    action=excluded.action,
                    quantity=excluded.quantity,
                    limit_price=excluded.limit_price,
                    stop_price=excluded.stop_price,
                    time_in_force=excluded.time_in_force,
                    transmit=excluded.transmit,
                    status=excluded.status,
                    submitted_at_utc=excluded.submitted_at_utc,
                    updated_at_utc=excluded.updated_at_utc,
                    metadata_json=excluded.metadata_json
                """,
                (
                    order_id, run_id, record.trade_id, record.signal_key,
                    record.parent_order_id, self._required(record.broker, "broker"),
                    str(broker_account) if broker_account not in {None, ""} else None,
                    int(broker_perm_id) if broker_perm_id not in {None, ""} else None,
                    int(broker_client_id) if broker_client_id not in {None, ""} else None,
                    int(broker_client_order_id)
                    if broker_client_order_id not in {None, ""} else None,
                    record.broker_order_id, record.order_ref,
                    self._required(record.order_type, "order_type"),
                    self._required(record.action, "action"),
                    self._positive(record.quantity, "order.quantity"),
                    self._finite(record.limit_price, "order.limit_price"),
                    self._finite(record.stop_price, "order.stop_price"),
                    self._required(record.time_in_force, "time_in_force"),
                    int(record.transmit), self._required(record.status, "status"),
                    utc_iso(record.submitted_at_utc) if record.submitted_at_utc is not None else None,
                    utc_iso(record.updated_at_utc), canonical_json(record.metadata),
                ),
            )
            if legs is None:
                return order_id
            self._connection.execute("DELETE FROM order_legs WHERE order_id=?", (order_id,))
            for leg in legs:
                if leg.order_id != order_id:
                    raise ValueError("order leg order_id does not match its order")
                self._connection.execute(
                    "INSERT INTO order_legs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        self._required(leg.order_leg_id, "order_leg_id"), order_id,
                        int(leg.leg_index), leg.trade_leg_id, leg.con_id,
                        self._required(leg.symbol, "symbol"),
                        self._required(leg.action, "action"), int(leg.ratio),
                        leg.exchange, leg.open_close, canonical_json(leg.metadata),
                    ),
                )
        return order_id

    def find_order_binding(
        self,
        *,
        run_id: str,
        broker: str,
        account: str,
        perm_id: int | None,
        client_id: int | None,
        client_order_id: int | None,
    ) -> OrderBinding | None:
        """Resolve durable broker identity, preferring account + permanent id."""
        if perm_id is not None and perm_id > 0:
            row = self._connection.execute(
                """
                SELECT order_id, run_id, trade_id, signal_key FROM orders
                WHERE broker=? AND broker_account=? AND broker_perm_id=?
                """,
                (broker, account, int(perm_id)),
            ).fetchone()
            if row:
                return OrderBinding(
                    order_id=str(row["order_id"]),
                    run_id=str(row["run_id"]),
                    trade_id=row["trade_id"],
                    signal_key=row["signal_key"],
                )
        if (
            client_id is not None
            and client_order_id is not None
            and client_order_id > 0
        ):
            row = self._connection.execute(
                """
                SELECT order_id, run_id, trade_id, signal_key FROM orders
                WHERE run_id=? AND broker=? AND broker_account=?
                  AND broker_client_id=? AND broker_client_order_id=?
                """,
                (run_id, broker, account, int(client_id), int(client_order_id)),
            ).fetchone()
            if row:
                return OrderBinding(
                    order_id=str(row["order_id"]),
                    run_id=str(row["run_id"]),
                    trade_id=row["trade_id"],
                    signal_key=row["signal_key"],
                )
        return None

    def find_order_id(
        self,
        *,
        run_id: str,
        broker: str,
        account: str,
        perm_id: int | None,
        client_id: int | None,
        client_order_id: int | None,
    ) -> str | None:
        binding = self.find_order_binding(
            run_id=run_id,
            broker=broker,
            account=account,
            perm_id=perm_id,
            client_id=client_id,
            client_order_id=client_order_id,
        )
        return binding.order_id if binding else None

    def add_order_status(self, record: OrderStatusEventRecord) -> str:
        with self.transaction():
            existing = self._connection.execute(
                "SELECT order_id FROM order_status_events WHERE event_id=?",
                (record.event_id,),
            ).fetchone()
            if existing and existing["order_id"] != record.order_id:
                raise IdentityConflictError("status event_id belongs to another order")
            self._connection.execute(
                """
                INSERT INTO order_status_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(event_id) DO UPDATE SET
                    status=excluded.status,
                    filled_quantity=excluded.filled_quantity,
                    remaining_quantity=excluded.remaining_quantity,
                    average_fill_price=excluded.average_fill_price,
                    broker_message=excluded.broker_message,
                    payload_json=excluded.payload_json
                """,
                (
                    self._required(record.event_id, "event_id"),
                    self._required(record.order_id, "order_id"),
                    self._required(record.status, "status"), utc_iso(record.occurred_at_utc),
                    self._finite(record.filled_quantity, "status.filled_quantity"),
                    self._finite(record.remaining_quantity, "status.remaining_quantity"),
                    self._finite(record.average_fill_price, "status.average_fill_price"),
                    record.broker_message,
                    canonical_json(record.payload),
                ),
            )
        return record.event_id

    def upsert_execution(self, record: ExecutionRecord) -> str:
        with self.transaction():
            order = self._connection.execute(
                "SELECT trade_id FROM orders WHERE order_id=?", (record.order_id,)
            ).fetchone()
            if order is None or order["trade_id"] != record.trade_id:
                raise ValueError("execution trade_id must match its order")
            existing = self._connection.execute(
                "SELECT order_id, trade_id FROM executions WHERE execution_id=?",
                (record.execution_id,),
            ).fetchone()
            if existing and (existing["order_id"], existing["trade_id"]) != (
                record.order_id, record.trade_id
            ):
                raise IdentityConflictError("execution_id belongs to another order/trade")
            self._connection.execute(
                """
                INSERT INTO executions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(execution_id) DO UPDATE SET
                    order_id=excluded.order_id,
                    trade_id=excluded.trade_id,
                    broker_execution_id=excluded.broker_execution_id,
                    executed_at_utc=excluded.executed_at_utc,
                    side=excluded.side,
                    quantity=excluded.quantity,
                    price=excluded.price,
                    exchange=excluded.exchange,
                    liquidity=excluded.liquidity,
                    metadata_json=excluded.metadata_json
                """,
                (
                    self._required(record.execution_id, "execution_id"),
                    self._required(record.order_id, "order_id"), record.trade_id,
                    self._required(record.broker_execution_id, "broker_execution_id"),
                    utc_iso(record.executed_at_utc), self._required(record.side, "side"),
                    self._positive(record.quantity, "execution.quantity"),
                    self._finite(record.price, "execution.price"),
                    record.exchange, record.liquidity,
                    canonical_json(record.metadata),
                ),
            )
        return record.execution_id

    def upsert_commission(self, record: CommissionRecord) -> str:
        with self.transaction():
            existing = self._connection.execute(
                "SELECT execution_id FROM commissions WHERE commission_id=?",
                (record.commission_id,),
            ).fetchone()
            if existing and existing["execution_id"] != record.execution_id:
                raise IdentityConflictError("commission_id belongs to another execution")
            self._connection.execute(
                """
                INSERT INTO commissions VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(commission_id) DO UPDATE SET
                    execution_id=excluded.execution_id,
                    currency=excluded.currency,
                    amount=excluded.amount,
                    realized_pnl=excluded.realized_pnl,
                    metadata_json=excluded.metadata_json
                """,
                (
                    self._required(record.commission_id, "commission_id"),
                    self._required(record.execution_id, "execution_id"),
                    self._required(record.currency, "currency"),
                    self._finite(record.amount, "commission.amount"),
                    self._finite(record.realized_pnl, "commission.realized_pnl"),
                    utc_iso(record.reported_at_utc),
                    canonical_json(record.metadata),
                ),
            )
        return record.commission_id

    def refresh_order_filled_from_executions(self, order_id: str) -> str:
        """Promote an order snapshot to Filled when its fills reach its quantity."""
        with self.transaction():
            order = self._connection.execute(
                "SELECT quantity, status FROM orders WHERE order_id=?", (order_id,)
            ).fetchone()
            if order is None:
                raise ValueError(f"unknown order_id {order_id!r}")
            totals = self._connection.execute(
                """
                SELECT COALESCE(SUM(quantity), 0) AS filled_quantity,
                       MAX(executed_at_utc) AS last_execution_at_utc
                FROM executions WHERE order_id=?
                """,
                (order_id,),
            ).fetchone()
            if float(totals["filled_quantity"]) + 1e-9 < float(order["quantity"]):
                return str(order["status"])
            self._connection.execute(
                "UPDATE orders SET status='Filled', updated_at_utc=? WHERE order_id=?",
                (str(totals["last_execution_at_utc"]), order_id),
            )
            return "Filled"

    def add_event(self, record: TradeEventRecord) -> str:
        with self.transaction():
            self._connection.execute(
                """
                INSERT INTO trade_events VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(event_id) DO UPDATE SET
                    trade_id=excluded.trade_id,
                    order_id=excluded.order_id,
                    signal_key=excluded.signal_key,
                    event_type=excluded.event_type,
                    occurred_at_utc=excluded.occurred_at_utc,
                    payload_json=excluded.payload_json
                """,
                (
                    self._required(record.event_id, "event_id"),
                    self._required(record.run_id, "run_id"), record.trade_id,
                    record.order_id, record.signal_key,
                    self._required(record.event_type, "event_type"),
                    utc_iso(record.occurred_at_utc), canonical_json(record.payload),
                ),
            )
        return record.event_id

    def add_risk_event(self, record: RiskEventRecord) -> str:
        with self.transaction():
            self._connection.execute(
                """
                INSERT INTO risk_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(risk_event_id) DO UPDATE SET
                    trade_id=excluded.trade_id,
                    decision_id=excluded.decision_id,
                    signal_key=excluded.signal_key,
                    event_type=excluded.event_type,
                    status=excluded.status,
                    occurred_at_utc=excluded.occurred_at_utc,
                    risk_amount=excluded.risk_amount,
                    risk_currency=excluded.risk_currency,
                    payload_json=excluded.payload_json
                """,
                (
                    self._required(record.risk_event_id, "risk_event_id"),
                    self._required(record.run_id, "run_id"), record.trade_id,
                    record.decision_id, record.signal_key,
                    self._required(record.event_type, "event_type"),
                    self._required(record.status, "status"), utc_iso(record.occurred_at_utc),
                    self._finite(record.risk_amount, "risk.risk_amount"),
                    record.risk_currency, canonical_json(record.payload),
                ),
            )
        return record.risk_event_id

    def upsert_reconciliation(self, record: ReconciliationRecord) -> str:
        if record.live_trade_id == record.reference_trade_id:
            raise ValueError("live and reference trades must be different")
        with self.transaction():
            rows = self._connection.execute(
                """
                SELECT trades.trade_id, trades.signal_key, strategy_runs.run_type
                FROM trades
                JOIN strategy_runs ON strategy_runs.run_id = trades.run_id
                WHERE trades.trade_id IN (?, ?)
                """,
                (record.live_trade_id, record.reference_trade_id),
            ).fetchall()
            if len(rows) != 2:
                raise ValueError("both reconciliation trades must exist")
            by_id = {row["trade_id"]: row for row in rows}
            live = by_id[record.live_trade_id]
            reference = by_id[record.reference_trade_id]
            if live["run_type"] not in {"paper", "live"} or reference["run_type"] != "backtest":
                raise ValueError("reconciliation must link paper/live to backtest")
            if (
                live["signal_key"] is None
                or reference["signal_key"] is None
                or live["signal_key"] != reference["signal_key"]
                or live["signal_key"] != record.signal_key
            ):
                raise ValueError("reconciliation requires one matching non-null signal_key")
            self._connection.execute(
                """
                INSERT INTO reconciliation VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(reconciliation_id) DO UPDATE SET
                    live_trade_id=excluded.live_trade_id,
                    reference_trade_id=excluded.reference_trade_id,
                    signal_key=excluded.signal_key,
                    status=excluded.status,
                    matched_at_utc=excluded.matched_at_utc,
                    price_slippage=excluded.price_slippage,
                    quantity_difference=excluded.quantity_difference,
                    pnl_difference=excluded.pnl_difference,
                    notes=excluded.notes,
                    details_json=excluded.details_json
                """,
                (
                    self._required(record.reconciliation_id, "reconciliation_id"),
                    self._required(record.live_trade_id, "live_trade_id"),
                    self._required(record.reference_trade_id, "reference_trade_id"),
                    self._required(record.signal_key, "signal_key"),
                    self._required(record.status, "status"), utc_iso(record.matched_at_utc),
                    self._finite(record.price_slippage, "reconciliation.price_slippage"),
                    self._finite(
                        record.quantity_difference, "reconciliation.quantity_difference"
                    ),
                    self._finite(record.pnl_difference, "reconciliation.pnl_difference"),
                    record.notes, canonical_json(record.details),
                ),
            )
        return record.reconciliation_id

    def get_decision(self, run_id: str, signal_key: str) -> dict[str, object] | None:
        row = self._connection.execute(
            "SELECT * FROM signal_decisions WHERE run_id=? AND signal_key=?",
            (run_id, signal_key),
        ).fetchone()
        return dict(row) if row else None

    def get_run(self, run_id: str) -> dict[str, object] | None:
        row = self._connection.execute(
            "SELECT * FROM strategy_runs WHERE run_id=?", (run_id,)
        ).fetchone()
        return dict(row) if row else None

    def get_trade(self, trade_id: str) -> dict[str, object] | None:
        row = self._connection.execute(
            "SELECT * FROM trades WHERE trade_id=?", (trade_id,)
        ).fetchone()
        return dict(row) if row else None

    def list_open_trades_for_rule(
        self,
        *,
        run_type: str,
        variant_id: str,
        asset_class: str,
        side: str,
        rule: str,
        broker_account: str,
    ) -> list[dict[str, object]]:
        """Return open trades whose original intent matches one exact rule/account."""
        rows = self._connection.execute(
            """
            SELECT trades.*
            FROM trades
            JOIN strategy_runs ON strategy_runs.run_id = trades.run_id
            WHERE strategy_runs.run_type=?
              AND strategy_runs.variant_id=?
              AND trades.status='open'
              AND upper(trades.asset_class)=upper(?)
              AND lower(trades.side)=lower(?)
              AND json_extract(trades.metadata_json, '$.rule')=?
              AND EXISTS (
                  SELECT 1
                  FROM orders
                  WHERE orders.trade_id=trades.trade_id
                    AND orders.broker_account=?
              )
            ORDER BY trades.opened_at_utc, trades.trade_id
            """,
            (run_type, variant_id, asset_class, side, rule, broker_account),
        ).fetchall()
        return [dict(row) for row in rows]

    def list_orders_for_trade(self, trade_id: str) -> list[dict[str, object]]:
        rows = self._connection.execute(
            """
            SELECT * FROM orders
            WHERE trade_id=?
            ORDER BY submitted_at_utc, order_id
            """,
            (trade_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def refresh_trade_open_from_executions(self, trade_id: str) -> str | None:
        """Promote a planned trade to open from its bound entry executions.

        Exit executions are deliberately not used to mark a trade closed here;
        closing requires a separate realized-PnL lifecycle step. Replays are
        idempotent because the state is derived from the complete execution set.
        """
        with self.transaction():
            trade = self._connection.execute(
                "SELECT * FROM trades WHERE trade_id=?", (trade_id,)
            ).fetchone()
            if trade is None:
                raise ValueError(f"execution references unknown trade_id {trade_id!r}")
            if trade["status"] not in {"planned", "open"}:
                return str(trade["status"])
            side = str(trade["side"]).strip().lower()
            if side not in {"long", "short"}:
                raise ValueError(f"unsupported trade side for fill lifecycle: {side!r}")
            executions = self._connection.execute(
                """
                SELECT execution_id, executed_at_utc, side, quantity, price
                FROM executions
                WHERE trade_id=?
                ORDER BY executed_at_utc, execution_id
                """,
                (trade_id,),
            ).fetchall()
            if not executions:
                return str(trade["status"])

            def direction(value: object) -> int:
                normalized = str(value).strip().upper()
                if normalized in {"BOT", "BUY"}:
                    return 1
                if normalized in {"SLD", "SELL"}:
                    return -1
                raise ValueError(f"unsupported execution side for trade lifecycle: {value!r}")

            entry_direction = 1 if side == "long" else -1
            opening = [row for row in executions if direction(row["side"]) == entry_direction]
            closing = [row for row in executions if direction(row["side"]) == -entry_direction]
            if not opening:
                return str(trade["status"])
            opened_quantity = sum(float(row["quantity"]) for row in opening)
            closed_quantity = sum(float(row["quantity"]) for row in closing)
            net_quantity = opened_quantity - closed_quantity
            if net_quantity <= 1e-9:
                return str(trade["status"])
            entry_price = sum(
                float(row["quantity"]) * float(row["price"]) for row in opening
            ) / opened_quantity
            opened_at = min(str(row["executed_at_utc"]) for row in opening)
            metadata = json.loads(str(trade["metadata_json"]))
            metadata["execution_lifecycle"] = {
                "source": "bound_entry_executions",
                "opening_execution_count": len(opening),
                "gross_open_quantity": opened_quantity,
                "gross_close_quantity": closed_quantity,
                "net_open_quantity": net_quantity,
                "entry_vwap": entry_price,
                "last_execution_at_utc": max(
                    str(row["executed_at_utc"]) for row in executions
                ),
            }
            self._connection.execute(
                """
                UPDATE trades
                SET status='open', opened_at_utc=?, quantity=?, entry_price=?,
                    metadata_json=?
                WHERE trade_id=?
                """,
                (
                    opened_at,
                    net_quantity,
                    entry_price,
                    canonical_json(metadata),
                    trade_id,
                ),
            )
            return "open"

    def refresh_trade_lifecycle_from_executions(self, trade_id: str) -> str | None:
        """Derive a single-leg stock trade's open/closed state from all fills.

        This is replay-safe and is used by the next-day TWS sync.  A trade is
        closed only when bound closing fills exactly flatten its opening fills.
        """
        with self.transaction():
            trade = self._connection.execute(
                "SELECT * FROM trades WHERE trade_id=?", (trade_id,)
            ).fetchone()
            if trade is None:
                raise ValueError(f"execution references unknown trade_id {trade_id!r}")
            if str(trade["asset_class"]).strip().upper() != "STK":
                return str(trade["status"])
            side = str(trade["side"]).strip().lower()
            if side not in {"long", "short"}:
                raise ValueError(f"unsupported trade side for fill lifecycle: {side!r}")
            executions = self._connection.execute(
                """
                SELECT e.execution_id, e.executed_at_utc, e.side, e.quantity, e.price,
                       c.amount AS commission
                FROM executions e
                LEFT JOIN commissions c ON c.execution_id=e.execution_id
                WHERE e.trade_id=?
                ORDER BY e.executed_at_utc, e.execution_id
                """,
                (trade_id,),
            ).fetchall()
            if not executions:
                return str(trade["status"])

            def direction(value: object) -> int:
                normalized = str(value).strip().upper()
                if normalized in {"BOT", "BUY"}:
                    return 1
                if normalized in {"SLD", "SELL"}:
                    return -1
                raise ValueError(
                    f"unsupported execution side for trade lifecycle: {value!r}"
                )

            entry_direction = 1 if side == "long" else -1
            opening = [row for row in executions if direction(row["side"]) == entry_direction]
            closing = [row for row in executions if direction(row["side"]) == -entry_direction]
            if not opening:
                return str(trade["status"])
            opened_quantity = sum(float(row["quantity"]) for row in opening)
            closed_quantity = sum(float(row["quantity"]) for row in closing)
            net_quantity = opened_quantity - closed_quantity
            if net_quantity < -1e-9:
                raise ValueError(
                    f"closing fills exceed opening fills for trade {trade_id!r}"
                )
            entry_price = sum(
                float(row["quantity"]) * float(row["price"]) for row in opening
            ) / opened_quantity
            opened_at = min(str(row["executed_at_utc"]) for row in opening)
            metadata = json.loads(str(trade["metadata_json"]))
            lifecycle = {
                "source": "bound_executions",
                "opening_execution_count": len(opening),
                "closing_execution_count": len(closing),
                "gross_open_quantity": opened_quantity,
                "gross_close_quantity": closed_quantity,
                "net_open_quantity": max(0.0, net_quantity),
                "entry_vwap": entry_price,
                "last_execution_at_utc": max(
                    str(row["executed_at_utc"]) for row in executions
                ),
            }
            metadata["execution_lifecycle"] = lifecycle
            if net_quantity > 1e-9:
                self._connection.execute(
                    """
                    UPDATE trades
                    SET status='open', opened_at_utc=?, closed_at_utc=NULL,
                        quantity=?, entry_price=?, exit_price=NULL,
                        realized_pnl=NULL, metadata_json=?
                    WHERE trade_id=?
                    """,
                    (
                        opened_at, net_quantity, entry_price,
                        canonical_json(metadata), trade_id,
                    ),
                )
                return "open"

            exit_price = sum(
                float(row["quantity"]) * float(row["price"]) for row in closing
            ) / closed_quantity
            gross_pnl = (
                (exit_price - entry_price) * opened_quantity
                if side == "long"
                else (entry_price - exit_price) * opened_quantity
            )
            known_commissions = sum(
                float(row["commission"])
                for row in executions
                if row["commission"] is not None
            )
            commission_count = sum(row["commission"] is not None for row in executions)
            realized_pnl = gross_pnl - known_commissions
            lifecycle.update(
                {
                    "exit_vwap": exit_price,
                    "gross_realized_pnl": gross_pnl,
                    "known_commissions": known_commissions,
                    "commission_count": commission_count,
                    "commission_complete": commission_count == len(executions),
                    "net_realized_pnl": realized_pnl,
                }
            )
            self._connection.execute(
                """
                UPDATE trades
                SET status='closed', opened_at_utc=?, closed_at_utc=?,
                    quantity=?, entry_price=?, exit_price=?, realized_pnl=?,
                    metadata_json=?
                WHERE trade_id=?
                """,
                (
                    opened_at,
                    max(str(row["executed_at_utc"]) for row in closing),
                    opened_quantity,
                    entry_price,
                    exit_price,
                    realized_pnl,
                    canonical_json(metadata),
                    trade_id,
                ),
            )
            return "closed"

    def get_trade_by_signal(
        self,
        run_id: str,
        signal_key: str,
    ) -> dict[str, object] | None:
        row = self._connection.execute(
            "SELECT * FROM trades WHERE run_id=? AND signal_key=?",
            (run_id, signal_key),
        ).fetchone()
        return dict(row) if row else None

    def list_trade_legs(self, trade_id: str) -> list[dict[str, object]]:
        rows = self._connection.execute(
            "SELECT * FROM trade_legs WHERE trade_id=? ORDER BY leg_index", (trade_id,)
        ).fetchall()
        return [dict(row) for row in rows]

    def count(self, table: str) -> int:
        allowed = {
            "strategy_runs", "signal_decisions", "decision_factors", "trades",
            "trade_legs", "orders", "order_legs", "order_status_events",
            "executions", "commissions", "trade_events", "risk_events",
            "reconciliation", "rules", "rule_versions", "strategy_variants",
            "strategy_variant_rules",
        }
        if table not in allowed:
            raise ValueError("unsupported table")
        row = self._connection.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()
        return int(row["count"])

    def pragma(self, name: str) -> object:
        if name not in {"foreign_keys", "journal_mode", "busy_timeout"}:
            raise ValueError("unsupported pragma")
        row = self._connection.execute(f"PRAGMA {name}").fetchone()
        return row[0]
