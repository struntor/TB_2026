from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import hashlib
from typing import Any, Mapping

import pandas as pd


STRATEGY_ID = "multi-stock-ivr70-put-credit"
STRATEGY_VERSION = "1.0.1"
STRATEGY_VARIANT_ID = f"{STRATEGY_ID}@{STRATEGY_VERSION}"
ENGINE_VERSION = "stock-ivr70-live-engine@1.0.1"
MONEY_MANAGEMENT_VERSION = "shared-defined-risk-10pct-credit-priority@1.0.0"
SIGNAL_KEY_VERSION = "stock-ivr70-pcs-signal@1"
ENTRY_RULE_ID = "stock-ivr70-causal-cross-entry"
ENTRY_RULE_VERSION = "1.0.1"
EXIT_RULE_ID = "stock-ivr70-tp50-or-21dte-exit"
# The journal hashes the shared strategy/execution implementation for every
# rule version. The 1.0.1 entry/quote hardening therefore also requires a new
# immutable provenance version for the otherwise unchanged exit rule.
EXIT_RULE_VERSION = "1.0.1"


@dataclass(frozen=True, slots=True)
class StockIvr70Config:
    ivr_threshold: float = 70.0
    target_short_delta: float = -0.19
    minimum_short_abs_delta: float = 0.16
    maximum_short_abs_delta: float = 0.22
    spread_width_points: float = 10.0
    target_calendar_dte: int = 42
    minimum_calendar_dte: int = 35
    maximum_calendar_dte: int = 50
    mandatory_exit_dte: int = 21
    take_profit_fraction: float = 0.50
    earnings_buffer_days: int = 7
    entry_start_time: str = "09:40"
    entry_end_time: str = "10:15"
    exit_start_time: str = "15:50"
    exit_end_time: str = "15:55"
    estimated_round_trip_fees_dollars: float = 4.0
    quantity: int = 1


@dataclass(frozen=True, slots=True)
class CandidateEvaluation:
    symbol: str
    signal_day: str
    entry_day: str
    status: str
    reason: str
    decision_time: pd.Timestamp
    modeled_iv_rank: float
    previous_modeled_iv_rank: float
    median_dollar_volume60: float
    predicted_earnings: str
    factors: tuple[dict[str, Any], ...]

    @property
    def accepted(self) -> bool:
        return self.status == "accepted"


def config_hash(config: StockIvr70Config) -> str:
    return hashlib.sha256(repr(asdict(config)).encode("utf-8")).hexdigest()


def evaluate_candidate(
    row: Mapping[str, Any], config: StockIvr70Config
) -> CandidateEvaluation:
    symbol = str(row["symbol"]).upper()
    signal_day = pd.Timestamp(row["signal_date"]).strftime("%Y-%m-%d")
    entry_day = pd.Timestamp(row["entry_date"]).strftime("%Y-%m-%d")
    rank = float(row["modeled_iv_rank"])
    previous = float(row["previous_modeled_iv_rank"])
    liquidity = float(row["median_dollar_volume60"])
    predicted = pd.Timestamp(row["predicted_earnings"]).strftime("%Y-%m-%d")
    checks = (
        (
            "modeled_ivr_cross_above_70",
            {"previous": previous, "current": rank},
            "cross_above",
            config.ivr_threshold,
            previous <= config.ivr_threshold < rank,
        ),
        (
            "entry_is_next_available_session",
            entry_day,
            ">",
            signal_day,
            pd.Timestamp(entry_day) > pd.Timestamp(signal_day),
        ),
    )
    factors = tuple(
        {
            "factor_code": code,
            "observed_value": observed,
            "operator": operator,
            "threshold": threshold,
            "passed": bool(passed),
        }
        for code, observed, operator, threshold, passed in checks
    )
    failed = next((factor for factor in factors if not factor["passed"]), None)
    return CandidateEvaluation(
        symbol,
        signal_day,
        entry_day,
        "accepted" if failed is None else "vetoed",
        "causal_ivr70_candidate" if failed is None else str(failed["factor_code"]),
        pd.Timestamp(f"{entry_day} {config.entry_start_time}"),
        rank,
        previous,
        liquidity,
        predicted,
        factors,
    )


def apply_expiry_and_earnings_gate(
    evaluation: CandidateEvaluation,
    *,
    expiry: str,
    config: StockIvr70Config,
) -> CandidateEvaluation:
    if not evaluation.accepted:
        return evaluation
    expiry_day = pd.Timestamp(expiry).normalize()
    predicted = pd.Timestamp(evaluation.predicted_earnings).normalize()
    exclusion_end = expiry_day + pd.Timedelta(days=config.earnings_buffer_days)
    checks = (
        (
            "earnings_after_expiry_buffer",
            evaluation.predicted_earnings,
            ">",
            exclusion_end.strftime("%Y-%m-%d"),
            predicted > exclusion_end,
        ),
    )
    extra = tuple(
        {
            "factor_code": code,
            "observed_value": observed,
            "operator": operator,
            "threshold": threshold,
            "passed": bool(passed),
        }
        for code, observed, operator, threshold, passed in checks
    )
    failed = next((factor for factor in extra if not factor["passed"]), None)
    return replace(
        evaluation,
        status="accepted" if failed is None else "vetoed",
        reason="expiry_and_earnings_gate_accepted" if failed is None else str(failed["factor_code"]),
        factors=evaluation.factors + extra,
    )


def apply_quote_gates(
    evaluation: CandidateEvaluation,
    *,
    conservative_credit: float,
    maximum_loss_dollars: float,
    config: StockIvr70Config,
) -> CandidateEvaluation:
    if not evaluation.accepted:
        return evaluation
    checks = (
        (
            "positive_executable_credit",
            conservative_credit,
            ">",
            0.0,
            conservative_credit > 0,
        ),
        (
            "positive_defined_maximum_loss",
            maximum_loss_dollars,
            ">",
            0.0,
            maximum_loss_dollars > 0,
        ),
    )
    extra = tuple(
        {
            "factor_code": code,
            "observed_value": observed,
            "operator": operator,
            "threshold": threshold,
            "passed": bool(passed),
        }
        for code, observed, operator, threshold, passed in checks
    )
    failed = next((factor for factor in extra if not factor["passed"]), None)
    return replace(
        evaluation,
        status="accepted" if failed is None else "vetoed",
        reason="quote_gates_accepted" if failed is None else str(failed["factor_code"]),
        factors=evaluation.factors + extra,
    )


def apply_quote_and_expiry_gates(
    evaluation: CandidateEvaluation,
    *,
    expiry: str,
    conservative_credit: float,
    maximum_loss_dollars: float,
    config: StockIvr70Config,
) -> CandidateEvaluation:
    """Compatibility composition for research/tests using the former one-step gate."""
    after_expiry = apply_expiry_and_earnings_gate(
        evaluation,
        expiry=expiry,
        config=config,
    )
    return apply_quote_gates(
        after_expiry,
        conservative_credit=conservative_credit,
        maximum_loss_dollars=maximum_loss_dollars,
        config=config,
    )
