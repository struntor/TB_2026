from __future__ import annotations

import argparse
from pathlib import Path
from typing import Iterable

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


RULE = "bearish_failed_countermove_short_baseline"
DEFAULT_TRADES = Path(
    r"D:\Trading\journal\baselines\manual-regime-baseline@1.0.0"
    r"\2024-01-01_2026-08-10\trades.csv"
)
DEFAULT_RAW_DIR = Path(r"D:\Trading\Rohdaten")
DEFAULT_OUTPUT_DIR = Path(
    r"D:\Trading\journal\research\failed_countermove_macro_event_exit"
)

BLS_SOURCES = {
    2024: "https://www.bls.gov/schedule/2024/home.htm",
    2025: "https://www.bls.gov/schedule/2025/home.htm",
    2026: "https://www.bls.gov/schedule/2026/home.htm",
}
FED_SOURCE = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"

# Actual publication dates from the official BLS calendars. The irregular
# dates in late 2025 and early 2026 are intentional (publication backlog).
EVENT_DATES = {
    "CPI": [
        "2024-01-11", "2024-02-13", "2024-03-12", "2024-04-10",
        "2024-05-15", "2024-06-12", "2024-07-11", "2024-08-14",
        "2024-09-11", "2024-10-10", "2024-11-13", "2024-12-11",
        "2025-01-15", "2025-02-12", "2025-03-12", "2025-04-10",
        "2025-05-13", "2025-06-11", "2025-07-15", "2025-08-12",
        "2025-09-11", "2025-10-24", "2025-12-18",
        "2026-01-13", "2026-02-13", "2026-03-11", "2026-04-10",
        "2026-05-12", "2026-06-10", "2026-07-14", "2026-08-12",
    ],
    "PPI": [
        "2024-01-12", "2024-02-16", "2024-03-14", "2024-04-11",
        "2024-05-14", "2024-06-13", "2024-07-12", "2024-08-13",
        "2024-09-12", "2024-10-11", "2024-11-14", "2024-12-12",
        "2025-01-14", "2025-02-13", "2025-03-13", "2025-04-11",
        "2025-05-15", "2025-06-12", "2025-07-16", "2025-08-14",
        "2025-09-10", "2025-11-25",
        "2026-01-14", "2026-01-30", "2026-02-27", "2026-03-18",
        "2026-04-14", "2026-05-13", "2026-06-11", "2026-07-15",
        "2026-08-13",
    ],
    "EMPLOYMENT": [
        "2024-01-05", "2024-02-02", "2024-03-08", "2024-04-05",
        "2024-05-03", "2024-06-07", "2024-07-05", "2024-08-02",
        "2024-09-06", "2024-10-04", "2024-11-01", "2024-12-06",
        "2025-01-10", "2025-02-07", "2025-03-07", "2025-04-04",
        "2025-05-02", "2025-06-06", "2025-07-03", "2025-08-01",
        "2025-09-05", "2025-11-20", "2025-12-16",
        "2026-01-09", "2026-02-11", "2026-03-06", "2026-04-03",
        "2026-05-08", "2026-06-05", "2026-07-02", "2026-08-07",
    ],
    "FOMC": [
        "2024-01-31", "2024-03-20", "2024-05-01", "2024-06-12",
        "2024-07-31", "2024-09-18", "2024-11-07", "2024-12-18",
        "2025-01-29", "2025-03-19", "2025-05-07", "2025-06-18",
        "2025-07-30", "2025-09-17", "2025-10-29", "2025-12-10",
        "2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17",
        "2026-07-29", "2026-09-16", "2026-10-28", "2026-12-09",
    ],
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Backtest a day-before-macro-event exit on the fixed historical "
            "entry set of bearish_failed_countermove_short_baseline."
        )
    )
    parser.add_argument("--trades", type=Path, default=DEFAULT_TRADES)
    parser.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--exit-time",
        default="15:55:00",
        help="ET timestamp whose bar open is used as the event-eve market exit.",
    )
    return parser.parse_args()


def build_event_calendar(sessions: pd.DatetimeIndex, exit_time: str) -> pd.DataFrame:
    records: list[dict[str, object]] = []
    normalized_sessions = pd.DatetimeIndex(sessions).normalize().unique().sort_values()
    for event_type, dates in EVENT_DATES.items():
        for value in dates:
            release_date = pd.Timestamp(value)
            eligible = normalized_sessions[normalized_sessions < release_date]
            if eligible.empty:
                continue
            prior_session = eligible[-1]
            source = FED_SOURCE if event_type == "FOMC" else BLS_SOURCES[release_date.year]
            records.append(
                {
                    "event_type": event_type,
                    "release_date": release_date,
                    "release_time_et": "14:00" if event_type == "FOMC" else "08:30",
                    "prior_session": prior_session,
                    "exit_timestamp": pd.Timestamp(f"{prior_session.date()} {exit_time}"),
                    "source": source,
                }
            )
    return pd.DataFrame(records).sort_values(["release_date", "event_type"])


def load_sessions(raw_dir: Path) -> pd.DatetimeIndex:
    spy_path = raw_dir / "Rohdaten_SPY.csv"
    dates = pd.read_csv(spy_path, usecols=["date"])
    timestamps = pd.to_datetime(dates["date"])
    return pd.DatetimeIndex(timestamps.dt.normalize().unique()).sort_values()


def load_exit_prices(
    raw_dir: Path,
    symbol: str,
    needed: Iterable[pd.Timestamp],
) -> dict[pd.Timestamp, float]:
    path = raw_dir / f"Rohdaten_{symbol}.csv"
    bars = pd.read_csv(path, usecols=["date", "open"])
    bars["date"] = pd.to_datetime(bars["date"])
    needed_index = pd.DatetimeIndex(needed)
    selected = bars[bars["date"].isin(needed_index)].copy()
    return dict(zip(selected["date"], selected["open"].astype(float)))


def prepare_trades(path: Path) -> pd.DataFrame:
    trades = pd.read_csv(path)
    selected = trades.loc[
        (trades["track"] == "baseline") & (trades["rule"] == RULE)
    ].copy()
    selected["entry_date"] = pd.to_datetime(selected["entry_date"])
    selected["exit_date"] = pd.to_datetime(selected["exit_date"])
    selected = selected.sort_values(["entry_date", "symbol"]).reset_index(drop=True)
    if selected.empty:
        raise RuntimeError(f"No baseline trades found for {RULE!r} in {path}")
    if set(selected["side"].unique()) != {"short"}:
        raise RuntimeError("The selected rule contains a non-short trade.")
    selected["trade_id"] = np.arange(1, len(selected) + 1)
    return selected


def event_candidates(
    calendar: pd.DataFrame,
    allowed_types: set[str],
) -> pd.DataFrame:
    chosen = calendar[calendar["event_type"].isin(allowed_types)].copy()
    if chosen.empty:
        return chosen
    grouped = (
        chosen.groupby(["release_date", "prior_session", "exit_timestamp"], as_index=False)
        .agg(event_type=("event_type", lambda values: "+".join(sorted(values))))
        .sort_values("exit_timestamp")
    )
    return grouped


def simulate_variant(
    trades: pd.DataFrame,
    calendar: pd.DataFrame,
    allowed_types: set[str],
    price_cache: dict[str, dict[pd.Timestamp, float]],
    variant: str,
    event_exit_slippage_bps: float = 0.0,
) -> pd.DataFrame:
    candidates = event_candidates(calendar, allowed_types)
    rows: list[dict[str, object]] = []
    for trade in trades.itertuples(index=False):
        row = trade._asdict()
        row.update(
            {
                "variant": variant,
                "effective_exit_date": trade.exit_date,
                "effective_exit": float(trade.exit),
                "effective_exit_reason": trade.exit_reason,
                "effective_pnl": float(trade.pnl_dollars),
                "event_type": "",
                "event_release_date": pd.NaT,
                "event_exit_slippage_bps": 0.0,
                "changed": False,
            }
        )
        if not candidates.empty:
            valid = candidates.loc[
                (candidates["exit_timestamp"] > trade.entry_date)
                & (candidates["exit_timestamp"] < trade.exit_date)
            ]
            if not valid.empty:
                event = valid.iloc[0]
                timestamp = pd.Timestamp(event["exit_timestamp"])
                raw_price = price_cache.get(trade.symbol, {}).get(timestamp)
                if raw_price is None:
                    raise RuntimeError(
                        f"Missing {trade.symbol} bar open at {timestamp} for trade {trade.trade_id}"
                    )
                effective_price = raw_price * (1.0 + event_exit_slippage_bps / 10_000.0)
                row.update(
                    {
                        "effective_exit_date": timestamp,
                        "effective_exit": effective_price,
                        "effective_exit_reason": "macro_event_eve",
                        "effective_pnl": (float(trade.entry) - effective_price)
                        * int(trade.quantity),
                        "event_type": event["event_type"],
                        "event_release_date": event["release_date"],
                        "event_exit_slippage_bps": event_exit_slippage_bps,
                        "changed": True,
                    }
                )
        rows.append(row)
    result = pd.DataFrame(rows)
    result["pnl_delta"] = result["effective_pnl"] - result["pnl_dollars"]
    return result


def summarize(trades: pd.DataFrame) -> dict[str, object]:
    pnl = trades["effective_pnl"].astype(float)
    ordered = trades.sort_values(["effective_exit_date", "trade_id"])
    equity = ordered["effective_pnl"].cumsum()
    drawdown = equity - equity.cummax().clip(lower=0.0)
    gains = pnl[pnl > 0].sum()
    losses = pnl[pnl < 0].sum()
    return {
        "variant": trades["variant"].iloc[0],
        "trades": len(trades),
        "event_exits": int(trades["changed"].sum()),
        "net_pnl": pnl.sum(),
        "delta_vs_baseline": trades["pnl_delta"].sum(),
        "win_rate_pct": (pnl > 0).mean() * 100.0,
        "avg_pnl": pnl.mean(),
        "median_pnl": pnl.median(),
        "profit_factor": gains / abs(losses) if losses < 0 else np.inf,
        "max_drawdown": drawdown.min(),
        "worst_trade": pnl.min(),
        "best_trade": pnl.max(),
    }


def annual_summary(all_trades: pd.DataFrame) -> pd.DataFrame:
    data = all_trades.copy()
    data["exit_year"] = pd.to_datetime(data["effective_exit_date"]).dt.year
    return (
        data.groupby(["variant", "exit_year"], as_index=False)
        .agg(
            trades=("trade_id", "size"),
            event_exits=("changed", "sum"),
            net_pnl=("effective_pnl", "sum"),
            win_rate_pct=("effective_pnl", lambda values: (values > 0).mean() * 100.0),
        )
    )


def plot_equity(all_trades: pd.DataFrame, output_path: Path) -> None:
    styles = {
        "baseline": {"color": "#303f9f", "linewidth": 2.4},
        "cpi_only": {"color": "#ef6c00", "linewidth": 1.8},
        "cpi_ppi_employment": {"color": "#00897b", "linewidth": 2.0},
        "all_four": {"color": "#2e7d32", "linewidth": 2.5},
        "all_four_5bps": {"color": "#7cb342", "linewidth": 1.7, "linestyle": "--"},
    }
    fig, axes = plt.subplots(2, 1, figsize=(13, 9), sharey=False)
    for ax, start_date, title in [
        (axes[0], pd.Timestamp("2024-01-01"), "Gesamter Backtest"),
        (axes[1], pd.Timestamp("2026-01-01"), "Nur 2026 (bis 10.08.)"),
    ]:
        for variant, style in styles.items():
            subset = all_trades[all_trades["variant"] == variant].copy()
            subset = subset[subset["effective_exit_date"] >= start_date]
            subset = subset.sort_values(["effective_exit_date", "trade_id"])
            if subset.empty:
                continue
            y = subset["effective_pnl"].cumsum()
            x = subset["effective_exit_date"]
            ax.step(x, y, where="post", label=variant, **style)
        ax.axhline(0.0, color="#777777", linewidth=0.8)
        ax.grid(alpha=0.22)
        ax.set_title(title)
        ax.set_ylabel("Kumuliertes P&L (USD)")
        ax.legend(ncol=3, fontsize=8)
    axes[1].set_xlabel("Effektives Exit-Datum")
    fig.suptitle(
        f"{RULE}: Exit am Vortag wichtiger US-Makroevents um 15:55 ET",
        fontsize=13,
    )
    fig.tight_layout()
    fig.savefig(output_path, dpi=170, bbox_inches="tight")
    plt.close(fig)


def markdown_table(frame: pd.DataFrame, columns: list[str]) -> str:
    view = frame[columns].copy()
    for column in view.select_dtypes(include=["float"]).columns:
        view[column] = view[column].map(lambda value: f"{value:.2f}")
    headers = "| " + " | ".join(view.columns) + " |"
    separator = "| " + " | ".join(["---"] * len(view.columns)) + " |"
    rows = [
        "| " + " | ".join(str(value) for value in row) + " |"
        for row in view.itertuples(index=False, name=None)
    ]
    return "\n".join([headers, separator, *rows])


def write_report(
    output_path: Path,
    source_path: Path,
    summary: pd.DataFrame,
    annual: pd.DataFrame,
    attribution: pd.DataFrame,
    changed: pd.DataFrame,
) -> None:
    main_variants = [
        "baseline", "cpi_only", "cpi_ppi", "cpi_ppi_employment",
        "all_four", "all_four_5bps", "all_four_10bps",
    ]
    main = summary[summary["variant"].isin(main_variants)].copy()
    main["variant"] = pd.Categorical(main["variant"], main_variants, ordered=True)
    main = main.sort_values("variant")
    annual_main = annual[annual["variant"].isin(["baseline", "all_four"])]
    largest = changed.reindex(changed["pnl_delta"].abs().sort_values(ascending=False).index).head(15)
    largest = largest.copy()
    largest["entry_date"] = pd.to_datetime(largest["entry_date"]).dt.strftime("%Y-%m-%d")
    largest["event_release_date"] = pd.to_datetime(
        largest["event_release_date"]
    ).dt.strftime("%Y-%m-%d")

    text = f"""# Macro-event-eve exit backtest

Rule: `{RULE}`  
Fixed source trade set: `{source_path}`  
Primary exit: market exit at 15:55 ET on the last trading session before the release, approximated causally by the 15:55 five-minute bar open. Entries, position sizes, stops and targets are otherwise unchanged.

## Main variants

{markdown_table(main, ['variant', 'trades', 'event_exits', 'net_pnl', 'delta_vs_baseline', 'win_rate_pct', 'profit_factor', 'max_drawdown', 'worst_trade'])}

## Individual event attribution

Each row below activates only one event family.

{markdown_table(attribution, ['variant', 'event_exits', 'net_pnl', 'delta_vs_baseline', 'win_rate_pct', 'max_drawdown'])}

## Baseline versus all four event families by year

{markdown_table(annual_main, ['variant', 'exit_year', 'trades', 'event_exits', 'net_pnl', 'win_rate_pct'])}

## Largest changed trades (all four, no slippage)

{markdown_table(largest, ['trade_id', 'symbol', 'entry_date', 'event_type', 'event_release_date', 'pnl_dollars', 'effective_pnl', 'pnl_delta'])}

## Interpretation constraints

- This is an overlay on a fixed entry set. It does not recycle freed capital into additional trades.
- The event list is pre-scheduled and therefore knowable at entry time; it uses actual official release dates, including the irregular 2025/2026 backlog dates.
- The primary variant has the same zero-fee/zero-slippage convention as the source backtest. The 5 bp and 10 bp variants stress only the added event exits.
- A 15:55 bar open is observable at the decision time. It avoids using the final close of the day with hindsight.
- The 11 August 2026 QQQ trade is not part of this sample; the source run ends on 10 August 2026.

## Calendar sources

- BLS 2024: {BLS_SOURCES[2024]}
- BLS 2025: {BLS_SOURCES[2025]}
- BLS 2026: {BLS_SOURCES[2026]}
- Federal Reserve FOMC calendars: {FED_SOURCE}
"""
    output_path.write_text(text, encoding="utf-8")


def main() -> None:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    trades = prepare_trades(args.trades)
    sessions = load_sessions(args.raw_dir)
    calendar = build_event_calendar(sessions, args.exit_time)

    all_exit_timestamps = pd.DatetimeIndex(calendar["exit_timestamp"].unique())
    price_cache: dict[str, dict[pd.Timestamp, float]] = {}
    for symbol in sorted(trades["symbol"].unique()):
        price_cache[symbol] = load_exit_prices(args.raw_dir, symbol, all_exit_timestamps)

    variants: list[tuple[str, set[str], float]] = [
        ("baseline", set(), 0.0),
        ("cpi_only", {"CPI"}, 0.0),
        ("ppi_only", {"PPI"}, 0.0),
        ("employment_only", {"EMPLOYMENT"}, 0.0),
        ("fomc_only", {"FOMC"}, 0.0),
        ("cpi_ppi", {"CPI", "PPI"}, 0.0),
        ("cpi_ppi_employment", {"CPI", "PPI", "EMPLOYMENT"}, 0.0),
        ("all_four", {"CPI", "PPI", "EMPLOYMENT", "FOMC"}, 0.0),
        ("all_four_5bps", {"CPI", "PPI", "EMPLOYMENT", "FOMC"}, 5.0),
        ("all_four_10bps", {"CPI", "PPI", "EMPLOYMENT", "FOMC"}, 10.0),
    ]
    results = [
        simulate_variant(
            trades,
            calendar,
            allowed_types=event_types,
            price_cache=price_cache,
            variant=name,
            event_exit_slippage_bps=slippage_bps,
        )
        for name, event_types, slippage_bps in variants
    ]
    all_trades = pd.concat(results, ignore_index=True)
    summary = pd.DataFrame([summarize(result) for result in results])
    annual = annual_summary(all_trades)
    attribution = summary[
        summary["variant"].isin(
            ["cpi_only", "ppi_only", "employment_only", "fomc_only"]
        )
    ].copy()
    changed = all_trades[
        (all_trades["variant"] == "all_four") & all_trades["changed"]
    ].copy()

    calendar.to_csv(args.output_dir / "event_calendar.csv", index=False)
    all_trades.to_csv(args.output_dir / "trade_comparison.csv", index=False)
    summary.to_csv(args.output_dir / "variant_summary.csv", index=False)
    annual.to_csv(args.output_dir / "annual_summary.csv", index=False)
    changed.to_csv(args.output_dir / "changed_trades_all_four.csv", index=False)
    plot_equity(all_trades, args.output_dir / "equity_comparison.png")
    write_report(
        args.output_dir / "report.md",
        args.trades,
        summary,
        annual,
        attribution,
        changed,
    )

    print(summary.to_string(index=False, float_format=lambda value: f"{value:.2f}"))
    print(f"\nWrote results to {args.output_dir}")


if __name__ == "__main__":
    main()
