from __future__ import annotations

import argparse
import math
from pathlib import Path
import sys

import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from research.audit_single_stock_drawdown_option_fit import load_sec_earnings_dates  # noqa: E402
from research.calibrate_iv_proxy_vix_earnings import market_curve, modeled_iv, predict_next_earnings  # noqa: E402
from research.model_drawdown_recovery_put_credit_proxy import (  # noqa: E402
    load_daily,
    load_splits,
    put_delta,
    put_price,
    round_strike,
    split_context,
    strike_for_put_delta,
    strike_increment,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backtest out-and-down rolls as complete put-credit campaigns.")
    parser.add_argument("--screen", type=Path, required=True)
    parser.add_argument("--spreads", type=Path, required=True)
    parser.add_argument("--raw-dir", type=Path, required=True)
    parser.add_argument("--vix-path", type=Path, required=True)
    parser.add_argument("--spy-path", type=Path, required=True)
    parser.add_argument("--expiration-dir", type=Path, required=True)
    parser.add_argument("--sec-cache-dir", type=Path, required=True)
    parser.add_argument("--sec-mode", choices=["download", "cache-only"], required=True)
    parser.add_argument("--split-cache-dir", type=Path, required=True)
    parser.add_argument("--split-mode", choices=["download", "cache-only"], required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--max-rolls-values", required=True)
    parser.add_argument("--target-roll-dte", type=int, required=True)
    parser.add_argument("--min-roll-dte", type=int, required=True)
    parser.add_argument("--max-roll-dte", type=int, required=True)
    parser.add_argument("--mandatory-exit-dte", type=int, required=True)
    parser.add_argument("--min-roll-net-credit", type=float, required=True)
    parser.add_argument("--short-strike-touch", choices=["on", "off"], required=True)
    parser.add_argument("--initial-entry-time", required=True)
    parser.add_argument("--touch-iv-multiplier", type=float, required=True)
    parser.add_argument("--market-weight", type=float, required=True)
    parser.add_argument("--earnings-jump", type=float, required=True)
    parser.add_argument("--earnings-cycle-lookback", type=int, required=True)
    parser.add_argument("--vrp-min", type=float, required=True)
    parser.add_argument("--vrp-max", type=float, required=True)
    parser.add_argument("--skew-slope", type=float, required=True)
    parser.add_argument("--risk-free-rate", type=float, required=True)
    parser.add_argument("--take-profit-fraction", type=float, required=True)
    parser.add_argument("--entry-slippage-min", type=float, required=True)
    parser.add_argument("--entry-slippage-fraction", type=float, required=True)
    parser.add_argument("--exit-slippage-min", type=float, required=True)
    parser.add_argument("--exit-slippage-fraction", type=float, required=True)
    parser.add_argument("--opening-fees", type=float, required=True)
    parser.add_argument("--closing-fees", type=float, required=True)
    parser.add_argument("--ivr-lookback", type=int, required=True)
    parser.add_argument("--min-ivr-observations", type=int, required=True)
    return parser.parse_args()


def load_expirations(expiration_dir: Path, symbol: str) -> list[pd.Timestamp]:
    path = expiration_dir / f"expirations_{symbol}.csv"
    frame = pd.read_csv(path)
    if "expiration" not in frame.columns:
        raise ValueError(f"Missing expiration column in {path}")
    values = pd.to_datetime(frame["expiration"], errors="coerce").dropna().dt.normalize().unique()
    return sorted(pd.Timestamp(value) for value in values)


def load_intraday(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path, usecols=["date", "open", "low", "close"], low_memory=False)
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    for column in ["open", "low", "close"]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame.dropna(subset=["date", "open", "low", "close"]).sort_values("date").set_index("date")


def select_roll_expiration(
    expirations: list[pd.Timestamp],
    roll_date: pd.Timestamp,
    current_expiration: pd.Timestamp,
    target_dte: int,
    min_dte: int,
    max_dte: int,
) -> pd.Timestamp | None:
    valid = [
        expiration
        for expiration in expirations
        if expiration > current_expiration and min_dte <= (expiration - roll_date).days <= max_dte
    ]
    if not valid:
        return None
    return min(valid, key=lambda expiration: abs((expiration - roll_date).days - target_dte))


def modeled_volatility(
    session: pd.Timestamp,
    expiration: pd.Timestamp,
    stock_row: pd.Series,
    market: pd.DataFrame,
    earnings_dates: list[pd.Timestamp],
    args: argparse.Namespace,
) -> tuple[float, float, float]:
    remaining_dte = int((expiration - session).days)
    predicted, _, _ = predict_next_earnings(session, earnings_dates, args.earnings_cycle_lookback)
    expected_days = float((predicted - session).days) if pd.notna(predicted) else np.nan
    short_iv, earnings_inside = modeled_iv(
        float(stock_row["iv_base_proxy"]),
        float(market.loc[session, "market_vrp"]),
        args.market_weight,
        args.earnings_jump,
        expected_days,
        remaining_dte,
    )
    return short_iv, expected_days, float(earnings_inside)


def close_leg_value(
    session: pd.Timestamp,
    stock_row: pd.Series,
    leg: dict,
    market: pd.DataFrame,
    earnings_dates: list[pd.Timestamp],
    args: argparse.Namespace,
) -> dict | None:
    if session not in market.index:
        return None
    remaining_dte = int((leg["expiration"] - session).days)
    short_iv, expected_days, earnings_inside = modeled_volatility(
        session, leg["expiration"], stock_row, market, earnings_dates, args
    )
    if not np.isfinite(short_iv):
        return None
    spot = float(stock_row["close"]) * leg["split_factor"]
    long_iv = short_iv + args.skew_slope * abs(math.log(leg["long_strike"] / leg["short_strike"]))
    years = remaining_dte / 365.0
    mid_debit = put_price(spot, leg["short_strike"], years, args.risk_free_rate, short_iv) - put_price(
        spot, leg["long_strike"], years, args.risk_free_rate, long_iv
    )
    exit_slippage = max(args.exit_slippage_min, args.exit_slippage_fraction * max(0.0, mid_debit))
    return {
        "spot": spot,
        "remaining_dte": remaining_dte,
        "expected_days_to_earnings": expected_days,
        "earnings_inside": earnings_inside,
        "short_iv": short_iv,
        "long_iv": long_iv,
        "mid_debit": mid_debit,
        "close_debit": max(0.0, mid_debit + exit_slippage),
    }


def touch_leg_value(
    session: pd.Timestamp,
    leg: dict,
    intraday: pd.DataFrame,
    vix_intraday: pd.DataFrame,
    daily: pd.DataFrame,
    market: pd.DataFrame,
    earnings_dates: list[pd.Timestamp],
    args: argparse.Namespace,
) -> dict | None:
    session_end = session + pd.Timedelta(days=1)
    start = max(session, leg["touch_start_timestamp"])
    bars = intraday[(intraday.index >= start) & (intraday.index < session_end)].copy()
    if bars.empty:
        return None
    hits = bars[bars["low"] * leg["split_factor"] <= leg["short_strike"]]
    if hits.empty:
        return None
    touch_time = hits.index[0]
    touch_bar = hits.iloc[0]
    prior_stock = daily[daily.index < session]
    prior_market = market[market.index < session]
    if prior_stock.empty or prior_market.empty:
        return None
    vix_bars = vix_intraday[
        (vix_intraday.index >= session) & (vix_intraday.index <= touch_time)
    ]
    if vix_bars.empty:
        market_vrp = float(prior_market.iloc[-1]["market_vrp"])
        vix_at_touch = np.nan
        vrp_source = "prior_close"
    else:
        vix_at_touch = float(vix_bars.iloc[-1]["close"])
        spy_rv20 = float(prior_market.iloc[-1]["spy_rv20"])
        if not np.isfinite(spy_rv20) or spy_rv20 <= 0:
            return None
        market_vrp = float(np.clip((vix_at_touch / 100.0) / spy_rv20, args.vrp_min, args.vrp_max))
        vrp_source = "intraday_vix"
    remaining_dte = int((leg["expiration"] - session).days)
    predicted, _, _ = predict_next_earnings(session, earnings_dates, args.earnings_cycle_lookback)
    expected_days = float((predicted - session).days) if pd.notna(predicted) else np.nan
    short_iv, earnings_inside = modeled_iv(
        float(prior_stock.iloc[-1]["iv_base_proxy"]),
        market_vrp,
        args.market_weight,
        args.earnings_jump,
        expected_days,
        remaining_dte,
    )
    short_iv *= args.touch_iv_multiplier
    if not np.isfinite(short_iv) or short_iv <= 0:
        return None
    adjusted_bar_open = float(touch_bar["open"]) * leg["split_factor"]
    adjusted_bar_low = float(touch_bar["low"]) * leg["split_factor"]
    adjusted_bar_close = float(touch_bar["close"]) * leg["split_factor"]
    execution_spot = min(leg["short_strike"], adjusted_bar_close)
    long_iv = short_iv + args.skew_slope * abs(math.log(leg["long_strike"] / leg["short_strike"]))
    years = remaining_dte / 365.0
    mid_debit = put_price(
        execution_spot, leg["short_strike"], years, args.risk_free_rate, short_iv
    ) - put_price(execution_spot, leg["long_strike"], years, args.risk_free_rate, long_iv)
    exit_slippage = max(args.exit_slippage_min, args.exit_slippage_fraction * max(0.0, mid_debit))
    return {
        "spot": execution_spot,
        "remaining_dte": remaining_dte,
        "expected_days_to_earnings": expected_days,
        "earnings_inside": float(earnings_inside),
        "short_iv": short_iv,
        "long_iv": long_iv,
        "mid_debit": mid_debit,
        "close_debit": max(0.0, mid_debit + exit_slippage),
        "touch_time": touch_time,
        "touch_bar_open": adjusted_bar_open,
        "touch_bar_low": adjusted_bar_low,
        "touch_bar_close": adjusted_bar_close,
        "vix_at_touch": vix_at_touch,
        "market_vrp_at_touch": market_vrp,
        "vrp_source": vrp_source,
    }


def open_roll_leg(
    symbol: str,
    session: pd.Timestamp,
    expiration: pd.Timestamp,
    stock_row: pd.Series,
    previous_short_strike: float,
    target_delta: float,
    spread_width: float,
    daily: pd.DataFrame,
    market: pd.DataFrame,
    earnings_dates: list[pd.Timestamp],
    splits: list[tuple[pd.Timestamp, float]],
    args: argparse.Namespace,
) -> tuple[dict | None, str | None]:
    split_factor, split_during_leg, split_description = split_context(
        session, expiration, daily.index.max(), splits
    )
    if split_during_leg:
        return None, "roll_crosses_split"
    if session not in market.index:
        return None, "missing_roll_market"
    short_iv, expected_days, earnings_inside = modeled_volatility(
        session, expiration, stock_row, market, earnings_dates, args
    )
    if not np.isfinite(short_iv):
        return None, "missing_roll_iv"
    dte = int((expiration - session).days)
    spot = float(stock_row["close"]) * split_factor
    years = dte / 365.0
    increment = strike_increment(spot)
    delta_strike = round_strike(
        strike_for_put_delta(spot, target_delta, years, args.risk_free_rate, short_iv), increment
    )
    short_strike = min(delta_strike, previous_short_strike - increment)
    long_strike = short_strike - spread_width
    if long_strike <= 0:
        return None, "invalid_roll_strikes"
    long_iv = short_iv + args.skew_slope * abs(math.log(long_strike / short_strike))
    short_value = put_price(spot, short_strike, years, args.risk_free_rate, short_iv)
    long_value = put_price(spot, long_strike, years, args.risk_free_rate, long_iv)
    mid_credit = short_value - long_value
    entry_slippage = max(args.entry_slippage_min, args.entry_slippage_fraction * max(0.0, mid_credit))
    executable_credit = max(0.0, mid_credit - entry_slippage)
    if executable_credit <= 0:
        return None, "nonpositive_roll_credit"
    return {
        "symbol": symbol,
        "entry_date": session,
        "expiration": expiration,
        "entry_spot": spot,
        "split_factor": split_factor,
        "split_events": split_description,
        "target_abs_delta": target_delta,
        "short_strike": short_strike,
        "long_strike": long_strike,
        "spread_width": spread_width,
        "entry_credit": executable_credit,
        "entry_mid_credit": mid_credit,
        "entry_short_iv": short_iv,
        "entry_long_iv": long_iv,
        "short_delta_after_rounding": put_delta(spot, short_strike, years, args.risk_free_rate, short_iv),
        "expected_days_to_earnings": expected_days,
        "earnings_inside": earnings_inside,
    }, None


def summarize(group: pd.DataFrame) -> dict:
    if group.empty:
        return {"campaigns": 0}
    return {
        "campaigns": len(group),
        "total_net_pnl": group["net_pnl_dollars"].sum(),
        "average_net_pnl": group["net_pnl_dollars"].mean(),
        "median_net_pnl": group["net_pnl_dollars"].median(),
        "win_rate": group["net_pnl_dollars"].gt(0).mean(),
        "campaign_target_rate": group["campaign_exit_reason"].eq("campaign_take_profit").mean(),
        "average_rolls": group["rolls_executed"].mean(),
        "roll_attempt_rate": group["rolls_attempted"].gt(0).mean(),
        "average_days_held": group["calendar_days_held"].mean(),
        "max_loss": group["net_pnl_dollars"].min(),
    }


def main() -> None:
    args = parse_args()
    max_roll_values = sorted({int(value) for value in args.max_rolls_values.split(",")})
    if any(value < 0 for value in max_roll_values):
        raise ValueError("--max-rolls-values must contain non-negative integers")
    if args.touch_iv_multiplier <= 0:
        raise ValueError("--touch-iv-multiplier must be positive")
    initial_entry_offset = pd.Timedelta(args.initial_entry_time)
    if not 0 <= args.mandatory_exit_dte < args.min_roll_dte <= args.max_roll_dte:
        raise ValueError("Require mandatory-exit-dte < min-roll-dte <= max-roll-dte")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    screen = pd.read_csv(args.screen).set_index("event_index")
    spreads = pd.read_csv(args.spreads, parse_dates=["signal_date", "entry_date", "expiration_proxy"])
    spreads = spreads[np.isclose(spreads["skew_slope"], args.skew_slope)].copy()
    symbols = sorted(spreads["symbol"].unique())
    earnings_by_symbol, sec_diagnostics = load_sec_earnings_dates(symbols, args.sec_cache_dir, args.sec_mode)
    market = market_curve(args.vix_path, args.spy_path, args.vrp_min, args.vrp_max)
    vix_intraday = load_intraday(args.vix_path)

    campaign_rows: list[dict] = []
    leg_rows: list[dict] = []
    path_rows: list[dict] = []
    diagnostics: list[dict] = []
    for symbol, symbol_spreads in spreads.groupby("symbol", sort=True):
        try:
            daily = load_daily(args.raw_dir, symbol, args.ivr_lookback, args.min_ivr_observations)
            intraday = load_intraday(args.raw_dir / f"Rohdaten_{symbol}.csv")
            expirations = load_expirations(args.expiration_dir, symbol)
            splits = load_splits(symbol, args.split_cache_dir, args.split_mode)
        except Exception as exc:
            diagnostics.append({"symbol": symbol, "status": "error", "error": f"{type(exc).__name__}: {exc}"})
            continue

        for spread in symbol_spreads.itertuples(index=False):
            for max_rolls in max_roll_values:
                initial_credit = float(spread.executable_credit_proxy)
                leg = {
                    "symbol": symbol,
                    "entry_date": pd.Timestamp(spread.entry_date),
                    "expiration": pd.Timestamp(spread.expiration_proxy),
                    "entry_spot": float(spread.entry_price),
                    "split_factor": float(spread.future_split_factor),
                    "split_events": spread.future_split_events,
                    "target_abs_delta": float(spread.target_abs_delta),
                    "short_strike": float(spread.short_strike),
                    "long_strike": float(spread.long_strike),
                    "spread_width": float(spread.spread_width),
                    "entry_credit": initial_credit,
                    "entry_mid_credit": float(spread.modeled_mid_credit),
                    "entry_short_iv": float(spread.modeled_short_iv),
                    "entry_long_iv": float(spread.modeled_long_iv),
                    "short_delta_after_rounding": float(spread.short_delta_after_rounding),
                    "expected_days_to_earnings": float(spread.days_to_expected_earnings),
                    "earnings_inside": float(spread.earnings_inside_modeled_expiry),
                    "touch_start_timestamp": pd.Timestamp(spread.entry_date).normalize() + initial_entry_offset,
                }
                opening_credits = initial_credit
                prior_close_debits = 0.0
                rolls_executed = 0
                rolls_attempted = 0
                leg_number = 0
                campaign_exit_reason = ""
                campaign_exit_date = pd.NaT
                final_close_debit = np.nan
                campaign_target_dollars = initial_credit * args.take_profit_fraction * 100.0

                while True:
                    leg_number += 1
                    sessions = daily[(daily.index >= leg["entry_date"]) & (daily.index <= leg["expiration"])]
                    cutoff = leg["expiration"] - pd.Timedelta(days=args.mandatory_exit_dte)
                    close_sessions = sessions.index[sessions.index > leg["entry_date"]]
                    model_sessions = close_sessions.intersection(market.index)
                    mandatory_candidates = model_sessions[model_sessions <= cutoff]
                    if sessions.empty or mandatory_candidates.empty:
                        campaign_exit_reason = "missing_leg_exit_path"
                        diagnostics.append(
                            {
                                "event_index": spread.event_index,
                                "symbol": symbol,
                                "max_rolls": max_rolls,
                                "leg_number": leg_number,
                                "status": campaign_exit_reason,
                            }
                        )
                        break
                    mandatory_exit_session = mandatory_candidates[-1]
                    leg_closed = False
                    for session, stock_row in sessions.iterrows():
                        touch_value = None
                        if args.short_strike_touch == "on":
                            touch_value = touch_leg_value(
                                session,
                                leg,
                                intraday,
                                vix_intraday,
                                daily,
                                market,
                                earnings_by_symbol.get(symbol, []),
                                args,
                            )
                        if touch_value is not None:
                            gross_if_closed = (
                                opening_credits - prior_close_debits - touch_value["close_debit"]
                            ) * 100.0
                            path_rows.append(
                                {
                                    "event_index": spread.event_index,
                                    "symbol": symbol,
                                    "target_abs_delta": spread.target_abs_delta,
                                    "max_rolls": max_rolls,
                                    "leg_number": leg_number,
                                    "session": session,
                                    "expiration": leg["expiration"],
                                    "remaining_dte": touch_value["remaining_dte"],
                                    "spot": touch_value["spot"],
                                    "short_strike": leg["short_strike"],
                                    "long_strike": leg["long_strike"],
                                    "modeled_close_debit": touch_value["close_debit"],
                                    "campaign_gross_pnl_if_closed": gross_if_closed,
                                    "campaign_target_dollars": campaign_target_dollars,
                                    "path_event": "short_strike_touch",
                                    "touch_time": touch_value["touch_time"],
                                    "touch_bar_open": touch_value["touch_bar_open"],
                                    "touch_bar_low": touch_value["touch_bar_low"],
                                    "touch_bar_close": touch_value["touch_bar_close"],
                                    "vix_at_touch": touch_value["vix_at_touch"],
                                    "market_vrp_at_touch": touch_value["market_vrp_at_touch"],
                                    "vrp_source": touch_value["vrp_source"],
                                }
                            )
                            campaign_exit_reason = "short_strike_touch"
                            campaign_exit_date = session
                            final_close_debit = touch_value["close_debit"]
                            leg_closed = True
                            leg_rows.append(
                                {
                                    "event_index": spread.event_index,
                                    "symbol": symbol,
                                    "target_abs_delta": spread.target_abs_delta,
                                    "max_rolls": max_rolls,
                                    "leg_number": leg_number,
                                    **leg,
                                    "exit_date": session,
                                    "exit_remaining_dte": touch_value["remaining_dte"],
                                    "exit_mid_debit": touch_value["mid_debit"],
                                    "exit_debit": touch_value["close_debit"],
                                    "leg_exit_reason": campaign_exit_reason,
                                    "touch_time": touch_value["touch_time"],
                                }
                            )
                            break
                        if session <= leg["entry_date"]:
                            continue
                        value = close_leg_value(
                            session,
                            stock_row,
                            leg,
                            market,
                            earnings_by_symbol.get(symbol, []),
                            args,
                        )
                        if value is None:
                            continue
                        gross_if_closed = (opening_credits - prior_close_debits - value["close_debit"]) * 100.0
                        path_rows.append(
                            {
                                "event_index": spread.event_index,
                                "symbol": symbol,
                                "target_abs_delta": spread.target_abs_delta,
                                "max_rolls": max_rolls,
                                "leg_number": leg_number,
                                "session": session,
                                "expiration": leg["expiration"],
                                "remaining_dte": value["remaining_dte"],
                                "spot": value["spot"],
                                "short_strike": leg["short_strike"],
                                "long_strike": leg["long_strike"],
                                "modeled_close_debit": value["close_debit"],
                                "campaign_gross_pnl_if_closed": gross_if_closed,
                                "campaign_target_dollars": campaign_target_dollars,
                                "path_event": "daily_close",
                            }
                        )
                        if gross_if_closed >= campaign_target_dollars:
                            campaign_exit_reason = "campaign_take_profit"
                            campaign_exit_date = session
                            final_close_debit = value["close_debit"]
                            leg_exit_reason = campaign_exit_reason
                            leg_closed = True
                        elif session == mandatory_exit_session:
                            rolls_attempted += 1 if rolls_executed < max_rolls else 0
                            leg_exit_reason = "roll_out_down" if rolls_executed < max_rolls else "max_rolls_21dte_exit"
                            leg_closed = True
                        else:
                            continue

                        leg_rows.append(
                            {
                                "event_index": spread.event_index,
                                "symbol": symbol,
                                "target_abs_delta": spread.target_abs_delta,
                                "max_rolls": max_rolls,
                                "leg_number": leg_number,
                                **leg,
                                "exit_date": session,
                                "exit_remaining_dte": value["remaining_dte"],
                                "exit_mid_debit": value["mid_debit"],
                                "exit_debit": value["close_debit"],
                                "leg_exit_reason": leg_exit_reason,
                            }
                        )
                        if campaign_exit_reason == "campaign_take_profit":
                            break
                        if rolls_executed >= max_rolls:
                            campaign_exit_reason = "max_rolls_21dte_exit"
                            campaign_exit_date = session
                            final_close_debit = value["close_debit"]
                            break

                        new_expiration = select_roll_expiration(
                            expirations,
                            session,
                            leg["expiration"],
                            args.target_roll_dte,
                            args.min_roll_dte,
                            args.max_roll_dte,
                        )
                        if new_expiration is None:
                            campaign_exit_reason = "no_roll_expiration"
                            campaign_exit_date = session
                            final_close_debit = value["close_debit"]
                            leg_rows[-1]["leg_exit_reason"] = campaign_exit_reason
                            break
                        new_leg, roll_error = open_roll_leg(
                            symbol,
                            session,
                            new_expiration,
                            stock_row,
                            leg["short_strike"],
                            leg["target_abs_delta"],
                            leg["spread_width"],
                            daily,
                            market,
                            earnings_by_symbol.get(symbol, []),
                            splits,
                            args,
                        )
                        if roll_error is not None:
                            campaign_exit_reason = roll_error
                            campaign_exit_date = session
                            final_close_debit = value["close_debit"]
                            leg_rows[-1]["leg_exit_reason"] = campaign_exit_reason
                            break
                        net_roll_credit = new_leg["entry_credit"] - value["close_debit"]
                        leg_rows[-1]["roll_expiration"] = new_expiration
                        leg_rows[-1]["roll_opening_credit"] = new_leg["entry_credit"]
                        leg_rows[-1]["roll_net_credit"] = net_roll_credit
                        leg_rows[-1]["roll_new_short_strike"] = new_leg["short_strike"]
                        leg_rows[-1]["roll_new_long_strike"] = new_leg["long_strike"]
                        if net_roll_credit < args.min_roll_net_credit:
                            campaign_exit_reason = "roll_credit_below_minimum"
                            campaign_exit_date = session
                            final_close_debit = value["close_debit"]
                            leg_rows[-1]["leg_exit_reason"] = campaign_exit_reason
                            break
                        prior_close_debits += value["close_debit"]
                        opening_credits += new_leg["entry_credit"]
                        rolls_executed += 1
                        new_leg["touch_start_timestamp"] = session + pd.Timedelta(days=1)
                        leg = new_leg
                        break

                    if campaign_exit_reason:
                        break
                    if not leg_closed:
                        campaign_exit_reason = "no_valid_leg_close"
                        diagnostics.append(
                            {
                                "event_index": spread.event_index,
                                "symbol": symbol,
                                "max_rolls": max_rolls,
                                "leg_number": leg_number,
                                "status": campaign_exit_reason,
                            }
                        )
                        break

                if pd.isna(campaign_exit_date) or not np.isfinite(final_close_debit):
                    continue
                gross_pnl = (opening_credits - prior_close_debits - final_close_debit) * 100.0
                legs_opened = rolls_executed + 1
                fees = legs_opened * (args.opening_fees + args.closing_fees)
                net_pnl = gross_pnl - fees
                event = screen.loc[spread.event_index]
                campaign_rows.append(
                    {
                        "event_index": spread.event_index,
                        "symbol": symbol,
                        "signal_date": spread.signal_date,
                        "initial_entry_date": spread.entry_date,
                        "campaign_exit_date": campaign_exit_date,
                        "calendar_days_held": (campaign_exit_date - pd.Timestamp(spread.entry_date)).days,
                        "target_abs_delta": spread.target_abs_delta,
                        "max_rolls": max_rolls,
                        "rolls_attempted": rolls_attempted,
                        "rolls_executed": rolls_executed,
                        "legs_opened": legs_opened,
                        "initial_entry_credit": initial_credit,
                        "total_opening_credits": opening_credits,
                        "total_prior_close_debits": prior_close_debits,
                        "final_close_debit": final_close_debit,
                        "campaign_target_dollars": campaign_target_dollars,
                        "gross_pnl_dollars": gross_pnl,
                        "fees_dollars": fees,
                        "net_pnl_dollars": net_pnl,
                        "campaign_exit_reason": campaign_exit_reason,
                        "short_strike_touch": args.short_strike_touch,
                        "drawdown_atr20": event.get("drawdown_atr20", np.nan),
                        "historical_composite_value_width_atr": event.get(
                            "historical_composite_value_width_atr", np.nan
                        ),
                    }
                )

    campaigns = pd.DataFrame(campaign_rows)
    legs = pd.DataFrame(leg_rows)
    paths = pd.DataFrame(path_rows)
    campaigns.to_csv(args.output_dir / "roll_campaigns.csv", index=False)
    legs.to_csv(args.output_dir / "roll_legs.csv", index=False)
    paths.to_csv(args.output_dir / "roll_daily_paths.csv", index=False)
    pd.DataFrame(diagnostics).to_csv(args.output_dir / "diagnostics.csv", index=False)
    pd.DataFrame(sec_diagnostics).to_csv(args.output_dir / "sec_earnings_diagnostics.csv", index=False)

    summary_rows = []
    if not campaigns.empty:
        campaigns["year"] = pd.to_datetime(campaigns["initial_entry_date"]).dt.year
        for (max_rolls, delta), group in campaigns.groupby(["max_rolls", "target_abs_delta"]):
            summary_rows.append({"segment": "all", "value": "all", "max_rolls": max_rolls, "delta": delta, **summarize(group)})
            for year, part in group.groupby("year"):
                summary_rows.append(
                    {"segment": "year", "value": year, "max_rolls": max_rolls, "delta": delta, **summarize(part)}
                )
            for reason, part in group.groupby("campaign_exit_reason"):
                summary_rows.append(
                    {"segment": "exit_reason", "value": reason, "max_rolls": max_rolls, "delta": delta, **summarize(part)}
                )
    summary = pd.DataFrame(summary_rows)
    summary.to_csv(args.output_dir / "roll_summary.csv", index=False)

    report = [
        "# Out-and-down Roll-Kampagnenbacktest",
        "",
        f"- Maximal erlaubte Rolls: {max_roll_values}",
        f"- Roll-Verfall: {args.min_roll_dte}–{args.max_roll_dte} DTE, Ziel {args.target_roll_dte} DTE",
        f"- Roll-Stichtag: {args.mandatory_exit_dte} DTE",
        f"- Mindest-Netto-Credit pro Roll: {args.min_roll_net_credit:.2f}",
        f"- Short-Strike-Touch-Exit: {args.short_strike_touch}",
        f"- Touch-IV-Multiplikator: {args.touch_iv_multiplier:.2f}",
        "- Neuer Short Strike: Ziel-Delta, mindestens einen Strike-Schritt tiefer als der vorherige Short Strike",
        f"- Kampagnenziel: {args.take_profit_fraction:.0%} des ursprünglichen Entry-Credits nach Ausgleich vorheriger Rollverluste",
        "",
        "Ein Roll wird nur ausgeführt, wenn neuer Opening-Credit minus altem Closing-Debit mindestens den Mindest-Netto-Credit erreicht.",
        "Ein Touch wird auf 5-Minuten-Tiefs erkannt. Der Exit-Spot ist der ungünstigere Wert aus Short Strike und Bar-Schluss; die IV nutzt nur vorherige Aktien-Renditen und den bis dahin beobachteten VIX.",
        "",
        "## Gesamtergebnisse",
        "",
        "| Max Rolls | Short-Delta | Kampagnen | Gesamt-PnL | Ø PnL | Winrate | Ø Rolls |",
        "|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in summary[summary["segment"].eq("all")].itertuples(index=False):
        report.append(
            f"| {row.max_rolls} | {row.delta:.2f} | {row.campaigns} | ${row.total_net_pnl:,.0f} | "
            f"${row.average_net_pnl:,.2f} | {row.win_rate:.1%} | {row.average_rolls:.3f} |"
        )
    report.append("")
    (args.output_dir / "REPORT.md").write_text("\n".join(report), encoding="utf-8")
    print(summary[summary["segment"].eq("all")].to_string(index=False), flush=True)


if __name__ == "__main__":
    main()
