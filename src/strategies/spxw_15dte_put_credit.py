from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import hashlib
from typing import Any

import numpy as np
import pandas as pd


STRATEGY_ID = "spx-15dte-put-credit-income-core"
STRATEGY_VERSION = "1.1.0"
STRATEGY_VARIANT_ID = f"{STRATEGY_ID}@{STRATEGY_VERSION}"
ENGINE_VERSION = "spxw-15dte-live-engine@1.0.0"
MONEY_MANAGEMENT_VERSION = "one-contract-max-six-damage-reentry@1.0.0"
SIGNAL_KEY_VERSION = "spxw15-pcs-signal@1"
ENTRY_RULE_ID = "spxw-15dte-causal-entry"
ENTRY_RULE_VERSION = "1.0.0"
DAMAGE_RULE_ID = "spxw-15dte-damage-exit"
DAMAGE_RULE_VERSION = "1.0.0"


@dataclass(frozen=True, slots=True)
class Spxw15DteConfig:
    signal_bar_time: str = "13:30:00"
    entry_time: str = "13:35:00"
    target_calendar_dte: int = 15
    target_short_delta: float = -0.25
    spread_width_points: float = 10.0
    minimum_open_to_signal: float = 0.001
    minimum_entry_credit: float = 1.4
    maximum_gap_down: float = -0.005
    high_iv_threshold: float = 0.19
    low10_distance_block: float = 0.05
    reentry_cooldown_trading_days: int = 3
    reentry_minimum_low10_distance: float = 0.003
    damage_low10_distance: float = 0.003
    damage_high5_drawdown: float = -0.02
    maximum_friction_gap_dollars: float = 100.0
    maximum_concurrent_positions: int = 6
    quantity: int = 1


@dataclass(frozen=True, slots=True)
class EntryEvaluation:
    day: str
    status: str
    reason: str
    decision_time: pd.Timestamp
    spy_open: float | None
    signal_close: float | None
    previous_close: float | None
    previous_ema20: float | None
    gap: float | None
    open_to_signal: float | None
    dist_low10: float | None
    trading_day_rank: int | None
    reentry_elapsed_days: int | None
    factors: tuple[dict[str, Any], ...]

    @property
    def accepted(self) -> bool:
        return self.status == "accepted"


@dataclass(frozen=True, slots=True)
class DamageEvaluation:
    day: str
    status: str
    reason: str
    decision_time: pd.Timestamp
    close: float | None
    dist_low10: float | None
    off_high5: float | None
    factors: tuple[dict[str, Any], ...]

    @property
    def triggered(self) -> bool:
        return self.status == "accepted"


def config_hash(config: Spxw15DteConfig) -> str:
    return hashlib.sha256(repr(asdict(config)).encode("utf-8")).hexdigest()


def _bars(bars: pd.DataFrame) -> pd.DataFrame:
    required = {"date", "open", "high", "low", "close"}
    missing = required - set(bars.columns)
    if missing:
        raise ValueError(f"SPY bars missing columns: {sorted(missing)}")
    out = bars.copy()
    stamps = pd.to_datetime(out["date"], errors="coerce")
    if getattr(stamps.dt, "tz", None) is not None:
        stamps = stamps.dt.tz_convert("America/New_York").dt.tz_localize(None)
    out["date"] = stamps
    out = out.dropna(subset=["date"]).sort_values("date")
    out["trade_date"] = out["date"].dt.strftime("%Y-%m-%d")
    out["time"] = out["date"].dt.strftime("%H:%M:%S")
    return out


def _daily(bars: pd.DataFrame) -> pd.DataFrame:
    daily = (
        bars.groupby("trade_date", sort=True)
        .agg(open=("open", "first"), high=("high", "max"), low=("low", "min"), close=("close", "last"))
        .reset_index()
    )
    daily["ema20"] = daily["close"].ewm(span=20, adjust=False).mean()
    daily["low10"] = daily["low"].rolling(10).min()
    daily["high5"] = daily["high"].rolling(5).max()
    daily["rank"] = np.arange(len(daily))
    return daily


def _finite(value: object) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if np.isfinite(parsed) else None


def evaluate_entry(
    bars: pd.DataFrame,
    day: pd.Timestamp | str,
    config: Spxw15DteConfig = Spxw15DteConfig(),
    *,
    reentry_activation_day: str | None = None,
) -> EntryEvaluation:
    frame = _bars(bars)
    day_text = pd.Timestamp(day).strftime("%Y-%m-%d")
    daily = _daily(frame)
    day_rows = frame[frame["trade_date"].eq(day_text)]
    signal = day_rows[day_rows["time"].eq(config.signal_bar_time)]
    decision_time = pd.Timestamp(f"{day_text} {config.entry_time}")
    if signal.empty or day_rows.empty:
        return EntryEvaluation(day_text, "missing_data", "missing_signal_bar", decision_time, None, None, None, None, None, None, None, None, None, ())
    daily_index = daily.index[daily["trade_date"].eq(day_text)]
    if len(daily_index) != 1 or int(daily_index[0]) < 1:
        return EntryEvaluation(day_text, "missing_data", "missing_daily_history", decision_time, None, None, None, None, None, None, None, None, None, ())
    idx = int(daily_index[0])
    prior = daily.iloc[idx - 1]
    current_rank = int(daily.iloc[idx]["rank"])
    through_signal = day_rows[day_rows["date"].le(signal.iloc[-1]["date"])]
    spy_open = _finite(day_rows.iloc[0]["open"])
    signal_close = _finite(signal.iloc[-1]["close"])
    previous_close = _finite(prior["close"])
    previous_ema20 = _finite(prior["ema20"])
    prior_low10 = _finite(prior["low10"])
    day_low = _finite(pd.to_numeric(through_signal["low"], errors="coerce").min())
    low10 = min(value for value in (prior_low10, day_low) if value is not None) if any(value is not None for value in (prior_low10, day_low)) else None
    gap = spy_open / previous_close - 1.0 if spy_open and previous_close else None
    move = signal_close / spy_open - 1.0 if signal_close and spy_open else None
    dist_low10 = signal_close / low10 - 1.0 if signal_close and low10 else None
    elapsed = None
    if reentry_activation_day:
        activation = daily.index[daily["trade_date"].eq(str(reentry_activation_day))]
        if len(activation) == 1:
            elapsed = current_rank - int(daily.iloc[int(activation[0])]["rank"])
    checks = (
        ("open_to_1330_ge_0p1pct", move, ">=", config.minimum_open_to_signal, move is not None and move >= config.minimum_open_to_signal),
        ("previous_close_above_ema20", previous_close, ">", previous_ema20, previous_close is not None and previous_ema20 is not None and previous_close > previous_ema20),
        ("opening_gap_gt_minus_0p5pct", gap, ">", config.maximum_gap_down, gap is not None and gap > config.maximum_gap_down),
    )
    factors = [
        {"factor_code": code, "observed_value": observed, "operator": operator, "threshold": threshold, "passed": passed}
        for code, observed, operator, threshold, passed in checks
    ]
    if reentry_activation_day:
        cooldown_pass = elapsed is not None and elapsed >= config.reentry_cooldown_trading_days
        distance_pass = dist_low10 is not None and dist_low10 > config.reentry_minimum_low10_distance
        factors.extend(
            [
                {"factor_code": "reentry_cooldown_trading_days", "observed_value": elapsed, "operator": ">=", "threshold": config.reentry_cooldown_trading_days, "passed": cooldown_pass},
                {"factor_code": "reentry_not_near_low10", "observed_value": dist_low10, "operator": ">", "threshold": config.reentry_minimum_low10_distance, "passed": distance_pass},
            ]
        )
    failed = next((factor for factor in factors if not factor["passed"]), None)
    return EntryEvaluation(
        day=day_text,
        status="accepted" if failed is None else "vetoed",
        reason="causal_entry_prequote_accepted" if failed is None else str(failed["factor_code"]),
        decision_time=decision_time,
        spy_open=spy_open,
        signal_close=signal_close,
        previous_close=previous_close,
        previous_ema20=previous_ema20,
        gap=gap,
        open_to_signal=move,
        dist_low10=dist_low10,
        trading_day_rank=current_rank,
        reentry_elapsed_days=elapsed,
        factors=tuple(factors),
    )


def apply_quote_gates(
    evaluation: EntryEvaluation,
    *,
    conservative_credit: float,
    short_iv: float | None,
    config: Spxw15DteConfig = Spxw15DteConfig(),
) -> EntryEvaluation:
    if not evaluation.accepted:
        return evaluation
    credit_pass = np.isfinite(conservative_credit) and conservative_credit >= config.minimum_entry_credit
    iv_distance_block = (
        short_iv is not None
        and np.isfinite(short_iv)
        and short_iv >= config.high_iv_threshold
        and evaluation.dist_low10 is not None
        and evaluation.dist_low10 <= config.low10_distance_block
    )
    extra = (
        {"factor_code": "conservative_credit_minimum", "observed_value": conservative_credit, "operator": ">=", "threshold": config.minimum_entry_credit, "passed": bool(credit_pass)},
        {"factor_code": "high_iv_near_low10_block", "observed_value": {"short_iv": short_iv, "dist_low10": evaluation.dist_low10}, "operator": "not_both", "threshold": {"short_iv": config.high_iv_threshold, "dist_low10": config.low10_distance_block}, "passed": not iv_distance_block},
    )
    failed = next((factor for factor in extra if not factor["passed"]), None)
    return replace(
        evaluation,
        status="accepted" if failed is None else "vetoed",
        reason="causal_entry_all_gates_accepted" if failed is None else str(failed["factor_code"]),
        factors=evaluation.factors + extra,
    )


def evaluate_damage(
    bars: pd.DataFrame,
    day: pd.Timestamp | str,
    config: Spxw15DteConfig = Spxw15DteConfig(),
) -> DamageEvaluation:
    frame = _bars(bars)
    day_text = pd.Timestamp(day).strftime("%Y-%m-%d")
    daily = _daily(frame)
    rows = frame[frame["trade_date"].eq(day_text)]
    signal = rows[rows["time"].eq("15:55:00")]
    decision_time = pd.Timestamp(f"{day_text} 16:00:00")
    index = daily.index[daily["trade_date"].eq(day_text)]
    if signal.empty or len(index) != 1:
        return DamageEvaluation(day_text, "missing_data", "missing_1555_bar", decision_time, None, None, None, ())
    idx = int(index[0])
    prior = daily.iloc[idx - 1] if idx else daily.iloc[idx]
    close = _finite(signal.iloc[-1]["close"])
    day_low = _finite(pd.to_numeric(rows["low"], errors="coerce").min())
    day_high = _finite(pd.to_numeric(rows["high"], errors="coerce").max())
    prior_low10 = _finite(prior["low10"])
    prior_high5 = _finite(prior["high5"])
    low10 = min(value for value in (prior_low10, day_low) if value is not None)
    high5 = max(value for value in (prior_high5, day_high) if value is not None)
    dist = close / low10 - 1.0 if close and low10 else None
    off = close / high5 - 1.0 if close and high5 else None
    low_pass = dist is not None and dist <= config.damage_low10_distance
    high_pass = off is not None and off <= config.damage_high5_drawdown
    factors = (
        {"factor_code": "damage_low10_near_eod", "observed_value": dist, "operator": "<=", "threshold": config.damage_low10_distance, "passed": low_pass},
        {"factor_code": "damage_off_high5_2pct_eod", "observed_value": off, "operator": "<=", "threshold": config.damage_high5_drawdown, "passed": high_pass},
    )
    triggered = low_pass and high_pass
    return DamageEvaluation(day_text, "accepted" if triggered else "no_setup", "damage_combo_accepted" if triggered else "damage_combo_not_met", decision_time, close, dist, off, factors)
