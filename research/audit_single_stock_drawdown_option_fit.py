from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from statistics import NormalDist
import time
from urllib.request import Request, urlopen

import numpy as np
import pandas as pd


RAW_DIR = Path(r"D:\Trading\Rohdaten")
SCREEN_DIR = Path(
    r"D:\Trading\research\single_stock_drawdown_recovery_put_credit\equity_screen_v1"
)
CANDIDATE_PATH = SCREEN_DIR / "candidate_tiers.csv"
EVENT_PATH = SCREEN_DIR / "events.csv"
VALIDATION_START = pd.Timestamp("2024-01-01")
TARGET_DTE = 42
SHORT_DELTAS = (0.16, 0.19, 0.22)
IV_PREMIUM_FACTORS = (1.00, 1.15, 1.30)
SPREAD_WIDTH = 10.0
RISK_FREE_RATE = 0.04
MIN_RV_OBSERVATIONS = 126
SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
SEC_USER_AGENT = "Trading strategy research torsten.codex.research@example.com"


def read_daily(symbol: str) -> pd.DataFrame:
    frame = pd.read_csv(
        RAW_DIR / f"Rohdaten_{symbol}.csv",
        usecols=["date", "open", "high", "low", "close", "volume"],
        low_memory=False,
    )
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    for column in ("open", "high", "low", "close", "volume"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["date", "open", "high", "low", "close"])
    frame["session"] = frame["date"].dt.normalize()
    daily = (
        frame.groupby("session", sort=True)
        .agg(
            open=("open", "first"),
            high=("high", "max"),
            low=("low", "min"),
            close=("close", "last"),
            volume=("volume", "sum"),
        )
        .reset_index()
    )
    daily = daily[(daily[["open", "high", "low", "close"]] > 0).all(axis=1)].copy()
    log_return = np.log(daily["close"] / daily["close"].shift(1))
    daily["rv20"] = log_return.rolling(20).std(ddof=1) * math.sqrt(252)
    daily["rv60"] = log_return.rolling(60).std(ddof=1) * math.sqrt(252)
    daily["rv20_percentile252"] = daily["rv20"].rolling(
        252, min_periods=MIN_RV_OBSERVATIONS
    ).apply(lambda values: float(np.mean(values <= values[-1])), raw=True)
    return daily.sort_values("session").reset_index(drop=True)


def normal_cdf(value: float) -> float:
    return NormalDist().cdf(value)


def put_price(spot: float, strike: float, years: float, rate: float, vol: float) -> float:
    if min(spot, strike, years, vol) <= 0:
        return np.nan
    root_t = math.sqrt(years)
    d1 = (math.log(spot / strike) + (rate + 0.5 * vol * vol) * years) / (vol * root_t)
    d2 = d1 - vol * root_t
    return strike * math.exp(-rate * years) * normal_cdf(-d2) - spot * normal_cdf(-d1)


def strike_for_put_delta(
    spot: float, abs_delta: float, years: float, rate: float, vol: float
) -> float:
    d1 = NormalDist().inv_cdf(1.0 - abs_delta)
    return spot * math.exp((rate + 0.5 * vol * vol) * years - d1 * vol * math.sqrt(years))


def rounded_strike(value: float) -> float:
    return float(round(value))


def session_at_or_after(daily: pd.DataFrame, target: pd.Timestamp) -> int | None:
    matches = np.flatnonzero(daily["session"].to_numpy() >= target.to_datetime64())
    return int(matches[0]) if len(matches) else None


def download_json(url: str, cache_path: Path, sec_mode: str) -> dict:
    if cache_path.exists():
        return json.loads(cache_path.read_text(encoding="utf-8"))
    if sec_mode == "cache-only":
        raise FileNotFoundError(f"SEC cache missing: {cache_path}")
    if sec_mode == "skip":
        return {}
    request = Request(url, headers={"User-Agent": SEC_USER_AGENT})
    with urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(payload), encoding="utf-8")
    time.sleep(0.12)
    return payload


def load_sec_earnings_dates(symbols: list[str], cache_dir: Path, sec_mode: str) -> tuple[dict[str, list[pd.Timestamp]], list[dict[str, object]]]:
    if sec_mode == "skip":
        return {symbol: [] for symbol in symbols}, [
            {"symbol": symbol, "sec_status": "skipped", "earnings_proxy_dates": 0}
            for symbol in symbols
        ]
    ticker_payload = download_json(SEC_TICKERS_URL, cache_dir / "company_tickers.json", sec_mode)
    ticker_to_cik = {
        str(row["ticker"]).upper(): int(row["cik_str"])
        for row in ticker_payload.values()
    }
    dates_by_symbol: dict[str, list[pd.Timestamp]] = {}
    diagnostics: list[dict[str, object]] = []
    for symbol in symbols:
        cik = ticker_to_cik.get(symbol)
        if cik is None:
            dates_by_symbol[symbol] = []
            diagnostics.append({"symbol": symbol, "sec_status": "missing_cik", "earnings_proxy_dates": 0})
            continue
        try:
            payload = download_json(
                SEC_SUBMISSIONS_URL.format(cik=cik),
                cache_dir / f"CIK{cik:010d}.json",
                sec_mode,
            )
            recent = pd.DataFrame(payload.get("filings", {}).get("recent", {}))
            if recent.empty:
                found: list[pd.Timestamp] = []
            else:
                item = recent.get("items", pd.Series("", index=recent.index)).fillna("").astype(str)
                form = recent.get("form", pd.Series("", index=recent.index)).fillna("").astype(str)
                filing_date = pd.to_datetime(recent.get("filingDate"), errors="coerce")
                mask = form.isin(["8-K", "8-K/A"]) & item.str.contains(
                    r"(?:^|,|\s)2\.02(?:,|\s|$)", regex=True
                )
                found = sorted(pd.Timestamp(value).normalize() for value in filing_date[mask].dropna().unique())
            dates_by_symbol[symbol] = found
            diagnostics.append(
                {"symbol": symbol, "sec_status": "ok", "cik": cik, "earnings_proxy_dates": len(found)}
            )
        except Exception as exc:
            dates_by_symbol[symbol] = []
            diagnostics.append(
                {"symbol": symbol, "sec_status": f"error:{type(exc).__name__}", "cik": cik, "earnings_proxy_dates": 0, "error": str(exc)}
            )
    return dates_by_symbol, diagnostics


def earnings_context(
    entry_date: pd.Timestamp, dates: list[pd.Timestamp]
) -> tuple[pd.Timestamp | pd.NaT, pd.Timestamp | pd.NaT, float]:
    prior_day = entry_date - pd.Timedelta(days=1)
    prior = prior_day if prior_day in dates else pd.NaT
    end = entry_date + pd.Timedelta(days=TARGET_DTE)
    eligible = [date for date in dates if entry_date <= date <= end]
    if not eligible:
        return prior, pd.NaT, np.nan
    nearest = min(eligible)
    return prior, nearest, float((nearest - entry_date).days)


def build_event_audit(
    candidates: pd.DataFrame,
    events: pd.DataFrame,
    earnings_dates: dict[str, list[pd.Timestamp]],
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    selected = candidates[["symbol", "family", "tier"]].drop_duplicates()
    events = events.merge(selected, on=["symbol", "family"], how="inner")
    events = events[events["signal_date"].ge(VALIDATION_START)].copy()
    for symbol, symbol_events in events.groupby("symbol", sort=True):
        daily = read_daily(symbol)
        index_by_session = pd.Series(daily.index, index=daily["session"]).to_dict()
        for event in symbol_events.itertuples(index=False):
            signal_date = pd.Timestamp(event.signal_date).normalize()
            entry_date = pd.Timestamp(event.entry_date).normalize()
            signal_index = index_by_session.get(signal_date)
            entry_index = index_by_session.get(entry_date)
            if signal_index is None or entry_index is None:
                continue
            signal = daily.iloc[int(signal_index)]
            expiry_index = session_at_or_after(daily, entry_date + pd.Timedelta(days=TARGET_DTE))
            if expiry_index is None or expiry_index <= int(entry_index):
                continue
            spot = float(event.entry_price)
            rv20 = float(signal["rv20"])
            rv60 = float(signal["rv60"])
            rv_rank = float(signal["rv20_percentile252"])
            future = daily.iloc[int(entry_index) : expiry_index + 1]
            prior_earnings_date, earnings_date, earnings_days = earnings_context(
                entry_date, earnings_dates.get(symbol, [])
            )
            base: dict[str, object] = {
                "symbol": symbol,
                "family": event.family,
                "tier": event.tier,
                "signal_date": signal_date,
                "entry_date": entry_date,
                "entry_price": spot,
                "rv20": rv20,
                "rv60": rv60,
                "rv20_percentile252": rv_rank,
                "rv_rank_proxy_ge_30": rv_rank >= 0.30,
                "earnings_proxy_prior_day": prior_earnings_date,
                "earnings_proxy_date": earnings_date,
                "upcoming_earnings_within_42cd": pd.notna(earnings_date),
                "days_to_earnings_proxy": earnings_days,
                "expiry_session_42cd": daily.iloc[expiry_index]["session"],
                "expiry_close_42cd": float(daily.iloc[expiry_index]["close"]),
                "minimum_low_42cd": float(future["low"].min()),
            }
            for iv_factor in IV_PREMIUM_FACTORS:
                vol = max(rv20, rv60) * iv_factor
                for delta in SHORT_DELTAS:
                    suffix = f"d{int(round(delta * 100)):02d}_ivx{int(round(iv_factor * 100)):03d}"
                    short_strike = rounded_strike(
                        strike_for_put_delta(spot, delta, TARGET_DTE / 365.0, RISK_FREE_RATE, vol)
                    )
                    long_strike = short_strike - SPREAD_WIDTH
                    short_price = put_price(spot, short_strike, TARGET_DTE / 365.0, RISK_FREE_RATE, vol)
                    long_price = put_price(spot, long_strike, TARGET_DTE / 365.0, RISK_FREE_RATE, vol)
                    credit = short_price - long_price
                    max_loss = SPREAD_WIDTH - credit
                    expiry_close = base["expiry_close_42cd"]
                    intrinsic_loss = min(
                        SPREAD_WIDTH,
                        max(0.0, short_strike - float(expiry_close))
                        - max(0.0, long_strike - float(expiry_close)),
                    )
                    base[f"short_strike_{suffix}"] = short_strike
                    base[f"short_distance_pct_{suffix}"] = short_strike / spot - 1.0
                    base[f"theoretical_credit_{suffix}"] = credit
                    base[f"credit_to_max_loss_{suffix}"] = credit / max_loss if max_loss > 0 else np.nan
                    base[f"short_breached_intraday_{suffix}"] = float(base["minimum_low_42cd"]) <= short_strike
                    base[f"expiry_below_short_{suffix}"] = float(expiry_close) < short_strike
                    base[f"expiry_proxy_pnl_{suffix}"] = credit - intrinsic_loss
            rows.append(base)
    return pd.DataFrame(rows).sort_values(["tier", "symbol", "entry_date"]).reset_index(drop=True)


def wilson_lower(successes: int, count: int, z: float = 1.645) -> float:
    if count <= 0:
        return np.nan
    p = successes / count
    denominator = 1 + z * z / count
    center = p + z * z / (2 * count)
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * count)) / count)
    return (center - margin) / denominator


def summarize(event_audit: pd.DataFrame, candidates: pd.DataFrame) -> pd.DataFrame:
    suffix = "d19_ivx115"
    rows: list[dict[str, object]] = []
    candidate_lookup = candidates.set_index(["symbol", "family"])
    for (symbol, family, tier), group in event_audit.groupby(["symbol", "family", "tier"], sort=True):
        clean = group[
            group["rv_rank_proxy_ge_30"] & ~group["upcoming_earnings_within_42cd"]
        ]
        wins = int((clean[f"expiry_proxy_pnl_{suffix}"] > 0).sum())
        width_pct = SPREAD_WIDTH / group["entry_price"]
        source = candidate_lookup.loc[(symbol, family)]
        rows.append(
            {
                "symbol": symbol,
                "family": family,
                "tier": tier,
                "validation_events": len(group),
                "events_rv_rank_proxy_ge_30": int(group["rv_rank_proxy_ge_30"].sum()),
                "upcoming_earnings_conflicts_42cd": int(
                    group["upcoming_earnings_within_42cd"].sum()
                ),
                "prior_day_earnings_events": int(group["earnings_proxy_prior_day"].notna().sum()),
                "clean_proxy_events": len(clean),
                "clean_proxy_expiry_win_rate": wins / len(clean) if len(clean) else np.nan,
                "clean_proxy_expiry_win_wilson90_lower": wilson_lower(wins, len(clean)),
                "clean_proxy_mean_expiry_pnl_points": clean[f"expiry_proxy_pnl_{suffix}"].mean(),
                "short_breach_rate_d19_ivx115": clean[f"short_breached_intraday_{suffix}"].mean(),
                "expiry_below_short_rate_d19_ivx115": clean[f"expiry_below_short_{suffix}"].mean(),
                "median_theoretical_credit_d19_ivx115": clean[f"theoretical_credit_{suffix}"].median(),
                "median_credit_to_max_loss_d19_ivx115": clean[f"credit_to_max_loss_{suffix}"].median(),
                "median_fixed_width_pct_of_spot": width_pct.median(),
                "validation_floor_hold_35d": source["validation_floor_hold_35d"],
                "validation_median_mae_35d": source["validation_median_mae_35d"],
            }
        )
    summary = pd.DataFrame(rows)
    summary["audit_status"] = np.select(
        [
            (summary["clean_proxy_events"] >= 4)
            & (summary["clean_proxy_expiry_win_rate"] >= 0.75)
            & (summary["expiry_below_short_rate_d19_ivx115"] <= 0.25),
            summary["clean_proxy_events"] >= 3,
        ],
        ["priority_for_exact_options", "watchlist"],
        default="insufficient_clean_events",
    )
    return summary.sort_values(
        ["audit_status", "clean_proxy_expiry_win_rate", "clean_proxy_events"],
        ascending=[True, False, False],
    ).reset_index(drop=True)


def write_report(summary: pd.DataFrame, event_audit: pd.DataFrame, sec_diagnostics: pd.DataFrame, output_dir: Path) -> None:
    priority = summary[summary["audit_status"].eq("priority_for_exact_options")]
    lines = [
        "# Options-Eignungsprüfung der Drawdown-Bottom-Kandidaten",
        "",
        "## Methodische Grenze",
        "",
        "Diese Stufe verwendet ausschließlich zum Signalzeitpunkt bekannte Aktienkurse. "
        "Der RV20-Perzentilrang ist **kein IVR**. Die Optionspreise sind Black-Scholes-Proxys "
        "mit `max(RV20, RV60) × 1,15`, ohne Skew, Bid/Ask, Dividenden und frühe Ausübung. "
        "Sie dienen nur zur Auswahl der Aktien, für die echte ThetaData-Ketten geladen werden sollen.",
        "",
        "Earnings werden historisch durch SEC-8-K-Filings mit Item 2.02 angenähert. "
        "Das Filing-Datum kann vom tatsächlichen Veröffentlichungszeitpunkt abweichen.",
        "",
        "## Ergebnis",
        "",
        f"- Kandidaten geprüft: {summary['symbol'].nunique()}",
        f"- Validierungsereignisse geprüft: {len(event_audit)}",
        f"- Ereignisse mit RV-Rank-Proxy >= 30 und ohne Earnings-Filing bis 42 Kalendertage: "
        f"{int(summary['clean_proxy_events'].sum())}",
        f"- Priorität für echten Optionsdaten-Download: {len(priority)}",
        "",
    ]
    if len(priority):
        lines.extend(["## Priorisierte Kandidaten", ""])
        for row in priority.itertuples(index=False):
            lines.append(
                f"- **{row.symbol} / {row.family}**: {row.clean_proxy_events} saubere Ereignisse, "
                f"Proxy-Expiry-Winrate {row.clean_proxy_expiry_win_rate:.1%}, "
                f"Short-Strike-Breach {row.short_breach_rate_d19_ivx115:.1%}."
            )
        lines.append("")
    lines.extend(
        [
            "## Nächster belastbarer Schritt",
            "",
            "Nur für die priorisierten Kandidaten echte historische Optionsketten laden. Dann werden "
            "IVR (nicht RV-Rank), tatsächliche 16/19/22-Delta-Strikes, 10-Punkte-Long-Leg, Bid/Ask, "
            "Open Interest/Volumen und der 50%-Take-Profit geprüft.",
            "",
            "## SEC-Abdeckung",
            "",
        ]
    )
    for row in sec_diagnostics.itertuples(index=False):
        lines.append(f"- {row.symbol}: {row.sec_status}, {row.earnings_proxy_dates} Earnings-Proxydaten")
    (output_dir / "option_fit_audit_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--sec-mode", choices=("download", "cache-only", "skip"), required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    candidates = pd.read_csv(CANDIDATE_PATH)
    candidates = candidates[candidates["tier"].isin(["A_repeatable_core", "B_exploratory"])].copy()
    events = pd.read_csv(EVENT_PATH, parse_dates=["signal_date", "entry_date"])
    symbols = sorted(candidates["symbol"].unique())
    earnings_dates, sec_rows = load_sec_earnings_dates(
        symbols, args.output_dir / "sec_cache", args.sec_mode
    )
    sec_diagnostics = pd.DataFrame(sec_rows)
    event_audit = build_event_audit(candidates, events, earnings_dates)
    summary = summarize(event_audit, candidates)
    event_audit.to_csv(args.output_dir / "option_fit_event_audit.csv", index=False)
    summary.to_csv(args.output_dir / "option_fit_candidate_summary.csv", index=False)
    sec_diagnostics.to_csv(args.output_dir / "sec_earnings_diagnostics.csv", index=False)
    write_report(summary, event_audit, sec_diagnostics, args.output_dir)
    print(summary.to_string(index=False))


if __name__ == "__main__":
    main()
