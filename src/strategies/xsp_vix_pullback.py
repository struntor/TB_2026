from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
from pathlib import Path
from typing import Any

import pandas as pd


STRATEGY_ID = "xsp-vix-pullback-100dte-put-credit"
STRATEGY_VERSION = "1.0.2-paper"
STRATEGY_VARIANT_ID = f"{STRATEGY_ID}@{STRATEGY_VERSION}"
ENGINE_VERSION = "xsp-vix-pullback-live@1.0.2"
MONEY_MANAGEMENT_VERSION = "shared-defined-risk-2500@1.0.0"
SIGNAL_KEY_VERSION = "xsp-vix-pullback-signal@2"
ENTRY_RULE_ID = "xsp-vix19-drop1-momentum20-entry"
ENTRY_RULE_VERSION = "1.0.2"
PROFIT_RULE_ID = "xsp-put-credit-tp60"
PROFIT_RULE_VERSION = "1.0.1"
TIME_EXIT_RULE_ID = "xsp-put-credit-time60"
TIME_EXIT_RULE_VERSION = "1.0.1"


@dataclass(frozen=True, slots=True)
class XspVixPullbackConfig:
    entry_time_et: str = "13:35:00"
    raw_cboe_entry_time_ct: str = "12:35:00"
    latest_submission_time_et: str = "13:50:00"
    spike_threshold: float = 19.0
    drop_from_peak: float = 1.0
    minimum_entry_vix: float = 15.0
    cooldown_calendar_days: int = 25
    maximum_reversal_sessions: int = 10
    momentum_sessions: int = 20
    target_calendar_dte_floor: int = 100
    target_short_delta: float = -0.20
    maximum_delta_deviation: float = 0.015
    width_points: float = 10.0
    profit_target_fraction: float = 0.60
    time_exit_remaining_dte: int = 60
    maximum_relative_leg_spread: float = 0.20
    quantity: int = 1
    maximum_concurrent_positions: int = 2


@dataclass(frozen=True, slots=True)
class EntryEvaluation:
    day: str
    status: str
    reason: str
    decision_time: pd.Timestamp
    vix: float | None
    peak_vix: float | None
    peak_day: str | None
    vix_drop: float | None
    momentum_20_sessions: float | None
    factors: tuple[dict[str, Any], ...]

    @property
    def accepted(self) -> bool:
        return self.status == "accepted"


def config_hash(config: XspVixPullbackConfig) -> str:
    return hashlib.sha256(repr(asdict(config)).encode("utf-8")).hexdigest()


def load_intraday_value_frame(path: Path, clock: str, value_name: str) -> pd.DataFrame:
    raw = pd.read_csv(path, usecols=["date", "close"], low_memory=False)
    raw["date"] = pd.to_datetime(raw["date"], errors="coerce")
    raw[value_name] = pd.to_numeric(raw["close"], errors="coerce")
    raw = raw.dropna(subset=["date", value_name])
    selected = raw[raw["date"].dt.strftime("%H:%M:%S").eq(clock)].copy()
    selected["trade_date"] = selected["date"].dt.normalize()
    return selected[["date", "trade_date", value_name]].sort_values("date")


def load_daily_close_frame(path: Path, value_name: str) -> pd.DataFrame:
    raw = pd.read_csv(path, usecols=["date", "close"], low_memory=False)
    raw["date"] = pd.to_datetime(raw["date"], errors="coerce")
    raw[value_name] = pd.to_numeric(raw["close"], errors="coerce")
    raw = raw.dropna(subset=["date", value_name]).sort_values("date")
    raw["trade_date"] = raw["date"].dt.normalize()
    return raw.groupby("trade_date", as_index=False).last()[["date", "trade_date", value_name]]


def compute_reversal_signals(
    frame: pd.DataFrame,
    config: XspVixPullbackConfig = XspVixPullbackConfig(),
) -> pd.DataFrame:
    """Causal state machine used by the frozen research candidate."""
    rows: list[dict[str, Any]] = []
    peak_vix: float | None = None
    peak_day: str | None = None
    peak_session_index: int | None = None
    active_until: pd.Timestamp | None = None
    ordered = frame.sort_values("date").drop_duplicates("trade_date", keep="last")
    for session_index, row in enumerate(ordered.itertuples(index=False)):
        day = pd.Timestamp(row.trade_date).normalize()
        current = float(row.vix)
        if active_until is not None and day <= active_until:
            continue
        if peak_session_index is not None and session_index - peak_session_index > config.maximum_reversal_sessions:
            peak_vix = peak_day = peak_session_index = None
        if current >= config.spike_threshold and (peak_vix is None or current > peak_vix):
            peak_vix = current
            peak_day = day.strftime("%Y-%m-%d")
            peak_session_index = session_index
            continue
        if peak_vix is None:
            continue
        drop = peak_vix - current
        if drop >= config.drop_from_peak and current >= config.minimum_entry_vix:
            rows.append(
                {
                    "peak_day": peak_day,
                    "peak_vix": peak_vix,
                    "entry_day": day.strftime("%Y-%m-%d"),
                    "entry_vix": current,
                    "vix_drop": drop,
                }
            )
            active_until = day + pd.Timedelta(days=config.cooldown_calendar_days)
            peak_vix = peak_day = peak_session_index = None
    return pd.DataFrame(rows)


def momentum_at_entry(
    spx_frame: pd.DataFrame,
    day: pd.Timestamp | str,
    current_spx: float,
    sessions: int = 20,
) -> float | None:
    entry_day = pd.Timestamp(day).normalize()
    prior = spx_frame.loc[spx_frame["trade_date"] < entry_day, "spx"].dropna()
    if len(prior) < sessions or current_spx <= 0:
        return None
    return float(current_spx / float(prior.iloc[-sessions]) - 1.0)


def evaluate_entry(
    day: pd.Timestamp | str,
    *,
    vix_frame: pd.DataFrame,
    spx_frame: pd.DataFrame,
    current_vix: float | None,
    current_spx: float | None,
    config: XspVixPullbackConfig = XspVixPullbackConfig(),
) -> EntryEvaluation:
    stamp = pd.Timestamp(day).normalize()
    day_text = stamp.strftime("%Y-%m-%d")
    decision = pd.Timestamp(f"{day_text} {config.entry_time_et}")
    if current_vix is None or current_vix <= 0 or current_spx is None or current_spx <= 0:
        return EntryEvaluation(
            day_text, "missing_data", "live_vix_or_spx_unavailable", decision,
            current_vix, None, None, None, None, (),
        )
    current_row = pd.DataFrame(
        [{"date": decision, "trade_date": stamp, "vix": float(current_vix)}]
    )
    history = vix_frame.loc[vix_frame["trade_date"] < stamp, ["date", "trade_date", "vix"]]
    signals = compute_reversal_signals(pd.concat([history, current_row], ignore_index=True), config)
    today_signal = signals[signals["entry_day"].eq(day_text)] if not signals.empty else signals
    signaled = not today_signal.empty
    signal = today_signal.iloc[-1] if signaled else None
    momentum = momentum_at_entry(spx_frame, stamp, float(current_spx), config.momentum_sessions)
    momentum_ok = momentum is not None and momentum > 0
    factors = (
        {
            "factor_code": "vix_pullback_state_machine",
            "observed_value": bool(signaled),
            "operator": "==",
            "threshold": True,
            "passed": bool(signaled),
        },
        {
            "factor_code": "spx_momentum_20_sessions",
            "observed_value": momentum,
            "operator": ">",
            "threshold": 0.0,
            "passed": bool(momentum_ok),
        },
    )
    if not signaled:
        status, reason = "no_setup", "vix_pullback_state_machine"
    elif not momentum_ok:
        status, reason = "no_setup", "spx_momentum_20_sessions"
    else:
        status, reason = "accepted", "vix_pullback_and_positive_momentum"
    return EntryEvaluation(
        day_text, status, reason, decision, float(current_vix),
        float(signal["peak_vix"]) if signaled else None,
        str(signal["peak_day"]) if signaled else None,
        float(signal["vix_drop"]) if signaled else None,
        momentum, factors,
    )
