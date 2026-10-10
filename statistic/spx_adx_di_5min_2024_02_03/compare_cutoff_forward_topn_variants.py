from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

import manual_regime_rules_backtest as rules
from simulate_selected_symbols_portfolio_mm import build_trades, load_ledger, simulate_portfolio, split_summary, summarize


OUT_DIR = Path(__file__).resolve().parent
RANK_PATH = OUT_DIR / "rank_evolution_top10_scores.csv"
LEDGER_PATH = OUT_DIR / "trade_ledger_baseline_best_modes.csv"
START_CAPITAL = 10_000.0
RISK_PCT = 0.01


@dataclass(frozen=True)
class VariantConfig:
    name: str
    cutoff: str
    top_n: int
    symbols: list[str]

    @property
    def train_start(self) -> pd.Timestamp:
        return pd.Timestamp("2024-01-01").normalize()

    @property
    def cutoff_end(self) -> pd.Timestamp:
        return pd.Period(self.cutoff, freq="M").to_timestamp(how="end").normalize()

    @property
    def forward_start(self) -> pd.Timestamp:
        return (self.cutoff_end + pd.Timedelta(days=1)).normalize()

    @property
    def forward_end(self) -> pd.Timestamp:
        return pd.Timestamp("2026-06-18").normalize()


def load_cutoff_topn(path: Path, cutoff: str, top_n: int) -> list[str]:
    df = pd.read_csv(path)
    if df.empty:
        raise ValueError(f"Empty ranking export: {path}")
    df = df[df["cutoff"].astype(str) == cutoff].copy()
    if df.empty:
        raise ValueError(f"No rows for cutoff={cutoff} in {path}")
    df["stability_score"] = pd.to_numeric(df["stability_score"], errors="coerce")
    df = df.dropna(subset=["stability_score"])
    df = df.sort_values(["stability_score", "weighted_points", "points"], ascending=[False, False, False]).reset_index(drop=True)
    return df.head(top_n)["symbol"].astype(str).str.upper().tolist()


def max_drawdown(equity: pd.Series) -> float:
    if equity.empty:
        return 0.0
    return float((equity - equity.cummax()).min())


def profit_factor(pnl: pd.Series) -> float:
    gains = float(pnl[pnl > 0].sum())
    losses = float(-pnl[pnl < 0].sum())
    if losses == 0.0:
        return float("inf") if gains > 0 else 0.0
    return gains / losses


def load_spx_delta_curve(plot_start: pd.Timestamp, end_exclusive: pd.Timestamp) -> pd.DataFrame:
    _, _, daily = rules.run_backtest(
        "SPX",
        "2023-12-01",
        end_exclusive.strftime("%Y-%m-%d"),
        structure_lookback=10,
        bull_continuation_mode="combo",
    )
    daily = daily.copy()
    daily["date"] = pd.to_datetime(daily["date"]).dt.normalize()
    curve = daily[daily["date"] >= plot_start].copy()
    if curve.empty:
        return pd.DataFrame(columns=["date", "delta"])
    base = float(curve.loc[curve["date"] == plot_start, "close"].iloc[0]) if (curve["date"] == plot_start).any() else float(curve["close"].iloc[0])
    curve["delta"] = curve["close"].astype(float) - base
    return curve[["date", "delta"]]


def summarize_pairs(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(
            columns=["symbol", "rule", "trades", "wins", "losses", "win_rate", "points", "weighted_points", "profit_factor", "max_drawdown", "disable", "disable_reason"]
        )

    rows: list[dict] = []
    for (symbol, rule), group in df.groupby(["symbol", "rule"], sort=False):
        points = group["points"].astype(float)
        weighted = group["weighted_points"].astype(float) if "weighted_points" in group.columns else points
        equity = points.cumsum()
        wins = int((points > 0).sum())
        losses = int((points <= 0).sum())
        disable = len(group) >= 5 and wins < losses and float(points.sum()) <= 0.0 and float(weighted.sum()) <= 0.0
        rows.append(
            {
                "symbol": symbol,
                "rule": rule,
                "trades": int(len(group)),
                "wins": wins,
                "losses": losses,
                "win_rate": float((points > 0).mean()),
                "points": float(points.sum()),
                "weighted_points": float(weighted.sum()),
                "profit_factor": profit_factor(points),
                "max_drawdown": max_drawdown(equity),
                "disable": disable,
                "disable_reason": "losses_gt_wins_and_negative_points" if disable else "",
            }
        )

    return pd.DataFrame(rows).sort_values(
        ["disable", "points", "trades", "symbol", "rule"], ascending=[False, True, False, True, True]
    ).reset_index(drop=True)


def prepare_variant_ledger(ledger: pd.DataFrame, variant: VariantConfig, min_trades: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    symbols = {s.upper() for s in variant.symbols}
    window = ledger[
        (ledger["symbol"].str.upper().isin(symbols))
        & (ledger["entry_date"].dt.normalize() >= variant.train_start)
        & (ledger["entry_date"].dt.normalize() <= variant.cutoff_end)
    ].copy()

    pair_df = summarize_pairs(window)
    pair_df["keep"] = ~pair_df["disable"]
    pair_df.loc[pair_df["trades"] < min_trades, "keep"] = True
    pair_df.loc[pair_df["trades"] < min_trades, "disable"] = False
    pair_df.loc[pair_df["trades"] < min_trades, "disable_reason"] = ""

    keep_pairs = {(row.symbol, row.rule) for row in pair_df.itertuples(index=False) if bool(row.keep)}
    filtered = ledger[
        ledger["symbol"].str.upper().isin(symbols)
        & ledger.apply(lambda r: (str(r["symbol"]).upper(), str(r["rule"])) in keep_pairs, axis=1)
    ].copy()
    return filtered, pair_df


def run_variant(ledger: pd.DataFrame, variant: VariantConfig, min_trades: int) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    filtered_ledger, pair_df = prepare_variant_ledger(ledger, variant, min_trades)
    trades = build_trades(filtered_ledger, variant.symbols, variant.train_start.strftime("%Y-%m-%d"))
    trades_df = simulate_portfolio(trades, START_CAPITAL, RISK_PCT, compound=False)
    summary_df = summarize(trades_df, START_CAPITAL)
    split_df = split_summary(trades_df, START_CAPITAL, variant.forward_start)

    executed = trades_df[trades_df["executed"]].copy()
    forward_exec = executed[executed["exit_date"] >= variant.forward_start].copy()
    span_df = pd.DataFrame(
        [
            {
                "variant": variant.name,
                "cutoff": variant.cutoff,
                "top_n": variant.top_n,
                "train_start": variant.train_start,
                "cutoff_end": variant.cutoff_end,
                "forward_start": variant.forward_start,
                "forward_end": variant.forward_end,
                "symbols": ",".join(variant.symbols),
                "selected_pairs": int((~pair_df["disable"]).sum()),
                "veto_pairs": int(pair_df["disable"].sum()),
                "train_trades": int(len(executed)),
                "forward_trades": int(len(forward_exec)),
                "train_points": float(executed["pnl_dollars"].sum()),
                "forward_points": float(forward_exec["pnl_dollars"].sum()),
            }
        ]
    )
    return trades_df, summary_df, split_df, pair_df, span_df


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ledger", default=str(LEDGER_PATH))
    parser.add_argument("--rank-path", default=str(RANK_PATH))
    parser.add_argument("--top-n", type=int, default=50)
    parser.add_argument("--min-trades", type=int, default=5)
    parser.add_argument(
        "--output-prefix",
        default=None,
        help="Output filename prefix. Defaults to cutoff_forward_top{top_n}_variants.",
    )
    parser.add_argument(
        "--cutoffs",
        nargs="*",
        default=["2025-07", "2025-11", "2026-01", "2026-03", "2026-04", "2026-05", "2026-06"],
    )
    args = parser.parse_args()

    ledger = load_ledger(Path(args.ledger))
    variants = [
        VariantConfig(
            name=f"cutoff_{cutoff}_top{args.top_n}",
            cutoff=cutoff,
            top_n=args.top_n,
            symbols=load_cutoff_topn(Path(args.rank_path), cutoff, args.top_n),
        )
        for cutoff in args.cutoffs
    ]

    all_trades = []
    all_summary = []
    all_split = []
    all_pairs = []
    all_span = []
    all_equity = []

    for variant in variants:
        trades_df, summary_df, split_df, pair_df, span_df = run_variant(ledger, variant, args.min_trades)
        all_trades.append(trades_df.assign(variant=variant.name))
        all_summary.append(summary_df.assign(variant=variant.name))
        all_split.append(split_df.assign(variant=variant.name))
        all_pairs.append(pair_df.assign(variant=variant.name))
        all_span.append(span_df.assign(variant=variant.name))

        executed = trades_df[trades_df["executed"]].copy()
        if not executed.empty:
            curve = executed[["exit_date", "equity_after"]].copy()
            curve["variant"] = variant.name
            curve["normalized_equity"] = curve["equity_after"] / START_CAPITAL
            all_equity.append(curve)

    trades_all = pd.concat(all_trades, ignore_index=True) if all_trades else pd.DataFrame()
    summary_all = pd.concat(all_summary, ignore_index=True) if all_summary else pd.DataFrame()
    split_all = pd.concat(all_split, ignore_index=True) if all_split else pd.DataFrame()
    pairs_all = pd.concat(all_pairs, ignore_index=True) if all_pairs else pd.DataFrame()
    span_all = pd.concat(all_span, ignore_index=True) if all_span else pd.DataFrame()
    equity_all = pd.concat(all_equity, ignore_index=True) if all_equity else pd.DataFrame()

    output_prefix = args.output_prefix or f"cutoff_forward_top{args.top_n}_variants"
    trades_path = OUT_DIR / f"{output_prefix}_trades.csv"
    summary_path = OUT_DIR / f"{output_prefix}_summary.csv"
    split_path = OUT_DIR / f"{output_prefix}_split_summary.csv"
    pairs_path = OUT_DIR / f"{output_prefix}_symbol_rule_selection.csv"
    span_path = OUT_DIR / f"{output_prefix}_span.csv"
    equity_path = OUT_DIR / f"{output_prefix}_equity.csv"
    plot_path = OUT_DIR / f"{output_prefix}_equity.png"
    forward_plot_path = OUT_DIR / f"{output_prefix}_forward_only_equity.png"
    cutoff_plot_path = OUT_DIR / f"{output_prefix}_cutoff_aligned_equity.png"
    zero_plot_path = OUT_DIR / f"{output_prefix}_cutoff_zero_equity.png"
    absolute_zero_plot_path = OUT_DIR / f"{output_prefix}_absolute_zero_start_equity.png"
    spx_curve = load_spx_delta_curve(pd.Timestamp("2024-01-01"), pd.Timestamp("2026-06-18"))

    trades_all.to_csv(trades_path, index=False)
    summary_all.to_csv(summary_path, index=False)
    split_all.to_csv(split_path, index=False)
    pairs_all.to_csv(pairs_path, index=False)
    span_all.to_csv(span_path, index=False)
    equity_all.to_csv(equity_path, index=False)

    if not equity_all.empty:
        plt.figure(figsize=(15, 7))
        ax_eq = plt.gca()
        palette = [
            "#1D4ED8",
            "#059669",
            "#DC2626",
            "#7C3AED",
            "#D97706",
            "#0F766E",
            "#111827",
        ]
        for idx, variant in enumerate(variants):
            curve = equity_all[equity_all["variant"] == variant.name].copy()
            if curve.empty:
                continue
            curve = curve.sort_values("exit_date")
            ax_eq.plot(curve["exit_date"], curve["normalized_equity"], linewidth=2.0, color=palette[idx % len(palette)], label=variant.name)
            ax_eq.axvline(variant.forward_start, color=palette[idx % len(palette)], linestyle="--", linewidth=1.0, alpha=0.5)
        ax_eq.axhline(1.0, color="#9CA3AF", linestyle="--", linewidth=1.0)
        ax_eq.set_title(f"Fixed basket walk-forward variants, top {args.top_n}")
        ax_eq.set_xlabel("Exit date")
        ax_eq.set_ylabel("Equity / 10,000")
        ax_eq.grid(True, alpha=0.25)
        ax_eq.legend()
        plt.tight_layout()
        plt.savefig(plot_path, dpi=180)
        plt.close()

        if not span_all.empty:
            span_lookup = span_all[["variant", "forward_start"]].drop_duplicates()
            forward_all = equity_all.merge(span_lookup, on="variant", how="left")
            forward_all["forward_start"] = pd.to_datetime(forward_all["forward_start"])
            plt.figure(figsize=(15, 7))
            ax_eq = plt.gca()
            ax_spx = ax_eq.twinx()
            for idx, variant in enumerate(variants):
                curve = forward_all[forward_all["variant"] == variant.name].copy()
                curve = curve[curve["exit_date"] >= variant.forward_start]
                if curve.empty:
                    continue
                curve = curve.sort_values("exit_date").reset_index(drop=True)
                base = float(curve["equity_after"].iloc[0])
                curve["forward_normalized_equity"] = curve["equity_after"] / base
                ax_eq.plot(
                    curve["exit_date"],
                    curve["forward_normalized_equity"],
                    linewidth=2.0,
                    color=palette[idx % len(palette)],
                    label=variant.name,
                )
                ax_eq.axvline(variant.forward_start, color=palette[idx % len(palette)], linestyle="--", linewidth=1.0, alpha=0.5)
            if not spx_curve.empty:
                ax_spx.plot(spx_curve["date"], spx_curve["delta"], color="black", linewidth=0.85, alpha=0.65, label="SPX delta")
                ax_spx.set_ylabel("SPX delta vs 2024-01-01")
            ax_eq.axhline(1.0, color="#9CA3AF", linestyle="--", linewidth=1.0)
            ax_eq.set_title(f"Fixed basket walk-forward variants, top {args.top_n}, forward-only normalized")
            ax_eq.set_xlabel("Exit date")
            ax_eq.set_ylabel("Forward equity / first forward trade")
            ax_eq.grid(True, alpha=0.25)
            eq_handles, eq_labels = ax_eq.get_legend_handles_labels()
            spx_handles, spx_labels = ax_spx.get_legend_handles_labels()
            ax_eq.legend(eq_handles + spx_handles, eq_labels + spx_labels, loc="upper left")
            plt.tight_layout()
            plt.savefig(forward_plot_path, dpi=180)
            plt.close()

            cutoff_lookup = span_all[["variant", "cutoff_end"]].drop_duplicates()
            cutoff_all = equity_all.merge(cutoff_lookup, on="variant", how="left")
            cutoff_all["cutoff_end"] = pd.to_datetime(cutoff_all["cutoff_end"])
            plt.figure(figsize=(15, 7))
            ax_eq = plt.gca()
            ax_spx = ax_eq.twinx()
            cutoff_zero_frames = []
            for idx, variant in enumerate(variants):
                curve = cutoff_all[cutoff_all["variant"] == variant.name].copy()
                if curve.empty:
                    continue
                curve = curve.sort_values("exit_date").reset_index(drop=True)
                cutoff_end = variant.cutoff_end
                pre_mask = curve["exit_date"] <= cutoff_end
                if not pre_mask.any():
                    continue
                base = float(curve.loc[pre_mask, "equity_after"].iloc[-1])
                if base == 0.0:
                    continue
                curve["cutoff_normalized_equity"] = curve["equity_after"] / base
                cutoff_zero = curve[["exit_date", "variant", "cutoff_normalized_equity"]].copy()
                cutoff_zero["cutoff_zero_equity"] = cutoff_zero["cutoff_normalized_equity"] - 1.0
                cutoff_zero_frames.append(cutoff_zero)
                anchor = pd.DataFrame(
                    {
                        "exit_date": [cutoff_end],
                        "equity_after": [base],
                        "variant": [variant.name],
                        "normalized_equity": [1.0],
                        "cutoff_normalized_equity": [1.0],
                    }
                )
                curve = pd.concat([curve, anchor], ignore_index=True)
                curve = curve.sort_values("exit_date").reset_index(drop=True)
                ax_eq.plot(
                    curve["exit_date"],
                    curve["cutoff_normalized_equity"],
                    linewidth=2.0,
                    color=palette[idx % len(palette)],
                    label=variant.name,
                )
                ax_eq.axvline(cutoff_end, color=palette[idx % len(palette)], linestyle="--", linewidth=1.0, alpha=0.5)
            if not spx_curve.empty:
                ax_spx.plot(spx_curve["date"], spx_curve["delta"], color="black", linewidth=0.85, alpha=0.65, label="SPX delta")
                ax_spx.set_ylabel("SPX delta vs 2024-01-01")
            ax_eq.axhline(1.0, color="#9CA3AF", linestyle="--", linewidth=1.0)
            ax_eq.set_title(f"Fixed basket walk-forward variants, top {args.top_n}, cutoff-aligned")
            ax_eq.set_xlabel("Exit date")
            ax_eq.set_ylabel("Equity / equity at own cutoff")
            ax_eq.grid(True, alpha=0.25)
            eq_handles, eq_labels = ax_eq.get_legend_handles_labels()
            spx_handles, spx_labels = ax_spx.get_legend_handles_labels()
            ax_eq.legend(eq_handles + spx_handles, eq_labels + spx_labels, loc="upper left")
            plt.tight_layout()
            plt.savefig(cutoff_plot_path, dpi=180)
            plt.close()

            if cutoff_zero_frames:
                zero_all = pd.concat(cutoff_zero_frames, ignore_index=True)
                plt.figure(figsize=(15, 7))
                ax_eq = plt.gca()
                ax_spx = ax_eq.twinx()
                for idx, variant in enumerate(variants):
                    curve = zero_all[zero_all["variant"] == variant.name].copy()
                    if curve.empty:
                        continue
                    curve = curve.sort_values("exit_date")
                    ax_eq.plot(
                        curve["exit_date"],
                        curve["cutoff_zero_equity"],
                        linewidth=2.0,
                        color=palette[idx % len(palette)],
                        label=variant.name,
                    )
                    ax_eq.axvline(variant.cutoff_end, color=palette[idx % len(palette)], linestyle="--", linewidth=1.0, alpha=0.5)
                if not spx_curve.empty:
                    ax_spx.plot(spx_curve["date"], spx_curve["delta"], color="black", linewidth=0.85, alpha=0.65, label="SPX delta")
                    ax_spx.set_ylabel("SPX delta vs 2024-01-01")
                ax_eq.axhline(0.0, color="#9CA3AF", linestyle="--", linewidth=1.0)
                ax_eq.set_title(f"Fixed basket walk-forward variants, top {args.top_n}, cutoff-aligned zero start")
                ax_eq.set_xlabel("Exit date")
                ax_eq.set_ylabel("Equity delta vs own cutoff")
                ax_eq.grid(True, alpha=0.25)
                eq_handles, eq_labels = ax_eq.get_legend_handles_labels()
                spx_handles, spx_labels = ax_spx.get_legend_handles_labels()
                ax_eq.legend(eq_handles + spx_handles, eq_labels + spx_labels, loc="upper left")
                plt.tight_layout()
                plt.savefig(zero_plot_path, dpi=180)
                plt.close()

            plt.figure(figsize=(15, 7))
            ax_eq = plt.gca()
            ax_spx = ax_eq.twinx()
            for idx, variant in enumerate(variants):
                curve = equity_all[equity_all["variant"] == variant.name].copy()
                if curve.empty:
                    continue
                curve = curve.sort_values("exit_date").reset_index(drop=True)
                curve["absolute_zero_equity"] = curve["normalized_equity"] - 1.0
                ax_eq.plot(
                    curve["exit_date"],
                    curve["absolute_zero_equity"],
                    linewidth=2.0,
                    color=palette[idx % len(palette)],
                    label=variant.name,
                )
                ax_eq.axvline(variant.cutoff_end, color=palette[idx % len(palette)], linestyle="--", linewidth=1.0, alpha=0.5)
            if not spx_curve.empty:
                ax_spx.plot(spx_curve["date"], spx_curve["delta"], color="black", linewidth=0.85, alpha=0.65, label="SPX delta")
                ax_spx.set_ylabel("SPX delta vs 2024-01-01")
            ax_eq.axhline(0.0, color="#9CA3AF", linestyle="--", linewidth=1.0)
            ax_eq.set_title(f"Fixed basket walk-forward variants, top {args.top_n}, absolute zero start")
            ax_eq.set_xlabel("Exit date")
            ax_eq.set_ylabel("Equity delta vs 2024-01-01")
            ax_eq.grid(True, alpha=0.25)
            eq_max = float(equity_all["normalized_equity"].max() - 1.0) if not equity_all.empty else 1.0
            ax_eq.set_ylim(min(-0.05, 0.0), max(0.05, eq_max * 1.05))
            eq_handles, eq_labels = ax_eq.get_legend_handles_labels()
            spx_handles, spx_labels = ax_spx.get_legend_handles_labels()
            ax_eq.legend(eq_handles + spx_handles, eq_labels + spx_labels, loc="upper left")
            plt.tight_layout()
            plt.savefig(absolute_zero_plot_path, dpi=180)
            plt.close()

    print(f"Wrote {trades_path}")
    print(f"Wrote {summary_path}")
    print(f"Wrote {split_path}")
    print(f"Wrote {pairs_path}")
    print(f"Wrote {span_path}")
    print(f"Wrote {equity_path}")
    print(f"Wrote {plot_path}")
    print(f"Wrote {forward_plot_path}")
    print(f"Wrote {cutoff_plot_path}")
    print(f"Wrote {zero_plot_path}")
    print(f"Wrote {absolute_zero_plot_path}")
    if not summary_all.empty:
        print(summary_all.to_string(index=False))


if __name__ == "__main__":
    main()
