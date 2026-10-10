from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

from compare_cutoff_forward_topn_variants import VariantConfig
from current_live_baseline_config import LIVE_PAIR_SELECTION_PATH, load_optimized_live_top50_symbols
from manual_regime_rules_backtest import load_bars
from simulate_selected_symbols_portfolio_mm import build_trades, load_ledger, simulate_portfolio, split_summary, summarize


OUT_DIR = Path(__file__).resolve().parent
BASE_LEDGER = OUT_DIR / "trade_ledger_cutoff_2025_11_top50_live_history_548d_monitor60m_early_lhf_veto_combo.csv"
BASE_FIXED_TRADES = OUT_DIR / "cutoff_2025_11_top50_live_history_548d_monitor60m_early_lhf_veto_combo_fixed_pairs_trades.csv"
BASE_PAIR_SELECTION = LIVE_PAIR_SELECTION_PATH

OUT_SUMMARY = OUT_DIR / "prior_churn_warning_veto_sweep_summary.csv"
OUT_AUDIT = OUT_DIR / "prior_churn_warning_veto_sweep_audit.csv"
OUT_PREFIX = "cutoff_2025_11_top50_live_history_548d_prior_churn_warning"

START = "2023-01-01"
END = "2026-06-18"
START_CAPITAL = 10_000.0
RISK_PCT = 0.01


@dataclass(frozen=True)
class ChurnConfig:
    name: str
    target_rules: tuple[str, ...]
    vol_rank_min: int = 20
    vol_ratio_min: float = 1.50
    range_ratio_max: float = 0.90
    body_to_range_max: float = 0.20
    close_pos_min: float = 0.35
    close_pos_max: float = 0.65


CONFIGS = [
    ChurnConfig(
        "strict_gap_short_only",
        target_rules=("bearish_gap_breakdown_continuation_short",),
    ),
    ChurnConfig(
        "strict_gap_and_failed_countermove",
        target_rules=(
            "bearish_gap_breakdown_continuation_short",
            "bearish_failed_countermove_short_baseline",
        ),
    ),
    ChurnConfig(
        "strict_all_core_shorts",
        target_rules=(
            "bearish_gap_breakdown_continuation_short",
            "bearish_failed_countermove_short_baseline",
            "bearish_lower_high_failure_short",
        ),
    ),
    ChurnConfig(
        "very_strict_gap_short_only",
        target_rules=("bearish_gap_breakdown_continuation_short",),
        vol_ratio_min=1.75,
        range_ratio_max=0.85,
        body_to_range_max=0.15,
    ),
]


def variant_config() -> VariantConfig:
    return VariantConfig(
        name="cutoff_2025-11_top50",
        cutoff="2025-11",
        top_n=50,
        symbols=load_optimized_live_top50_symbols(),
    )


def add_daily_churn_features(symbol: str) -> pd.DataFrame:
    bars = load_bars(symbol, START, END)
    bars["day"] = pd.to_datetime(bars["date"]).dt.normalize()
    daily = (
        bars.groupby("day")
        .agg(
            open=("open", "first"),
            high=("high", "max"),
            low=("low", "min"),
            close=("close", "last"),
            volume=("volume", "sum"),
            bars=("date", "size"),
        )
        .reset_index()
    )
    daily["range"] = daily["high"] - daily["low"]
    daily["body"] = (daily["close"] - daily["open"]).abs()
    daily["body_to_range"] = daily["body"] / daily["range"].replace(0, pd.NA)
    daily["close_pos"] = (daily["close"] - daily["low"]) / daily["range"].replace(0, pd.NA)
    daily["vol_avg20_prev"] = daily["volume"].shift(1).rolling(20, min_periods=10).mean()
    daily["range_avg20_prev"] = daily["range"].shift(1).rolling(20, min_periods=10).mean()
    daily["vol_ratio20"] = daily["volume"] / daily["vol_avg20_prev"]
    daily["range_ratio20"] = daily["range"] / daily["range_avg20_prev"]

    ranks: list[int] = []
    for idx, row in daily.iterrows():
        prev = daily.iloc[max(0, idx - 20) : idx]
        ranks.append(int((prev["volume"] <= row["volume"]).sum() + 1) if len(prev) > 0 else 1)
    daily["vol_rank20"] = ranks
    daily["symbol"] = symbol
    return daily


def is_churn(row: pd.Series, config: ChurnConfig) -> bool:
    values = [
        row.get("vol_rank20"),
        row.get("vol_ratio20"),
        row.get("range_ratio20"),
        row.get("body_to_range"),
        row.get("close_pos"),
    ]
    if any(pd.isna(value) for value in values):
        return False
    return bool(
        float(row["vol_rank20"]) >= config.vol_rank_min
        and float(row["vol_ratio20"]) >= config.vol_ratio_min
        and float(row["range_ratio20"]) <= config.range_ratio_max
        and float(row["body_to_range"]) <= config.body_to_range_max
        and config.close_pos_min <= float(row["close_pos"]) <= config.close_pos_max
    )


def build_prior_churn_table(symbols: list[str], config: ChurnConfig) -> pd.DataFrame:
    parts: list[pd.DataFrame] = []
    for idx, symbol in enumerate(symbols, start=1):
        print(f"churn {config.name} {idx}/{len(symbols)} {symbol}", flush=True)
        daily = add_daily_churn_features(symbol)
        daily["prior_churn_warning"] = daily.apply(lambda row: is_churn(row, config), axis=1)
        daily["entry_date"] = daily["day"].shift(-1)
        parts.append(
            daily[
                [
                    "symbol",
                    "entry_date",
                    "day",
                    "prior_churn_warning",
                    "volume",
                    "range",
                    "body_to_range",
                    "close_pos",
                    "vol_ratio20",
                    "range_ratio20",
                    "vol_rank20",
                ]
            ].rename(columns={"day": "prior_day"})
        )
    out = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
    out["symbol"] = out["symbol"].astype(str).str.upper()
    out["entry_date"] = pd.to_datetime(out["entry_date"]).dt.normalize()
    return out


def run_fixed_pairs(ledger_path: Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    ledger = load_ledger(ledger_path)
    variant = variant_config()
    pairs = pd.read_csv(BASE_PAIR_SELECTION)
    keep_pairs = {
        (str(row.symbol).upper(), str(row.rule))
        for row in pairs.itertuples(index=False)
        if bool(row.keep)
    }
    filtered = ledger[
        ledger["symbol"].astype(str).str.upper().isin({symbol.upper() for symbol in variant.symbols})
        & ledger.apply(lambda row: (str(row["symbol"]).upper(), str(row["rule"])) in keep_pairs, axis=1)
    ].copy()
    trades = build_trades(filtered, variant.symbols, variant.train_start.strftime("%Y-%m-%d"))
    trades_df = simulate_portfolio(trades, START_CAPITAL, RISK_PCT, compound=False)
    return trades_df, summarize(trades_df, START_CAPITAL), split_summary(trades_df, START_CAPITAL, variant.forward_start)


def apply_veto(base: pd.DataFrame, config: ChurnConfig) -> tuple[pd.DataFrame, pd.DataFrame]:
    work = base.copy()
    work["symbol"] = work["symbol"].astype(str).str.upper()
    work["entry_date_norm"] = pd.to_datetime(work["entry_date"]).dt.normalize()
    symbols = sorted(work["symbol"].unique().tolist())
    churn = build_prior_churn_table(symbols, config)
    merged = work.merge(
        churn,
        left_on=["symbol", "entry_date_norm"],
        right_on=["symbol", "entry_date"],
        how="left",
        suffixes=("", "_churn"),
    )
    mask = (
        merged["rule"].astype(str).isin(config.target_rules)
        & merged["side"].astype(str).eq("short")
        & merged["prior_churn_warning"].fillna(False).astype(bool)
    )
    audit_cols = [
        "symbol",
        "entry_date",
        "exit_date",
        "rule",
        "side",
        "points",
        "weighted_points",
        "exit_reason",
        "prior_day",
        "volume",
        "vol_ratio20",
        "vol_rank20",
        "range_ratio20",
        "body_to_range",
        "close_pos",
    ]
    audit = merged.loc[mask, [col for col in audit_cols if col in merged.columns]].copy()
    audit["config"] = config.name
    out = merged.loc[~mask].copy()
    drop_cols = [
        "entry_date_norm",
        "entry_date_churn",
        "prior_day",
        "prior_churn_warning",
        "volume",
        "range",
        "body_to_range",
        "close_pos",
        "vol_ratio20",
        "range_ratio20",
        "vol_rank20",
    ]
    out = out.drop(columns=[col for col in drop_cols if col in out.columns], errors="ignore")
    if "equity_after_trade" in out.columns:
        out["equity_after_trade"] = pd.to_numeric(out["points"], errors="coerce").fillna(0.0).cumsum()
    return out, audit


def flatten(config: str, trades: pd.DataFrame, summary: pd.DataFrame, split: pd.DataFrame, audit: pd.DataFrame) -> dict[str, object]:
    row = {"config": config}
    row.update(summary.iloc[0].to_dict())
    for part in split.itertuples(index=False):
        row[f"{part.segment}_net_profit"] = float(part.net_profit)
        row[f"{part.segment}_profit_factor"] = float(part.profit_factor)
        row[f"{part.segment}_max_drawdown"] = float(part.max_drawdown)
    veto_points = pd.to_numeric(audit.get("points", pd.Series(dtype=float)), errors="coerce").fillna(0.0)
    row["vetoed_trades"] = int(len(audit))
    row["vetoed_points"] = float(veto_points.sum())
    row["vetoed_winners"] = int((veto_points > 0).sum())
    row["vetoed_losers"] = int((veto_points <= 0).sum())
    row["vetoed_2026_trades"] = int((pd.to_datetime(audit.get("exit_date", pd.Series(dtype=str)), errors="coerce").dt.normalize() >= pd.Timestamp("2026-01-01")).sum()) if not audit.empty else 0
    return row


def write_plot(curves: dict[str, pd.DataFrame]) -> None:
    plt.figure(figsize=(14, 7))
    for name, trades in curves.items():
        executed = trades[trades["executed"].astype(bool)].copy()
        if executed.empty:
            continue
        equity = executed[["exit_date", "equity_after"]].copy()
        equity["normalized_equity"] = equity["equity_after"] / START_CAPITAL
        plt.plot(equity["exit_date"], equity["normalized_equity"], linewidth=2.0, label=name)
    plt.axhline(1.0, color="#9CA3AF", linestyle="--", linewidth=1.0)
    plt.axvline(pd.Timestamp("2025-12-01"), color="#DC2626", linestyle="--", linewidth=1.0, alpha=0.75, label="forward start")
    plt.title("Top50 neue Baseline: prior churn warning veto")
    plt.xlabel("Exit date")
    plt.ylabel("Normalized equity")
    plt.grid(True, alpha=0.25)
    plt.legend()
    plt.tight_layout()
    plt.savefig(OUT_DIR / f"{OUT_PREFIX}_equity_compare.png", dpi=150)
    plt.close()


def main() -> None:
    base = pd.read_csv(BASE_LEDGER, parse_dates=["entry_date", "exit_date"])
    base_trades = pd.read_csv(BASE_FIXED_TRADES, parse_dates=["entry_date", "exit_date"])
    base_summary = summarize(base_trades, START_CAPITAL)
    base_split = split_summary(base_trades, START_CAPITAL, pd.Timestamp("2025-12-01"))
    rows = [flatten("baseline_gap_lhf", base_trades, base_summary, base_split, pd.DataFrame())]
    audits: list[pd.DataFrame] = []
    curves = {"baseline_gap_lhf": base_trades}

    for config in CONFIGS:
        ledger, audit = apply_veto(base, config)
        ledger_path = OUT_DIR / f"trade_ledger_{OUT_PREFIX}_{config.name}.csv"
        ledger.to_csv(ledger_path, index=False)
        trades_df, summary_df, split_df = run_fixed_pairs(ledger_path)
        prefix = OUT_DIR / f"{OUT_PREFIX}_{config.name}_fixed_pairs"
        trades_df.assign(variant=variant_config().name).to_csv(prefix.with_name(prefix.name + "_trades.csv"), index=False)
        summary_df.assign(variant=variant_config().name).to_csv(prefix.with_name(prefix.name + "_summary.csv"), index=False)
        split_df.assign(variant=variant_config().name).to_csv(prefix.with_name(prefix.name + "_split_summary.csv"), index=False)
        rows.append(flatten(config.name, trades_df, summary_df, split_df, audit))
        if not audit.empty:
            audits.append(audit)
        if config.name in {"strict_gap_short_only", "very_strict_gap_short_only"}:
            curves[config.name] = trades_df

    summary = pd.DataFrame(rows)
    summary.to_csv(OUT_SUMMARY, index=False)
    audit_all = pd.concat(audits, ignore_index=True) if audits else pd.DataFrame()
    audit_all.to_csv(OUT_AUDIT, index=False)
    write_plot(curves)

    show_cols = [
        "config",
        "net_profit",
        "profit_factor",
        "max_drawdown",
        "post_split_net_profit",
        "vetoed_trades",
        "vetoed_points",
        "vetoed_winners",
        "vetoed_losers",
        "vetoed_2026_trades",
    ]
    print(summary[show_cols].round(3).to_string(index=False))
    print(f"\nwrote {OUT_SUMMARY}")
    print(f"wrote {OUT_AUDIT}")


if __name__ == "__main__":
    main()
