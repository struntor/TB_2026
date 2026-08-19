from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
from pathlib import Path
from typing import Any

from src.journal.models import (
    RuleDefinitionRecord,
    RuleVersionRecord,
    StrategyVariantRecord,
    StrategyVariantRuleRecord,
    deterministic_id,
)
from src.strategies.xsp_vix_pullback import (
    ENGINE_VERSION,
    ENTRY_RULE_ID,
    ENTRY_RULE_VERSION,
    MONEY_MANAGEMENT_VERSION,
    PROFIT_RULE_ID,
    PROFIT_RULE_VERSION,
    SIGNAL_KEY_VERSION,
    STRATEGY_ID,
    STRATEGY_VARIANT_ID,
    STRATEGY_VERSION,
    TIME_EXIT_RULE_ID,
    TIME_EXIT_RULE_VERSION,
    EntryEvaluation,
    XspVixPullbackConfig,
    config_hash,
)


CREATED_AT_UTC = "2026-08-11T00:00:00Z"


def code_hash() -> str:
    digest = hashlib.sha256()
    for path in (
        Path(__file__).with_name("xsp_vix_pullback.py"),
        Path(__file__).parents[1] / "execution" / "xsp_put_credit.py",
    ):
        digest.update(path.read_bytes())
    return digest.hexdigest()


def register_manifest(store: Any, config: XspVixPullbackConfig) -> None:
    source_hash = code_hash()
    definitions = (
        (
            ENTRY_RULE_ID,
            ENTRY_RULE_VERSION,
            "VIX >=19 peak followed within ten sessions by >=1-point pullback, VIX >=15 and positive 20-session SPX momentum",
            "entry",
        ),
        (PROFIT_RULE_ID, PROFIT_RULE_VERSION, "GTC combo buyback after retaining 40% of entry credit (60% profit)", "exit"),
        (TIME_EXIT_RULE_ID, TIME_EXIT_RULE_VERSION, "Close at or below 60 remaining DTE when both option quotes pass quality gates", "exit"),
    )
    memberships = []
    with store.transaction():
        for position, (rule_id, version, description, role) in enumerate(definitions):
            store.register_rule(
                RuleDefinitionRecord(
                    rule_id, rule_id, description,
                    {"asset": "XSP", "structure": "100DTE 10-point bull put credit spread"},
                )
            )
            version_id = deterministic_id("rule-version", rule_id, version)
            store.register_rule_version(
                RuleVersionRecord(
                    version_id, rule_id, version,
                    hashlib.sha256(f"{rule_id}:{version}:{config_hash(config)}".encode()).hexdigest(),
                    source_hash, ENGINE_VERSION, CREATED_AT_UTC, asdict(config),
                    {"paper_only": True, "research_start": "2021-01-01", "preferred_monitoring_start": "2024-01-01"},
                )
            )
            memberships.append(StrategyVariantRuleRecord(STRATEGY_VARIANT_ID, rule_id, version_id, position, role, True, {}))
        store.register_strategy_variant(
            StrategyVariantRecord(
                STRATEGY_VARIANT_ID, STRATEGY_ID, STRATEGY_VERSION,
                "XSP VIX-pullback 100DTE delta-20 10-point put credit",
                ENGINE_VERSION, MONEY_MANAGEMENT_VERSION, config_hash(config),
                "local-VIX-SPX-history-plus-IBKR-live-13:35", "ThetaData-XSP-NBBO-research",
                "conservative-entry-TP60-time60-quality-filter",
                CREATED_AT_UTC,
                {
                    "paper_only": True,
                    "source_code_hash": source_hash,
                    "monitoring_note": "Performance stable from 2024; retain 2021-2022 stress evidence",
                },
            ),
            memberships,
        )


def decision_payload(
    evaluation: EntryEvaluation,
    config: XspVixPullbackConfig,
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
            "category": "entry_rule" if factor["factor_code"] == "vix_pullback_state_machine" else "filter",
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
            "vix": evaluation.vix,
            "peak_vix": evaluation.peak_vix,
            "peak_day": evaluation.peak_day,
            "vix_drop": evaluation.vix_drop,
            "momentum_20_sessions": evaluation.momentum_20_sessions,
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
        "symbol": "XSP",
        "primary_rule": ENTRY_RULE_ID,
        "side": "short",
        "track": "xsp_vix_pullback_100dte",
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
