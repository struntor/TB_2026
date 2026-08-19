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
from src.strategies.stock_ivr70_put_credit import (
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
    CandidateEvaluation,
    StockIvr70Config,
    config_hash,
)


CREATED_AT_UTC = "2026-07-23T00:00:00Z"


def code_hash() -> str:
    paths = (
        Path(__file__).with_name("stock_ivr70_put_credit.py"),
        Path(__file__).parents[1] / "execution" / "stock_put_credit.py",
    )
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.read_bytes())
    return digest.hexdigest()


def register_manifest(store: Any, config: StockIvr70Config) -> None:
    source_hash = code_hash()
    memberships = []
    definitions = (
        # Rule definitions are immutable journal identities. The hard
        # 0.16-0.22 delta gate belongs to rule version 1.0.1 parameters, not
        # to the stable definition that was already registered by 1.0.0.
        (ENTRY_RULE_ID, ENTRY_RULE_VERSION, "Causal modeled-IVR cross above 70 with earnings and live quote gates", "entry"),
        (EXIT_RULE_ID, EXIT_RULE_VERSION, "Close at 50 percent credit capture or mandatory 21 DTE", "exit"),
    )
    with store.transaction():
        for position, (rule_id, version, description, role) in enumerate(definitions):
            store.register_rule(
                RuleDefinitionRecord(
                    rule_id,
                    rule_id,
                    description,
                    {"asset": "US single-stock options", "structure": "42DTE 10-point put credit"},
                )
            )
            version_id = deterministic_id("rule-version", rule_id, version)
            store.register_rule_version(
                RuleVersionRecord(
                    version_id,
                    rule_id,
                    version,
                    hashlib.sha256(f"{rule_id}:{version}:{config_hash(config)}".encode()).hexdigest(),
                    source_hash,
                    ENGINE_VERSION,
                    CREATED_AT_UTC,
                    asdict(config),
                    {"paper_only": True},
                )
            )
            memberships.append(
                StrategyVariantRuleRecord(
                    STRATEGY_VARIANT_ID,
                    rule_id,
                    version_id,
                    position,
                    role,
                    True,
                    {},
                )
            )
        store.register_strategy_variant(
            StrategyVariantRecord(
                STRATEGY_VARIANT_ID,
                STRATEGY_ID,
                STRATEGY_VERSION,
                "Multi-stock modeled-IVR70 42DTE put-credit spread",
                ENGINE_VERSION,
                MONEY_MANAGEMENT_VERSION,
                config_hash(config),
                "local-daily-causal-ivr-proxy",
                "IBKR-paper-observed",
                "conservative-combo-limit",
                CREATED_AT_UTC,
                {"paper_only": True, "source_code_hash": source_hash},
            ),
            memberships,
        )


def _utc(value: pd.Timestamp) -> str:
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is None:
        stamp = stamp.tz_localize("America/New_York")
    return stamp.tz_convert("UTC").isoformat().replace("+00:00", "Z")


def decision_payload(
    evaluation: CandidateEvaluation,
    config: StockIvr70Config,
    *,
    structure: dict[str, Any] | None = None,
) -> dict[str, Any]:
    signal_key = (
        f"{SIGNAL_KEY_VERSION}:"
        + hashlib.sha256(
            f"{STRATEGY_ID}:{evaluation.symbol}:{evaluation.signal_day}:{ENTRY_RULE_ID}:0".encode()
        ).hexdigest()[:32]
    )
    decision_utc = _utc(evaluation.decision_time)
    context = {
        "evaluation": {
            "signal_day": evaluation.signal_day,
            "entry_day": evaluation.entry_day,
            "modeled_iv_rank": evaluation.modeled_iv_rank,
            "previous_modeled_iv_rank": evaluation.previous_modeled_iv_rank,
            "median_dollar_volume60": evaluation.median_dollar_volume60,
            "predicted_earnings": evaluation.predicted_earnings,
        },
        "structure": structure or {},
    }
    factors = tuple(
        {
            **factor,
            "category": "entry_rule" if factor["factor_code"].startswith("modeled_ivr") else "filter",
            "is_veto": not bool(factor["passed"]),
            "reason": str(factor["factor_code"]),
            "as_of_time_utc": decision_utc,
            "calculation_version": ENGINE_VERSION,
        }
        for factor in evaluation.factors
    )
    accepted = evaluation.accepted
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
        "symbol": evaluation.symbol,
        "primary_rule": ENTRY_RULE_ID,
        "side": "short",
        "track": "stock_ivr70_put_credit",
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
