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
from research.model_drawdown_recovery_put_credit_proxy import load_daily  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Calibrate a causal RV/VIX/earnings IV proxy.")
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--chain-validation", type=Path, required=True)
    parser.add_argument("--actual-ivr-screen", type=Path, required=True)
    parser.add_argument("--raw-dir", type=Path, required=True)
    parser.add_argument("--vix-path", type=Path, required=True)
    parser.add_argument("--spy-path", type=Path, required=True)
    parser.add_argument("--sec-cache-dir", type=Path, required=True)
    parser.add_argument("--sec-mode", choices=["download", "cache-only"], required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--market-weights", required=True)
    parser.add_argument("--earnings-jumps", required=True)
    parser.add_argument("--ivr-lookback", type=int, required=True)
    parser.add_argument("--min-ivr-observations", type=int, required=True)
    parser.add_argument("--earnings-cycle-lookback", type=int, required=True)
    parser.add_argument("--vrp-min", type=float, required=True)
    parser.add_argument("--vrp-max", type=float, required=True)
    return parser.parse_args()


def read_market_daily(path: Path, value_name: str) -> pd.DataFrame:
    raw = pd.read_csv(path, usecols=["date", "close"], low_memory=False)
    raw["date"] = pd.to_datetime(raw["date"], errors="coerce")
    raw["close"] = pd.to_numeric(raw["close"], errors="coerce")
    daily = (
        raw.dropna(subset=["date", "close"])
        .sort_values("date")
        .groupby(raw["date"].dt.normalize(), sort=True)
        .agg(**{value_name: ("close", "last")})
    )
    return daily


def market_curve(vix_path: Path, spy_path: Path, vrp_min: float, vrp_max: float) -> pd.DataFrame:
    vix = read_market_daily(vix_path, "vix_close")
    spy = read_market_daily(spy_path, "spy_close")
    market = spy.join(vix, how="outer").sort_index()
    log_return = np.log(market["spy_close"] / market["spy_close"].shift(1))
    market["spy_rv20"] = log_return.rolling(20).std(ddof=1) * math.sqrt(252)
    market["vix_decimal"] = market["vix_close"] / 100.0
    market["market_vrp_raw"] = market["vix_decimal"] / market["spy_rv20"]
    market["market_vrp"] = market["market_vrp_raw"].clip(vrp_min, vrp_max)
    return market


def predict_next_earnings(
    as_of: pd.Timestamp,
    earnings_dates: list[pd.Timestamp],
    cycle_lookback: int,
) -> tuple[pd.Timestamp | pd.NaT, float, int]:
    prior = [pd.Timestamp(value).normalize() for value in earnings_dates if pd.Timestamp(value).normalize() < as_of]
    if len(prior) < 2:
        return pd.NaT, np.nan, len(prior)
    intervals = pd.Series(prior).diff().dt.days.dropna().tail(cycle_lookback)
    cycle = float(intervals.median())
    if not np.isfinite(cycle):
        return pd.NaT, np.nan, len(prior)
    cycle = float(np.clip(cycle, 70.0, 120.0))
    predicted = prior[-1] + pd.Timedelta(days=round(cycle))
    while predicted < as_of:
        predicted += pd.Timedelta(days=round(cycle))
    return predicted.normalize(), cycle, len(prior)


def actual_next_earnings(as_of: pd.Timestamp, earnings_dates: list[pd.Timestamp]) -> pd.Timestamp | pd.NaT:
    future = sorted(pd.Timestamp(value).normalize() for value in earnings_dates if pd.Timestamp(value).normalize() >= as_of)
    return future[0] if future else pd.NaT


def modeled_iv(
    stock_rv: float,
    market_vrp: float,
    market_weight: float,
    earnings_jump: float,
    days_to_expected_earnings: float,
    dte: int,
) -> tuple[float, bool]:
    if not np.isfinite(stock_rv) or stock_rv <= 0 or not np.isfinite(market_vrp):
        return np.nan, False
    base = stock_rv * market_vrp**market_weight
    earnings_inside = np.isfinite(days_to_expected_earnings) and 0 <= days_to_expected_earnings <= dte
    if not earnings_inside or earnings_jump <= 0:
        return base, bool(earnings_inside)
    total_variance = base * base * (dte / 365.0) + earnings_jump * earnings_jump
    return math.sqrt(total_variance / (dte / 365.0)), True


def modeled_curve_for_symbol(
    daily: pd.DataFrame,
    market: pd.DataFrame,
    earnings_dates: list[pd.Timestamp],
    market_weight: float,
    earnings_jump: float,
    cycle_lookback: int,
    dte: int,
    ivr_lookback: int,
    min_observations: int,
) -> pd.DataFrame:
    curve = daily[["iv_base_proxy"]].join(market[["market_vrp"]], how="left").copy()
    expected_days = []
    values = []
    for session, row in curve.iterrows():
        predicted, _, _ = predict_next_earnings(session, earnings_dates, cycle_lookback)
        days = float((predicted - session).days) if pd.notna(predicted) else np.nan
        value, _ = modeled_iv(
            float(row["iv_base_proxy"]),
            float(row["market_vrp"]),
            market_weight,
            earnings_jump,
            days,
            dte,
        )
        expected_days.append(days)
        values.append(value)
    curve["days_to_expected_earnings"] = expected_days
    curve["modeled_iv"] = values
    rolling = curve["modeled_iv"].rolling(ivr_lookback, min_periods=min_observations)
    curve["iv_min"] = rolling.min()
    curve["iv_max"] = rolling.max()
    denominator = curve["iv_max"] - curve["iv_min"]
    curve["modeled_iv_rank"] = np.where(
        denominator > 1e-12,
        100.0 * (curve["modeled_iv"] - curve["iv_min"]) / denominator,
        np.nan,
    )
    return curve


def main() -> None:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    market_weights = [float(value) for value in args.market_weights.split(",")]
    earnings_jumps = [float(value) for value in args.earnings_jumps.split(",")]
    events = pd.read_csv(args.events, parse_dates=["signal_date", "entry_date"])
    symbols = sorted(events["symbol"].astype(str).str.upper().unique())
    earnings_by_symbol, sec_diagnostics = load_sec_earnings_dates(symbols, args.sec_cache_dir, args.sec_mode)
    pd.DataFrame(sec_diagnostics).to_csv(args.output_dir / "sec_earnings_diagnostics.csv", index=False)
    market = market_curve(args.vix_path, args.spy_path, args.vrp_min, args.vrp_max)
    market.to_csv(args.output_dir / "market_vrp_curve.csv")

    chain = pd.read_csv(args.chain_validation, parse_dates=["signal_date", "entry_date", "actual_expiration"])
    chain = chain[chain["status"].eq("ok")].copy()
    actual_ivr = pd.read_csv(args.actual_ivr_screen, parse_dates=["signal_date", "iv_date"])
    actual_ivr = actual_ivr[actual_ivr["iv_rank"].notna()].copy()
    needed_symbols = sorted(set(chain["symbol"]) | set(actual_ivr["symbol"]))
    daily_by_symbol = {
        symbol: load_daily(args.raw_dir, symbol, args.ivr_lookback, args.min_ivr_observations)
        for symbol in needed_symbols
    }

    level_rows = []
    rank_rows = []
    summary_rows = []
    for market_weight in market_weights:
        for earnings_jump in earnings_jumps:
            for event in chain.itertuples(index=False):
                daily = daily_by_symbol[event.symbol]
                signal = daily.loc[event.signal_date]
                market_row = market.loc[event.signal_date] if event.signal_date in market.index else None
                predicted, cycle, history_count = predict_next_earnings(
                    event.signal_date,
                    earnings_by_symbol.get(event.symbol, []),
                    args.earnings_cycle_lookback,
                )
                actual_next = actual_next_earnings(event.signal_date, earnings_by_symbol.get(event.symbol, []))
                expected_days = float((predicted - event.signal_date).days) if pd.notna(predicted) else np.nan
                actual_days = float((actual_next - event.signal_date).days) if pd.notna(actual_next) else np.nan
                dte = int((event.actual_expiration - event.entry_date).days)
                value, earnings_inside = modeled_iv(
                    float(signal["iv_base_proxy"]),
                    float(market_row["market_vrp"]) if market_row is not None else np.nan,
                    market_weight,
                    earnings_jump,
                    expected_days,
                    dte,
                )
                level_rows.append(
                    {
                        "event_index": event.event_index,
                        "symbol": event.symbol,
                        "signal_date": event.signal_date,
                        "market_weight": market_weight,
                        "earnings_jump": earnings_jump,
                        "stock_rv_proxy": float(signal["iv_base_proxy"]),
                        "market_vrp": float(market_row["market_vrp"]) if market_row is not None else np.nan,
                        "predicted_earnings": predicted,
                        "actual_next_earnings": actual_next,
                        "expected_days_to_earnings": expected_days,
                        "actual_days_to_earnings": actual_days,
                        "earnings_date_error_days": expected_days - actual_days if np.isfinite(actual_days) else np.nan,
                        "earnings_history_count": history_count,
                        "earnings_cycle_days": cycle,
                        "earnings_inside_modeled_expiry": earnings_inside,
                        "modeled_short_iv": value,
                        "actual_short_iv": event.actual_short_iv,
                        "iv_error": value - event.actual_short_iv,
                        "absolute_iv_error": abs(value - event.actual_short_iv),
                    }
                )

            for symbol, group in actual_ivr.groupby("symbol"):
                curve = modeled_curve_for_symbol(
                    daily_by_symbol[symbol],
                    market,
                    earnings_by_symbol.get(symbol, []),
                    market_weight,
                    earnings_jump,
                    args.earnings_cycle_lookback,
                    30,
                    args.ivr_lookback,
                    args.min_ivr_observations,
                )
                for event in group.itertuples(index=False):
                    modeled_rank = curve.loc[event.signal_date, "modeled_iv_rank"] if event.signal_date in curve.index else np.nan
                    rank_rows.append(
                        {
                            "symbol": symbol,
                            "signal_date": event.signal_date,
                            "market_weight": market_weight,
                            "earnings_jump": earnings_jump,
                            "modeled_iv_rank": modeled_rank,
                            "actual_iv_rank": event.iv_rank,
                            "rank_error": modeled_rank - event.iv_rank,
                            "absolute_rank_error": abs(modeled_rank - event.iv_rank),
                        }
                    )

            level_part = pd.DataFrame(level_rows)
            level_part = level_part[
                np.isclose(level_part["market_weight"], market_weight)
                & np.isclose(level_part["earnings_jump"], earnings_jump)
            ]
            rank_part = pd.DataFrame(rank_rows)
            rank_part = rank_part[
                np.isclose(rank_part["market_weight"], market_weight)
                & np.isclose(rank_part["earnings_jump"], earnings_jump)
            ]
            summary_rows.append(
                {
                    "market_weight": market_weight,
                    "earnings_jump": earnings_jump,
                    "chain_events": len(level_part),
                    "median_iv_error": level_part["iv_error"].median(),
                    "median_absolute_iv_error": level_part["absolute_iv_error"].median(),
                    "mean_absolute_iv_error": level_part["absolute_iv_error"].mean(),
                    "ivr_events": len(rank_part),
                    "median_rank_error": rank_part["rank_error"].median(),
                    "median_absolute_rank_error": rank_part["absolute_rank_error"].median(),
                    "mean_absolute_rank_error": rank_part["absolute_rank_error"].mean(),
                }
            )

    levels = pd.DataFrame(level_rows)
    ranks = pd.DataFrame(rank_rows)
    summary = pd.DataFrame(summary_rows).sort_values(
        ["median_absolute_iv_error", "median_absolute_rank_error"]
    )
    levels.to_csv(args.output_dir / "iv_level_calibration_events.csv", index=False)
    ranks.to_csv(args.output_dir / "iv_rank_calibration_events.csv", index=False)
    summary.to_csv(args.output_dir / "iv_model_grid_summary.csv", index=False)
    best = summary.iloc[0]
    earnings_errors = levels.drop_duplicates(["event_index"])["earnings_date_error_days"].dropna()
    report = [
        "# VIX-/Earnings-IV-Proxy-Kalibrierung",
        "",
        "## Methode",
        "",
        "- Aktienbasis: max(RV20, RV60).",
        "- Marktkomponente: VIX / SPY-RV20, begrenzt und mit Gewicht 0 / 0,5 / 1.",
        "- Earnings-Komponente: zusätzlicher diskreter Varianzsprung von 0 / 4 / 6 Prozent.",
        "- Nächster Earnings-Termin wird nur aus früheren SEC-2.02-Abständen prognostiziert.",
        "",
        "## Ergebnis",
        "",
        f"- Echte Ketten für IV-Level: {len(chain)}",
        f"- Echte IVR-Punkte: {len(actual_ivr)}",
        f"- Median absoluter Earnings-Terminfehler: {earnings_errors.abs().median() if not earnings_errors.empty else np.nan:.1f} Tage",
        f"- Kleinster IV-Level-Medianfehler: weight={best['market_weight']}, jump={best['earnings_jump']:.2%}, MAE={best['median_absolute_iv_error']:.4f}",
        f"- Zugehöriger IVR-Medianfehler: {best['median_absolute_rank_error']:.2f} Punkte",
        "",
        "Die Stichprobe ist klein. Das Raster dient zur Wahl einer stabilen Modellfamilie, nicht zur Feinoptimierung.",
        "",
    ]
    (args.output_dir / "iv_model_calibration_report.md").write_text("\n".join(report), encoding="utf-8")
    print(summary.to_string(index=False), flush=True)


if __name__ == "__main__":
    main()
