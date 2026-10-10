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
from research.backtest_vix_earnings_put_credit_out_down_rolls import close_leg_value, load_expirations  # noqa: E402
from research.calibrate_iv_proxy_vix_earnings import (  # noqa: E402
    market_curve,
    modeled_curve_for_symbol,
    modeled_iv,
    predict_next_earnings,
)
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
    parser = argparse.ArgumentParser(description="Backtest high-IVR put-credit episodes without earnings exposure.")
    parser.add_argument("--universe", type=Path, required=True)
    parser.add_argument("--min-latest-price", type=float, required=True)
    parser.add_argument("--min-median-dollar-volume", type=float, required=True)
    parser.add_argument("--min-sessions", type=int, required=True)
    parser.add_argument("--min-last-session", required=True)
    parser.add_argument("--raw-dir", type=Path, required=True)
    parser.add_argument("--vix-path", type=Path, required=True)
    parser.add_argument("--spy-path", type=Path, required=True)
    parser.add_argument("--expiration-dir", type=Path, required=True)
    parser.add_argument("--sec-cache-dir", type=Path, required=True)
    parser.add_argument("--sec-mode", choices=["download", "cache-only"], required=True)
    parser.add_argument("--split-cache-dir", type=Path, required=True)
    parser.add_argument("--split-mode", choices=["download", "cache-only"], required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--ivr-threshold", type=float, required=True)
    parser.add_argument("--ivr-lookback", type=int, required=True)
    parser.add_argument("--min-ivr-observations", type=int, required=True)
    parser.add_argument("--iv-rank-dte", type=int, required=True)
    parser.add_argument("--target-dte", type=int, required=True)
    parser.add_argument("--min-dte", type=int, required=True)
    parser.add_argument("--max-dte", type=int, required=True)
    parser.add_argument("--mandatory-exit-dte", type=int, required=True)
    parser.add_argument("--max-mandatory-calendar-gap", type=int, required=True)
    parser.add_argument("--earnings-buffer-days", type=int, required=True)
    parser.add_argument("--spread-width", type=float, required=True)
    parser.add_argument("--short-deltas", required=True)
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
    parser.add_argument("--enforce-symbol-position-limit", choices=["on", "off"], required=True)
    return parser.parse_args()


def load_universe(
    path: Path,
    min_latest_price: float,
    min_median_dollar_volume: float,
    min_sessions: int,
    min_last_session: pd.Timestamp,
) -> list[str]:
    frame = pd.read_csv(path)
    required = {
        "symbol",
        "status",
        "sessions",
        "last_session",
        "latest_close",
        "latest_median_dollar_volume60",
    }
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"Universe missing columns: {sorted(missing)}")
    frame["last_session"] = pd.to_datetime(frame["last_session"], errors="coerce")
    eligible = frame[
        frame["status"].eq("ok")
        & frame["sessions"].ge(min_sessions)
        & frame["last_session"].ge(min_last_session)
        & frame["latest_close"].ge(min_latest_price)
        & frame["latest_median_dollar_volume60"].ge(min_median_dollar_volume)
    ]
    return sorted(eligible["symbol"].astype(str).str.upper().str.strip().unique())


def load_daily_with_open(raw_dir: Path, symbol: str, lookback: int, min_observations: int) -> pd.DataFrame:
    daily = load_daily(raw_dir, symbol, lookback, min_observations)
    raw = pd.read_csv(
        raw_dir / f"Rohdaten_{symbol}.csv",
        usecols=["date", "open", "volume"],
        low_memory=False,
    )
    raw["date"] = pd.to_datetime(raw["date"], errors="coerce")
    raw["open"] = pd.to_numeric(raw["open"], errors="coerce")
    raw["volume"] = pd.to_numeric(raw["volume"], errors="coerce")
    raw = raw.dropna(subset=["date", "open"]).sort_values("date").copy()
    raw["session"] = raw["date"].dt.normalize()
    intraday_daily = (
        raw
        .sort_values("date")
        .groupby("session", sort=True)
        .agg(open=("open", "first"), volume=("volume", "sum"))
    )
    daily = daily.join(intraday_daily, how="left")
    daily["median_dollar_volume60"] = (daily["close"] * daily["volume"]).rolling(
        60, min_periods=60
    ).median()
    return daily


def regular_monthly_expirations(entry_date: pd.Timestamp, min_dte: int, max_dte: int) -> list[pd.Timestamp]:
    fridays = pd.date_range(
        entry_date + pd.Timedelta(days=min_dte),
        entry_date + pd.Timedelta(days=max_dte),
        freq="W-FRI",
    )
    return [pd.Timestamp(value) for value in fridays if 15 <= value.day <= 21]


def select_expiration(
    entry_date: pd.Timestamp,
    listed: list[pd.Timestamp] | None,
    target_dte: int,
    min_dte: int,
    max_dte: int,
) -> tuple[pd.Timestamp | None, str]:
    if listed is not None:
        candidates = [
            value for value in listed if min_dte <= (value - entry_date).days <= max_dte
        ]
        source = "listed_inventory"
    else:
        candidates = regular_monthly_expirations(entry_date, min_dte, max_dte)
        source = "regular_monthly_proxy"
    if not candidates:
        return None, source
    return min(candidates, key=lambda value: abs((value - entry_date).days - target_dte)), source


def ivr_bucket(value: float) -> str:
    if value < 40:
        return "30-40"
    if value < 50:
        return "40-50"
    if value < 70:
        return "50-70"
    return "70+"


def summarize(group: pd.DataFrame) -> dict:
    if group.empty:
        return {"trades": 0}
    return {
        "trades": len(group),
        "total_net_pnl": group["net_pnl_dollars"].sum(),
        "average_net_pnl": group["net_pnl_dollars"].mean(),
        "median_net_pnl": group["net_pnl_dollars"].median(),
        "win_rate": group["net_pnl_dollars"].gt(0).mean(),
        "take_profit_rate": group["exit_reason"].eq("take_profit_50").mean(),
        "average_days_held": group["calendar_days_held"].mean(),
        "max_loss": group["net_pnl_dollars"].min(),
        "p10_pnl": group["net_pnl_dollars"].quantile(0.10),
        "median_entry_ivr": group["entry_modeled_iv_rank"].median(),
        "median_iv_change": group["exit_short_iv"].sub(group["entry_short_iv"]).median(),
    }


def main() -> None:
    args = parse_args()
    start_date = pd.Timestamp(args.start_date).normalize()
    end_date = pd.Timestamp(args.end_date).normalize()
    deltas = [float(value) for value in args.short_deltas.split(",")]
    if not 0 <= args.mandatory_exit_dte < args.min_dte <= args.max_dte:
        raise ValueError("Require mandatory-exit-dte < min-dte <= max-dte")
    if args.earnings_buffer_days < 0:
        raise ValueError("--earnings-buffer-days must be non-negative")
    args.output_dir.mkdir(parents=True, exist_ok=True)

    symbols = load_universe(
        args.universe,
        args.min_latest_price,
        args.min_median_dollar_volume,
        args.min_sessions,
        pd.Timestamp(args.min_last_session).normalize(),
    )
    earnings_by_symbol, sec_diagnostics = load_sec_earnings_dates(symbols, args.sec_cache_dir, args.sec_mode)
    market = market_curve(args.vix_path, args.spy_path, args.vrp_min, args.vrp_max)
    listed_by_symbol: dict[str, list[pd.Timestamp] | None] = {}
    for symbol in symbols:
        path = args.expiration_dir / f"expirations_{symbol}.csv"
        listed_by_symbol[symbol] = load_expirations(args.expiration_dir, symbol) if path.exists() else None

    trade_rows: list[dict] = []
    path_rows: list[dict] = []
    signal_rows: list[dict] = []
    diagnostics: list[dict] = []
    for number, symbol in enumerate(symbols, start=1):
        try:
            daily = load_daily_with_open(args.raw_dir, symbol, args.ivr_lookback, args.min_ivr_observations)
            splits = load_splits(symbol, args.split_cache_dir, args.split_mode)
            curve = modeled_curve_for_symbol(
                daily,
                market,
                earnings_by_symbol.get(symbol, []),
                args.market_weight,
                args.earnings_jump,
                args.earnings_cycle_lookback,
                args.iv_rank_dte,
                args.ivr_lookback,
                args.min_ivr_observations,
            )
        except Exception as exc:
            diagnostics.append({"symbol": symbol, "status": "error", "error": f"{type(exc).__name__}: {exc}"})
            continue
        rank = curve["modeled_iv_rank"]
        crossings = rank.gt(args.ivr_threshold) & rank.shift(1).le(args.ivr_threshold)
        signal_dates = rank.index[crossings & rank.index.to_series().between(start_date, end_date)]

        for target_delta in deltas:
            active_until = pd.NaT
            for signal_date in signal_dates:
                signal_date = pd.Timestamp(signal_date)
                signal_base = {
                    "symbol": symbol,
                    "signal_date": signal_date,
                    "target_abs_delta": target_delta,
                    "signal_modeled_iv_rank": float(rank.loc[signal_date]),
                    "status": "",
                }
                if (
                    args.enforce_symbol_position_limit == "on"
                    and pd.notna(active_until)
                    and signal_date < active_until
                ):
                    signal_rows.append({**signal_base, "status": "skipped_active_position", "active_until": active_until})
                    continue
                future_sessions = daily.index[daily.index > signal_date]
                if future_sessions.empty:
                    signal_rows.append({**signal_base, "status": "missing_entry_session"})
                    continue
                entry_date = pd.Timestamp(future_sessions[0])
                if entry_date > end_date or not np.isfinite(daily.loc[entry_date, "open"]):
                    signal_rows.append({**signal_base, "status": "entry_outside_period_or_missing_open"})
                    continue
                expiration, expiration_source = select_expiration(
                    entry_date,
                    listed_by_symbol[symbol],
                    args.target_dte,
                    args.min_dte,
                    args.max_dte,
                )
                if expiration is None:
                    signal_rows.append({**signal_base, "entry_date": entry_date, "status": "no_expiration"})
                    continue
                predicted_earnings, earnings_cycle, earnings_history = predict_next_earnings(
                    signal_date,
                    earnings_by_symbol.get(symbol, []),
                    args.earnings_cycle_lookback,
                )
                if pd.isna(predicted_earnings):
                    signal_rows.append(
                        {
                            **signal_base,
                            "entry_date": entry_date,
                            "expiration": expiration,
                            "status": "excluded_unknown_earnings",
                            "earnings_history_count": earnings_history,
                        }
                    )
                    continue
                earnings_exclusion_end = expiration + pd.Timedelta(days=args.earnings_buffer_days)
                if signal_date < predicted_earnings <= earnings_exclusion_end:
                    signal_rows.append(
                        {
                            **signal_base,
                            "entry_date": entry_date,
                            "expiration": expiration,
                            "predicted_earnings": predicted_earnings,
                            "status": "excluded_predicted_earnings",
                            "earnings_cycle_days": earnings_cycle,
                            "earnings_history_count": earnings_history,
                        }
                    )
                    continue
                if signal_date not in market.index:
                    signal_rows.append({**signal_base, "status": "missing_signal_market"})
                    continue
                dte = int((expiration - entry_date).days)
                split_factor, split_during_trade, split_description = split_context(
                    entry_date, expiration, daily.index.max(), splits
                )
                if split_during_trade:
                    signal_rows.append({**signal_base, "status": "excluded_split_during_trade"})
                    continue
                entry_spot = float(daily.loc[entry_date, "open"]) * split_factor
                expected_days = float((predicted_earnings - signal_date).days)
                entry_short_iv, earnings_inside = modeled_iv(
                    float(daily.loc[signal_date, "iv_base_proxy"]),
                    float(market.loc[signal_date, "market_vrp"]),
                    args.market_weight,
                    args.earnings_jump,
                    expected_days,
                    dte,
                )
                if not np.isfinite(entry_short_iv) or earnings_inside:
                    signal_rows.append({**signal_base, "status": "invalid_entry_iv_or_earnings_inside"})
                    continue
                years = dte / 365.0
                increment = strike_increment(entry_spot)
                short_strike = round_strike(
                    strike_for_put_delta(
                        entry_spot, target_delta, years, args.risk_free_rate, entry_short_iv
                    ),
                    increment,
                )
                long_strike = short_strike - args.spread_width
                if long_strike <= 0:
                    signal_rows.append({**signal_base, "status": "invalid_strikes"})
                    continue
                entry_long_iv = entry_short_iv + args.skew_slope * abs(math.log(long_strike / short_strike))
                short_value = put_price(entry_spot, short_strike, years, args.risk_free_rate, entry_short_iv)
                long_value = put_price(entry_spot, long_strike, years, args.risk_free_rate, entry_long_iv)
                entry_mid_credit = short_value - long_value
                entry_slippage = max(
                    args.entry_slippage_min,
                    args.entry_slippage_fraction * max(0.0, entry_mid_credit),
                )
                entry_credit = max(0.0, entry_mid_credit - entry_slippage)
                if entry_credit <= 0:
                    signal_rows.append({**signal_base, "status": "nonpositive_entry_credit"})
                    continue
                leg = {
                    "entry_date": entry_date,
                    "expiration": expiration,
                    "split_factor": split_factor,
                    "short_strike": short_strike,
                    "long_strike": long_strike,
                }
                sessions = daily[(daily.index > entry_date) & (daily.index <= expiration)]
                cutoff = expiration - pd.Timedelta(days=args.mandatory_exit_dte)
                model_sessions = sessions.index.intersection(market.index)
                mandatory_candidates = model_sessions[model_sessions <= cutoff]
                if sessions.empty or mandatory_candidates.empty:
                    signal_rows.append({**signal_base, "status": "missing_exit_path"})
                    continue
                mandatory_exit_session = mandatory_candidates[-1]
                if (cutoff - mandatory_exit_session).days > args.max_mandatory_calendar_gap:
                    signal_rows.append(
                        {
                            **signal_base,
                            "entry_date": entry_date,
                            "expiration": expiration,
                            "status": "incomplete_exit_horizon",
                        }
                    )
                    continue
                target_debit = entry_credit * (1.0 - args.take_profit_fraction)
                exit_value = None
                exit_date = pd.NaT
                exit_reason = ""
                for session, stock_row in sessions.iterrows():
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
                    reason = "open"
                    if value["close_debit"] <= target_debit:
                        reason = "take_profit_50"
                    elif session == mandatory_exit_session:
                        reason = "mandatory_21dte_exit"
                    path_rows.append(
                        {
                            "symbol": symbol,
                            "signal_date": signal_date,
                            "entry_date": entry_date,
                            "target_abs_delta": target_delta,
                            "session": session,
                            "expiration": expiration,
                            "remaining_dte": value["remaining_dte"],
                            "spot": value["spot"],
                            "modeled_close_debit": value["close_debit"],
                            "modeled_short_iv": value["short_iv"],
                            "target_debit": target_debit,
                            "path_status": reason,
                        }
                    )
                    if reason != "open":
                        exit_value = value
                        exit_date = session
                        exit_reason = reason
                        break
                if exit_value is None:
                    signal_rows.append({**signal_base, "status": "no_valid_exit_value"})
                    continue
                gross_pnl = (entry_credit - exit_value["close_debit"]) * 100.0
                fees = args.opening_fees + args.closing_fees
                net_pnl = gross_pnl - fees
                trade_rows.append(
                    {
                        "symbol": symbol,
                        "signal_date": signal_date,
                        "entry_date": entry_date,
                        "expiration": expiration,
                        "expiration_source": expiration_source,
                        "exit_date": exit_date,
                        "calendar_days_held": (exit_date - entry_date).days,
                        "target_abs_delta": target_delta,
                        "entry_modeled_iv_rank": float(rank.loc[signal_date]),
                        "signal_median_dollar_volume60": float(
                            daily.loc[signal_date, "median_dollar_volume60"]
                        ),
                        "entry_ivr_bucket": ivr_bucket(float(rank.loc[signal_date])),
                        "predicted_earnings": predicted_earnings,
                        "days_to_predicted_earnings": expected_days,
                        "entry_spot": entry_spot,
                        "short_strike": short_strike,
                        "long_strike": long_strike,
                        "short_delta_after_rounding": put_delta(
                            entry_spot, short_strike, years, args.risk_free_rate, entry_short_iv
                        ),
                        "entry_short_iv": entry_short_iv,
                        "entry_long_iv": entry_long_iv,
                        "entry_mid_credit": entry_mid_credit,
                        "entry_credit": entry_credit,
                        "target_debit": target_debit,
                        "exit_debit": exit_value["close_debit"],
                        "exit_short_iv": exit_value["short_iv"],
                        "exit_reason": exit_reason,
                        "gross_pnl_dollars": gross_pnl,
                        "fees_dollars": fees,
                        "net_pnl_dollars": net_pnl,
                        "future_split_events": split_description,
                    }
                )
                signal_rows.append(
                    {
                        **signal_base,
                        "entry_date": entry_date,
                        "expiration": expiration,
                        "predicted_earnings": predicted_earnings,
                        "status": "traded",
                        "exit_date": exit_date,
                    }
                )
                if args.enforce_symbol_position_limit == "on":
                    active_until = exit_date
        if number % 25 == 0:
            print(f"symbols {number}/{len(symbols)} trades={len(trade_rows)}", flush=True)

    trades = pd.DataFrame(trade_rows)
    paths = pd.DataFrame(path_rows)
    signals = pd.DataFrame(signal_rows)
    diagnostics_frame = pd.DataFrame(diagnostics)
    trades.to_csv(args.output_dir / "high_ivr_trades.csv", index=False)
    paths.to_csv(args.output_dir / "high_ivr_daily_paths.csv", index=False)
    signals.to_csv(args.output_dir / "high_ivr_signal_audit.csv", index=False)
    diagnostics_frame.to_csv(args.output_dir / "diagnostics.csv", index=False)
    pd.DataFrame(sec_diagnostics).to_csv(args.output_dir / "sec_earnings_diagnostics.csv", index=False)

    summary_rows = []
    if not trades.empty:
        trades["year"] = pd.to_datetime(trades["entry_date"]).dt.year
        for delta, group in trades.groupby("target_abs_delta"):
            summary_rows.append({"segment": "all", "value": "all", "delta": delta, **summarize(group)})
            for year, part in group.groupby("year"):
                summary_rows.append({"segment": "year", "value": year, "delta": delta, **summarize(part)})
            for bucket, part in group.groupby("entry_ivr_bucket"):
                summary_rows.append({"segment": "ivr_bucket", "value": bucket, "delta": delta, **summarize(part)})
            for source, part in group.groupby("expiration_source"):
                summary_rows.append({"segment": "expiration_source", "value": source, "delta": delta, **summarize(part)})
    summary = pd.DataFrame(summary_rows)
    summary.to_csv(args.output_dir / "high_ivr_summary.csv", index=False)

    concurrency_rows = []
    if not trades.empty:
        for delta, group in trades.groupby("target_abs_delta"):
            dates = pd.date_range(group["entry_date"].min(), group["exit_date"].max(), freq="D")
            for date in dates:
                count = ((pd.to_datetime(group["entry_date"]) <= date) & (pd.to_datetime(group["exit_date"]) >= date)).sum()
                concurrency_rows.append({"date": date, "target_abs_delta": delta, "open_campaigns": int(count)})
    concurrency = pd.DataFrame(concurrency_rows)
    concurrency.to_csv(args.output_dir / "high_ivr_concurrency.csv", index=False)

    all_rows = summary[summary["segment"].eq("all")]
    report = [
        "# High-IVR Put-Credit-Spread ohne Earnings",
        "",
        f"- Universum: {len(symbols)} liquiditätsgefilterte Aktien",
        f"- Liquidität: Kurs ≥ ${args.min_latest_price:g}, Median-Dollarvolumen ≥ ${args.min_median_dollar_volume:,.0f}, mindestens {args.min_sessions} Sessions",
        f"- Zeitraum: {start_date.date()} bis {end_date.date()}",
        f"- Einstieg: kausaler IVR-Cross über {args.ivr_threshold:.1f}, nächster Handelstag Open",
        f"- Earnings-Ausschluss: prognostizierter Termin bis Verfall plus {args.earnings_buffer_days} Tage",
        f"- Laufzeit: {args.min_dte}–{args.max_dte} DTE, Ziel {args.target_dte} DTE",
        f"- Exit: {args.take_profit_fraction:.0%} Take Profit oder {args.mandatory_exit_dte} DTE",
        f"- Symbol-Positionslimit im Kandidatenlauf: {args.enforce_symbol_position_limit}",
        "",
        "| Delta | Trades | Gesamt-PnL | Ø PnL | Winrate | TP-Rate | Worst Trade |",
        "|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in all_rows.itertuples(index=False):
        report.append(
            f"| {row.delta:.2f} | {row.trades} | ${row.total_net_pnl:,.0f} | ${row.average_net_pnl:,.2f} | "
            f"{row.win_rate:.1%} | {row.take_profit_rate:.1%} | ${row.max_loss:,.0f} |"
        )
    report.extend(
        [
            "",
            "Der IVR ist ein lokaler kausaler Proxy. Reguläre Monatsverfälle ersetzen dort die echte Verfallsliste, wo noch kein Inventory vorliegt.",
            "",
        ]
    )
    (args.output_dir / "REPORT.md").write_text("\n".join(report), encoding="utf-8")
    print(all_rows.to_string(index=False), flush=True)


if __name__ == "__main__":
    main()
