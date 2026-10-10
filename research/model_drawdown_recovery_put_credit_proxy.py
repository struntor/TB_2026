from __future__ import annotations

import argparse
from datetime import timedelta
import json
import math
from pathlib import Path
from statistics import NormalDist
from urllib.request import Request, urlopen

import numpy as np
import pandas as pd


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fast causal IV-rank and put-credit-spread proxy.")
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--raw-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--ivr-threshold", type=float, required=True)
    parser.add_argument("--ivr-lookback", type=int, required=True)
    parser.add_argument("--min-ivr-observations", type=int, required=True)
    parser.add_argument("--target-dte", type=int, required=True)
    parser.add_argument("--min-dte", type=int, required=True)
    parser.add_argument("--max-dte", type=int, required=True)
    parser.add_argument("--spread-width", type=float, required=True)
    parser.add_argument("--short-deltas", required=True)
    parser.add_argument("--iv-factors", required=True)
    parser.add_argument("--skew-slopes", required=True)
    parser.add_argument("--risk-free-rate", type=float, required=True)
    parser.add_argument("--slippage-min", type=float, required=True)
    parser.add_argument("--slippage-fraction", type=float, required=True)
    parser.add_argument("--split-cache-dir", type=Path, required=True)
    parser.add_argument("--split-mode", choices=["download", "cache-only"], required=True)
    return parser.parse_args()


def normal_cdf(value: float) -> float:
    return NormalDist().cdf(value)


def put_price(spot: float, strike: float, years: float, rate: float, vol: float) -> float:
    if min(spot, strike, years, vol) <= 0:
        return np.nan
    root_t = math.sqrt(years)
    d1 = (math.log(spot / strike) + (rate + 0.5 * vol * vol) * years) / (vol * root_t)
    d2 = d1 - vol * root_t
    return strike * math.exp(-rate * years) * normal_cdf(-d2) - spot * normal_cdf(-d1)


def put_delta(spot: float, strike: float, years: float, rate: float, vol: float) -> float:
    root_t = math.sqrt(years)
    d1 = (math.log(spot / strike) + (rate + 0.5 * vol * vol) * years) / (vol * root_t)
    return normal_cdf(d1) - 1.0


def strike_for_put_delta(spot: float, abs_delta: float, years: float, rate: float, vol: float) -> float:
    d1 = NormalDist().inv_cdf(1.0 - abs_delta)
    return spot * math.exp((rate + 0.5 * vol * vol) * years - d1 * vol * math.sqrt(years))


def strike_increment(spot: float) -> float:
    if spot < 25:
        return 0.5
    if spot < 200:
        return 1.0
    return 2.5


def round_strike(value: float, increment: float) -> float:
    return round(value / increment) * increment


def approximate_expiration(entry_date: pd.Timestamp, target_dte: int, min_dte: int, max_dte: int) -> pd.Timestamp:
    candidates = pd.date_range(
        entry_date + pd.Timedelta(days=min_dte),
        entry_date + pd.Timedelta(days=max_dte),
        freq="W-FRI",
    )
    if candidates.empty:
        raise ValueError("No Friday expiration in requested DTE range")
    return min(candidates, key=lambda value: abs((value - entry_date).days - target_dte))


def load_daily(raw_dir: Path, symbol: str, lookback: int, min_observations: int) -> pd.DataFrame:
    path = raw_dir / f"Rohdaten_{symbol}.csv"
    raw = pd.read_csv(path, usecols=["date", "close"], low_memory=False)
    raw["date"] = pd.to_datetime(raw["date"], errors="coerce")
    raw["close"] = pd.to_numeric(raw["close"], errors="coerce")
    raw = raw.dropna(subset=["date", "close"]).sort_values("date")
    daily = raw.groupby(raw["date"].dt.normalize(), sort=True).agg(close=("close", "last")).reset_index()
    daily = daily.rename(columns={"date": "session"})
    log_return = np.log(daily["close"] / daily["close"].shift(1))
    daily["rv20"] = log_return.rolling(20).std(ddof=1) * math.sqrt(252)
    daily["rv60"] = log_return.rolling(60).std(ddof=1) * math.sqrt(252)
    daily["iv_base_proxy"] = daily[["rv20", "rv60"]].max(axis=1)
    rolling = daily["iv_base_proxy"].rolling(lookback, min_periods=min_observations)
    daily["proxy_min"] = rolling.min()
    daily["proxy_max"] = rolling.max()
    denominator = daily["proxy_max"] - daily["proxy_min"]
    daily["iv_rank_proxy"] = np.where(
        denominator > 1e-12,
        100.0 * (daily["iv_base_proxy"] - daily["proxy_min"]) / denominator,
        np.nan,
    )
    daily["proxy_observations"] = daily["iv_base_proxy"].rolling(lookback).count()
    return daily.set_index("session")


def load_splits(symbol: str, cache_dir: Path, mode: str) -> list[tuple[pd.Timestamp, float]]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"{symbol}.json"
    if path.exists():
        payload = json.loads(path.read_text(encoding="utf-8"))
    elif mode == "cache-only":
        raise FileNotFoundError(path)
    else:
        yahoo_symbol = symbol.replace(".", "-")
        url = (
            f"https://query2.finance.yahoo.com/v8/finance/chart/{yahoo_symbol}"
            "?period1=1262304000&period2=1798761600&interval=1d&events=splits"
        )
        request = Request(url, headers={"User-Agent": "Mozilla/5.0 options-research/1.0"})
        with urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
        path.write_text(json.dumps(payload), encoding="utf-8")
    result = payload.get("chart", {}).get("result") or []
    events = result[0].get("events", {}).get("splits", {}) if result else {}
    splits = []
    for event in events.values():
        split_date = pd.to_datetime(event.get("date"), unit="s", errors="coerce").normalize()
        numerator = pd.to_numeric(event.get("numerator"), errors="coerce")
        denominator = pd.to_numeric(event.get("denominator"), errors="coerce")
        if pd.notna(split_date) and pd.notna(numerator) and pd.notna(denominator) and denominator > 0:
            splits.append((split_date, float(numerator / denominator)))
    return sorted(splits)


def split_context(
    event_date: pd.Timestamp,
    expiration: pd.Timestamp,
    last_local_date: pd.Timestamp,
    splits: list[tuple[pd.Timestamp, float]],
) -> tuple[float, bool, str]:
    later = [(split_date, ratio) for split_date, ratio in splits if event_date < split_date <= last_local_date]
    factor = float(np.prod([ratio for _, ratio in later])) if later else 1.0
    crosses = any(event_date < split_date <= expiration for split_date, _ in splits)
    description = "|".join(f"{split_date.date()}:{ratio:g}" for split_date, ratio in later)
    return factor, crosses, description


def main() -> None:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    deltas = [float(value) for value in args.short_deltas.split(",")]
    iv_factors = [float(value) for value in args.iv_factors.split(",")]
    skew_slopes = [float(value) for value in args.skew_slopes.split(",")]
    events = pd.read_csv(args.events)
    events["symbol"] = events["symbol"].astype(str).str.upper().str.strip()
    events["signal_date"] = pd.to_datetime(events["signal_date"], errors="coerce").dt.normalize()
    events["entry_date"] = pd.to_datetime(events["entry_date"], errors="coerce").dt.normalize()
    events = events.dropna(subset=["symbol", "signal_date", "entry_date", "entry_price"]).copy()

    screen_rows: list[dict] = []
    spread_rows: list[dict] = []
    diagnostics: list[dict] = []
    for symbol, group in events.groupby("symbol", sort=True):
        try:
            daily = load_daily(args.raw_dir, symbol, args.ivr_lookback, args.min_ivr_observations)
            splits = load_splits(symbol, args.split_cache_dir, args.split_mode)
        except Exception as exc:
            diagnostics.append({"symbol": symbol, "status": "error", "error": f"{type(exc).__name__}: {exc}"})
            continue
        for event_index, event in group.iterrows():
            signal = daily.loc[event["signal_date"]] if event["signal_date"] in daily.index else None
            if signal is None:
                diagnostics.append({"symbol": symbol, "signal_date": event["signal_date"], "status": "missing_signal_session"})
                continue
            proxy_rank = float(signal["iv_rank_proxy"])
            base_iv = float(signal["iv_base_proxy"])
            proxy_observations = float(signal["proxy_observations"])
            eligible = bool(
                np.isfinite(proxy_rank)
                and proxy_rank > args.ivr_threshold
                and np.isfinite(proxy_observations)
                and proxy_observations >= args.min_ivr_observations
            )
            screen_rows.append(
                {
                    "event_index": event_index,
                    "symbol": symbol,
                    "signal_date": event["signal_date"],
                    "entry_date": event["entry_date"],
                    "entry_price": float(event["entry_price"]),
                    "rv20": float(signal["rv20"]),
                    "rv60": float(signal["rv60"]),
                    "iv_base_proxy": base_iv,
                    "iv_rank_proxy": proxy_rank,
                    "proxy_min": float(signal["proxy_min"]),
                    "proxy_max": float(signal["proxy_max"]),
                    "proxy_observations": int(proxy_observations) if np.isfinite(proxy_observations) else np.nan,
                    "proxy_ivr_eligible": eligible,
                }
            )
            if not eligible:
                continue
            expiration = approximate_expiration(event["entry_date"], args.target_dte, args.min_dte, args.max_dte)
            split_factor, split_during_trade, split_description = split_context(
                event["entry_date"], expiration, daily.index.max(), splits
            )
            adjusted_spot = float(event["entry_price"])
            spot = adjusted_spot * split_factor
            if split_during_trade:
                diagnostics.append(
                    {
                        "symbol": symbol,
                        "signal_date": event["signal_date"],
                        "status": "split_during_option_lifetime",
                        "split_events": split_description,
                    }
                )
                continue
            dte = int((expiration - event["entry_date"]).days)
            years = dte / 365.0
            increment = strike_increment(spot)
            for iv_factor in iv_factors:
                modeled_iv = base_iv * iv_factor
                for skew_slope in skew_slopes:
                    for target_delta in deltas:
                        short_strike = round_strike(
                            strike_for_put_delta(spot, target_delta, years, args.risk_free_rate, modeled_iv),
                            increment,
                        )
                        long_strike = short_strike - args.spread_width
                        long_modeled_iv = modeled_iv + skew_slope * abs(math.log(long_strike / short_strike))
                        short_value = put_price(spot, short_strike, years, args.risk_free_rate, modeled_iv)
                        long_value = put_price(spot, long_strike, years, args.risk_free_rate, long_modeled_iv)
                        mid_credit = short_value - long_value
                        slippage = max(args.slippage_min, args.slippage_fraction * max(0.0, mid_credit))
                        executable_credit_proxy = max(0.0, mid_credit - slippage)
                        max_loss = args.spread_width - executable_credit_proxy
                        spread_rows.append(
                            {
                            "event_index": event_index,
                            "symbol": symbol,
                            "signal_date": event["signal_date"],
                            "entry_date": event["entry_date"],
                            "expiration_proxy": expiration,
                            "dte": dte,
                            "entry_price": spot,
                            "adjusted_source_entry_price": adjusted_spot,
                            "future_split_factor": split_factor,
                            "future_split_events": split_description,
                            "iv_rank_proxy": proxy_rank,
                            "iv_factor": iv_factor,
                            "modeled_iv": modeled_iv,
                            "skew_slope": skew_slope,
                            "long_modeled_iv": long_modeled_iv,
                            "target_abs_delta": target_delta,
                            "short_strike": short_strike,
                            "short_delta_after_rounding": put_delta(
                                spot, short_strike, years, args.risk_free_rate, modeled_iv
                            ),
                            "long_strike": long_strike,
                            "spread_width": args.spread_width,
                            "modeled_short_value": short_value,
                            "modeled_long_value": long_value,
                            "modeled_mid_credit": mid_credit,
                            "slippage_proxy": slippage,
                            "executable_credit_proxy": executable_credit_proxy,
                            "max_loss_proxy": max_loss,
                            "credit_to_max_loss_proxy": executable_credit_proxy / max_loss if max_loss > 0 else np.nan,
                            }
                        )

    screen = pd.DataFrame(screen_rows).sort_values(["signal_date", "symbol"])
    spreads = pd.DataFrame(spread_rows).sort_values(
        ["signal_date", "symbol", "iv_factor", "skew_slope", "target_abs_delta"]
    ) if spread_rows else pd.DataFrame()
    screen.to_csv(args.output_dir / "proxy_iv_rank_screen.csv", index=False)
    spreads.to_csv(args.output_dir / "modeled_put_credit_chains.csv", index=False)
    pd.DataFrame(diagnostics).to_csv(args.output_dir / "proxy_diagnostics.csv", index=False)

    if not spreads.empty:
        reference_factor = min(iv_factors, key=lambda value: abs(value - 1.15))
        reference_skew = min(skew_slopes, key=lambda value: abs(value - 0.70))
        reference_delta = min(deltas, key=lambda value: abs(value - 0.19))
        reference = spreads[
            np.isclose(spreads["iv_factor"], reference_factor)
            & np.isclose(spreads["skew_slope"], reference_skew)
            & np.isclose(spreads["target_abs_delta"], reference_delta)
        ].copy()
        reference["proxy_rank_bin"] = pd.cut(
            reference["iv_rank_proxy"],
            bins=[args.ivr_threshold, 40.0, 60.0, 80.0, 101.0],
            labels=["25_40", "40_60", "60_80", "80_100"],
            right=False,
        )
        sample_parts = []
        for _, group in reference.groupby("proxy_rank_bin", observed=True):
            ordered = group.sort_values(["entry_price", "signal_date", "symbol"]).reset_index(drop=True)
            if len(ordered) <= 3:
                chosen = ordered
            else:
                positions = np.linspace(0, len(ordered) - 1, 3).round().astype(int)
                chosen = ordered.iloc[positions]
            sample_parts.append(chosen)
        calibration_sample = pd.concat(sample_parts, ignore_index=True) if sample_parts else pd.DataFrame()
        calibration_sample.to_csv(args.output_dir / "calibration_sample.csv", index=False)
        shuffled_groups = {
            str(label): group.sample(frac=1.0, random_state=20260721).reset_index(drop=True)
            for label, group in reference.groupby("proxy_rank_bin", observed=True)
        }
        pool_rows = []
        labels = ["25_40", "40_60", "60_80", "80_100"]
        for position in range(max((len(group) for group in shuffled_groups.values()), default=0)):
            for label in labels:
                group = shuffled_groups.get(label)
                if group is not None and position < len(group):
                    pool_rows.append(group.iloc[position])
        pd.DataFrame(pool_rows).to_csv(args.output_dir / "calibration_pool.csv", index=False)

    eligible = screen[screen["proxy_ivr_eligible"]]
    report = [
        "# Schneller IV-/Put-Credit-Proxy",
        "",
        "## Methodische Einordnung",
        "",
        "- `iv_rank_proxy` ist ein Rang von max(RV20, RV60), kein echter IVR.",
        "- Optionswerte sind europäische Black-Scholes-Mittelwerte ohne Skew, Dividenden und Early Exercise.",
        "- Mehrere IV-Faktoren bilden ein Preisband; der Proxy entscheidet nicht endgültig über Handelbarkeit.",
        "- ThetaData wird nur für eine geschichtete Stichprobe und Grenzfälle verwendet.",
        "",
        "## Ergebnis",
        "",
        f"- Eingangssignale: {len(events)}",
        f"- Berechenbare Signale: {len(screen)}",
        f"- Proxy-Rang > {args.ivr_threshold:.1f}: {len(eligible)}",
        f"- Modellierte Spread-Varianten: {len(spreads)}",
        f"- Diagnosefehler: {sum(row.get('status') == 'error' for row in diagnostics)}",
        "",
    ]
    (args.output_dir / "proxy_report.md").write_text("\n".join(report), encoding="utf-8")
    print(f"signals={len(screen)} eligible={len(eligible)} spreads={len(spreads)}", flush=True)


if __name__ == "__main__":
    main()
