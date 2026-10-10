from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from research.audit_single_stock_drawdown_option_fit import load_sec_earnings_dates
from research.backtest_high_ivr_put_credit_no_earnings import (
    load_daily_with_open,
    load_universe,
)
from research.calibrate_iv_proxy_vix_earnings import (
    market_curve,
    modeled_curve_for_symbol,
    predict_next_earnings,
)


CANDIDATE_COLUMNS = (
    "symbol",
    "signal_date",
    "entry_date",
    "modeled_iv_rank",
    "previous_modeled_iv_rank",
    "median_dollar_volume60",
    "predicted_earnings",
    "earnings_cycle_days",
    "earnings_history_count",
)


@dataclass(frozen=True, slots=True)
class LiveScanConfig:
    universe_path: Path
    raw_dir: Path
    vix_path: Path
    spy_path: Path
    sec_cache_dir: Path
    min_latest_price: float
    min_median_dollar_volume: float
    min_sessions: int
    min_last_session: pd.Timestamp
    ivr_threshold: float
    ivr_lookback: int
    min_ivr_observations: int
    iv_rank_dte: int
    market_weight: float
    earnings_jump: float
    earnings_cycle_lookback: int
    vrp_min: float
    vrp_max: float
    maximum_expiry_dte: int
    earnings_buffer_days: int


def build_live_candidates(
    entry_day: pd.Timestamp | str,
    config: LiveScanConfig,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return yesterday's causal IVR crosses eligible for entry today."""
    entry_day = pd.Timestamp(entry_day).normalize()
    symbols = load_universe(
        config.universe_path,
        config.min_latest_price,
        config.min_median_dollar_volume,
        config.min_sessions,
        config.min_last_session,
    )
    earnings_by_symbol, _ = load_sec_earnings_dates(
        symbols, config.sec_cache_dir, "cache-only"
    )
    market = market_curve(
        config.vix_path, config.spy_path, config.vrp_min, config.vrp_max
    )
    rows: list[dict[str, object]] = []
    diagnostics: list[dict[str, object]] = []
    for symbol in symbols:
        try:
            daily = load_daily_with_open(
                config.raw_dir,
                symbol,
                config.ivr_lookback,
                config.min_ivr_observations,
            )
            curve = modeled_curve_for_symbol(
                daily,
                market,
                earnings_by_symbol.get(symbol, []),
                config.market_weight,
                config.earnings_jump,
                config.earnings_cycle_lookback,
                config.iv_rank_dte,
                config.ivr_lookback,
                config.min_ivr_observations,
            )
            prior = curve.index[curve.index < entry_day]
            if len(prior) < 2:
                diagnostics.append({"symbol": symbol, "status": "missing_prior_sessions"})
                continue
            signal_day, previous_day = pd.Timestamp(prior[-1]), pd.Timestamp(prior[-2])
            current_rank = float(curve.loc[signal_day, "modeled_iv_rank"])
            previous_rank = float(curve.loc[previous_day, "modeled_iv_rank"])
            if not (
                current_rank > config.ivr_threshold
                and previous_rank <= config.ivr_threshold
            ):
                continue
            predicted, cycle_days, history_count = predict_next_earnings(
                signal_day,
                earnings_by_symbol.get(symbol, []),
                config.earnings_cycle_lookback,
            )
            if pd.isna(predicted):
                diagnostics.append({"symbol": symbol, "status": "unknown_earnings"})
                continue
            # The concrete listed expiration is only known after the live option
            # chain is loaded.  The monitor applies the causal earnings veto
            # against that expiration; filtering against the maximum DTE here
            # would incorrectly reject otherwise valid nearer expirations.
            rows.append(
                {
                    "symbol": symbol,
                    "signal_date": signal_day.strftime("%Y-%m-%d"),
                    "entry_date": entry_day.strftime("%Y-%m-%d"),
                    "modeled_iv_rank": current_rank,
                    "previous_modeled_iv_rank": previous_rank,
                    "median_dollar_volume60": float(
                        daily.loc[signal_day, "median_dollar_volume60"]
                    ),
                    "predicted_earnings": pd.Timestamp(predicted).strftime("%Y-%m-%d"),
                    "earnings_cycle_days": cycle_days,
                    "earnings_history_count": history_count,
                }
            )
        except Exception as exc:
            diagnostics.append(
                {
                    "symbol": symbol,
                    "status": "error",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
    candidates = pd.DataFrame(rows, columns=CANDIDATE_COLUMNS)
    if not candidates.empty:
        candidates = candidates.sort_values(
            ["modeled_iv_rank", "median_dollar_volume60", "symbol"],
            ascending=[False, False, True],
        ).reset_index(drop=True)
    return candidates, pd.DataFrame(diagnostics)
