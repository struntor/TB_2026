from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
from pathlib import Path
from typing import Any

import pandas as pd

from src.journal.models import (
    RuleDefinitionRecord,
    RuleVersionRecord,
    StrategyVariantRecord,
    StrategyVariantRuleRecord,
    deterministic_id,
)
from src.strategies.spxw_75dte_put_bwb import (
    ENGINE_VERSION,
    ENTRY_RULE_ID,
    ENTRY_RULE_VERSION,
    EXIT_RULE_ID,
    EXIT_RULE_VERSION,
    MONEY_MANAGEMENT_VERSION,
    SIGNAL_KEY_VERSION,
    STRATEGY_ID,
    STRATEGY_VARIANT_ID,
    STRATEGY_VERSION,
    EntryEvaluation,
    Spxw75DteBwbConfig,
    config_hash,
)


CREATED_AT_UTC = "2026-09-03T00:00:00Z"


def code_hash() -> str:
    digest = hashlib.sha256()
    for path in (
        Path(__file__).with_name("spxw_75dte_put_bwb.py"),
        Path(__file__).parents[1] / "execution" / "spxw_put_bwb.py",
        Path(__file__).parents[1] / "bot" / "spxw_75dte_put_bwb_monitor.py",
    ):
        digest.update(path.read_bytes())
    return digest.hexdigest()


def register_manifest(store: Any, config: Spxw75DteBwbConfig) -> None:
    source_hash = code_hash()
    definitions = (
        (
            ENTRY_RULE_ID,
            ENTRY_RULE_VERSION,
            # Stable rule definitions are immutable in existing journals.
            # The current schedule lives in versioned parameters and variant name.
            "Thursday 09:32 ET entry when opening VIX is at least 15, with a paper-only Monday-Friday experiment for the next ten sessions from 2026-08-14 through 2026-08-27",
            "entry",
        ),
        (EXIT_RULE_ID, EXIT_RULE_VERSION, "GTC combo buyback at 40% of opening credit profit", "exit"),
    )
    memberships = []
    with store.transaction():
        for position, (rule_id, version, description, role) in enumerate(definitions):
            store.register_rule(
                RuleDefinitionRecord(
                    rule_id, rule_id, description,
                    {"asset": "SPXW", "structure": "75DTE +35/-55 put broken-wing butterfly"},
                )
            )
            version_id = deterministic_id("rule-version", rule_id, version)
            store.register_rule_version(
                RuleVersionRecord(
                    version_id, rule_id, version,
                    hashlib.sha256(f"{rule_id}:{version}:{config_hash(config)}".encode()).hexdigest(),
                    source_hash, ENGINE_VERSION, CREATED_AT_UTC, asdict(config), {"paper_only": True},
                )
            )
            memberships.append(
                StrategyVariantRuleRecord(STRATEGY_VARIANT_ID, rule_id, version_id, position, role, True, {})
            )
        store.register_strategy_variant(
            StrategyVariantRecord(
                STRATEGY_VARIANT_ID, STRATEGY_ID, STRATEGY_VERSION,
                "SPXW daily paper fill test VIX15 75DTE +35/-55 put BWB with normal risk limits",
                ENGINE_VERSION, MONEY_MANAGEMENT_VERSION, config_hash(config),
                "IBKR-SPX-and-VIX-live-09:32", "IBKR-paper-observed",
        "mid-minus-0.20-5m-then-mid-minus-0.40-5m-then-mid-minus-0.65-5m-else-cancel-and-GTC-40pct-profit-target",
                CREATED_AT_UTC, {"paper_only": True, "source_code_hash": source_hash},
            ),
            memberships,
        )


def decision_payload(
    evaluation: EntryEvaluation,
    config: Spxw75DteBwbConfig,
    *,
    structure: dict[str, Any] | None = None,
) -> dict[str, Any]:
    signal_key = (
        f"{SIGNAL_KEY_VERSION}:"
        + hashlib.sha256(f"{STRATEGY_ID}:{evaluation.day}:{ENTRY_RULE_ID}:0".encode()).hexdigest()[:32]
    )
    decision = evaluation.decision_time.tz_localize("America/New_York").tz_convert("UTC")
    decision_utc = decision.isoformat().replace("+00:00", "Z")
    factors = tuple(
        {
            **factor,
            "category": "entry_rule" if factor["factor_code"] == "entry_weekday_schedule" else "filter",
            "is_veto": not bool(factor["passed"]),
            "reason": str(factor["factor_code"]),
            "as_of_time_utc": decision_utc,
            "calculation_version": ENGINE_VERSION,
        }
        for factor in evaluation.factors
    )
    context = {
        "evaluation": {
            "day": evaluation.day,
            "decision_time_et": evaluation.decision_time.isoformat(),
            "weekday": evaluation.weekday,
            "opening_vix": evaluation.opening_vix,
        },
        "structure": structure or {},
    }
    return {
        "schema_version": "1.0",
        "decision_key": signal_key,
        "signal_key": signal_key,
        "signal_key_version": SIGNAL_KEY_VERSION,
        "decision_stage": "live_final",
        "strategy_id": STRATEGY_ID,
        "strategy_version": STRATEGY_VERSION,
        "engine_version": ENGINE_VERSION,
        "config_hash": config_hash(config),
        "decision_time_utc": decision_utc,
        "symbol": "SPXW",
        "primary_rule": ENTRY_RULE_ID,
        "side": "short",
        "track": "spxw_75dte_put_bwb",
        "occurrence": 0,
        "status": "accepted" if evaluation.accepted else evaluation.status,
        "trade_action": "trade" if evaluation.accepted else "no_trade",
        "setup_state": "trade_planned" if evaluation.accepted else evaluation.status,
        "primary_reason": evaluation.reason,
        "factors": factors,
        "context": context,
        "raw_record": {"context": context, "generated_at_utc": datetime.now(timezone.utc).isoformat()},
        "rule_id": ENTRY_RULE_ID,
        "rule_version": ENTRY_RULE_VERSION,
        "strategy_variant_id": STRATEGY_VARIANT_ID,
        "money_management_version": MONEY_MANAGEMENT_VERSION,
    }
