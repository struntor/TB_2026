from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd


OUT_DIR = Path(__file__).resolve().parent
START_CAPITAL = 10_000.0
FORWARD_START = pd.Timestamp("2025-12-01")

VETO_NAME = "failed_countermove_trend_normal_veto"
TARGET_RULE = "bearish_failed_countermove_short_baseline"
TARGET_SIDE = "short"
TARGET_PHASE = "trend_or_unclassified"
TARGET_VOLUME_PHASE = "normal_volume_trend"

TOP50_TRADES = OUT_DIR / "top50_current_baseline_trades_enriched.csv"
TOP100_TRADES = OUT_DIR / "cutoff_2025_11_top100_live_history_548d_monitor60m_early_lhf_veto_combo_fixed_pairs_trades.csv"
TOP100_CONTEXT = OUT_DIR / "top100_regime_holding_time_trades_enriched.csv"

OUT_SUMMARY = OUT_DIR / "failed_countermove_trend_normal_veto_top50_top100_summary.csv"
OUT_AUDIT = OUT_DIR / "failed_countermove_trend_normal_veto_top50_top100_audit.csv"


def clean(value: object) -> str:
    if pd.isna(value):
        return ""
    text = str(value)
    return "" if text.lower() == "nan" else text


def profit_factor(pnl: pd.Series) -> float:
    vals = pd.to_numeric(pnl, errors="coerce").fillna(0.0)
    gains = float(vals[vals > 0.0].sum())
    losses = float(-vals[vals <= 0.0].sum())
    if losses == 0.0:
        return 999.0 if gains > 0.0 else 0.0
    return gains / losses


def max_drawdown(equity: pd.Series) -> float:
    if equity.empty:
        return 0.0
    return float((equity - equity.cummax()).min())


def ulcer_index(equity: pd.Series) -> float:
    if equity.empty:
        return 0.0
    drawdown_pct = (equity / equity.cummax() - 1.0).fillna(0.0)
    return float((drawdown_pct.pow(2).mean()) ** 0.5)


def rolling_worst(pnl: pd.Series, window: int) -> float:
    vals = pd.to_numeric(pnl, errors="coerce").fillna(0.0)
    if vals.empty:
        return 0.0
    if len(vals) < window:
        return float(vals.sum())
    return float(vals.rolling(window).sum().min())


def recompute_equity(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if "executed" in out.columns:
        out = out[out["executed"].astype(bool)].copy()
    out["entry_date"] = pd.to_datetime(out["entry_date"])
    out["exit_date"] = pd.to_datetime(out["exit_date"])
    out["pnl_dollars"] = pd.to_numeric(out["pnl_dollars"], errors="coerce").fillna(0.0)
    out = out.sort_values(["exit_date", "entry_date", "symbol", "rule"]).reset_index(drop=True)
    out["equity_before"] = START_CAPITAL + out["pnl_dollars"].cumsum().shift(1, fill_value=0.0)
    out["equity_after"] = START_CAPITAL + out["pnl_dollars"].cumsum()
    out["equity_curve"] = out["equity_after"]
    return out


def summarize(label: str, df: pd.DataFrame, vetoed: pd.DataFrame) -> dict[str, object]:
    executed = recompute_equity(df)
    pnl = executed["pnl_dollars"] if not executed.empty else pd.Series(dtype=float)
    forward = executed[executed["exit_date"] >= FORWARD_START].copy() if not executed.empty else executed
    veto_pnl = pd.to_numeric(vetoed.get("pnl_dollars", pd.Series(dtype=float)), errors="coerce").fillna(0.0)
    veto_forward = vetoed[pd.to_datetime(vetoed.get("exit_date", pd.Series(dtype=str)), errors="coerce") >= FORWARD_START].copy() if not vetoed.empty else vetoed
    return {
        "variant": label,
        "net_profit": float(pnl.sum()),
        "trades": int(len(executed)),
        "wins": int((pnl > 0.0).sum()),
        "losses": int((pnl <= 0.0).sum()),
        "win_rate": float((pnl > 0.0).mean()) if len(pnl) else 0.0,
        "profit_factor": profit_factor(pnl),
        "max_drawdown": max_drawdown(executed["equity_after"]) if not executed.empty else 0.0,
        "ulcer_index": ulcer_index(executed["equity_after"]) if not executed.empty else 0.0,
        "rolling20_worst_pnl": rolling_worst(pnl, 20),
        "rolling50_worst_pnl": rolling_worst(pnl, 50),
        "forward_net_profit": float(forward["pnl_dollars"].sum()) if not forward.empty else 0.0,
        "forward_profit_factor": profit_factor(forward["pnl_dollars"]) if not forward.empty else 0.0,
        "vetoed_trades": int(len(vetoed)),
        "vetoed_winners": int((veto_pnl > 0.0).sum()),
        "vetoed_losers": int((veto_pnl <= 0.0).sum()),
        "vetoed_net_profit": float(veto_pnl.sum()),
        "vetoed_forward_trades": int(len(veto_forward)),
        "vetoed_forward_net_profit": float(pd.to_numeric(veto_forward.get("pnl_dollars", pd.Series(dtype=float)), errors="coerce").fillna(0.0).sum())
        if not veto_forward.empty
        else 0.0,
    }


def add_match_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["_match_symbol"] = out["symbol"].astype(str).str.upper()
    out["_match_entry_date"] = pd.to_datetime(out["entry_date"]).dt.normalize()
    out["_match_rule"] = out["rule"].astype(str)
    out["_match_side"] = out["side"].astype(str)
    for col in ("entry", "stop", "target"):
        out[f"_match_{col}"] = pd.to_numeric(out[col], errors="coerce").round(6)
    keys = [
        "_match_symbol",
        "_match_entry_date",
        "_match_rule",
        "_match_side",
        "_match_entry",
        "_match_stop",
        "_match_target",
    ]
    out["_match_seq"] = out.groupby(keys, dropna=False).cumcount()
    return out


def merge_top100_context(trades: pd.DataFrame, context: pd.DataFrame) -> pd.DataFrame:
    ctx_cols = [
        "_match_symbol",
        "_match_entry_date",
        "_match_rule",
        "_match_side",
        "_match_entry",
        "_match_stop",
        "_match_target",
        "_match_seq",
        "daily_structure_phase_fresh",
        "volume_price_phase",
        "signal_regime_fresh",
        "big_picture_regime_fresh",
        "bearish_countermove_subregime",
        "profile_price_location_fresh",
    ]
    ctx = add_match_columns(context)
    ctx = ctx[[col for col in ctx_cols if col in ctx.columns]].copy()
    work = add_match_columns(trades).merge(
        ctx,
        on=[
            "_match_symbol",
            "_match_entry_date",
            "_match_rule",
            "_match_side",
            "_match_entry",
            "_match_stop",
            "_match_target",
            "_match_seq",
        ],
        how="left",
    )
    return work.drop(columns=[col for col in work.columns if col.startswith("_match_")], errors="ignore")


def veto_mask(df: pd.DataFrame) -> pd.Series:
    return (
        df["rule"].astype(str).eq(TARGET_RULE)
        & df["side"].astype(str).eq(TARGET_SIDE)
        & df["daily_structure_phase_fresh"].map(clean).eq(TARGET_PHASE)
        & df["volume_price_phase"].map(clean).eq(TARGET_VOLUME_PHASE)
    )


def prepare_audit(name: str, vetoed: pd.DataFrame) -> pd.DataFrame:
    cols = [
        "symbol",
        "entry_date",
        "exit_date",
        "rule",
        "side",
        "entry",
        "stop",
        "target",
        "pnl_dollars",
        "exit_reason",
        "daily_structure_phase_fresh",
        "volume_price_phase",
        "signal_regime_fresh",
        "big_picture_regime_fresh",
        "bearish_countermove_subregime",
        "profile_price_location_fresh",
        "mfe_r",
        "mae_r",
        "loss_diagnosis",
    ]
    out = vetoed[[col for col in cols if col in vetoed.columns]].copy()
    out.insert(0, "basket", name)
    out.insert(1, "veto", VETO_NAME)
    return out


def write_plot(name: str, before: pd.DataFrame, after: pd.DataFrame) -> None:
    plt.figure(figsize=(14, 7))
    for label, trades in [("current baseline", before), (f"+ {VETO_NAME}", after)]:
        curve = recompute_equity(trades)
        if curve.empty:
            continue
        plt.plot(curve["exit_date"], curve["equity_after"] / START_CAPITAL, linewidth=2.0, label=label)
    plt.axhline(1.0, color="#9CA3AF", linestyle="--", linewidth=1.0)
    plt.axvline(FORWARD_START, color="#DC2626", linestyle="--", linewidth=1.0, alpha=0.7, label="forward start")
    plt.title(f"{name}: current baseline vs {VETO_NAME}")
    plt.xlabel("Exit date")
    plt.ylabel("Normalized equity")
    plt.grid(True, alpha=0.25)
    plt.legend()
    plt.tight_layout()
    plt.savefig(OUT_DIR / f"{name}_{VETO_NAME}_equity_compare.png", dpi=150)
    plt.close()


def run_basket(name: str, trades: pd.DataFrame) -> tuple[list[dict[str, object]], pd.DataFrame]:
    work = trades.copy()
    work["entry_date"] = pd.to_datetime(work["entry_date"])
    work["exit_date"] = pd.to_datetime(work["exit_date"])
    work["pnl_dollars"] = pd.to_numeric(work["pnl_dollars"], errors="coerce").fillna(0.0)
    mask = veto_mask(work)
    vetoed = work.loc[mask].copy()
    kept = work.loc[~mask].copy()

    rows = [
        summarize(f"{name}_current_baseline", work, pd.DataFrame()),
        summarize(f"{name}_{VETO_NAME}", kept, vetoed),
    ]
    rows[1]["delta_net_profit"] = rows[1]["net_profit"] - rows[0]["net_profit"]
    rows[1]["delta_forward_net_profit"] = rows[1]["forward_net_profit"] - rows[0]["forward_net_profit"]
    rows[1]["delta_max_drawdown"] = rows[1]["max_drawdown"] - rows[0]["max_drawdown"]
    rows[0]["delta_net_profit"] = 0.0
    rows[0]["delta_forward_net_profit"] = 0.0
    rows[0]["delta_max_drawdown"] = 0.0

    write_plot(name, work, kept)
    recompute_equity(work).to_csv(OUT_DIR / f"{name}_current_baseline_recomputed_trades.csv", index=False)
    recompute_equity(kept).to_csv(OUT_DIR / f"{name}_{VETO_NAME}_trades.csv", index=False)
    return rows, prepare_audit(name, vetoed)


def main() -> None:
    top50 = pd.read_csv(TOP50_TRADES, parse_dates=["entry_date", "exit_date"])

    top100_trades = pd.read_csv(TOP100_TRADES, parse_dates=["entry_date", "exit_date"])
    top100_context = pd.read_csv(TOP100_CONTEXT, parse_dates=["entry_date", "exit_date"])
    top100 = merge_top100_context(top100_trades, top100_context)

    rows: list[dict[str, object]] = []
    audits: list[pd.DataFrame] = []
    for name, trades in [("top50", top50), ("top100", top100)]:
        basket_rows, audit = run_basket(name, trades)
        rows.extend(basket_rows)
        audits.append(audit)

    summary = pd.DataFrame(rows)
    audit_df = pd.concat(audits, ignore_index=True) if audits else pd.DataFrame()
    summary.to_csv(OUT_SUMMARY, index=False)
    audit_df.to_csv(OUT_AUDIT, index=False)

    print(summary.round(4).to_string(index=False))
    if not audit_df.empty:
        print("\naudit")
        audit_cols = ["basket", "symbol", "entry_date", "exit_date", "pnl_dollars", "exit_reason"]
        print(audit_df[[col for col in audit_cols if col in audit_df.columns]].to_string(index=False))


if __name__ == "__main__":
    main()
