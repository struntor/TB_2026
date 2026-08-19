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
from src.strategies.failed_auction_poc_fly import (
    ENGINE_VERSION,
    EXIT_RULE_ID,
    EXIT_RULE_VERSION,
    RULE_ID,
    RULE_VERSION,
    SIGNAL_KEY_VERSION,
    STRATEGY_ID,
    STRATEGY_VARIANT_ID,
    STRATEGY_VERSION,
    FailedAuctionConfig,
    FailedAuctionEvaluation,
)


MONEY_MANAGEMENT_VERSION = "fixed-one-fly-paper@1.0.0"
CREATED_AT_UTC = "2026-07-18T00:00:00Z"


def code_hash() -> str:
    paths = (
        Path(__file__).with_name("failed_auction_poc_fly.py"),
        Path(__file__).parents[1] / "execution" / "spy_poc_fly.py",
    )
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.read_bytes())
    return digest.hexdigest()


def config_hash(config: FailedAuctionConfig) -> str:
    return hashlib.sha256(repr(asdict(config)).encode("utf-8")).hexdigest()


def register_manifest(store: Any, config: FailedAuctionConfig) -> None:
    source_hash = code_hash()
    definitions = (
        (
            RULE_ID,
            RULE_VERSION,
            "Failed auction Hard-12 entry with causal RVOL 1.00-1.20",
            "entry",
        ),
        (
            EXIT_RULE_ID,
            EXIT_RULE_VERSION,
            "POC-touch closing combo mid limit worked for 30 minutes",
            "exit",
        ),
    )
    memberships = []
    with store.transaction():
        for position, (rule_id, version, description, role) in enumerate(definitions):
            store.register_rule(
                RuleDefinitionRecord(
                    rule_id=rule_id,
                    name=rule_id,
                    description=description,
                    metadata={"asset": "SPY", "structure": "1DTE U10/L10 put fly"},
                )
            )
            version_id = deterministic_id("rule-version", rule_id, version)
            store.register_rule_version(
                RuleVersionRecord(
                    rule_version_id=version_id,
                    rule_id=rule_id,
                    semver=version,
                    logic_hash=hashlib.sha256(
                        f"{rule_id}:{version}:{config_hash(config)}".encode()
                    ).hexdigest(),
                    code_hash=source_hash,
                    engine_version=ENGINE_VERSION,
                    created_at_utc=CREATED_AT_UTC,
                    parameters=asdict(config),
                    metadata={"paper_only": True},
                )
            )
            memberships.append(
                StrategyVariantRuleRecord(
                    variant_id=STRATEGY_VARIANT_ID,
                    rule_id=rule_id,
                    rule_version_id=version_id,
                    position=position,
                    role=role,
                    enabled=True,
                    parameters={},
                )
            )
        store.register_strategy_variant(
            StrategyVariantRecord(
                variant_id=STRATEGY_VARIANT_ID,
                strategy_id=STRATEGY_ID,
                variant_version=STRATEGY_VERSION,
                name="SPY failed-auction Hard-12 RVOL 1.00-1.20 1DTE U10/L10",
                engine_version=ENGINE_VERSION,
                money_management_version=MONEY_MANAGEMENT_VERSION,
                config_hash=config_hash(config),
                data_revision="live-IBKR-bars",
                fee_model="IBKR-paper-observed",
                slippage_model="marketable-entry-and-touch-mid-work-30m",
                created_at_utc=CREATED_AT_UTC,
                metadata={"paper_only": True, "source_code_hash": source_hash},
            ),
            memberships,
        )


def _utc(value: pd.Timestamp) -> str:
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is None:
        stamp = stamp.tz_localize("America/New_York")
    return stamp.tz_convert("UTC").isoformat().replace("+00:00", "Z")


def decision_payload(
    evaluation: FailedAuctionEvaluation,
    config: FailedAuctionConfig,
) -> dict[str, Any]:
    decision_time = evaluation.entry_time or evaluation.confirmation_time
    if decision_time is None:
        decision_time = pd.Timestamp(evaluation.day) + pd.Timedelta(hours=16)
    decision_time_utc = _utc(decision_time)
    signal_key = (
        f"{SIGNAL_KEY_VERSION}:"
        + hashlib.sha256(
            f"{STRATEGY_ID}:{evaluation.day}:{RULE_ID}:0".encode()
        ).hexdigest()[:32]
    )
    accepted = evaluation.status == "accepted"
    factors = [
        {
            **factor,
            "category": (
                "entry_rule"
                if factor["factor_code"] in {"open_above_prior5_vah", "hard12_inside_value"}
                else "filter"
            ),
            "is_veto": not bool(factor["passed"]),
            "reason": str(factor["factor_code"]),
            "as_of_time_utc": decision_time_utc,
            "calculation_version": ENGINE_VERSION,
        }
        for factor in evaluation.factors
    ]
    context = {
        "evaluation": {
            key: (
                value.isoformat()
                if isinstance(value, pd.Timestamp)
                else value
            )
            for key, value in evaluation.to_dict().items()
            if key != "factors"
        },
        "structure": {
            "symbol": "SPY",
            "right": "PUT",
            "calendar_dte": config.calendar_dte,
            "body_strike": evaluation.body_strike,
            "upper_width": config.upper_width_points,
            "lower_width": config.lower_width_points,
            "quantity": config.quantity,
        },
        "exit": {
            "rule_id": EXIT_RULE_ID,
            "limit": "combo_mid_at_confirmed_poc_touch",
            "work_minutes": 30,
            "fallback": "marketable_combo_limit",
        },
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
        "decision_time_utc": decision_time_utc,
        "symbol": "SPY",
        "primary_rule": RULE_ID,
        "side": "long",
        "track": "failed_auction_poc_fly",
        "occurrence": 0,
        "status": "accepted" if accepted else evaluation.status,
        "trade_action": "trade" if accepted else "no_trade",
        "setup_state": "trade_opened" if accepted else evaluation.status,
        "primary_reason": evaluation.reason,
        "factors": factors,
        "context": context,
        "raw_record": {"evaluation": context["evaluation"], "generated_at_utc": datetime.now(timezone.utc).isoformat()},
        "rule_id": RULE_ID,
        "rule_version": RULE_VERSION,
        "strategy_variant_id": STRATEGY_VARIANT_ID,
        "money_management_version": MONEY_MANAGEMENT_VERSION,
    }
