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
from src.strategies.spxw_15dte_put_credit import (
    DAMAGE_RULE_ID,
    DAMAGE_RULE_VERSION,
    ENGINE_VERSION,
    ENTRY_RULE_ID,
    ENTRY_RULE_VERSION,
    MONEY_MANAGEMENT_VERSION,
    SIGNAL_KEY_VERSION,
    STRATEGY_ID,
    STRATEGY_VARIANT_ID,
    STRATEGY_VERSION,
    EntryEvaluation,
    Spxw15DteConfig,
    config_hash,
)


CREATED_AT_UTC = "2026-07-21T00:00:00Z"


def code_hash() -> str:
    paths = (
        Path(__file__).with_name("spxw_15dte_put_credit.py"),
        Path(__file__).parents[1] / "execution" / "spxw_put_credit.py",
    )
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.read_bytes())
    return digest.hexdigest()


def register_manifest(store: Any, config: Spxw15DteConfig) -> None:
    memberships = []
    definitions = (
        (ENTRY_RULE_ID, ENTRY_RULE_VERSION, "Causal 13:30 signal and 13:35 SPXW quote gates", "entry"),
        (DAMAGE_RULE_ID, DAMAGE_RULE_VERSION, "EOD low10/off-high5 damage exit at next 09:35", "exit"),
    )
    source_hash = code_hash()
    with store.transaction():
        for position, (rule_id, version, description, role) in enumerate(definitions):
            store.register_rule(RuleDefinitionRecord(rule_id, rule_id, description, {"asset": "SPXW", "structure": "15DTE 10-point put credit"}))
            version_id = deterministic_id("rule-version", rule_id, version)
            store.register_rule_version(
                RuleVersionRecord(
                    version_id, rule_id, version,
                    hashlib.sha256(f"{rule_id}:{version}:{config_hash(config)}".encode()).hexdigest(),
                    source_hash, ENGINE_VERSION, CREATED_AT_UTC, asdict(config), {"paper_only": True},
                )
            )
            memberships.append(StrategyVariantRuleRecord(STRATEGY_VARIANT_ID, rule_id, version_id, position, role, True, {}))
        store.register_strategy_variant(
            StrategyVariantRecord(
                STRATEGY_VARIANT_ID, STRATEGY_ID, STRATEGY_VERSION,
                "SPXW causal 15DTE put-credit spread",
                ENGINE_VERSION, MONEY_MANAGEMENT_VERSION, config_hash(config),
                "SPY-5m-causal-13:30", "IBKR-paper-observed", "marketable-combo-limit",
                CREATED_AT_UTC, {"paper_only": True, "source_code_hash": source_hash},
            ),
            memberships,
        )


def _utc(value: pd.Timestamp) -> str:
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is None:
        stamp = stamp.tz_localize("America/New_York")
    return stamp.tz_convert("UTC").isoformat().replace("+00:00", "Z")


def decision_payload(
    evaluation: EntryEvaluation,
    config: Spxw15DteConfig,
    *,
    structure: dict[str, Any] | None = None,
) -> dict[str, Any]:
    signal_key = (
        f"{SIGNAL_KEY_VERSION}:"
        + hashlib.sha256(f"{STRATEGY_ID}:{evaluation.day}:{ENTRY_RULE_ID}:0".encode()).hexdigest()[:32]
    )
    accepted = evaluation.accepted
    decision_utc = _utc(evaluation.decision_time)
    factors = tuple(
        {
            **factor,
            "category": "entry_rule" if factor["factor_code"].startswith("open_to") else "filter",
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
            "spy_open": evaluation.spy_open,
            "signal_close": evaluation.signal_close,
            "previous_close": evaluation.previous_close,
            "previous_ema20": evaluation.previous_ema20,
            "gap": evaluation.gap,
            "open_to_signal": evaluation.open_to_signal,
            "dist_low10": evaluation.dist_low10,
            "reentry_elapsed_days": evaluation.reentry_elapsed_days,
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
        "track": "spxw_15dte_put_credit",
        "occurrence": 0,
        "status": "accepted" if accepted else evaluation.status,
        "trade_action": "trade" if accepted else "no_trade",
        "setup_state": "trade_planned" if accepted else evaluation.status,
        "primary_reason": evaluation.reason,
        "factors": factors,
        "context": context,
        "raw_record": {"context": context, "generated_at_utc": datetime.now(timezone.utc).isoformat()},
        "rule_id": ENTRY_RULE_ID,
        "rule_version": ENTRY_RULE_VERSION,
        "strategy_variant_id": STRATEGY_VARIANT_ID,
        "money_management_version": MONEY_MANAGEMENT_VERSION,
    }
