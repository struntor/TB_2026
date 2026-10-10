from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "IBKR_TradingBot" / "statistic" / "spx_adx_di_5min_2024_02_03"
OUT = ROOT / "IBKR_TradingBot" / "out"

SOURCE_TRADES = BASE / "top50_current_baseline_trades_enriched.csv"
JUNE_TRADES = OUT / "manual_regime_backtest_june_2026_trades.csv"
OUT_TRADES = OUT / "manual_regime_equity_curve_2024_june_2026_trades.csv"
OUT_CURVE = OUT / "manual_regime_equity_curve_2024_june_2026.csv"
OUT_SUMMARY = OUT / "manual_regime_equity_curve_2024_june_2026_summary.csv"
OUT_PNG = OUT / "manual_regime_equity_curve_2024_june_2026.png"

START = pd.Timestamp("2024-01-01")
JUNE_START = pd.Timestamp("2026-06-01")
END_EXCLUSIVE = pd.Timestamp("2026-07-01")
START_CAPITAL = 10_000.0


def max_drawdown(equity: pd.Series) -> float:
    if equity.empty:
        return 0.0
    return float((equity - equity.cummax()).min())


def profit_factor(pnl: pd.Series) -> float:
    gains = float(pnl[pnl > 0].sum())
    losses = float(-pnl[pnl < 0].sum())
    if losses == 0:
        return float("inf") if gains > 0 else 0.0
    return gains / losses


def normalize_base_trades(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["entry_date"] = pd.to_datetime(df["entry_date"])
    df["exit_date"] = pd.to_datetime(df["exit_date"])
    df = df[df["executed"].astype(bool)].copy()
    df = df[(df["entry_date"] >= START) & (df["entry_date"] < END_EXCLUSIVE)].copy()
    df["qty"] = pd.to_numeric(df["shares"], errors="coerce").fillna(0).astype(int)
    df["result"] = df["pnl_dollars"].map(lambda value: "win" if float(value) > 0 else "loss")
    if "exit" not in df.columns:
        entry = pd.to_numeric(df["entry"], errors="coerce")
        points = pd.to_numeric(df["points"], errors="coerce")
        side = df["side"].astype(str).str.lower()
        df["exit"] = entry.where(side.eq("short"), entry + points)
        df.loc[side.eq("short"), "exit"] = entry.loc[side.eq("short")] - points.loc[side.eq("short")]
    keep = [
        "symbol",
        "entry_date",
        "exit_date",
        "rule",
        "side",
        "entry",
        "stop",
        "target",
        "exit",
        "exit_reason",
        "points",
        "qty",
        "pnl_dollars",
        "result",
    ]
    return df[keep].copy()


def load_june_trades() -> pd.DataFrame:
    df = pd.read_csv(JUNE_TRADES, parse_dates=["entry_date", "exit_date"])
    df = df[(df["entry_date"] >= JUNE_START) & (df["entry_date"] < END_EXCLUSIVE)].copy()
    keep = [
        "symbol",
        "entry_date",
        "exit_date",
        "rule",
        "side",
        "entry",
        "stop",
        "target",
        "exit",
        "exit_reason",
        "points",
        "qty",
        "pnl_dollars",
        "result",
    ]
    return df[keep].copy()


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)

    trades = normalize_base_trades(pd.read_csv(SOURCE_TRADES))
    trades = trades.sort_values(["exit_date", "entry_date", "symbol", "rule"]).reset_index(drop=True)

    trades["pnl_dollars"] = pd.to_numeric(trades["pnl_dollars"], errors="coerce").fillna(0.0)
    trades["equity"] = START_CAPITAL + trades["pnl_dollars"].cumsum()
    trades["drawdown"] = trades["equity"] - trades["equity"].cummax()

    curve = trades[["exit_date", "equity", "drawdown"]].copy()
    curve.insert(0, "point", range(1, len(curve) + 1))

    pnl = trades["pnl_dollars"]
    summary = pd.DataFrame(
        [
            {
                "start": START.date().isoformat(),
                "end_entry_exclusive": END_EXCLUSIVE.date().isoformat(),
                "start_capital": START_CAPITAL,
                "end_equity": float(trades["equity"].iloc[-1]) if not trades.empty else START_CAPITAL,
                "net_pnl_dollars": float(pnl.sum()),
                "trades": int(len(trades)),
                "wins": int((pnl > 0).sum()),
                "losses": int((pnl <= 0).sum()),
                "win_rate": float((pnl > 0).mean()) if len(pnl) else 0.0,
                "profit_factor": profit_factor(pnl),
                "max_drawdown_dollars": max_drawdown(trades["equity"]) if not trades.empty else 0.0,
                "june_trades_recomputed": 0,
            }
        ]
    )

    trades.to_csv(OUT_TRADES, index=False)
    curve.to_csv(OUT_CURVE, index=False)
    summary.to_csv(OUT_SUMMARY, index=False)

    fig, ax = plt.subplots(figsize=(14, 7))
    if not curve.empty:
        ax.plot(curve["exit_date"], curve["equity"], linewidth=2.0, color="#1f77b4")
    ax.axhline(START_CAPITAL, color="#6b7280", linestyle="--", linewidth=1.0)
    ax.set_title("Manual-Regime Current Baseline Equity Curve: 2024 through June 2026")
    ax.set_xlabel("Exit date")
    ax.set_ylabel("Equity ($)")
    ax.grid(True, alpha=0.25)
    fig.tight_layout()
    fig.savefig(OUT_PNG, dpi=160)
    plt.close(fig)

    print(summary.to_string(index=False))
    print(f"wrote {OUT_TRADES}")
    print(f"wrote {OUT_CURVE}")
    print(f"wrote {OUT_SUMMARY}")
    print(f"wrote {OUT_PNG}")


if __name__ == "__main__":
    main()
