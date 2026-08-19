from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
SOURCE_DIR = ROOT / "PythonProject" / "statistic" / "spx_adx_di_5min_2024_02_03"
OUT_DIR = ROOT / "IBKR_TradingBot" / "out"

TRADES_PATH = SOURCE_DIR / "current_baseline_optimized_cutoff_2025_11_top50_vs_old_trades.csv"

OUT_PNG = OUT_DIR / "old_vs_optimized_top50_equity_2024_june_2026.png"
OUT_CURVE = OUT_DIR / "old_vs_optimized_top50_equity_2024_june_2026.csv"
OUT_SUMMARY = OUT_DIR / "old_vs_optimized_top50_equity_2024_june_2026_summary.csv"

START = pd.Timestamp("2024-01-01")
END_ENTRY_EXCLUSIVE = pd.Timestamp("2026-07-01")
START_CAPITAL = 10_000.0

LABELS = {
    "old_cutoff_2025_11_top50_current_baseline": "Old rank Top50",
    "optimized_cutoff_2025_11_top50_current_baseline": "Optimized live Top50",
}


def profit_factor(pnl: pd.Series) -> float:
    gains = float(pnl[pnl > 0].sum())
    losses = float(-pnl[pnl < 0].sum())
    if losses == 0.0:
        return float("inf") if gains > 0.0 else 0.0
    return gains / losses


def max_drawdown(equity: pd.Series) -> float:
    if equity.empty:
        return 0.0
    return float((equity - equity.cummax()).min())


def build_curves(trades: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    curve_parts: list[pd.DataFrame] = []
    summary_rows: list[dict[str, object]] = []

    for variant, group in trades.groupby("variant", sort=False):
        group = group.copy()
        group = group[group["executed"].astype(bool)].copy()
        group = group[(group["entry_date"] >= START) & (group["entry_date"] < END_ENTRY_EXCLUSIVE)].copy()
        group = group.sort_values(["exit_date", "entry_date", "symbol", "rule"]).reset_index(drop=True)
        group["pnl_dollars"] = pd.to_numeric(group["pnl_dollars"], errors="coerce").fillna(0.0)
        group["equity"] = START_CAPITAL + group["pnl_dollars"].cumsum()
        group["drawdown"] = group["equity"] - group["equity"].cummax()

        label = LABELS.get(str(variant), str(variant))
        curve = group[["exit_date", "equity", "drawdown"]].copy()
        curve.insert(0, "variant", variant)
        curve.insert(1, "label", label)
        curve.insert(2, "point", range(1, len(curve) + 1))
        curve_parts.append(curve)

        pnl = group["pnl_dollars"]
        summary_rows.append(
            {
                "variant": variant,
                "label": label,
                "start_capital": START_CAPITAL,
                "end_equity": float(group["equity"].iloc[-1]) if not group.empty else START_CAPITAL,
                "net_pnl_dollars": float(pnl.sum()),
                "trades": int(len(group)),
                "wins": int((pnl > 0).sum()),
                "losses": int((pnl <= 0).sum()),
                "win_rate": float((pnl > 0).mean()) if len(pnl) else 0.0,
                "profit_factor": profit_factor(pnl),
                "max_drawdown_dollars": max_drawdown(group["equity"]) if not group.empty else 0.0,
            }
        )

    curves = pd.concat(curve_parts, ignore_index=True) if curve_parts else pd.DataFrame()
    summary = pd.DataFrame(summary_rows)
    return curves, summary


def write_plot(curves: pd.DataFrame, summary: pd.DataFrame) -> None:
    fig, ax = plt.subplots(figsize=(15, 8))
    colors = {
        "Old rank Top50": "#1f77b4",
        "Optimized live Top50": "#ff7f0e",
    }
    for label, group in curves.groupby("label", sort=False):
        ax.plot(group["exit_date"], group["equity"], linewidth=2.0, label=label, color=colors.get(label))

    ax.axhline(START_CAPITAL, color="#6b7280", linestyle="--", linewidth=1.0)
    ax.axvline(pd.Timestamp("2025-12-01"), color="#9ca3af", linestyle=":", linewidth=1.2, label="Forward start 2025-12")
    ax.set_title("Old Rank Top50 vs Optimized Live Top50 Equity Curve")
    ax.set_xlabel("Exit date")
    ax.set_ylabel("Equity ($)")
    ax.grid(True, alpha=0.25)
    ax.legend(loc="best")

    text_lines = []
    for row in summary.sort_values("label").itertuples(index=False):
        text_lines.append(
            f"{row.label}: end ${row.end_equity:,.0f}, "
            f"net ${row.net_pnl_dollars:,.0f}, DD ${row.max_drawdown_dollars:,.0f}, PF {row.profit_factor:.2f}"
        )
    ax.text(
        0.01,
        0.02,
        "\n".join(text_lines),
        transform=ax.transAxes,
        fontsize=9,
        va="bottom",
        ha="left",
        bbox={"facecolor": "white", "alpha": 0.85, "edgecolor": "#d1d5db"},
    )

    fig.tight_layout()
    fig.savefig(OUT_PNG, dpi=170)
    plt.close(fig)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    trades = pd.read_csv(TRADES_PATH, parse_dates=["entry_date", "exit_date"])
    curves, summary = build_curves(trades)
    curves.to_csv(OUT_CURVE, index=False)
    summary.to_csv(OUT_SUMMARY, index=False)
    write_plot(curves, summary)

    print(summary.round(4).to_string(index=False))
    print(f"wrote {OUT_PNG}")
    print(f"wrote {OUT_CURVE}")
    print(f"wrote {OUT_SUMMARY}")


if __name__ == "__main__":
    main()
