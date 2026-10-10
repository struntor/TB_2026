"""Variant-safe adapter for the frozen live baseline filter stack."""

from __future__ import annotations

import threading
from typing import Any

import pandas as pd

import decision_records as decision_model
import live_manual_regime_tws_bot as frozen_live


_FILTER_LOCK = threading.Lock()


def apply_live_baseline_filters_for_variant(
    trades: pd.DataFrame,
    keep_pairs: set[tuple[str, str]],
    day: pd.Timestamp,
    *,
    strategy_variant_id: str,
    decision_records: list[decision_model.DecisionRecord],
    final_records_out: list[decision_model.DecisionRecord],
    model_history: Any,
) -> pd.DataFrame:
    """Apply unchanged V1 filters while validating against a variant namespace.

    The frozen filter implementation reads its expected variant from the shared
    decision-record module.  Variant backtests are portfolio-sequential, so this
    adapter changes that value only under a process-local lock and restores it
    even if filtering fails.
    """
    if model_history.strategy_variant_id != strategy_variant_id:
        raise ValueError("model history strategy variant does not match requested variant")
    with _FILTER_LOCK:
        original_variant_id = decision_model.STRATEGY_VARIANT_ID
        decision_model.STRATEGY_VARIANT_ID = strategy_variant_id
        try:
            return frozen_live.apply_live_baseline_filters(
                trades,
                keep_pairs,
                day,
                decision_records=decision_records,
                final_records_out=final_records_out,
                model_history=model_history,
            )
        finally:
            decision_model.STRATEGY_VARIANT_ID = original_variant_id
