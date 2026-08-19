from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import csv
from dataclasses import dataclass
from datetime import datetime, timezone
import importlib
import json
from pathlib import Path
import sys
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd


BOT_ROOT = Path(__file__).resolve().parents[1]
PROJECTS_ROOT = BOT_ROOT.parent
ENGINE_ROOT = (
    PROJECTS_ROOT
    / "PythonProject"
    / "statistic"
    / "spx_adx_di_5min_2024_02_03"
)
for path in (ENGINE_ROOT, BOT_ROOT, BOT_ROOT / "scripts"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import current_live_baseline_config as live_config
import decision_records as decision_model
import manual_regime_engine_v1_2_manifest as manifest_v1_2
import manual_regime_engine_v1_3_manifest as manifest_v1_3
import run_manual_regime_baseline_v1 as baseline_runner
import run_manual_regime_decision_time_variants as portfolio_runner


WARMUP_START = "2023-01-01"
PERIOD_START = pd.Timestamp("2024-01-01")
OUTPUT_ROOT = Path(
    r"D:\Trading\journal\research\manual-regime-volume-weighted-acceptance"
)


@dataclass(frozen=True)
class VariantSpec:
    key: str
    label: str
    engine_module: str
    engine_path: Path
    manifest_path: Path
    strategy_version: str
    strategy_variant_id: str
    engine_version: str
    manifest_hash: str
    color: str


VARIANTS = {
    "v1.2-tpo": VariantSpec(
        key="v1.2-tpo",
        label="V1.2 TPO acceptance",
        engine_module="manual_regime_rules_backtest_v1_2",
        engine_path=ENGINE_ROOT / "manual_regime_rules_backtest_v1_2.py",
        manifest_path=ENGINE_ROOT / "manual_regime_engine_v1_2_manifest.py",
        strategy_version=manifest_v1_2.STRATEGY_VERSION,
        strategy_variant_id=manifest_v1_2.STRATEGY_VARIANT_ID,
        engine_version=manifest_v1_2.ENGINE_VERSION,
        manifest_hash=manifest_v1_2.manifest_hash(),
        color="#6b7280",
    ),
    "v1.3-volume": VariantSpec(
        key="v1.3-volume",
        label="V1.3 volume-weighted acceptance",
        engine_module="manual_regime_rules_backtest_v1_3",
        engine_path=ENGINE_ROOT / "manual_regime_rules_backtest_v1_3.py",
        manifest_path=ENGINE_ROOT / "manual_regime_engine_v1_3_manifest.py",
        strategy_version=manifest_v1_3.STRATEGY_VERSION,
        strategy_variant_id=manifest_v1_3.STRATEGY_VARIANT_ID,
        engine_version=manifest_v1_3.ENGINE_VERSION,
        manifest_hash=manifest_v1_3.manifest_hash(),
        color="#0f766e",
    ),
}


def latest_csv_timestamp(path: Path) -> pd.Timestamp:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        header = next(csv.reader(handle))
    try:
        date_index = header.index("date")
    except ValueError as exc:
        raise ValueError(f"{path} has no date column") from exc

    with path.open("rb") as handle:
        handle.seek(0, 2)
        position = handle.tell()
        buffer = b""
        while position > 0 and buffer.count(b"\n") < 2:
            block_size = min(4096, position)
            position -= block_size
            handle.seek(position)
            buffer = handle.read(block_size) + buffer
    lines = [line for line in buffer.decode("utf-8-sig").splitlines() if line.strip()]
    if not lines:
        raise ValueError(f"{path} contains no data rows")
    row = next(csv.reader([lines[-1]]))
    return pd.Timestamp(row[date_index])


def run_symbol(
    symbol: str,
    variant_key: str,
    end_exclusive: str,
    log_dir: str,
) -> tuple[str, pd.DataFrame, list[decision_model.DecisionRecord]]:
    spec = VARIANTS[variant_key]
    rules = importlib.import_module(spec.engine_module)
    records: list[decision_model.DecisionRecord] = []
    _signals, completed, _daily = rules.run_backtest(
        symbol,
        WARMUP_START,
        end_exclusive,
        structure_lookback=10,
        bull_continuation_mode="combo",
        learn_long_mode="off",
        structural_reset_mode="off",
        max_history_days=548,
        decision_records_out=records,
        runtime_log_path=Path(log_dir) / f"{symbol}.log",
    )
    openings = [
        record
        for record in records
        if record.status == "accepted" and record.trade_action == "trade"
    ]
    if any(record.strategy_variant_id != spec.strategy_variant_id for record in openings):
        raise RuntimeError(f"{symbol}: engine emitted a foreign strategy variant")
    if not completed.empty:
        completed = completed.copy()
        completed["symbol"] = symbol
        completed = decision_model.attach_record_keys_to_trades(completed, openings)
    return symbol, completed, openings


def common_input_manifest(
    symbols: list[str],
    end_exclusive: pd.Timestamp,
) -> dict[str, Any]:
    fixed_files = {
        "v1_2_engine": VARIANTS["v1.2-tpo"].engine_path,
        "v1_2_manifest": VARIANTS["v1.2-tpo"].manifest_path,
        "v1_3_engine": VARIANTS["v1.3-volume"].engine_path,
        "v1_3_manifest": VARIANTS["v1.3-volume"].manifest_path,
        "symbols": live_config.OPTIMIZED_LIVE_TOP50_PATH,
        "symbol_rule_pairs": live_config.LIVE_PAIR_SELECTION_PATH,
        "filter_adapter": ENGINE_ROOT / "manual_regime_variant_filters.py",
        "live_filter_pipeline": ENGINE_ROOT / "live_manual_regime_tws_bot.py",
        "runner": Path(__file__).resolve(),
    }
    raw_files = {
        symbol: Path(r"D:\Trading\Rohdaten") / f"Rohdaten_{symbol}.csv"
        for symbol in symbols
    }
    missing = [str(path) for path in (*fixed_files.values(), *raw_files.values()) if not path.exists()]
    if missing:
        raise FileNotFoundError(f"Missing acceptance-profile inputs: {missing}")

    raw_payload: dict[str, Any] = {}
    for symbol, path in raw_files.items():
        raw_payload[symbol] = {
            "path": str(path),
            "size": path.stat().st_size,
            "sha256": baseline_runner.sha256_file(path),
            "latest_timestamp": latest_csv_timestamp(path).isoformat(),
        }
    payload = {
        "period_start": PERIOD_START.date().isoformat(),
        "period_end_exclusive": end_exclusive.date().isoformat(),
        "warmup_start": WARMUP_START,
        "symbols": symbols,
        "risk_per_trade": portfolio_runner.RISK_PER_TRADE,
        "files": {
            name: {
                "path": str(path),
                "sha256": baseline_runner.sha256_file(path),
            }
            for name, path in fixed_files.items()
        },
        "raw_data": raw_payload,
    }
    payload["data_revision"] = baseline_runner.stable_payload_hash(payload)
    return payload


def run_variant(
    spec: VariantSpec,
    *,
    symbols: list[str],
    keep_pairs: set[tuple[str, str]],
    end_exclusive: pd.Timestamp,
    workers: int,
    output_dir: Path,
) -> dict[str, Any]:
    variant_dir = output_dir / spec.key
    log_dir = variant_dir / "engine_logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    completed_parts: list[pd.DataFrame] = []
    opening_records: list[decision_model.DecisionRecord] = []

    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(
                run_symbol,
                symbol,
                spec.key,
                end_exclusive.date().isoformat(),
                str(log_dir),
            ): symbol
            for symbol in symbols
        }
        for done, future in enumerate(as_completed(futures), start=1):
            symbol, completed, openings = future.result()
            if not completed.empty:
                completed_parts.append(completed)
            opening_records.extend(openings)
            print(
                f"{spec.key} {done}/{len(symbols)} {symbol}: "
                f"completed={len(completed)} openings={len(openings)}",
                flush=True,
            )

    completed = (
        pd.concat(completed_parts, ignore_index=True, sort=False)
        if completed_parts
        else pd.DataFrame()
    )
    opening_records.sort(
        key=lambda record: (
            record.decision_time_utc,
            record.symbol,
            record.primary_rule,
            record.track,
            record.occurrence,
            record.signal_key,
        )
    )
    final_records, model_trades, executable_trades = (
        portfolio_runner.apply_portfolio_pipeline(
            opening_records,
            completed,
            keep_pairs,
            spec,
            end_exclusive,
        )
    )
    metrics, curve = baseline_runner.metrics_and_curve(executable_trades)
    model_metrics, model_curve = baseline_runner.metrics_and_curve(model_trades)

    executable_trades.to_csv(variant_dir / "trades.csv", index=False)
    model_trades.to_csv(
        variant_dir / "model_trades_including_non_routable.csv",
        index=False,
    )
    curve.to_csv(variant_dir / "equity_curve.csv", index=False)
    model_curve.to_csv(
        variant_dir / "model_equity_curve_including_non_routable.csv",
        index=False,
    )
    decision_model.write_jsonl(variant_dir / "decision_records.jsonl", final_records)

    source_rows = [
        {
            "symbol": record.symbol,
            "acceptance_profile_source": str(
                record.context.get("acceptance_profile_source", "not_recorded")
            ),
        }
        for record in opening_records
    ]
    source_frame = pd.DataFrame(source_rows)
    if source_frame.empty:
        source_counts = pd.DataFrame(
            columns=["symbol", "acceptance_profile_source", "opening_records"]
        )
    else:
        source_counts = (
            source_frame.groupby(
                ["symbol", "acceptance_profile_source"],
                as_index=False,
            )
            .size()
            .rename(columns={"size": "opening_records"})
        )
    source_counts.to_csv(variant_dir / "acceptance_profile_sources.csv", index=False)

    manifest = {
        "strategy_version": spec.strategy_version,
        "strategy_variant_id": spec.strategy_variant_id,
        "engine_version": spec.engine_version,
        "manifest_hash": spec.manifest_hash,
        "engine_sha256": baseline_runner.sha256_file(spec.engine_path),
        "execution_metrics": metrics,
        "model_metrics_including_non_routable": model_metrics,
        "opening_record_count": len(opening_records),
        "final_decision_record_count": len(final_records),
        "acceptance_profile_source_counts": (
            source_frame["acceptance_profile_source"].value_counts().to_dict()
            if not source_frame.empty
            else {}
        ),
    }
    (variant_dir / "variant_run_manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False, allow_nan=False),
        encoding="utf-8",
    )
    return {"artifact_directory": str(variant_dir), **manifest}


def rule_metrics(trades: pd.DataFrame, variant_key: str) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame(
            columns=[
                "variant",
                "rule",
                "trades",
                "wins",
                "net_pnl_dollars",
                "avg_pnl_dollars",
            ]
        )
    frame = trades.copy()
    frame["pnl_dollars"] = pd.to_numeric(frame["pnl_dollars"], errors="raise")
    return (
        frame.groupby("rule", as_index=False)
        .agg(
            trades=("pnl_dollars", "size"),
            wins=("pnl_dollars", lambda values: int((values > 0).sum())),
            net_pnl_dollars=("pnl_dollars", "sum"),
            avg_pnl_dollars=("pnl_dollars", "mean"),
        )
        .assign(variant=variant_key)
        [
            [
                "variant",
                "rule",
                "trades",
                "wins",
                "net_pnl_dollars",
                "avg_pnl_dollars",
            ]
        ]
    )


def trade_changes(baseline: pd.DataFrame, candidate: pd.DataFrame) -> pd.DataFrame:
    identity = ["symbol", "track", "rule", "side", "entry_date"]
    left = baseline.copy()
    right = candidate.copy()
    for frame in (left, right):
        frame["entry_date"] = pd.to_datetime(frame["entry_date"])
        frame["_occurrence"] = frame.groupby(identity).cumcount()
    identity.append("_occurrence")
    columns = identity + [
        "exit_date",
        "entry",
        "stop",
        "target",
        "exit_reason",
        "points",
        "pnl_dollars",
    ]
    merged = left[columns].merge(
        right[columns],
        on=identity,
        how="outer",
        suffixes=("_v1_2", "_v1_3"),
        indicator=True,
    )
    merged["change_type"] = merged["_merge"].map(
        {"left_only": "removed", "right_only": "added", "both": "matched"}
    )
    merged["pnl_delta_v1_3_minus_v1_2"] = (
        pd.to_numeric(merged["pnl_dollars_v1_3"], errors="coerce").fillna(0.0)
        - pd.to_numeric(merged["pnl_dollars_v1_2"], errors="coerce").fillna(0.0)
    )
    return merged.drop(columns=["_merge"])


def write_comparison(
    runs: dict[str, dict[str, Any]],
    *,
    output_dir: Path,
    inputs: dict[str, Any],
    end_exclusive: pd.Timestamp,
    workers: int,
) -> None:
    curves: dict[str, pd.DataFrame] = {}
    trades: dict[str, pd.DataFrame] = {}
    for key, run in runs.items():
        variant_dir = Path(run["artifact_directory"])
        curves[key] = pd.read_csv(variant_dir / "equity_curve.csv", parse_dates=["exit_date"])
        trades[key] = pd.read_csv(variant_dir / "trades.csv")

    fig, ax = plt.subplots(figsize=(15, 8))
    for key, spec in VARIANTS.items():
        curve = curves[key]
        ax.plot(
            curve["exit_date"],
            curve["equity"],
            color=spec.color,
            linewidth=2.0,
            label=spec.label,
        )
    ax.axhline(
        baseline_runner.START_CAPITAL,
        color="#9ca3af",
        linestyle="--",
        linewidth=1.0,
    )
    ax.set_title("Manual-Regime: TPO vs volume-weighted acceptance zones")
    ax.set_xlabel("Exit date")
    ax.set_ylabel("Equity (USD)")
    ax.grid(True, alpha=0.25)
    ax.legend(loc="upper left")
    fig.tight_layout()
    fig.savefig(output_dir / "equity_comparison.png", dpi=180)
    plt.close(fig)

    baseline_metrics = runs["v1.2-tpo"]["execution_metrics"]
    rows = []
    for key, run in runs.items():
        metrics = run["execution_metrics"]
        rows.append(
            {
                "variant": key,
                "strategy_variant_id": run["strategy_variant_id"],
                **metrics,
                "delta_net_pnl_vs_v1_2": float(metrics["net_pnl_dollars"])
                - float(baseline_metrics["net_pnl_dollars"]),
                "delta_ending_equity_vs_v1_2": float(metrics["ending_equity"])
                - float(baseline_metrics["ending_equity"]),
                "delta_profit_factor_vs_v1_2": float(metrics["profit_factor"])
                - float(baseline_metrics["profit_factor"]),
                "delta_max_drawdown_vs_v1_2": float(metrics["max_drawdown_dollars"])
                - float(baseline_metrics["max_drawdown_dollars"]),
            }
        )
    pd.DataFrame(rows).to_csv(output_dir / "comparison_metrics.csv", index=False)
    pd.concat(
        [rule_metrics(trades[key], key) for key in VARIANTS],
        ignore_index=True,
    ).to_csv(output_dir / "rule_metrics.csv", index=False)
    changes = trade_changes(trades["v1.2-tpo"], trades["v1.3-volume"])
    changes.to_csv(output_dir / "trade_changes.csv", index=False)

    comparison_manifest = {
        "created_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "period_start": PERIOD_START.date().isoformat(),
        "period_end_inclusive": (
            end_exclusive - pd.Timedelta(days=1)
        ).date().isoformat(),
        "workers": workers,
        "data_revision": inputs["data_revision"],
        "runs": runs,
        "methodology": {
            "basket": "same frozen optimized Top50",
            "keep_pairs": "same 318 frozen symbol/rule pairs",
            "risk_per_trade": portfolio_runner.RISK_PER_TRADE,
            "portfolio_vetos": "same production weak-retest and causal LHF loss-cluster veto",
            "fees": "zero",
            "slippage": "zero",
            "only_intended_change": (
                "30-day price-acceptance bins use evenly distributed bar volume "
                "when total usable pre-decision volume is positive; otherwise V1.2 TPO fallback"
            ),
            "unchanged_acceptance_settings": {
                "lookback_days": 30,
                "bin_size": "max(tolerance, 0.04 * atr14, 0.01)",
                "smoothing": "three adjacent bins",
                "quantile": 0.65,
                "minimum_move": "max(0.60 * atr14, 6 * tolerance)",
            },
        },
        "change_counts": changes["change_type"].value_counts().to_dict(),
    }
    (output_dir / "comparison_manifest.json").write_text(
        json.dumps(
            comparison_manifest,
            indent=2,
            ensure_ascii=False,
            allow_nan=False,
        ),
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=24)
    parser.add_argument(
        "--end-exclusive",
        default=(
            pd.Timestamp.now(tz="Europe/Berlin").normalize() + pd.Timedelta(days=1)
        ).date().isoformat(),
    )
    args = parser.parse_args()
    if args.workers < 1:
        raise ValueError("--workers must be positive")
    requested_end_exclusive = pd.Timestamp(args.end_exclusive).normalize()
    if requested_end_exclusive <= PERIOD_START:
        raise ValueError("--end-exclusive must be after 2024-01-01")

    symbols = live_config.load_optimized_live_top50_symbols()
    keep_pairs = live_config.load_live_keep_pairs()
    if len(symbols) != 50 or len(keep_pairs) != 318:
        raise RuntimeError(
            f"Expected Top50/318 keep pairs, got {len(symbols)}/{len(keep_pairs)}"
        )

    print("Hashing common inputs...", flush=True)
    inputs = common_input_manifest(symbols, requested_end_exclusive)
    common_latest_day = min(
        pd.Timestamp(item["latest_timestamp"]).normalize()
        for item in inputs["raw_data"].values()
    )
    end_exclusive = min(
        requested_end_exclusive,
        common_latest_day + pd.Timedelta(days=1),
    )
    if end_exclusive != requested_end_exclusive:
        print(
            "Capping requested end-exclusive "
            f"{requested_end_exclusive.date()} to common raw-data end "
            f"{end_exclusive.date()} (last common day {common_latest_day.date()})",
            flush=True,
        )
        inputs["requested_period_end_exclusive"] = (
            requested_end_exclusive.date().isoformat()
        )
        inputs["period_end_exclusive"] = end_exclusive.date().isoformat()
        inputs["data_revision"] = baseline_runner.stable_payload_hash(
            {key: value for key, value in inputs.items() if key != "data_revision"}
        )

    end_inclusive = end_exclusive - pd.Timedelta(days=1)
    output_dir = OUTPUT_ROOT / f"{PERIOD_START:%Y-%m-%d}_{end_inclusive:%Y-%m-%d}"
    output_dir.mkdir(parents=True, exist_ok=True)
    runs = {
        key: run_variant(
            spec,
            symbols=symbols,
            keep_pairs=keep_pairs,
            end_exclusive=end_exclusive,
            workers=args.workers,
            output_dir=output_dir,
        )
        for key, spec in VARIANTS.items()
    }
    write_comparison(
        runs,
        output_dir=output_dir,
        inputs=inputs,
        end_exclusive=end_exclusive,
        workers=args.workers,
    )
    print(f"comparison artifacts: {output_dir}", flush=True)


if __name__ == "__main__":
    main()
