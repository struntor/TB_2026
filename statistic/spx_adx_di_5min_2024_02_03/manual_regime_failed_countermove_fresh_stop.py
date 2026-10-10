from __future__ import annotations

from dataclasses import dataclass, replace
import math
from typing import Mapping, Sequence

import pandas as pd

import decision_records as decision_model


TARGET_RULE = "bearish_failed_countermove_short_baseline"
POLICY_VERSION = "failed-countermove-fresh-structure-stop@1.0.0"
PARENT_VARIANT_ID = "manual-regime-baseline@2.0.0"
PRIOR_SESSION_WINDOW = ("12:00", "15:55")
OPENING_WINDOW = ("09:30", "09:35")
MINIMUM_CRV = 1.0


@dataclass(frozen=True)
class FreshStopDecision:
    accepted: bool
    original_stop: float
    stop: float
    prior_afternoon_high: float
    opening_high: float
    tolerance: float
    risk: float
    reward: float
    crv: float
    reason: str

    def to_dict(self) -> dict[str, object]:
        return {
            "policy_version": POLICY_VERSION,
            "parent_variant_id": PARENT_VARIANT_ID,
            "accepted": self.accepted,
            "original_stop": self.original_stop,
            "fresh_stop": self.stop,
            "prior_afternoon_high": self.prior_afternoon_high,
            "opening_high": self.opening_high,
            "tolerance": self.tolerance,
            "risk": self.risk,
            "reward": self.reward,
            "crv": self.crv,
            "reason": self.reason,
        }


def calculate_fresh_stop(
    *,
    entry: float,
    original_stop: float,
    target: float,
    tolerance: float,
    prior_afternoon_high: float,
    opening_high: float,
) -> FreshStopDecision:
    values = {
        "entry": entry,
        "original_stop": original_stop,
        "target": target,
        "tolerance": tolerance,
        "prior_afternoon_high": prior_afternoon_high,
        "opening_high": opening_high,
    }
    if not all(math.isfinite(float(value)) for value in values.values()):
        raise ValueError(f"Fresh-stop inputs must be finite: {values}")
    if tolerance < 0:
        raise ValueError("Fresh-stop tolerance must not be negative")

    stop = max(
        float(original_stop),
        float(prior_afternoon_high) + float(tolerance),
        float(opening_high) + float(tolerance),
    )
    risk = stop - float(entry)
    reward = float(entry) - float(target)
    crv = reward / risk if risk > 0 else float("-inf")
    accepted = risk > 0 and reward > 0 and crv >= MINIMUM_CRV
    reason = "fresh_structure_stop_accepted" if accepted else "fresh_structure_stop_crv_below_1"
    return FreshStopDecision(
        accepted=accepted,
        original_stop=float(original_stop),
        stop=stop,
        prior_afternoon_high=float(prior_afternoon_high),
        opening_high=float(opening_high),
        tolerance=float(tolerance),
        risk=risk,
        reward=reward,
        crv=crv,
        reason=reason,
    )


def extract_structure_references(
    bars: pd.DataFrame,
    day: pd.Timestamp,
) -> tuple[float, float]:
    required = {"date", "high"}
    missing = required - set(bars.columns)
    if missing:
        raise ValueError(f"Fresh-stop bars are missing columns: {sorted(missing)}")
    frame = bars.copy()
    frame["date"] = pd.to_datetime(frame["date"])
    frame["day"] = frame["date"].dt.normalize()
    frame["time"] = frame["date"].dt.strftime("%H:%M")
    decision_day = pd.Timestamp(day).normalize()

    prior_days = frame.loc[frame["day"] < decision_day, "day"]
    if prior_days.empty:
        raise ValueError(f"No prior session before {decision_day.date()}")
    prior_day = prior_days.max()
    prior = frame[
        (frame["day"] == prior_day)
        & (frame["time"] >= PRIOR_SESSION_WINDOW[0])
        & (frame["time"] <= PRIOR_SESSION_WINDOW[1])
    ]
    if prior.empty:
        raise ValueError(
            f"No prior-session afternoon bars for {prior_day.date()} in "
            f"{PRIOR_SESSION_WINDOW[0]}..{PRIOR_SESSION_WINDOW[1]}"
        )

    opening = frame[
        (frame["day"] == decision_day)
        & (frame["time"] >= OPENING_WINDOW[0])
        & (frame["time"] <= OPENING_WINDOW[1])
    ]
    if len(opening) < 2:
        raise ValueError(
            f"Expected two completed opening bars for {decision_day.date()}, got {len(opening)}"
        )
    return float(prior["high"].max()), float(opening["high"].max())


def apply_fresh_structure_stop(
    trades: pd.DataFrame,
    final_records: Sequence[decision_model.DecisionRecord],
    day: pd.Timestamp,
    bars_by_symbol: Mapping[str, pd.DataFrame],
) -> tuple[pd.DataFrame, list[decision_model.DecisionRecord]]:
    adjusted = trades.copy()
    metadata_columns: dict[str, object] = {
        "fresh_stop_policy_version": "",
        "fresh_stop_parent_variant_id": "",
        "legacy_v2_stop": float("nan"),
        "fresh_stop_prior_afternoon_high": float("nan"),
        "fresh_stop_opening_high": float("nan"),
        "fresh_stop_tolerance": float("nan"),
        "fresh_stop_crv": float("nan"),
        "fresh_stop_reason": "",
    }
    for column, value in metadata_columns.items():
        if column not in adjusted.columns:
            adjusted[column] = value
    if adjusted.empty:
        return adjusted, list(final_records)
    if "signal_key" not in adjusted.columns:
        raise ValueError("Fresh-stop trade rows require signal_key")

    records_by_key = {record.signal_key: record for record in final_records}
    decisions: dict[str, FreshStopDecision] = {}
    blocked_keys: set[str] = set()
    reference_cache: dict[str, tuple[float, float]] = {}

    for index, row in adjusted.iterrows():
        if str(row["rule"]) != TARGET_RULE or str(row["side"]).lower() != "short":
            continue
        signal_key = str(row["signal_key"])
        record = records_by_key.get(signal_key)
        if record is None:
            raise ValueError(f"Missing DecisionRecord for fresh-stop signal {signal_key}")
        symbol = str(row["symbol"]).upper()
        bars = bars_by_symbol.get(symbol)
        if bars is None:
            raise ValueError(f"Missing fresh-stop bars for {symbol}")
        if symbol not in reference_cache:
            reference_cache[symbol] = extract_structure_references(bars, day)
        prior_high, opening_high = reference_cache[symbol]
        context = dict(record.context)
        if "atr_tolerance" not in context:
            raise ValueError(f"Missing atr_tolerance for fresh-stop signal {signal_key}")
        decision = calculate_fresh_stop(
            entry=float(row["entry"]),
            original_stop=float(row["stop"]),
            target=float(row["target"]),
            tolerance=float(context["atr_tolerance"]),
            prior_afternoon_high=prior_high,
            opening_high=opening_high,
        )
        decisions[signal_key] = decision
        adjusted.at[index, "fresh_stop_policy_version"] = POLICY_VERSION
        adjusted.at[index, "fresh_stop_parent_variant_id"] = PARENT_VARIANT_ID
        adjusted.at[index, "legacy_v2_stop"] = decision.original_stop
        adjusted.at[index, "fresh_stop_prior_afternoon_high"] = prior_high
        adjusted.at[index, "fresh_stop_opening_high"] = opening_high
        adjusted.at[index, "fresh_stop_tolerance"] = decision.tolerance
        adjusted.at[index, "fresh_stop_crv"] = decision.crv
        adjusted.at[index, "fresh_stop_reason"] = decision.reason
        adjusted.at[index, "stop"] = decision.stop
        if not decision.accepted:
            blocked_keys.add(signal_key)

    versioned_records: list[decision_model.DecisionRecord] = []
    for record in final_records:
        decision = decisions.get(record.signal_key)
        if decision is None:
            versioned_records.append(record)
            continue
        observed = decision.to_dict()
        factor = decision_model.DecisionFactor(
            factor_code="failed_countermove_fresh_structure_stop_v1",
            category="risk",
            observed_value=observed,
            operator="max_structure_plus_tolerance_then_crv_gte",
            threshold={
                "minimum_crv": MINIMUM_CRV,
                "prior_session_window": list(PRIOR_SESSION_WINDOW),
                "opening_window": list(OPENING_WINDOW),
            },
            passed=decision.accepted,
            is_veto=not decision.accepted,
            reason=(
                "Fresh causal structure defines the stop; V2 stop remains recorded as parent shadow"
            ),
            as_of_time_utc=record.decision_time_utc,
        )
        context = {
            **dict(record.context),
            "failed_countermove_stop": decision.stop,
            "failed_countermove_risk": decision.risk,
            "failed_countermove_crv": decision.crv,
            "fresh_structure_stop": observed,
        }
        if decision.accepted:
            versioned_records.append(
                replace(record, factors=record.factors + (factor,), context=context)
            )
        else:
            versioned_records.append(
                replace(
                    record,
                    status="vetoed",
                    trade_action="no_trade",
                    setup_state="fresh_structure_stop_crv_blocked",
                    primary_reason="failed_countermove_fresh_structure_stop_v1",
                    factors=record.factors + (factor,),
                    context=context,
                )
            )

    adjusted = adjusted[~adjusted["signal_key"].astype(str).isin(blocked_keys)]
    return adjusted.reset_index(drop=True), versioned_records
