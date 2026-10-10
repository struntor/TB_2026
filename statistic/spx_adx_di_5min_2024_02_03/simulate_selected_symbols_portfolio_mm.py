from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd


OUT_DIR = Path(__file__).resolve().parent
LEDGER_PATH = OUT_DIR / "trade_ledger_baseline_best_modes.csv"


@dataclass(frozen=True)
class TradeSpec:
    symbol: str
    learn_mode: str
    entry_date: pd.Timestamp
    exit_date: pd.Timestamp
    side: str
    rule: str
    entry: float
    stop: float
    target: float
    points: float
    weighted_points: float
    regime_at_entry: str
    big_picture_regime: str
    big_picture_state: str
    signal_regime_at_entry: str
    labels: str
    entry_labels: str
    exit_reason: str


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


def load_ledger(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(path)
    df = pd.read_csv(path, parse_dates=["entry_date", "exit_date"])
    if df.empty:
        raise ValueError(f"Empty ledger: {path}")
    for col in ("symbol", "learn_mode", "track", "rule", "side"):
        if col in df.columns:
            df[col] = df[col].astype(str)
    return df


def build_trades(df: pd.DataFrame, symbols: list[str], start_date: str) -> list[TradeSpec]:
    filtered = df.copy()
    filtered["symbol"] = filtered["symbol"].astype(str).str.upper()
    filtered = filtered[filtered["track"] == "baseline"].copy()
    filtered = filtered[filtered["symbol"].isin(symbols)].copy()
    filtered = filtered[filtered["entry_date"].dt.date >= pd.Timestamp(start_date).date()].copy()
    filtered = filtered.sort_values(["entry_date", "exit_date", "symbol", "rule"])

    trades: list[TradeSpec] = []
    for _, row in filtered.iterrows():
        trades.append(
            TradeSpec(
                symbol=str(row["symbol"]),
                learn_mode=str(row.get("learn_mode", "")),
                entry_date=pd.Timestamp(row["entry_date"]),
                exit_date=pd.Timestamp(row["exit_date"]),
                side=str(row["side"]),
                rule=str(row["rule"]),
                entry=float(row["entry"]),
                stop=float(row["stop"]),
                target=float(row["target"]),
                points=float(row["points"]),
                weighted_points=float(row["weighted_points"]),
                regime_at_entry=str(row.get("regime_at_entry", "")),
                big_picture_regime=str(row.get("big_picture_regime", "")),
                big_picture_state=str(row.get("big_picture_state", "")),
                signal_regime_at_entry=str(row.get("signal_regime_at_entry", "")),
                labels=str(row.get("labels", "")),
                entry_labels=str(row.get("entry_labels", "")),
                exit_reason=str(row.get("exit_reason", "")),
            )
        )
    return trades


def simulate_portfolio(trades: list[TradeSpec], start_capital: float, risk_pct: float, compound: bool = False) -> pd.DataFrame:
    events: list[tuple[pd.Timestamp, int, str, int, TradeSpec]] = []
    for idx, trade in enumerate(trades):
        events.append((trade.entry_date, 0, "entry", idx, trade))
        events.append((trade.exit_date, 1, "exit", idx, trade))
    events.sort(key=lambda x: (x[0], x[1], x[4].symbol, x[4].rule))

    equity = float(start_capital)
    active: dict[int, dict] = {}
    rows: list[dict] = []

    for event_time, _, kind, idx, trade in events:
        if kind == "entry":
            sizing_capital = equity if compound else float(start_capital)
            risk_amount = sizing_capital * risk_pct
            stop_distance = abs(trade.entry - trade.stop)
            if stop_distance <= 0:
                shares = 0
            else:
                shares_by_risk = int(risk_amount // stop_distance)
                shares_by_capital = int(sizing_capital // trade.entry) if trade.entry > 0 else 0
                shares = max(0, min(shares_by_risk, shares_by_capital))
            active[idx] = {
                "entry_equity": equity,
                "risk_amount": risk_amount,
                "stop_distance": stop_distance,
                "shares": shares,
                "notional": shares * trade.entry,
                "executed": shares > 0,
            }
            continue

        state = active.get(idx)
        if not state or not state["executed"]:
            rows.append(
                {
                    "symbol": trade.symbol,
                    "learn_mode": trade.learn_mode,
                    "entry_date": trade.entry_date,
                    "exit_date": trade.exit_date,
                    "rule": trade.rule,
                    "side": trade.side,
                    "entry": trade.entry,
                    "stop": trade.stop,
                    "target": trade.target,
                    "points": trade.points,
                    "weighted_points": trade.weighted_points,
                    "risk_amount": state["risk_amount"] if state else 0.0,
                    "stop_distance": state["stop_distance"] if state else abs(trade.entry - trade.stop),
                    "shares": 0,
                    "position_notional": 0.0,
                    "pnl_dollars": 0.0,
                    "equity_before": state["entry_equity"] if state else equity,
                    "equity_after": state["entry_equity"] if state else equity,
                    "executed": False,
                    "exit_reason": trade.exit_reason,
                    "regime_at_entry": trade.regime_at_entry,
                    "big_picture_regime": trade.big_picture_regime,
                    "big_picture_state": trade.big_picture_state,
                    "signal_regime_at_entry": trade.signal_regime_at_entry,
                    "labels": trade.labels,
                    "entry_labels": trade.entry_labels,
                }
            )
            continue

        shares = int(state["shares"])
        pnl = float(trade.points * shares)
        equity_before = equity
        equity += pnl
        rows.append(
            {
                "symbol": trade.symbol,
                "learn_mode": trade.learn_mode,
                "entry_date": trade.entry_date,
                "exit_date": trade.exit_date,
                "rule": trade.rule,
                "side": trade.side,
                "entry": trade.entry,
                "stop": trade.stop,
                "target": trade.target,
                "points": trade.points,
                "weighted_points": trade.weighted_points,
                "risk_amount": state["risk_amount"],
                "stop_distance": state["stop_distance"],
                "shares": shares,
                "position_notional": state["notional"],
                "pnl_dollars": pnl,
                "equity_before": equity_before,
                "equity_after": equity,
                "executed": True,
                "exit_reason": trade.exit_reason,
                "regime_at_entry": trade.regime_at_entry,
                "big_picture_regime": trade.big_picture_regime,
                "big_picture_state": trade.big_picture_state,
                "signal_regime_at_entry": trade.signal_regime_at_entry,
                "labels": trade.labels,
                "entry_labels": trade.entry_labels,
            }
        )

    trades_df = pd.DataFrame(rows).sort_values(["exit_date", "entry_date", "symbol", "rule"]).reset_index(drop=True)
    if trades_df.empty:
        return trades_df
    trades_df["equity_curve"] = trades_df["equity_after"]
    return trades_df


def summarize(trades_df: pd.DataFrame, start_capital: float) -> pd.DataFrame:
    executed = trades_df[trades_df["executed"]].copy()
    if executed.empty:
        return pd.DataFrame(
            [
                {
                    "start_capital": start_capital,
                    "end_capital": start_capital,
                    "net_profit": 0.0,
                    "trades": 0,
                    "wins": 0,
                    "losses": 0,
                    "win_rate": 0.0,
                    "profit_factor": 0.0,
                    "max_drawdown": 0.0,
                    "avg_pnl_dollars": 0.0,
                    "avg_risk_dollars": 0.0,
                    "avg_shares": 0.0,
                    "max_position_notional": 0.0,
                    "skipped_trades": int((~trades_df["executed"]).sum()),
                }
            ]
        )
    return pd.DataFrame(
        [
            {
                "start_capital": start_capital,
                "end_capital": float(executed["equity_after"].iloc[-1]),
                "net_profit": float(executed["equity_after"].iloc[-1] - start_capital),
                "trades": int(len(executed)),
                "wins": int((executed["pnl_dollars"] > 0).sum()),
                "losses": int((executed["pnl_dollars"] <= 0).sum()),
                "win_rate": float((executed["pnl_dollars"] > 0).mean()),
                "profit_factor": profit_factor(executed["pnl_dollars"]),
                "max_drawdown": max_drawdown(executed["equity_after"]),
                "avg_pnl_dollars": float(executed["pnl_dollars"].mean()),
                "avg_risk_dollars": float(executed["risk_amount"].mean()),
                "avg_shares": float(executed["shares"].mean()),
                "max_position_notional": float(executed["position_notional"].max()),
                "skipped_trades": int((~trades_df["executed"]).sum()),
            }
        ]
    )


def split_summary(trades_df: pd.DataFrame, start_capital: float, split_date: pd.Timestamp) -> pd.DataFrame:
    executed = trades_df[trades_df["executed"]].copy()
    pre = executed[executed["exit_date"] < split_date].copy()
    post = executed[executed["exit_date"] >= split_date].copy()

    def _part_summary(part: pd.DataFrame, label: str, base_equity: float) -> dict:
        if part.empty:
            return {
                "segment": label,
                "start_capital": base_equity,
                "end_capital": base_equity,
                "net_profit": 0.0,
                "trades": 0,
                "wins": 0,
                "losses": 0,
                "win_rate": 0.0,
                "profit_factor": 0.0,
                "max_drawdown": 0.0,
            }
        equity = base_equity + part["pnl_dollars"].cumsum()
        return {
            "segment": label,
            "start_capital": base_equity,
            "end_capital": float(equity.iloc[-1]),
            "net_profit": float(equity.iloc[-1] - base_equity),
            "trades": int(len(part)),
            "wins": int((part["pnl_dollars"] > 0).sum()),
            "losses": int((part["pnl_dollars"] <= 0).sum()),
            "win_rate": float((part["pnl_dollars"] > 0).mean()),
            "profit_factor": profit_factor(part["pnl_dollars"]),
            "max_drawdown": max_drawdown(equity),
        }

    pre_summary = _part_summary(pre, "pre_split", start_capital)
    post_summary = _part_summary(post, "post_split", float(pre_summary["end_capital"]))
    return pd.DataFrame([pre_summary, post_summary])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--symbols",
        default="CME,EXPE,ORLY,VNQI,INTU,MA,RHM,EW,IDXX,MPC",
        help="Comma-separated symbols to keep fixed in the portfolio.",
    )
    parser.add_argument("--start", default="2024-01-01")
    parser.add_argument("--end", default="2026-06-18")
    parser.add_argument("--split-date", default="2025-08-01")
    parser.add_argument("--start-capital", type=float, default=10000.0)
    parser.add_argument("--risk-pct", type=float, default=0.01)
    parser.add_argument("--compound", action="store_true", help="Use current equity for position sizing instead of fixed start capital.")
    parser.add_argument("--ledger", default=str(LEDGER_PATH))
    parser.add_argument("--tag", default="selected_2025_07_then_2026")
    args = parser.parse_args()

    symbols = [sym.strip().upper() for sym in args.symbols.split(",") if sym.strip()]
    ledger = load_ledger(Path(args.ledger))
    trades = build_trades(ledger, symbols, args.start)
    trades_df = simulate_portfolio(trades, args.start_capital, args.risk_pct, compound=args.compound)
    summary_df = summarize(trades_df, args.start_capital)
    split_df = split_summary(trades_df, args.start_capital, pd.Timestamp(args.split_date))

    symbol_tag = "_".join(symbols)
    out_trades = OUT_DIR / f"{args.tag}_portfolio_trades.csv"
    out_summary = OUT_DIR / f"{args.tag}_portfolio_summary.csv"
    out_split = OUT_DIR / f"{args.tag}_portfolio_split_summary.csv"
    out_plot = OUT_DIR / f"{args.tag}_portfolio_equity.png"

    trades_df.to_csv(out_trades, index=False)
    summary_df.to_csv(out_summary, index=False)
    split_df.to_csv(out_split, index=False)

    print(f"Symbols: {', '.join(symbols)}")
    print(summary_df.to_string(index=False))
    print(split_df.to_string(index=False))
    print(f"Wrote {out_trades}")
    print(f"Wrote {out_summary}")
    print(f"Wrote {out_split}")

    executed = trades_df[trades_df["executed"]].copy()
    if not executed.empty:
        fig, ax = plt.subplots(figsize=(16, 7))
        ax.plot(pd.to_datetime(executed["exit_date"]), executed["equity_after"], lw=1.8, color="#1f77b4")
        ax.axhline(args.start_capital, color="gray", ls="--", lw=1)
        ax.axvline(pd.Timestamp(args.split_date), color="red", ls="--", lw=1.1, alpha=0.8)
        ax.text(
            pd.Timestamp(args.split_date),
            float(executed["equity_after"].max()),
            "  2025-08",
            color="red",
            va="top",
            ha="left",
            fontsize=9,
        )
        ax.set_title("Fixed 19M-selected Assets Extended Through 2026")
        ax.set_ylabel("Equity ($)")
        ax.set_xlabel("Exit date")
        ax.grid(True, alpha=0.25)
        ax.margins(x=0.01)
        fig.tight_layout()
        fig.savefig(out_plot, dpi=180)
        plt.close(fig)
        print(f"Wrote {out_plot}")
        print(f"Selection tag: {symbol_tag}")


if __name__ == "__main__":
    main()
