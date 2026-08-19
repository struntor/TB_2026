from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import sys

import pandas as pd


BOT_ROOT = Path(__file__).resolve().parents[1]
ENGINE_ROOT = (
    BOT_ROOT.parent
    / "PythonProject"
    / "statistic"
    / "spx_adx_di_5min_2024_02_03"
)
for path in (BOT_ROOT, BOT_ROOT / "scripts", ENGINE_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import current_live_baseline_config as live_config
import decision_records as decision_model
import run_manual_regime_decision_time_variants as runner


SYMBOLS = ["SPY", "QQQ", "GLD"]
END_EXCLUSIVE = pd.Timestamp("2024-04-01")
LOG_ROOT = Path(r"D:\Trading\journal\smoke\decision-time-parallelism")


def digest_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def deterministic_payload(variant_key: str, workers: int) -> dict[str, str]:
    log_dir = LOG_ROOT / variant_key / f"workers_{workers}"
    log_dir.mkdir(parents=True, exist_ok=True)
    results: list[tuple[str, pd.DataFrame, list[decision_model.DecisionRecord]]] = []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(
                runner.run_symbol,
                symbol,
                str(log_dir),
                END_EXCLUSIVE.date().isoformat(),
                variant_key,
            ): symbol
            for symbol in SYMBOLS
        }
        for future in as_completed(futures):
            results.append(future.result())
    results.sort(key=lambda item: item[0])
    completed = pd.concat(
        [item[1] for item in results if not item[1].empty],
        ignore_index=True,
        sort=False,
    )
    records = [record for _symbol, _completed, part in results for record in part]
    records.sort(
        key=lambda record: (
            record.decision_time_utc,
            record.symbol,
            record.primary_rule,
            record.track,
            record.occurrence,
            record.signal_key,
        )
    )
    variant = runner.TIMING_VARIANTS[variant_key]
    keep_pairs = {
        pair
        for pair in live_config.load_live_keep_pairs()
        if pair[0] in set(SYMBOLS)
    }
    final_records, _model, trades = runner.apply_portfolio_pipeline(
        records,
        completed,
        keep_pairs,
        variant,
        END_EXCLUSIVE,
    )
    final_records.sort(
        key=lambda record: (
            record.decision_time_utc,
            record.symbol,
            record.primary_rule,
            record.track,
            record.occurrence,
            record.signal_key,
        )
    )
    _metrics, curve = runner.baseline_runner.metrics_and_curve(trades)
    return {
        "trades": digest_text(trades.to_csv(index=False, lineterminator="\n")),
        "curve": digest_text(curve.to_csv(index=False, lineterminator="\n")),
        "decisions": digest_text(
            "\n".join(record.to_json() for record in final_records) + "\n"
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--variants",
        nargs="+",
        choices=sorted(runner.TIMING_VARIANTS),
        default=sorted(runner.TIMING_VARIANTS),
    )
    args = parser.parse_args()
    report: dict[str, dict[str, dict[str, str]]] = {}
    for variant_key in args.variants:
        one = deterministic_payload(variant_key, 1)
        twenty_four = deterministic_payload(variant_key, 24)
        if one != twenty_four:
            raise RuntimeError(
                f"{variant_key} output differs between 1 and 24 workers: "
                f"one={one} twenty_four={twenty_four}"
            )
        report[variant_key] = {"workers_1": one, "workers_24": twenty_four}
    print(json.dumps(report, indent=2), flush=True)


if __name__ == "__main__":
    main()
