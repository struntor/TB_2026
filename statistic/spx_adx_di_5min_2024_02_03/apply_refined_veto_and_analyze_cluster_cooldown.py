from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

import test_failed_countermove_trend_normal_veto_top50_top100 as base_tools


OUT_DIR = Path(__file__).resolve().parent
START_CAPITAL = 10_000.0
FORWARD_START = pd.Timestamp("2025-12-01")

TOP50_TRADES = OUT_DIR / "top50_current_baseline_trades_enriched.csv"
TOP100_TRADES = OUT_DIR / "cutoff_2025_11_top100_live_history_548d_monitor60m_early_lhf_veto_combo_fixed_pairs_trades.csv"
TOP100_CONTEXT = OUT_DIR / "top100_regime_holding_time_trades_enriched.csv"

REFINED_VETO_NAME = "failed_countermove_weak_retest_veto"
CORE_SHORT_RULES = (
    "bearish_gap_breakdown_continuation_short",
    "bearish_lower_high_failure_short",
    "bearish_failed_countermove_short_baseline",
)

OUT_BASELINE_SUMMARY = OUT_DIR / "refined_failed_countermove_veto_baseline_summary.csv"
OUT_CLUSTER_SWEEP = OUT_DIR / "cluster_cooldown_filter_sweep_summary.csv"
OUT_CLUSTER_AUDIT = OUT_DIR / "cluster_cooldown_filter_audit.csv"


@dataclass(frozen=True)
class ClusterConfig:
    name: str
    lookback_days: int
    min_losses: int
    max_net_pnl: float
    target_rules: tuple[str, ...]


def profit_factor(pnl: pd.Series) -> float:
    return base_tools.profit_factor(pnl)


def recompute(df: pd.DataFrame) -> pd.DataFrame:
    return base_tools.recompute_equity(df)


def load_top50() -> pd.DataFrame:
    return pd.read_csv(TOP50_TRADES, parse_dates=["entry_date", "exit_date"])


def load_top100() -> pd.DataFrame:
    trades = pd.read_csv(TOP100_TRADES, parse_dates=["entry_date", "exit_date"])
    context = pd.read_csv(TOP100_CONTEXT, parse_dates=["entry_date", "exit_date"])
    return base_tools.merge_top100_context(trades, context)


def refined_veto_mask(df: pd.DataFrame) -> pd.Series:
    return (
        df["rule"].astype(str).eq(base_tools.TARGET_RULE)
        & df["side"].astype(str).eq(base_tools.TARGET_SIDE)
        & df["daily_structure_phase_fresh"].map(base_tools.clean).eq(base_tools.TARGET_PHASE)
        & df["volume_price_phase"].map(base_tools.clean).eq(base_tools.TARGET_VOLUME_PHASE)
        & df["bearish_countermove_subregime"].map(base_tools.clean).eq("bearish_countermove_weak_retest")
    )


def prepare_trades(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if "executed" in out.columns:
        out = out[out["executed"].astype(bool)].copy()
    out["entry_date"] = pd.to_datetime(out["entry_date"])
    out["exit_date"] = pd.to_datetime(out["exit_date"])
    out["pnl_dollars"] = pd.to_numeric(out["pnl_dollars"], errors="coerce").fillna(0.0)
    return recompute(out)


def summarize(label: str, df: pd.DataFrame, removed: pd.DataFrame) -> dict[str, object]:
    trades = prepare_trades(df)
    pnl = trades["pnl_dollars"] if not trades.empty else pd.Series(dtype=float)
    forward = trades[trades["exit_date"] >= FORWARD_START].copy() if not trades.empty else trades
    removed_pnl = pd.to_numeric(removed.get("pnl_dollars", pd.Series(dtype=float)), errors="coerce").fillna(0.0)
    removed_forward = (
        removed[pd.to_datetime(removed.get("exit_date", pd.Series(dtype=str)), errors="coerce") >= FORWARD_START].copy()
        if not removed.empty
        else removed
    )
    return {
        "variant": label,
        "net_profit": float(pnl.sum()),
        "trades": int(len(trades)),
        "wins": int((pnl > 0.0).sum()),
        "losses": int((pnl <= 0.0).sum()),
        "win_rate": float((pnl > 0.0).mean()) if len(pnl) else 0.0,
        "profit_factor": profit_factor(pnl),
        "max_drawdown": base_tools.max_drawdown(trades["equity_after"]) if not trades.empty else 0.0,
        "ulcer_index": base_tools.ulcer_index(trades["equity_after"]) if not trades.empty else 0.0,
        "rolling20_worst_pnl": base_tools.rolling_worst(pnl, 20),
        "rolling50_worst_pnl": base_tools.rolling_worst(pnl, 50),
        "forward_net_profit": float(forward["pnl_dollars"].sum()) if not forward.empty else 0.0,
        "forward_profit_factor": profit_factor(forward["pnl_dollars"]) if not forward.empty else 0.0,
        "removed_trades": int(len(removed)),
        "removed_winners": int((removed_pnl > 0.0).sum()),
        "removed_losers": int((removed_pnl <= 0.0).sum()),
        "removed_net_profit": float(removed_pnl.sum()),
        "removed_forward_trades": int(len(removed_forward)),
        "removed_forward_net_profit": float(
            pd.to_numeric(removed_forward.get("pnl_dollars", pd.Series(dtype=float)), errors="coerce").fillna(0.0).sum()
        )
        if not removed_forward.empty
        else 0.0,
    }


def write_plot(name: str, before: pd.DataFrame, after: pd.DataFrame, suffix: str) -> None:
    plt.figure(figsize=(14, 7))
    for label, frame in [("before", before), ("after", after)]:
        curve = prepare_trades(frame)
        if curve.empty:
            continue
        plt.plot(curve["exit_date"], curve["equity_after"] / START_CAPITAL, linewidth=2.0, label=label)
    plt.axhline(1.0, color="#9CA3AF", linestyle="--", linewidth=1.0)
    plt.axvline(FORWARD_START, color="#DC2626", linestyle="--", linewidth=1.0, alpha=0.7, label="forward start")
    plt.title(f"{name}: {suffix}")
    plt.xlabel("Exit date")
    plt.ylabel("Normalized equity")
    plt.grid(True, alpha=0.25)
    plt.legend()
    plt.tight_layout()
    plt.savefig(OUT_DIR / f"{name}_{suffix}_equity_compare.png", dpi=150)
    plt.close()


def apply_refined_veto(name: str, df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, list[dict[str, object]]]:
    work = prepare_trades(df)
    mask = refined_veto_mask(work)
    removed = work.loc[mask].copy()
    kept = work.loc[~mask].copy()
    rows = [
        summarize(f"{name}_baseline_before_refined_veto", work, pd.DataFrame()),
        summarize(f"{name}_{REFINED_VETO_NAME}", kept, removed),
    ]
    rows[1]["delta_net_profit"] = rows[1]["net_profit"] - rows[0]["net_profit"]
    rows[1]["delta_forward_net_profit"] = rows[1]["forward_net_profit"] - rows[0]["forward_net_profit"]
    rows[1]["delta_rolling50_worst_pnl"] = rows[1]["rolling50_worst_pnl"] - rows[0]["rolling50_worst_pnl"]
    rows[0]["delta_net_profit"] = 0.0
    rows[0]["delta_forward_net_profit"] = 0.0
    rows[0]["delta_rolling50_worst_pnl"] = 0.0
    prepare_trades(kept).to_csv(OUT_DIR / f"{name}_{REFINED_VETO_NAME}_baseline_trades.csv", index=False)
    removed.assign(basket=name, veto=REFINED_VETO_NAME).to_csv(OUT_DIR / f"{name}_{REFINED_VETO_NAME}_removed_trades.csv", index=False)
    write_plot(name, work, kept, REFINED_VETO_NAME)
    return kept, removed, rows


def cluster_configs() -> list[ClusterConfig]:
    configs: list[ClusterConfig] = []
    target_sets = {
        "lhf": ("bearish_lower_high_failure_short",),
        "lhf_gap": (
            "bearish_lower_high_failure_short",
            "bearish_gap_breakdown_continuation_short",
        ),
        "core": CORE_SHORT_RULES,
    }
    for target_name, rules in target_sets.items():
        for lookback_days in (2, 3, 5):
            for min_losses in (2, 3, 4):
                for max_net_pnl in (-150.0, -250.0):
                    configs.append(
                        ClusterConfig(
                            name=f"{target_name}_lb{lookback_days}_loss{min_losses}_net{int(abs(max_net_pnl))}",
                            lookback_days=lookback_days,
                            min_losses=min_losses,
                            max_net_pnl=max_net_pnl,
                            target_rules=rules,
                        )
                    )
    return configs


def apply_cluster_filter(df: pd.DataFrame, config: ClusterConfig) -> tuple[pd.DataFrame, pd.DataFrame]:
    ordered = prepare_trades(df)
    kept_rows: list[pd.Series] = []
    skipped_rows: list[pd.Series] = []
    kept_history: list[dict[str, object]] = []

    for row in ordered.sort_values(["entry_date", "exit_date", "symbol", "rule"]).itertuples(index=False):
        entry_date = pd.Timestamp(row.entry_date)
        is_target = str(row.side) == "short" and str(row.rule) in config.target_rules
        start_date = entry_date - pd.Timedelta(days=config.lookback_days)
        recent_pnl_values = [
            float(item["pnl_dollars"])
            for item in kept_history
            if start_date <= pd.Timestamp(item["exit_date"]) < entry_date
            and str(item["side"]) == "short"
            and str(item["rule"]) in CORE_SHORT_RULES
        ]
        recent_pnl = pd.Series(recent_pnl_values, dtype=float)
        recent_losses = int((recent_pnl <= 0.0).sum())
        recent_net = float(recent_pnl.sum())
        should_skip = is_target and recent_losses >= config.min_losses and recent_net <= config.max_net_pnl
        row_dict = pd.Series(row._asdict())
        row_dict["cluster_filter"] = config.name
        row_dict["cluster_recent_trades"] = int(len(recent_pnl))
        row_dict["cluster_recent_losses"] = recent_losses
        row_dict["cluster_recent_net_pnl"] = recent_net
        if should_skip:
            skipped_rows.append(row_dict)
        else:
            kept_rows.append(row_dict)
            kept_history.append(row._asdict())

    kept = pd.DataFrame(kept_rows)
    skipped = pd.DataFrame(skipped_rows)
    return kept, skipped


def april_may_block_net(df: pd.DataFrame) -> float:
    trades = prepare_trades(df)
    mask = (trades["exit_date"] >= pd.Timestamp("2024-04-22")) & (trades["exit_date"] <= pd.Timestamp("2024-05-06 23:59:59"))
    return float(trades.loc[mask, "pnl_dollars"].sum())


def run_cluster_sweep(baselines: dict[str, pd.DataFrame]) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows: list[dict[str, object]] = []
    audit_rows: list[pd.DataFrame] = []
    configs = cluster_configs()
    base_stats = {name: summarize(f"{name}_refined_baseline", df, pd.DataFrame()) for name, df in baselines.items()}
    base_block = {name: april_may_block_net(df) for name, df in baselines.items()}

    for cfg_idx, config in enumerate(configs, start=1):
        print(f"cluster {cfg_idx}/{len(configs)} {config.name}", flush=True)
        per_basket: dict[str, dict[str, object]] = {}
        for name, df in baselines.items():
            kept, skipped = apply_cluster_filter(df, config)
            stats = summarize(f"{name}_{config.name}", kept, skipped)
            base = base_stats[name]
            stats.update(
                {
                    "basket": name,
                    "config": config.name,
                    "lookback_days": config.lookback_days,
                    "min_losses": config.min_losses,
                    "max_net_pnl": config.max_net_pnl,
                    "target_rules": "|".join(config.target_rules),
                    "delta_net_profit": stats["net_profit"] - base["net_profit"],
                    "delta_forward_net_profit": stats["forward_net_profit"] - base["forward_net_profit"],
                    "delta_profit_factor": stats["profit_factor"] - base["profit_factor"],
                    "delta_rolling50_worst_pnl": stats["rolling50_worst_pnl"] - base["rolling50_worst_pnl"],
                    "delta_max_drawdown": stats["max_drawdown"] - base["max_drawdown"],
                    "april_may_block_net": april_may_block_net(kept),
                    "delta_april_may_block_net": april_may_block_net(kept) - base_block[name],
                }
            )
            rows.append(stats)
            per_basket[name] = stats
            if not skipped.empty:
                audit = skipped.copy()
                audit["basket"] = name
                audit_rows.append(audit)
        if {"top50", "top100"}.issubset(per_basket):
            rows.append(
                {
                    "basket": "combined",
                    "config": config.name,
                    "lookback_days": config.lookback_days,
                    "min_losses": config.min_losses,
                    "max_net_pnl": config.max_net_pnl,
                    "target_rules": "|".join(config.target_rules),
                    "delta_net_profit": per_basket["top50"]["delta_net_profit"] + per_basket["top100"]["delta_net_profit"],
                    "delta_forward_net_profit": per_basket["top50"]["delta_forward_net_profit"]
                    + per_basket["top100"]["delta_forward_net_profit"],
                    "delta_rolling50_worst_pnl": per_basket["top50"]["delta_rolling50_worst_pnl"]
                    + per_basket["top100"]["delta_rolling50_worst_pnl"],
                    "removed_trades": per_basket["top50"]["removed_trades"] + per_basket["top100"]["removed_trades"],
                    "removed_winners": per_basket["top50"]["removed_winners"] + per_basket["top100"]["removed_winners"],
                    "removed_losers": per_basket["top50"]["removed_losers"] + per_basket["top100"]["removed_losers"],
                    "removed_net_profit": per_basket["top50"]["removed_net_profit"] + per_basket["top100"]["removed_net_profit"],
                    "removed_forward_net_profit": per_basket["top50"]["removed_forward_net_profit"]
                    + per_basket["top100"]["removed_forward_net_profit"],
                    "delta_april_may_block_net": per_basket["top50"]["delta_april_may_block_net"]
                    + per_basket["top100"]["delta_april_may_block_net"],
                }
            )

    summary = pd.DataFrame(rows)
    audit = pd.concat(audit_rows, ignore_index=True) if audit_rows else pd.DataFrame()
    summary.to_csv(OUT_CLUSTER_SWEEP, index=False)
    audit.to_csv(OUT_CLUSTER_AUDIT, index=False)
    return summary, audit


def main() -> None:
    top50 = load_top50()
    top100 = load_top100()

    baseline_rows: list[dict[str, object]] = []
    refined_baselines: dict[str, pd.DataFrame] = {}
    for name, trades in (("top50", top50), ("top100", top100)):
        kept, _removed, rows = apply_refined_veto(name, trades)
        refined_baselines[name] = prepare_trades(kept)
        baseline_rows.extend(rows)

    baseline_summary = pd.DataFrame(baseline_rows)
    baseline_summary.to_csv(OUT_BASELINE_SUMMARY, index=False)
    print("\nrefined baseline")
    print(baseline_summary.round(4).to_string(index=False))

    sweep, _audit = run_cluster_sweep(refined_baselines)
    print("\ncluster candidates with positive top50/top100 net and forward")
    pivot = sweep[sweep["basket"].isin(["top50", "top100"])].pivot_table(
        index="config",
        columns="basket",
        values=["delta_net_profit", "delta_forward_net_profit", "delta_rolling50_worst_pnl", "removed_trades"],
        aggfunc="first",
    )
    good_configs = []
    for config in pivot.index:
        try:
            if (
                pivot.loc[config, ("delta_net_profit", "top50")] > 0
                and pivot.loc[config, ("delta_net_profit", "top100")] > 0
                and pivot.loc[config, ("delta_forward_net_profit", "top50")] >= 0
                and pivot.loc[config, ("delta_forward_net_profit", "top100")] >= 0
            ):
                good_configs.append(config)
        except KeyError:
            continue
    combined = sweep[sweep["basket"].eq("combined") & sweep["config"].isin(good_configs)].copy()
    if not combined.empty:
        print(
            combined.sort_values(
                ["delta_rolling50_worst_pnl", "delta_net_profit", "delta_forward_net_profit"],
                ascending=[False, False, False],
            )
            .head(20)
            .round(4)
            .to_string(index=False)
        )
    else:
        print("no robust config passed positive top50/top100 net + forward guard")


if __name__ == "__main__":
    main()
