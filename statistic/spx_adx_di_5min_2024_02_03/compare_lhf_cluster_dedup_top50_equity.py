from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from compare_cutoff_forward_topn_variants import VariantConfig, prepare_variant_ledger
from simulate_selected_symbols_portfolio_mm import build_trades, simulate_portfolio

import plot_current_baseline_cutoff_2025_11_vs_2026_03 as active_pipeline


HERE = Path(__file__).resolve().parent
RANK_PATH = HERE / "current_baseline_optimized_cutoff_2025_11_top50_rank_scores.csv"
LEDGER_PATH = HERE / "trade_ledger_all_assets_current_baseline_symbol_rules_2026_07_04.csv"
OUTPUT_DIR = Path(r"D:\Trading\journal\research\lhf_cluster_top50_composition")

START_CAPITAL = 10_000.0
RISK_PCT = 0.01
TRAIN_START = pd.Timestamp("2024-01-01")
CUTOFF = "2025-11"
FORWARD_START = pd.Timestamp("2025-12-01")
YEAR_2026_START = pd.Timestamp("2026-01-01")
MIN_TRADES = 5
TOP_N = 50

LHF_RULE = "bearish_lower_high_failure_short"
MODEL_CLUSTER = ("SPX", "SPX_FUT", "SPY", "IVV", "VOO", "VTI", "OEF")
NON_ROUTABLE = {"SPX", "SPX_FUT"}
ROUTABLE_CLUSTER = ("SPY", "IVV", "VOO", "VTI", "OEF")
LIQUIDITY_PRIORITY = {symbol: rank for rank, symbol in enumerate(ROUTABLE_CLUSTER)}

# IWB is the next-ranked symbol, but it is another near-identical broad-market
# index exposure (stable correlation to IVV about 0.998), so it cannot replace a
# slot that was freed specifically to reduce that concentration.
REPLACEMENT_COUNT = 6
REPLACEMENT_EXCLUSIONS = {"IWB": "broad_market_duplicate"}


@dataclass(frozen=True)
class Composition:
    label: str
    candidate_symbols: list[str]
    apply_lhf_cluster_choice: bool


def load_inputs() -> tuple[pd.DataFrame, pd.DataFrame]:
    rank = pd.read_csv(RANK_PATH)
    rank["symbol"] = rank["symbol"].astype(str).str.upper()
    rank = rank.sort_values("rank").reset_index(drop=True)

    ledger = pd.read_csv(LEDGER_PATH)
    ledger["symbol"] = ledger["symbol"].astype(str).str.upper()
    ledger["entry_date"] = pd.to_datetime(ledger["entry_date"], format="mixed")
    ledger["exit_date"] = pd.to_datetime(ledger["exit_date"], format="mixed")
    return rank, ledger


def build_compositions(rank: pd.DataFrame) -> tuple[Composition, Composition, pd.DataFrame]:
    old_model = rank.head(TOP_N)["symbol"].tolist()
    old_routable = [symbol for symbol in old_model if symbol not in NON_ROUTABLE]

    replacement_rows: list[dict[str, object]] = []
    replacements: list[str] = []
    old_set = set(old_model)
    for row in rank.iloc[TOP_N:].itertuples(index=False):
        symbol = str(row.symbol)
        if symbol in old_set:
            continue
        exclusion = REPLACEMENT_EXCLUSIONS.get(symbol, "")
        accepted = not exclusion and len(replacements) < REPLACEMENT_COUNT
        replacement_rows.append(
            {
                "symbol": symbol,
                "optimization_rank": int(row.rank),
                "stability_score": float(row.stability_score),
                "accepted": accepted,
                "reason": "next_independent_rank" if accepted else exclusion or "below_required_count",
            }
        )
        if accepted:
            replacements.append(symbol)
        if len(replacements) == REPLACEMENT_COUNT and symbol == replacements[-1]:
            break

    if len(replacements) != REPLACEMENT_COUNT:
        raise RuntimeError(f"Expected {REPLACEMENT_COUNT} replacements, got {replacements}")

    old = Composition("old_cluster_all", old_routable, False)
    new = Composition(
        "new_cluster_top1_plus_replacements",
        old_routable + replacements,
        True,
    )
    return old, new, pd.DataFrame(replacement_rows)


def planned_crv(row: pd.Series) -> float:
    entry = float(row["entry"])
    stop = float(row["stop"])
    target = float(row["target"])
    risk = stop - entry
    reward = entry - target
    if not np.isfinite(risk) or not np.isfinite(reward) or risk <= 0.0 or reward <= 0.0:
        return float("-inf")
    return reward / risk


def choose_lhf_cluster_trades(ledger: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    work = ledger.copy()
    candidates = work[
        work["rule"].astype(str).eq(LHF_RULE)
        & work["side"].astype(str).eq("short")
        & work["symbol"].isin(ROUTABLE_CLUSTER)
    ].copy()
    candidates["planned_crv"] = candidates.apply(planned_crv, axis=1)
    candidates["liquidity_rank"] = candidates["symbol"].map(LIQUIDITY_PRIORITY)

    audit_rows: list[pd.DataFrame] = []
    remove_indexes: list[int] = []
    event_id = 0
    for entry_date, group in candidates.groupby("entry_date", sort=True):
        if len(group) < 2:
            continue
        event_id += 1
        ordered = group.sort_values(
            ["planned_crv", "liquidity_rank", "symbol"],
            ascending=[False, True, True],
        )
        winner_index = int(ordered.index[0])
        event = ordered.copy()
        event["event_id"] = event_id
        event["selected"] = event.index == winner_index
        event["selection_rule"] = "highest_planned_crv_then_liquidity"
        audit_rows.append(event)
        remove_indexes.extend(int(index) for index in ordered.index[1:])

    audit = pd.concat(audit_rows, ignore_index=False) if audit_rows else pd.DataFrame()
    kept = work.drop(index=remove_indexes).copy()
    return kept, audit.reset_index(names="ledger_index") if not audit.empty else audit


def run_composition(
    ledger: pd.DataFrame,
    composition: Composition,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    config = VariantConfig(
        name=composition.label,
        cutoff=CUTOFF,
        top_n=TOP_N,
        symbols=composition.candidate_symbols,
    )
    filtered, pairs = prepare_variant_ledger(ledger, config, MIN_TRADES)
    audit = pd.DataFrame()
    if composition.apply_lhf_cluster_choice:
        filtered, audit = choose_lhf_cluster_trades(filtered)

    specs = build_trades(filtered, composition.candidate_symbols, TRAIN_START.strftime("%Y-%m-%d"))
    trades = simulate_portfolio(specs, START_CAPITAL, RISK_PCT, compound=False)
    trades = active_pipeline.apply_cluster_to_simulated_trades(trades)
    trades["variant"] = composition.label
    pairs["variant"] = composition.label
    return trades, pairs, audit


def daily_curve(trades: pd.DataFrame, label: str, end_date: pd.Timestamp) -> pd.DataFrame:
    executed = trades[trades["executed"].astype(bool)].copy()
    executed["day"] = pd.to_datetime(executed["exit_date"]).dt.normalize()
    daily_pnl = executed.groupby("day")["pnl_dollars"].sum()
    dates = pd.date_range(TRAIN_START, end_date, freq="B")
    daily_pnl = daily_pnl.reindex(dates, fill_value=0.0)
    equity = START_CAPITAL + daily_pnl.cumsum()
    peak = equity.cummax()
    return pd.DataFrame(
        {
            "date": dates,
            "variant": label,
            "daily_pnl": daily_pnl.to_numpy(),
            "equity": equity.to_numpy(),
            "drawdown": (equity - peak).to_numpy(),
            "drawdown_pct": (equity / peak - 1.0).to_numpy(),
        }
    )


def longest_underwater_days(curve: pd.DataFrame) -> int:
    underwater = curve["drawdown"] < -1e-9
    groups = underwater.ne(underwater.shift(fill_value=False)).cumsum()
    lengths = underwater.groupby(groups).sum()
    return int(lengths.max()) if not lengths.empty else 0


def summarize_curve(curve: pd.DataFrame, trades: pd.DataFrame, segment: str) -> dict[str, object]:
    selected_curve = curve if segment == "all" else curve[curve["date"] >= FORWARD_START].copy()
    selected_trades = trades if segment == "all" else trades[trades["exit_date"] >= FORWARD_START].copy()
    if selected_curve.empty:
        raise RuntimeError(f"Empty curve segment: {segment}")

    base_equity = START_CAPITAL if segment == "all" else float(
        curve.loc[curve["date"] < FORWARD_START, "equity"].iloc[-1]
    )
    segment_equity = base_equity + selected_curve["daily_pnl"].cumsum()
    segment_peak = segment_equity.cummax()
    segment_drawdown = segment_equity - segment_peak
    segment_drawdown_pct = segment_equity / segment_peak - 1.0
    pnl = selected_trades["pnl_dollars"].astype(float)
    gains = float(pnl[pnl > 0.0].sum())
    losses = float(-pnl[pnl < 0.0].sum())
    profit_factor = gains / losses if losses > 0.0 else float("inf")
    underwater_frame = selected_curve.assign(drawdown=segment_drawdown.to_numpy())
    monthly = selected_curve.set_index("date")["daily_pnl"].resample("ME").sum()
    return {
        "variant": str(curve["variant"].iloc[0]),
        "segment": segment,
        "start_equity": base_equity,
        "end_equity": float(segment_equity.iloc[-1]),
        "net_profit": float(segment_equity.iloc[-1] - base_equity),
        "trades": int(len(selected_trades)),
        "win_rate": float((pnl > 0.0).mean()) if len(pnl) else 0.0,
        "profit_factor": profit_factor,
        "max_drawdown": float(segment_drawdown.min()),
        "max_drawdown_pct": float(segment_drawdown_pct.min()),
        "ulcer_index_pct": float(np.sqrt(np.mean(np.square(segment_drawdown_pct * 100.0)))),
        "daily_pnl_std": float(selected_curve["daily_pnl"].std(ddof=0)),
        "positive_month_rate": float((monthly > 0.0).mean()) if len(monthly) else 0.0,
        "longest_underwater_business_days": longest_underwater_days(underwater_frame),
    }


def write_plot(curves: pd.DataFrame, summary: pd.DataFrame) -> None:
    colors = {
        "old_cluster_all": "#2563EB",
        "new_cluster_top1_plus_replacements": "#059669",
    }
    labels = {
        "old_cluster_all": "Alt: alle korrelierten ETF-Signale",
        "new_cluster_top1_plus_replacements": "Neu: Cluster Top-1 + 6 Nachruecker",
    }
    plt.figure(figsize=(15, 8))
    for variant, group in curves.groupby("variant", sort=False):
        plt.plot(
            group["date"],
            group["equity"],
            linewidth=2.2,
            color=colors[variant],
            label=labels[variant],
        )
    plt.axvline(FORWARD_START, color="#DC2626", linestyle="--", linewidth=1.2, alpha=0.8, label="Forward ab 01.12.2025")
    plt.axhline(START_CAPITAL, color="#9CA3AF", linestyle=":", linewidth=1.0)
    plt.title("Top-50 Equity: alter Cluster vs. CRV-Top-1 mit Nachrueckern")
    plt.xlabel("Datum")
    plt.ylabel("Equity (USD, fixes Risiko 100 USD je Trade)")
    plt.grid(True, alpha=0.22)
    plt.legend()
    plt.tight_layout()
    plt.savefig(OUTPUT_DIR / "equity_comparison.png", dpi=170)
    plt.close()


def build_2026_comparison(
    curves: pd.DataFrame,
    trades: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    year_curves: list[pd.DataFrame] = []
    rows: list[dict[str, object]] = []
    for variant, group in curves.groupby("variant", sort=False):
        year = group[group["date"] >= YEAR_2026_START].copy()
        year["cumulative_pnl_2026"] = year["daily_pnl"].cumsum()
        year_curves.append(year)

        path = pd.concat(
            [pd.Series([0.0]), year["cumulative_pnl_2026"].reset_index(drop=True)],
            ignore_index=True,
        )
        drawdown = path - path.cummax()
        variant_trades = trades[
            trades["variant"].astype(str).eq(variant)
            & (trades["exit_date"] >= YEAR_2026_START)
        ].copy()
        pnl = variant_trades["pnl_dollars"].astype(float)
        gains = float(pnl[pnl > 0.0].sum())
        losses = float(-pnl[pnl < 0.0].sum())
        monthly = year.set_index("date")["daily_pnl"].resample("ME").sum()
        rows.append(
            {
                "variant": variant,
                "start": YEAR_2026_START,
                "end": year["date"].max(),
                "net_profit": float(year["daily_pnl"].sum()),
                "trades": int(len(variant_trades)),
                "win_rate": float((pnl > 0.0).mean()) if len(pnl) else 0.0,
                "profit_factor": gains / losses if losses > 0.0 else float("inf"),
                "max_drawdown": float(drawdown.min()),
                "daily_pnl_std": float(year["daily_pnl"].std(ddof=0)),
                "positive_months": int((monthly > 0.0).sum()),
                "months": int(len(monthly)),
            }
        )
    return pd.concat(year_curves, ignore_index=True), pd.DataFrame(rows)


def write_2026_plot(curves: pd.DataFrame) -> None:
    colors = {
        "old_cluster_all": "#2563EB",
        "new_cluster_top1_plus_replacements": "#059669",
    }
    labels = {
        "old_cluster_all": "Alt: alle korrelierten ETF-Signale",
        "new_cluster_top1_plus_replacements": "Neu: Cluster Top-1 + 6 Nachruecker",
    }
    plt.figure(figsize=(14, 7))
    for variant, group in curves.groupby("variant", sort=False):
        plt.plot(
            group["date"],
            group["cumulative_pnl_2026"],
            linewidth=2.3,
            color=colors[variant],
            label=labels[variant],
        )
    plt.axhline(0.0, color="#9CA3AF", linestyle=":", linewidth=1.0)
    plt.title("Equity-Vergleich 2026 YTD (auf 0 USD rebasiert)")
    plt.xlabel("Datum")
    plt.ylabel("Kumulierter Gewinn 2026 (USD)")
    plt.grid(True, alpha=0.22)
    plt.legend()
    plt.tight_layout()
    plt.savefig(OUTPUT_DIR / "equity_comparison_2026.png", dpi=170)
    plt.close()


def main() -> None:
    rank, ledger = load_inputs()
    old, new, replacements = build_compositions(rank)
    compositions = (old, new)

    all_trades: list[pd.DataFrame] = []
    all_pairs: list[pd.DataFrame] = []
    all_audits: list[pd.DataFrame] = []
    end_date = ledger["exit_date"].max().normalize()
    curves: list[pd.DataFrame] = []
    summaries: list[dict[str, object]] = []

    for composition in compositions:
        trades, pairs, audit = run_composition(ledger, composition)
        all_trades.append(trades)
        all_pairs.append(pairs)
        if not audit.empty:
            audit["variant"] = composition.label
            all_audits.append(audit)
        curve = daily_curve(trades, composition.label, end_date)
        curves.append(curve)
        for segment in ("all", "forward"):
            summaries.append(summarize_curve(curve, trades, segment))

    trades_df = pd.concat(all_trades, ignore_index=True)
    pairs_df = pd.concat(all_pairs, ignore_index=True)
    curves_df = pd.concat(curves, ignore_index=True)
    summary_df = pd.DataFrame(summaries)
    audit_df = pd.concat(all_audits, ignore_index=True) if all_audits else pd.DataFrame()

    attribution_rows: list[dict[str, object]] = []
    attribution_variants = (
        Composition("dedup_only", old.candidate_symbols, True),
        Composition("replacements_only", new.candidate_symbols, False),
    )
    for composition in attribution_variants:
        trades, _pairs, _audit = run_composition(ledger, composition)
        curve = daily_curve(trades, composition.label, end_date)
        for segment in ("all", "forward"):
            attribution_rows.append(summarize_curve(curve, trades, segment))
    attribution_df = pd.concat(
        [summary_df, pd.DataFrame(attribution_rows)],
        ignore_index=True,
    )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    trades_df.to_csv(OUTPUT_DIR / "trades.csv", index=False)
    pairs_df.to_csv(OUTPUT_DIR / "symbol_rule_selection.csv", index=False)
    curves_df.to_csv(OUTPUT_DIR / "equity_curves.csv", index=False)
    summary_df.to_csv(OUTPUT_DIR / "summary.csv", index=False)
    attribution_df.to_csv(OUTPUT_DIR / "attribution.csv", index=False)
    replacements.to_csv(OUTPUT_DIR / "replacement_selection.csv", index=False)
    audit_df.to_csv(OUTPUT_DIR / "lhf_cluster_choice_audit.csv", index=False)
    write_plot(curves_df, summary_df)
    curves_2026_df, summary_2026_df = build_2026_comparison(curves_df, trades_df)
    curves_2026_df.to_csv(OUTPUT_DIR / "equity_curves_2026.csv", index=False)
    summary_2026_df.to_csv(OUTPUT_DIR / "summary_2026.csv", index=False)
    write_2026_plot(curves_2026_df)

    print("Old routable symbols:", ",".join(old.candidate_symbols))
    print("New candidate symbols:", ",".join(new.candidate_symbols))
    print("Replacements:", ",".join(replacements.loc[replacements["accepted"], "symbol"]))
    print("\nSummary")
    print(summary_df.round(4).to_string(index=False))
    print("\n2026 YTD")
    print(
        summary_2026_df.round(
            {
                "net_profit": 4,
                "win_rate": 4,
                "profit_factor": 4,
                "max_drawdown": 4,
                "daily_pnl_std": 4,
            }
        ).to_string(index=False)
    )
    if not audit_df.empty:
        print("\nCluster choices")
        print(
            audit_df.loc[
                audit_df["selected"].astype(bool),
                ["entry_date", "symbol", "planned_crv", "points"],
            ].round({"planned_crv": 4, "points": 4}).to_string(index=False)
        )


if __name__ == "__main__":
    main()
