"""Decision-time variants of the frozen Manual-Regime V1 engine.

The production V1.0.0 source remains byte-for-byte untouched.  This module
delegates rule evaluation to that frozen engine and changes only which completed
5-minute bar is the daily decision bar.  Runs are process-isolated; a lock also
prevents concurrent calls inside one process while the frozen module's decision
selector is temporarily replaced.
"""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import threading
from pathlib import Path
from typing import Any

import pandas as pd

import decision_records as decision_model
import manual_regime_rules_backtest as _v1


RAW_DIR = _v1.RAW_DIR
_ENGINE_LOCK = threading.Lock()
VARIANT_SIGNAL_KEY_VERSION = "manual-regime-v2"


def detect_decision_time(
    df: pd.DataFrame,
    decision_bar_number: int = 2,
) -> str:
    """Return the bar-start timestamp of the Nth completed session bar.

    Bars are numbered from one.  With regular five-minute bars beginning at
    09:30 ET, the backward-compatible default (bar 2) is 09:35, bar 12 is
    10:25, and bar 18 is 10:55.  Each timestamp is observable five minutes
    later, at 09:40, 10:30, and 11:00 respectively.
    """
    if isinstance(decision_bar_number, bool) or not isinstance(decision_bar_number, int):
        raise TypeError("decision_bar_number must be an integer")
    if decision_bar_number < 1:
        raise ValueError("decision_bar_number must be positive")
    if df.empty:
        raise ValueError("Cannot determine decision time from empty bars")
    daily_starts = df.groupby("day", sort=False)["time"].min().dropna().astype(str)
    if daily_starts.empty:
        raise ValueError("Cannot determine modal session start from empty bars")
    counts = daily_starts.value_counts()
    max_count = int(counts.max())
    modal_start = min(str(value) for value in counts[counts == max_count].index)
    start = pd.Timestamp(f"2000-01-03 {modal_start}")
    selected = start + pd.Timedelta(minutes=5 * (decision_bar_number - 1))
    return selected.strftime("%H:%M")


def source_timezone_for_symbol(symbol: str) -> str:
    return (
        "America/Chicago"
        if str(symbol).strip().upper() in {"SPX", "SPX_FUT"}
        else decision_model.DECISION_TIMEZONE
    )


def correct_source_decision_time_utc(
    decision_time_utc_assuming_new_york: str,
    source_timezone: str,
) -> str:
    """Undo V1's NY assumption, then localize in the symbol's source timezone."""
    raw_naive = (
        pd.Timestamp(decision_time_utc_assuming_new_york)
        .tz_convert(decision_model.DECISION_TIMEZONE)
        .tz_localize(None)
    )
    return (
        raw_naive.tz_localize(source_timezone, ambiguous="raise", nonexistent="raise")
        .tz_convert("UTC")
        .isoformat()
        .replace("+00:00", "Z")
    )


def _versioned_record(
    record: decision_model.DecisionRecord,
    *,
    strategy_version: str,
    version_context: decision_model.DecisionVersionContext,
    config_hash: str,
    source_timezone: str,
) -> decision_model.DecisionRecord:
    rule_id = record.rule_id or str(record.primary_rule).strip().lower()
    provenance = {
        "strategy_variant_id": version_context.strategy_variant_id,
        "engine_version": version_context.engine_version,
        "money_management_version": version_context.money_management_version,
        "rule_id": rule_id,
        "rule_version": version_context.version_for(rule_id),
        "manifest_hash": version_context.manifest_hash,
    }
    corrected_decision_time_utc = correct_source_decision_time_utc(
        record.decision_time_utc, source_timezone
    )
    corrected_record = replace(
        record,
        decision_time_utc=corrected_decision_time_utc,
        factors=tuple(
            replace(factor, as_of_time_utc=corrected_decision_time_utc)
            for factor in record.factors
        ),
    )
    is_baseline_identity = (
        version_context.strategy_variant_id == decision_model.STRATEGY_VARIANT_ID
        and strategy_version == decision_model.STRATEGY_VERSION
        and corrected_decision_time_utc == record.decision_time_utc
    )
    if is_baseline_identity:
        decision_key = record.decision_key
        signal_key = record.signal_key
        signal_key_version = record.signal_key_version
    else:
        decision_key = _variant_signal_key(
            corrected_record,
            version_context=version_context,
            primary_rule="__decision__",
            side="flat",
            track="decision",
            occurrence=0,
        )
        signal_key = (
            decision_key
            if record.primary_rule == "__decision__"
            else _variant_signal_key(
                corrected_record,
                version_context=version_context,
                primary_rule=record.primary_rule,
                side=record.side,
                track=record.track,
                occurrence=record.occurrence,
            )
        )
        signal_key_version = VARIANT_SIGNAL_KEY_VERSION
    return replace(
        corrected_record,
        decision_key=decision_key,
        signal_key=signal_key,
        signal_key_version=signal_key_version,
        strategy_version=strategy_version,
        strategy_variant_id=version_context.strategy_variant_id,
        engine_version=version_context.engine_version,
        money_management_version=version_context.money_management_version,
        rule_id=rule_id,
        rule_version=version_context.version_for(rule_id),
        config_hash=config_hash,
        context={**dict(record.context), "version_provenance": provenance},
    )


def _variant_signal_key(
    record: decision_model.DecisionRecord,
    *,
    version_context: decision_model.DecisionVersionContext,
    primary_rule: str,
    side: str,
    track: str,
    occurrence: int,
) -> str:
    """Build a V2 identity whose namespace includes the strategy variant."""
    identity = {
        "key_version": VARIANT_SIGNAL_KEY_VERSION,
        "strategy_id": record.strategy_id.strip().lower(),
        "strategy_variant_id": version_context.strategy_variant_id.strip().lower(),
        "decision_time_utc": record.decision_time_utc,
        "symbol": record.symbol.strip().upper(),
        "primary_rule": str(primary_rule).strip().lower(),
        "side": str(side).strip().lower(),
        "track": str(track).strip().lower(),
        "occurrence": int(occurrence),
    }
    encoded = json.dumps(
        identity,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()[:32]
    return f"{VARIANT_SIGNAL_KEY_VERSION}:{digest}"


def run_backtest(
    symbol: str,
    start: str,
    end_exclusive: str,
    structure_lookback: int = 10,
    bull_continuation_mode: str = "none",
    learn_long_mode: str = "off",
    learn_long_window: int = 5,
    learn_long_min_trades: int = 3,
    learn_long_min_win_rate: float = 0.45,
    learn_long_cooldown_days: int = 15,
    bars: pd.DataFrame | None = None,
    decision_log_path: str | Path | None = None,
    runtime_log_path: str | Path | None = None,
    structural_reset_mode: str = "off",
    max_history_days: int | None = None,
    decision_records_out: list[decision_model.DecisionRecord] | None = None,
    decision_records_jsonl_path: str | Path | None = None,
    *,
    decision_bar_number: int = 2,
    strategy_version: str = decision_model.STRATEGY_VERSION,
    decision_version_context: decision_model.DecisionVersionContext = (
        decision_model.BASELINE_VERSION_CONTEXT
    ),
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Run frozen V1 rules at an explicitly selected completed session bar."""
    # Validate before loading multi-year data.
    if isinstance(decision_bar_number, bool) or not isinstance(decision_bar_number, int):
        raise TypeError("decision_bar_number must be an integer")
    if decision_bar_number < 1:
        raise ValueError("decision_bar_number must be positive")
    source_timezone = source_timezone_for_symbol(symbol)

    base_records: list[decision_model.DecisionRecord] = []
    with _ENGINE_LOCK:
        original_selector = _v1.detect_decision_time
        _v1.detect_decision_time = lambda frame: detect_decision_time(
            frame, decision_bar_number
        )
        try:
            result = _v1.run_backtest(
                symbol,
                start,
                end_exclusive,
                structure_lookback=structure_lookback,
                bull_continuation_mode=bull_continuation_mode,
                learn_long_mode=learn_long_mode,
                learn_long_window=learn_long_window,
                learn_long_min_trades=learn_long_min_trades,
                learn_long_min_win_rate=learn_long_min_win_rate,
                learn_long_cooldown_days=learn_long_cooldown_days,
                bars=bars,
                decision_log_path=decision_log_path,
                runtime_log_path=runtime_log_path,
                structural_reset_mode=structural_reset_mode,
                max_history_days=max_history_days,
                decision_records_out=base_records,
                decision_records_jsonl_path=None,
            )
        finally:
            _v1.detect_decision_time = original_selector

    timing_config_hash = decision_model.stable_config_hash(
        {
            "delegate_config_hashes": sorted({record.config_hash for record in base_records}),
            "decision_bar_number": decision_bar_number,
            "strategy_version": strategy_version,
            "strategy_variant_id": decision_version_context.strategy_variant_id,
            "engine_version": decision_version_context.engine_version,
            "manifest_hash": decision_version_context.manifest_hash,
        }
    )
    versioned = [
        _versioned_record(
            record,
            strategy_version=strategy_version,
            version_context=decision_version_context,
            config_hash=timing_config_hash,
            source_timezone=source_timezone,
        )
        for record in base_records
    ]
    if decision_records_out is not None:
        decision_records_out.extend(versioned)
    if decision_records_jsonl_path is not None:
        decision_model.write_jsonl(decision_records_jsonl_path, versioned)
    return result


def __getattr__(name: str) -> Any:
    """Expose read-only V1 helpers for analysis code without copying the engine."""
    return getattr(_v1, name)
