from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any

import decision_records as decision_model
import manual_regime_version_manifest as baseline_manifest


ENGINE_VERSION = "manual-regime-engine@1.1.0"
MONEY_MANAGEMENT_VERSION = baseline_manifest.MONEY_MANAGEMENT_VERSION
# Entry predicates, stops, targets, pair selection and veto thresholds are
# unchanged from V1.0.0.  The variant namespace, engine version and timing
# manifest carry the changed decision semantics.
RULE_VERSION = baseline_manifest.RULE_VERSION


@dataclass(frozen=True, slots=True)
class DecisionTimingVariant:
    key: str
    decision_bar_number: int
    decision_offset_minutes: int
    decision_bar_timestamp: str
    decision_available_at: str
    strategy_version: str
    strategy_variant_id: str

    def version_context(self) -> decision_model.DecisionVersionContext:
        return decision_model.DecisionVersionContext(
            strategy_variant_id=self.strategy_variant_id,
            engine_version=ENGINE_VERSION,
            money_management_version=MONEY_MANAGEMENT_VERSION,
            rule_versions={rule.rule_id: RULE_VERSION for rule in baseline_manifest.RULES},
            manifest_hash=manifest_hash(self),
        )


VARIANTS = {
    "10m": DecisionTimingVariant(
        key="10m",
        decision_bar_number=2,
        decision_offset_minutes=10,
        decision_bar_timestamp="09:35",
        decision_available_at="09:40",
        strategy_version="manual-regime-top50-ruleset-v1-decision-10m-control",
        strategy_variant_id="manual-regime-decision-10m-control@1.1.0",
    ),
    "60m": DecisionTimingVariant(
        key="60m",
        decision_bar_number=12,
        decision_offset_minutes=60,
        decision_bar_timestamp="10:25",
        decision_available_at="10:30",
        strategy_version="manual-regime-top50-ruleset-v1-decision-60m",
        strategy_variant_id="manual-regime-decision-60m@1.1.0",
    ),
    "90m": DecisionTimingVariant(
        key="90m",
        decision_bar_number=18,
        decision_offset_minutes=90,
        decision_bar_timestamp="10:55",
        decision_available_at="11:00",
        strategy_version="manual-regime-top50-ruleset-v1-decision-90m",
        strategy_variant_id="manual-regime-decision-90m@1.1.0",
    ),
}


def manifest_payload(variant: DecisionTimingVariant) -> dict[str, Any]:
    return {
        "strategy_id": baseline_manifest.STRATEGY_ID,
        "strategy_version": variant.strategy_version,
        "strategy_variant_id": variant.strategy_variant_id,
        "engine_version": ENGINE_VERSION,
        "money_management_version": MONEY_MANAGEMENT_VERSION,
        "rule_version": RULE_VERSION,
        "decision_timing": {
            "session_open": (
                "modal first bar per symbol: 09:30 America/New_York for equities, "
                "08:30 America/Chicago for SPX/SPX_FUT"
            ),
            "bar_size_minutes": 5,
            "decision_bar_number": variant.decision_bar_number,
            "decision_offset_minutes": variant.decision_offset_minutes,
            "decision_bar_timestamp_et": variant.decision_bar_timestamp,
            "decision_available_at_et": variant.decision_available_at,
            "timestamp_semantics": (
                "raw bar-start in the symbol's source timezone; manifest times are "
                "America/New_York; decision is available only after bar close"
            ),
            "context_semantics": (
                "the later decision price/bar plus the larger causal pre-decision "
                "intraday value-profile and price-acceptance window are evaluated"
            ),
            "selection_validation": (
                "session start is the mode of per-day first timestamps, not the "
                "first data day; full runs reject any observed ET mismatch"
            ),
        },
        "entry_rule_logic_changed": False,
        "baseline_manifest_hash": baseline_manifest.manifest_hash(),
        "baseline_engine_code_sha256": baseline_manifest.ENGINE_CODE_SHA256,
        "rules": [
            {
                "rule_id": rule.rule_id,
                "version": rule.version,
                "category": rule.category,
                "display_name": rule.display_name,
            }
            for rule in baseline_manifest.RULES
        ],
    }


def manifest_hash(variant: DecisionTimingVariant) -> str:
    encoded = json.dumps(
        manifest_payload(variant),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
