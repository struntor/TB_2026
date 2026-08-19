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
from src.strategies.spxw_0dte_bull_put_iu30 import (
    ENGINE_VERSION,
    ENTRY_RULE_ID,
    ENTRY_RULE_VERSION,
    MONEY_MANAGEMENT_VERSION,
    SIGNAL_KEY_VERSION,
    STRATEGY_ID,
    STRATEGY_VARIANT_ID,
    STRATEGY_VERSION,
    EntryEvaluation,
    Spxw0DteConfig,
    config_hash,
)


CREATED_AT_UTC = "2026-07-29T00:00:00Z"


def code_hash() -> str:
    paths = (
        Path(__file__).with_name("spxw_0dte_bull_put_iu30.py"),
        Path(__file__).parents[1] / "execution" / "spxw_0dte_iu30.py",
    )
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.read_bytes())
    return digest.hexdigest()


def register_manifest(store: Any, config: Spxw0DteConfig) -> None:
    source_hash = code_hash()
    version_id = deterministic_id("rule-version", ENTRY_RULE_ID, ENTRY_RULE_VERSION)
    membership = StrategyVariantRuleRecord(
        STRATEGY_VARIANT_ID, ENTRY_RULE_ID, version_id, 0, "entry", True, {}
    )
    with store.transaction():
        store.register_rule(
            RuleDefinitionRecord(
                ENTRY_RULE_ID,
                ENTRY_RULE_ID,
                "09:40 Top50 breadth with SPX stress veto and strict IU30 overlay",
                {"asset": "SPXW", "structure": "0DTE 20-point bull-put plus optional IU30"},
            )
        )
        store.register_rule_version(
            RuleVersionRecord(
                version_id,
                ENTRY_RULE_ID,
                ENTRY_RULE_VERSION,
                hashlib.sha256(f"{ENTRY_RULE_ID}:{ENTRY_RULE_VERSION}:{config_hash(config)}".encode()).hexdigest(),
                source_hash,
                ENGINE_VERSION,
                CREATED_AT_UTC,
                asdict(config),
                {"paper_only": True, "decision_time_et": "09:40"},
            )
        )
        store.register_strategy_variant(
            StrategyVariantRecord(
                STRATEGY_VARIANT_ID,
                STRATEGY_ID,
                STRATEGY_VERSION,
                "SPXW 0DTE bull-put with strict IU30 butterfly overlay at 09:40 ET",
                ENGINE_VERSION,
                MONEY_MANAGEMENT_VERSION,
                config_hash(config),
                "Top50-and-SPX-5m-causal-09:40",
                "IBKR-paper-observed",
                "natural-marketable-combo-limit",
                CREATED_AT_UTC,
                {"paper_only": True, "source_code_hash": source_hash},
            ),
            [membership],
        )


def decision_payload(
    evaluation: EntryEvaluation,
    config: Spxw0DteConfig,
    *,
    structure: dict[str, Any] | None = None,
) -> dict[str, Any]:
    signal_key = (
        f"{SIGNAL_KEY_VERSION}:"
        + hashlib.sha256(f"{STRATEGY_ID}:{evaluation.day}:{ENTRY_RULE_ID}:0".encode()).hexdigest()[:32]
    )
    decision = evaluation.decision_time.tz_localize("America/New_York").tz_convert("UTC")
    factors = tuple(
        {
            **factor,
            "category": "entry_rule" if factor["factor_code"] == "top50_breadth_context" else "filter",
            "is_veto": not bool(factor["passed"]),
            "reason": str(factor["factor_code"]),
            "as_of_time_utc": decision.isoformat().replace("+00:00", "Z"),
            "calculation_version": ENGINE_VERSION,
        }
        for factor in evaluation.factors
    )
    context = {
        "evaluation": {
            "day": evaluation.day,
            "decision_time_et": evaluation.decision_time.isoformat(),
            "decision_price": evaluation.decision_price,
            "prior_atr14": evaluation.prior_atr14,
            "breadth_count": evaluation.breadth_count,
            "breadth_symbols": evaluation.breadth_symbols,
            "ret5_prev": evaluation.ret5_prev,
            "weekly_range_percentile52": evaluation.weekly_range_percentile52,
            "spx_regime": evaluation.spx_regime,
            "spx_big_picture_regime": evaluation.spx_big_picture_regime,
            "spx_profile_price_location": evaluation.spx_profile_price_location,
            "iu30_overlay": evaluation.iu30_overlay,
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
        "decision_time_utc": decision.isoformat().replace("+00:00", "Z"),
        "symbol": "SPXW",
        "primary_rule": ENTRY_RULE_ID,
        "side": "short",
        "track": "spxw_0dte_bull_put_iu30",
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
