from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
from typing import Any

import pandas as pd


STRATEGY_ID = "spx-75dte-put-bwb-tuesday-vix15"
STRATEGY_VERSION = "1.6.0-paper-daily-fill-test"
STRATEGY_VARIANT_ID = f"{STRATEGY_ID}@{STRATEGY_VERSION}"
ENGINE_VERSION = "spxw-75dte-put-bwb-live@1.6.0"
MONEY_MANAGEMENT_VERSION = "one-contract-shared-defined-risk@1.0.0"
SIGNAL_KEY_VERSION = "spxw75-bwb-signal@4"
ENTRY_RULE_ID = "spxw-75dte-weekday-experiment-vix15-entry"
ENTRY_RULE_VERSION = "1.6.0"
EXIT_RULE_ID = "spxw-75dte-bwb-profit-target"
# The manifest hashes the full engine/config even when exit behavior is unchanged.
EXIT_RULE_VERSION = "1.0.4"


@dataclass(frozen=True, slots=True)
class Spxw75DteBwbConfig:
    entry_time_et: str = "09:32:00"
    latest_submission_time_et: str = "09:40:00"
    weekday: int = 3
    paper_daily_entry_start_day: str = "2026-09-03"
    minimum_opening_vix: float = 15.0
    target_calendar_dte_floor: int = 75
    body_spot_fraction: float = 0.99
    upper_width_points: float = 35.0
    lower_width_points: float = 55.0
    profit_target_fraction: float = 0.40
    entry_slippage_points: float = 0.20
    repriced_entry_slippage_points: float = 0.40
    final_entry_slippage_points: float = 0.65
    quantity: int = 1
    maximum_concurrent_positions: int = 7
    paper_weekday_experiment_start_day: str = "2026-08-14"
    paper_weekday_experiment_end_day: str = "2026-08-27"
    paper_experiment_maximum_concurrent_positions: int = 20
    paper_experiment_ignore_dedicated_risk_budget: bool = True


@dataclass(frozen=True, slots=True)
class EntryEvaluation:
    day: str
    status: str
    reason: str
    decision_time: pd.Timestamp
    weekday: int
    opening_vix: float | None
    factors: tuple[dict[str, Any], ...]

    @property
    def accepted(self) -> bool:
        return self.status == "accepted"


def config_hash(config: Spxw75DteBwbConfig) -> str:
    return hashlib.sha256(repr(asdict(config)).encode("utf-8")).hexdigest()


def paper_weekday_experiment_active(
    day: pd.Timestamp | str,
    config: Spxw75DteBwbConfig = Spxw75DteBwbConfig(),
) -> bool:
    stamp = pd.Timestamp(day).normalize()
    start = pd.Timestamp(config.paper_weekday_experiment_start_day).normalize()
    end = pd.Timestamp(config.paper_weekday_experiment_end_day).normalize()
    return start <= stamp <= end


def maximum_concurrent_positions_for_day(
    day: pd.Timestamp | str,
    config: Spxw75DteBwbConfig = Spxw75DteBwbConfig(),
) -> int:
    if paper_weekday_experiment_active(day, config):
        return config.paper_experiment_maximum_concurrent_positions
    return config.maximum_concurrent_positions


def evaluate_entry(
    day: pd.Timestamp | str,
    *,
    opening_vix: float | None,
    config: Spxw75DteBwbConfig = Spxw75DteBwbConfig(),
) -> EntryEvaluation:
    stamp = pd.Timestamp(day)
    day_text = stamp.strftime("%Y-%m-%d")
    decision = pd.Timestamp(f"{day_text} {config.entry_time_et}")
    experiment_active = paper_weekday_experiment_active(stamp, config)
    daily_test_active = stamp.normalize() >= pd.Timestamp(config.paper_daily_entry_start_day).normalize()
    allowed_weekdays = tuple(range(5)) if experiment_active or daily_test_active else (config.weekday,)
    weekday_ok = int(stamp.weekday()) in allowed_weekdays
    vix_ok = opening_vix is not None and float(opening_vix) >= config.minimum_opening_vix
    factors = (
        {
            "factor_code": "entry_weekday_schedule",
            "observed_value": int(stamp.weekday()),
            "operator": "in",
            "threshold": list(allowed_weekdays),
            "passed": weekday_ok,
            "paper_weekday_experiment_active": experiment_active,
            "paper_daily_fill_test_active": daily_test_active,
        },
        {
            "factor_code": "opening_vix_minimum",
            "observed_value": opening_vix,
            "operator": ">=",
            "threshold": config.minimum_opening_vix,
            "passed": vix_ok,
        },
    )
    failed = next((factor for factor in factors if not factor["passed"]), None)
    if opening_vix is None:
        status, reason = "missing_data", "opening_vix_unavailable"
    elif failed is None:
        status = "accepted"
        reason = (
            "daily_paper_fill_test_vix15_entry_accepted"
            if daily_test_active
            else "weekday_paper_experiment_vix15_entry_accepted"
            if experiment_active
            else "thursday_vix15_entry_accepted"
        )
    else:
        status = "no_setup"
        reason = str(failed["factor_code"])
    return EntryEvaluation(day_text, status, reason, decision, int(stamp.weekday()), opening_vix, factors)
