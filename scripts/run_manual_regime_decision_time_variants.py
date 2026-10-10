from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd


BOT_ROOT = Path(__file__).resolve().parents[1]
PROJECTS_ROOT = BOT_ROOT.parent
ENGINE_ROOT = (
    PROJECTS_ROOT
    / "IBKR_TradingBot"
    / "statistic"
    / "spx_adx_di_5min_2024_02_03"
)
for path in (ENGINE_ROOT, BOT_ROOT, BOT_ROOT / "scripts"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import current_live_baseline_config as live_config
import decision_records as decision_model
import live_manual_regime_tws_bot as live
import manual_regime_decision_time_manifest as timing_manifest
import manual_regime_rules_backtest_v1_1 as rules
import manual_regime_variant_filters as variant_filters
import manual_regime_version_manifest as baseline_manifest
import run_manual_regime_baseline_v1 as baseline_runner
from src.journal.realized_history import ModelHistorySnapshot, ModelHistoryTrade


WARMUP_START = "2023-01-01"
PERIOD_START = pd.Timestamp("2024-01-01")
DEFAULT_END_EXCLUSIVE = pd.Timestamp("2026-07-16")
START_CAPITAL = baseline_runner.START_CAPITAL
RISK_PER_TRADE = baseline_runner.RISK_PER_TRADE
ARTIFACT_ROOT = Path(r"D:\Trading\journal\baselines")
COMPARISON_ROOT = Path(r"D:\Trading\journal\comparisons\manual-regime-decision-time")
BASELINE_VARIANT_ID = baseline_manifest.STRATEGY_VARIANT_ID
BASELINE_ENGINE_PATH = ENGINE_ROOT / "manual_regime_rules_backtest.py"
TIMING_ENGINE_PATH = ENGINE_ROOT / "manual_regime_rules_backtest_v1_1.py"
TIMING_MANIFEST_PATH = ENGINE_ROOT / "manual_regime_decision_time_manifest.py"

# Keep the already published 10m/60m/90m manifest byte-stable so their
# immutable artifacts and symbol caches remain reproducible.  Added research
# timings are fully captured by the runner hash, variant manifest hash and run
# configuration without changing those existing variant definitions.
RESEARCH_TIMING_VARIANTS = (
    timing_manifest.DecisionTimingVariant(
        key="5m",
        decision_bar_number=1,
        decision_offset_minutes=5,
        decision_bar_timestamp="09:30",
        decision_available_at="09:35",
        strategy_version="manual-regime-top50-ruleset-v1-decision-5m",
        strategy_variant_id="manual-regime-decision-5m@1.1.0",
    ),
    timing_manifest.DecisionTimingVariant(
        key="15m",
        decision_bar_number=3,
        decision_offset_minutes=15,
        decision_bar_timestamp="09:40",
        decision_available_at="09:45",
        strategy_version="manual-regime-top50-ruleset-v1-decision-15m",
        strategy_variant_id="manual-regime-decision-15m@1.1.0",
    ),
    timing_manifest.DecisionTimingVariant(
        key="20m",
        decision_bar_number=4,
        decision_offset_minutes=20,
        decision_bar_timestamp="09:45",
        decision_available_at="09:50",
        strategy_version="manual-regime-top50-ruleset-v1-decision-20m",
        strategy_variant_id="manual-regime-decision-20m@1.1.0",
    ),
    timing_manifest.DecisionTimingVariant(
        key="30m",
        decision_bar_number=6,
        decision_offset_minutes=30,
        decision_bar_timestamp="09:55",
        decision_available_at="10:00",
        strategy_version="manual-regime-top50-ruleset-v1-decision-30m",
        strategy_variant_id="manual-regime-decision-30m@1.1.0",
    ),
)
TIMING_VARIANTS = {
    **{variant.key: variant for variant in RESEARCH_TIMING_VARIANTS},
    **timing_manifest.VARIANTS,
}


def build_common_input_manifest(
    symbols: list[str],
    end_exclusive: pd.Timestamp,
) -> dict[str, Any]:
    files = {
        "frozen_baseline_engine": BASELINE_ENGINE_PATH,
        "timing_engine": TIMING_ENGINE_PATH,
        "baseline_version_manifest": ENGINE_ROOT / "manual_regime_version_manifest.py",
        "timing_version_manifest": TIMING_MANIFEST_PATH,
        "variant_filter_adapter": ENGINE_ROOT / "manual_regime_variant_filters.py",
        "decision_records": ENGINE_ROOT / "decision_records.py",
        "live_pipeline": ENGINE_ROOT / "live_manual_regime_tws_bot.py",
        "live_config": ENGINE_ROOT / "current_live_baseline_config.py",
        "symbols": live_config.OPTIMIZED_LIVE_TOP50_PATH,
        "symbol_rule_pairs": live_config.LIVE_PAIR_SELECTION_PATH,
        "rule_stack": ENGINE_ROOT / "current_baseline_rule_stack_2026_07_04.json",
    }
    raw_files = {symbol: rules.RAW_DIR / f"Rohdaten_{symbol}.csv" for symbol in symbols}
    missing = [str(path) for path in (*files.values(), *raw_files.values()) if not path.exists()]
    if missing:
        raise FileNotFoundError(f"Missing decision-time variant inputs: {missing}")
    actual_frozen_hash = baseline_runner.sha256_file(BASELINE_ENGINE_PATH)
    if actual_frozen_hash != baseline_manifest.ENGINE_CODE_SHA256:
        raise RuntimeError(
            "Frozen V1 engine changed: "
            f"manifest={baseline_manifest.ENGINE_CODE_SHA256} actual={actual_frozen_hash}"
        )
    payload = {
        "period_start": PERIOD_START.date().isoformat(),
        "period_end_exclusive": end_exclusive.date().isoformat(),
        "warmup_start": WARMUP_START,
        "symbols": symbols,
        "live_variant": live_config.LIVE_VARIANT,
        "risk_per_trade": RISK_PER_TRADE,
        "files": {
            name: {"path": str(path), "sha256": baseline_runner.sha256_file(path)}
            for name, path in files.items()
        },
        "raw_data": {
            symbol: {
                "path": str(path),
                "size": path.stat().st_size,
                "sha256": baseline_runner.sha256_file(path),
            }
            for symbol, path in raw_files.items()
        },
    }
    payload["data_revision"] = baseline_runner.stable_payload_hash(payload)
    return payload


def run_symbol(
    symbol: str,
    log_dir: str,
    end_exclusive: str,
    variant_key: str,
) -> tuple[str, pd.DataFrame, list[decision_model.DecisionRecord]]:
    variant = TIMING_VARIANTS[variant_key]
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
        decision_bar_number=variant.decision_bar_number,
        strategy_version=variant.strategy_version,
        decision_version_context=variant.version_context(),
    )
    openings = [
        record
        for record in records
        if record.status == "accepted" and record.trade_action == "trade"
    ]
    if not completed.empty:
        completed = completed.copy()
        completed["symbol"] = symbol
        completed = decision_model.attach_record_keys_to_trades(completed, openings)
    return symbol, completed, openings


def symbol_cache_expectation(
    symbol: str,
    variant: timing_manifest.DecisionTimingVariant,
    common_inputs: dict[str, Any],
    end_exclusive: pd.Timestamp,
) -> dict[str, Any]:
    engine_settings = {
        "warmup_start": WARMUP_START,
        "end_exclusive": end_exclusive.date().isoformat(),
        "structure_lookback": 10,
        "bull_continuation_mode": "combo",
        "learn_long_mode": "off",
        "structural_reset_mode": "off",
        "max_history_days": 548,
        "decision_bar_number": variant.decision_bar_number,
        "strategy_version": variant.strategy_version,
        "strategy_variant_id": variant.strategy_variant_id,
    }
    raw = common_inputs["raw_data"][symbol]
    return {
        "cache_schema_version": "manual-regime-symbol-cache-v1",
        "symbol": symbol,
        "strategy_variant_id": variant.strategy_variant_id,
        "variant_manifest_hash": timing_manifest.manifest_hash(variant),
        "timing_engine_sha256": common_inputs["files"]["timing_engine"]["sha256"],
        "data_revision": common_inputs["data_revision"],
        "end_exclusive": end_exclusive.date().isoformat(),
        "symbol_raw_sha256": raw["sha256"],
        "symbol_raw_size": raw["size"],
        "engine_settings_hash": baseline_runner.stable_payload_hash(engine_settings),
    }


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="wb",
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
        delete=False,
    )
    temp_path = Path(handle.name)
    try:
        with handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def write_symbol_cache(
    cache_dir: Path,
    *,
    expectation: dict[str, Any],
    completed: pd.DataFrame,
    openings: list[decision_model.DecisionRecord],
) -> None:
    completed_payload = completed.to_csv(index=False, lineterminator="\n").encode("utf-8")
    openings_payload = (
        "\n".join(record.to_json() for record in openings) + ("\n" if openings else "")
    ).encode("utf-8")
    completed_path = cache_dir / "completed.csv"
    openings_path = cache_dir / "openings.jsonl"
    manifest_path = cache_dir / "cache_manifest.json"
    _atomic_write_bytes(completed_path, completed_payload)
    _atomic_write_bytes(openings_path, openings_payload)
    manifest = {
        **expectation,
        "completed_sha256": hashlib.sha256(completed_payload).hexdigest(),
        "openings_sha256": hashlib.sha256(openings_payload).hexdigest(),
        "completed_count": int(len(completed)),
        "opening_count": int(len(openings)),
        "completed_columns": list(completed.columns),
    }
    _atomic_write_bytes(
        manifest_path,
        json.dumps(
            manifest,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8"),
    )


def load_valid_symbol_cache(
    cache_dir: Path,
    *,
    expectation: dict[str, Any],
) -> tuple[pd.DataFrame, list[decision_model.DecisionRecord]] | None:
    manifest_path = cache_dir / "cache_manifest.json"
    completed_path = cache_dir / "completed.csv"
    openings_path = cache_dir / "openings.jsonl"
    if not manifest_path.is_file():
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if any(manifest.get(key) != value for key, value in expectation.items()):
            return None
        if not completed_path.is_file() or not openings_path.is_file():
            return None
        completed_payload = completed_path.read_bytes()
        openings_payload = openings_path.read_bytes()
        if hashlib.sha256(completed_payload).hexdigest() != manifest.get("completed_sha256"):
            return None
        if hashlib.sha256(openings_payload).hexdigest() != manifest.get("openings_sha256"):
            return None
        columns = list(manifest.get("completed_columns", []))
        if completed_payload.strip():
            completed = pd.read_csv(completed_path, float_precision="round_trip")
        else:
            completed = pd.DataFrame(columns=columns)
        for name in ("entry_date", "exit_date"):
            if name in completed.columns:
                completed[name] = pd.to_datetime(completed[name])
        opening_lines = openings_payload.decode("utf-8").splitlines()
        openings = [decision_model.DecisionRecord.from_json(line) for line in opening_lines]
        if len(completed) != int(manifest.get("completed_count", -1)):
            return None
        if len(openings) != int(manifest.get("opening_count", -1)):
            return None
        symbol = str(expectation["symbol"])
        variant_id = str(expectation["strategy_variant_id"])
        if any(record.symbol != symbol for record in openings):
            return None
        if any(record.strategy_variant_id != variant_id for record in openings):
            return None
        if "symbol" in completed.columns and not completed.empty:
            if set(completed["symbol"].astype(str)) != {symbol}:
                return None
        return completed, openings
    except (OSError, ValueError, TypeError, json.JSONDecodeError, UnicodeDecodeError):
        return None


def local_day(record: decision_model.DecisionRecord) -> pd.Timestamp:
    return (
        pd.Timestamp(record.decision_time_utc)
        .tz_convert(decision_model.DECISION_TIMEZONE)
        .tz_localize(None)
        .normalize()
    )


def causal_model_history(
    history_rows: list[dict[str, Any]],
    day: pd.Timestamp,
    strategy_variant_id: str,
) -> ModelHistorySnapshot:
    window_start = day.normalize() - pd.Timedelta(days=2)
    window_end = day.normalize()
    recent = [
        row
        for row in history_rows
        if window_start <= pd.Timestamp(row["exit_date"]) < window_end
        and str(row["rule"]) in live.CORE_SHORT_RULES
    ]

    def utc_text(value: pd.Timestamp) -> str:
        timestamp = pd.Timestamp(value)
        if timestamp.tzinfo is None:
            timestamp = timestamp.tz_localize(decision_model.DECISION_TIMEZONE)
        return timestamp.tz_convert("UTC").isoformat().replace("+00:00", "Z")

    trades = tuple(
        ModelHistoryTrade(
            signal_key=str(row["signal_key"]),
            run_id=f"causal-{strategy_variant_id}-backtest",
            run_type="backtest",
            rule_id=str(row["rule"]),
            closed_at_utc=utc_text(pd.Timestamp(row["exit_date"])),
            realized_pnl=float(row["pnl_dollars"]),
        )
        for row in sorted(
            recent,
            key=lambda item: (pd.Timestamp(item["exit_date"]), str(item["signal_key"])),
        )
    )
    return ModelHistorySnapshot(
        database_path="in-memory:causal-backtest-history",
        strategy_variant_id=strategy_variant_id,
        selected_run_id=f"causal-{strategy_variant_id}-backtest",
        run_selection="in_memory_snapshot",
        window_start_utc=utc_text(window_start),
        window_end_utc=utc_text(window_end),
        trades=trades,
        source_candidate_count=len(trades),
        duplicate_signal_count=0,
        run_candidate_count=1,
        data_quality_status="complete_empty_window" if not trades else "complete",
    )


def apply_portfolio_pipeline(
    openings: list[decision_model.DecisionRecord],
    completed: pd.DataFrame,
    keep_pairs: set[tuple[str, str]],
    variant: timing_manifest.DecisionTimingVariant,
    end_exclusive: pd.Timestamp,
) -> tuple[list[decision_model.DecisionRecord], pd.DataFrame, pd.DataFrame]:
    period_records = [
        record
        for record in openings
        if PERIOD_START <= local_day(record) < end_exclusive
    ]
    by_day: dict[pd.Timestamp, list[decision_model.DecisionRecord]] = {}
    for record in period_records:
        by_day.setdefault(local_day(record), []).append(record)

    completed = completed.copy()
    if completed.empty:
        completed = pd.DataFrame(columns=["signal_key"])
    else:
        completed["entry_date"] = pd.to_datetime(completed["entry_date"])
        completed["exit_date"] = pd.to_datetime(completed["exit_date"])
        completed = completed[
            (completed["entry_date"] >= PERIOD_START)
            & (completed["entry_date"] < end_exclusive)
        ].copy()
    closed_by_key = {
        str(row.signal_key): pd.Series(row._asdict())
        for row in completed.itertuples(index=False)
    }

    history_rows: list[dict[str, Any]] = []
    final_records: list[decision_model.DecisionRecord] = []
    model_rows: list[dict[str, Any]] = []
    executable_rows: list[dict[str, Any]] = []

    for day in sorted(by_day):
        day_records = by_day[day]
        raw = pd.DataFrame([baseline_runner.opening_row(record) for record in day_records])
        filtered_records: list[decision_model.DecisionRecord] = []
        history = causal_model_history(
            history_rows, day, variant.strategy_variant_id
        )
        surviving = variant_filters.apply_live_baseline_filters_for_variant(
            raw,
            keep_pairs,
            day,
            strategy_variant_id=variant.strategy_variant_id,
            decision_records=day_records,
            final_records_out=filtered_records,
            model_history=history,
        )
        surviving_keys = set(surviving.get("signal_key", pd.Series(dtype=str)).astype(str))
        filtered_by_key = {record.signal_key: record for record in filtered_records}

        for record in day_records:
            filtered = filtered_by_key[record.signal_key]
            if record.signal_key not in surviving_keys:
                final_records.append(filtered)
                continue
            closed = closed_by_key.get(record.signal_key)
            opening = pd.Series(baseline_runner.opening_row(record))
            qty = baseline_runner.quantity(opening)
            routable = record.symbol not in live.NON_ROUTABLE and qty > 0
            reason = (
                "production_routable"
                if routable
                else (
                    "non_routable_symbol"
                    if record.symbol in live.NON_ROUTABLE
                    else "non_positive_risk_quantity"
                )
            )
            final_records.append(
                baseline_runner.routing_record(
                    filtered, accepted=routable, reason=reason, quantity=qty
                )
            )
            if closed is None:
                continue
            model = closed.to_dict()
            model["quantity"] = qty
            model["qty"] = qty
            model["risk_amount"] = RISK_PER_TRADE * float(opening.get("size", 1.0))
            model["pnl_dollars"] = float(model["points"]) * qty
            model["realized_pnl"] = model["pnl_dollars"]
            model["asset_class"] = "IND" if record.symbol == "SPX" else "STK"
            model_rows.append(model)
            if not routable:
                continue
            executable_rows.append(model)
            history_rows.append(
                {
                    "signal_key": model["signal_key"],
                    "entry_date": model["entry_date"],
                    "exit_date": model["exit_date"],
                    "rule": model["rule"],
                    "pnl_dollars": model["pnl_dollars"],
                }
            )

    model_frame = pd.DataFrame(model_rows)
    executable_frame = pd.DataFrame(executable_rows)
    for frame in (model_frame, executable_frame):
        if not frame.empty:
            frame.sort_values(
                ["exit_date", "entry_date", "symbol", "rule"], inplace=True
            )
            frame.reset_index(drop=True, inplace=True)
    return final_records, model_frame, executable_frame


def write_curve(curve: pd.DataFrame, path: Path, title: str, color: str) -> None:
    fig, ax = plt.subplots(figsize=(14, 7))
    if not curve.empty:
        ax.plot(curve["exit_date"], curve["equity"], linewidth=2.0, color=color)
    ax.axhline(START_CAPITAL, color="#6b7280", linestyle="--", linewidth=1.0)
    ax.set_title(title)
    ax.set_xlabel("Exit date")
    ax.set_ylabel("Equity (USD)")
    ax.grid(True, alpha=0.25)
    fig.tight_layout()
    fig.savefig(path, dpi=170)
    plt.close(fig)


def expected_variant_run(
    variant: timing_manifest.DecisionTimingVariant,
    *,
    symbols: list[str],
    keep_pairs: set[tuple[str, str]],
    common_inputs: dict[str, Any],
    end_exclusive: pd.Timestamp,
    workers: int,
) -> tuple[Path, str, str, dict[str, Any], dict[str, Any]]:
    end_inclusive = end_exclusive - pd.Timedelta(days=1)
    artifact_dir = (
        ARTIFACT_ROOT
        / variant.strategy_variant_id
        / f"{PERIOD_START:%Y-%m-%d}_{end_inclusive:%Y-%m-%d}"
    )
    strategy_manifest = timing_manifest.manifest_payload(variant)
    config_payload = {
        "manifest_hash": timing_manifest.manifest_hash(variant),
        "data_revision": common_inputs["data_revision"],
        "period_start": PERIOD_START.date().isoformat(),
        "period_end_exclusive": end_exclusive.date().isoformat(),
        "warmup_start": WARMUP_START,
        "symbols": symbols,
        "keep_pair_count": len(keep_pairs),
        "live_variant": live_config.LIVE_VARIANT,
        "risk_per_trade": RISK_PER_TRADE,
        "non_routable_symbols": sorted(live.NON_ROUTABLE),
        "fee_model": "zero@1.0.0",
        "slippage_model": "zero@1.0.0",
        "decision_bar_number": variant.decision_bar_number,
        "decision_offset_minutes": variant.decision_offset_minutes,
        "decision_bar_timestamp": variant.decision_bar_timestamp,
        "decision_available_at": variant.decision_available_at,
        "workers": workers,
    }
    config_hash = baseline_runner.stable_payload_hash(config_payload)
    run_id = (
        f"backtest:{variant.strategy_variant_id}:"
        f"{PERIOD_START:%Y%m%d}-{end_inclusive:%Y%m%d}:"
        f"{common_inputs['data_revision'][:12]}"
    )
    return artifact_dir, run_id, config_hash, config_payload, strategy_manifest


def load_valid_existing_run(
    artifact_dir: Path,
    *,
    expected_run_id: str,
    expected_config_hash: str,
    expected_data_revision: str,
    expected_variant_id: str,
    expected_manifest_hash: str,
) -> dict[str, Any] | None:
    manifest_path = artifact_dir / "run_manifest.json"
    if not manifest_path.exists():
        return None
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected_fields = {
        "run_id": expected_run_id,
        "config_hash": expected_config_hash,
        "data_revision": expected_data_revision,
        "strategy_variant_id": expected_variant_id,
    }
    mismatches = {
        name: {"expected": expected, "actual": manifest.get(name)}
        for name, expected in expected_fields.items()
        if manifest.get(name) != expected
    }
    actual_manifest_hash = (
        manifest.get("strategy_manifest", {})
        .get("decision_timing", {})
    )
    if manifest.get("config", {}).get("manifest_hash") != expected_manifest_hash:
        mismatches["strategy_manifest_hash"] = {
            "expected": expected_manifest_hash,
            "actual": manifest.get("config", {}).get("manifest_hash"),
            "decision_timing": actual_manifest_hash,
        }
    if mismatches:
        raise RuntimeError(
            f"Existing run manifest is not reusable at {manifest_path}: {mismatches}"
        )

    required = (
        "trades.csv",
        "model_trades_including_non_routable.csv",
        "equity_curve.csv",
        "model_equity_curve_including_non_routable.csv",
        "equity_curve.png",
        "model_equity_curve_including_non_routable.png",
        "decision_records.jsonl",
        "strategy_manifest.json",
    )
    missing = [name for name in required if not (artifact_dir / name).is_file()]
    if missing:
        raise RuntimeError(f"Reusable run lacks required artifacts: {missing}")
    for name, expected_hash in manifest.get("artifact_hashes", {}).items():
        path = artifact_dir / name
        if not path.is_file():
            raise RuntimeError(f"Hashed reusable artifact is missing: {path}")
        actual_hash = baseline_runner.sha256_file(path)
        if actual_hash != expected_hash:
            raise RuntimeError(
                f"Reusable artifact hash mismatch for {path}: "
                f"expected={expected_hash} actual={actual_hash}"
            )
    if not manifest.get("artifact_hashes"):
        raise RuntimeError(f"Reusable run has no artifact hashes: {manifest_path}")
    return {"artifact_directory": str(artifact_dir), **manifest}


def run_variant(
    variant: timing_manifest.DecisionTimingVariant,
    *,
    symbols: list[str],
    keep_pairs: set[tuple[str, str]],
    common_inputs: dict[str, Any],
    end_exclusive: pd.Timestamp,
    workers: int,
    reuse_existing: bool,
    reuse_symbol_cache: bool,
) -> dict[str, Any]:
    end_inclusive = end_exclusive - pd.Timedelta(days=1)
    (
        artifact_dir,
        run_id,
        config_hash,
        config_payload,
        variant_manifest,
    ) = expected_variant_run(
        variant,
        symbols=symbols,
        keep_pairs=keep_pairs,
        common_inputs=common_inputs,
        end_exclusive=end_exclusive,
        workers=workers,
    )
    artifact_dir.mkdir(parents=True, exist_ok=True)
    log_dir = artifact_dir / "engine_logs"
    log_dir.mkdir(exist_ok=True)
    manifest_path = artifact_dir / "run_manifest.json"
    manifest_hash = timing_manifest.manifest_hash(variant)
    if reuse_existing:
        reusable = load_valid_existing_run(
            artifact_dir,
            expected_run_id=run_id,
            expected_config_hash=config_hash,
            expected_data_revision=str(common_inputs["data_revision"]),
            expected_variant_id=variant.strategy_variant_id,
            expected_manifest_hash=manifest_hash,
        )
        if reusable is not None:
            print(
                f"reuse {variant.key}: validated existing run {run_id} at {artifact_dir}",
                flush=True,
            )
            return reusable
    previous = (
        json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest_path.exists()
        else None
    )
    if previous and previous.get("run_id") != run_id:
        raise RuntimeError(
            f"Immutable variant artifact directory already belongs to {previous.get('run_id')}"
        )
    started_at = (
        previous["started_at_utc"]
        if previous
        else datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    )

    completed_parts: list[pd.DataFrame] = []
    opening_records: list[decision_model.DecisionRecord] = []
    cache_root = artifact_dir / "symbol_cache"
    missing_symbols: list[str] = []
    cached_count = 0
    for symbol in symbols:
        expectation = symbol_cache_expectation(
            symbol, variant, common_inputs, end_exclusive
        )
        cached = (
            load_valid_symbol_cache(
                cache_root / symbol,
                expectation=expectation,
            )
            if reuse_symbol_cache
            else None
        )
        if cached is None:
            missing_symbols.append(symbol)
            continue
        completed, openings = cached
        cached_count += 1
        if not completed.empty:
            completed_parts.append(completed)
        opening_records.extend(openings)
        print(
            f"{variant.key} cache {cached_count}/{len(symbols)} {symbol}: "
            f"completed={len(completed)} openings={len(openings)}",
            flush=True,
        )
    if reuse_symbol_cache:
        print(
            f"{variant.key} symbol cache: valid={cached_count} "
            f"missing_or_invalid={len(missing_symbols)}",
            flush=True,
        )

    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(
                run_symbol,
                symbol,
                str(log_dir),
                end_exclusive.date().isoformat(),
                variant.key,
            ): symbol
            for symbol in missing_symbols
        }
        done = cached_count
        for future in as_completed(futures):
            symbol, completed, openings = future.result()
            write_symbol_cache(
                cache_root / symbol,
                expectation=symbol_cache_expectation(
                    symbol, variant, common_inputs, end_exclusive
                ),
                completed=completed,
                openings=openings,
            )
            done += 1
            print(
                f"{variant.key} engine {done}/{len(symbols)} {symbol}: "
                f"completed={len(completed)} openings={len(openings)}",
                flush=True,
            )
            if not completed.empty:
                completed_parts.append(completed)
            opening_records.extend(openings)

    all_completed = (
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
    final_records, model_trades, executable_trades = apply_portfolio_pipeline(
        opening_records,
        all_completed,
        keep_pairs,
        variant,
        end_exclusive,
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
    model_metrics, model_curve = baseline_runner.metrics_and_curve(model_trades)
    execution_metrics, execution_curve = baseline_runner.metrics_and_curve(executable_trades)

    observed_times = {
        pd.Timestamp(record.decision_time_utc)
        .tz_convert(decision_model.DECISION_TIMEZONE)
        .strftime("%H:%M")
        for record in final_records
    }
    if observed_times != {variant.decision_bar_timestamp}:
        raise RuntimeError(
            f"Unexpected {variant.key} decision timestamps: {sorted(observed_times)}"
        )
    if set(executable_trades.get("symbol", pd.Series(dtype=str)).astype(str)) - set(symbols):
        raise RuntimeError("Variant produced trades outside the frozen Top50 basket")

    completed_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    executable_trades.to_csv(artifact_dir / "trades.csv", index=False)
    model_trades.to_csv(
        artifact_dir / "model_trades_including_non_routable.csv", index=False
    )
    execution_curve.to_csv(artifact_dir / "equity_curve.csv", index=False)
    model_curve.to_csv(
        artifact_dir / "model_equity_curve_including_non_routable.csv", index=False
    )
    decision_model.write_jsonl(artifact_dir / "decision_records.jsonl", final_records)
    (artifact_dir / "strategy_manifest.json").write_text(
        json.dumps(variant_manifest, indent=2, ensure_ascii=False, allow_nan=False),
        encoding="utf-8",
    )
    color = {
        "5m": "#d97706",
        "10m": "#7c3aed",
        "15m": "#16a34a",
        "20m": "#0891b2",
        "30m": "#a16207",
        "60m": "#2563eb",
        "90m": "#dc2626",
    }[variant.key]
    write_curve(
        execution_curve,
        artifact_dir / "equity_curve.png",
        f"Manual-Regime {variant.key} decision ({PERIOD_START:%Y-%m-%d} through {end_inclusive:%Y-%m-%d})",
        color,
    )
    write_curve(
        model_curve,
        artifact_dir / "model_equity_curve_including_non_routable.png",
        f"Manual-Regime {variant.key} model signals incl. non-routable",
        color,
    )
    artifact_hashes = {
        name: baseline_runner.sha256_file(artifact_dir / name)
        for name in (
            "trades.csv",
            "equity_curve.csv",
            "equity_curve.png",
            "decision_records.jsonl",
            "strategy_manifest.json",
        )
    }
    manifest = {
        "run_id": run_id,
        "strategy_variant_id": variant.strategy_variant_id,
        "started_at_utc": started_at,
        "completed_at_utc": completed_at,
        "config_hash": config_hash,
        "data_revision": common_inputs["data_revision"],
        "runner_sha256": baseline_runner.sha256_file(Path(__file__).resolve()),
        "artifact_hashes": artifact_hashes,
        "input_manifest": common_inputs,
        "strategy_manifest": variant_manifest,
        "config": config_payload,
        "decision_record_count": len(final_records),
        "execution_metrics": execution_metrics,
        "model_metrics_including_non_routable": model_metrics,
    }
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False, allow_nan=False),
        encoding="utf-8",
    )
    print(json.dumps({"artifact_directory": str(artifact_dir), **manifest}, indent=2), flush=True)
    return {"artifact_directory": str(artifact_dir), **manifest}


def comparison_metrics_row(
    label: str,
    variant_id: str,
    metrics: dict[str, Any],
    control_metrics: dict[str, Any],
) -> dict[str, Any]:
    return {
        "label": label,
        "strategy_variant_id": variant_id,
        **metrics,
        "delta_net_pnl_vs_10m_control": float(metrics["net_pnl_dollars"])
        - float(control_metrics["net_pnl_dollars"]),
        "delta_ending_equity_vs_10m_control": float(metrics["ending_equity"])
        - float(control_metrics["ending_equity"]),
        "delta_profit_factor_vs_10m_control": float(metrics["profit_factor"])
        - float(control_metrics["profit_factor"]),
        "delta_max_drawdown_vs_10m_control": float(metrics["max_drawdown_dollars"])
        - float(control_metrics["max_drawdown_dollars"]),
    }


def write_comparison(
    run_manifests: list[dict[str, Any]],
    end_exclusive: pd.Timestamp,
) -> Path:
    end_inclusive = end_exclusive - pd.Timedelta(days=1)
    comparison_dir = (
        COMPARISON_ROOT / f"{PERIOD_START:%Y-%m-%d}_{end_inclusive:%Y-%m-%d}"
    )
    comparison_dir.mkdir(parents=True, exist_ok=True)
    baseline_dir = (
        ARTIFACT_ROOT
        / BASELINE_VARIANT_ID
        / f"{PERIOD_START:%Y-%m-%d}_{end_inclusive:%Y-%m-%d}"
    )
    baseline_manifest_path = baseline_dir / "run_manifest.json"
    baseline_curve_path = baseline_dir / "equity_curve.csv"
    if not baseline_manifest_path.exists() or not baseline_curve_path.exists():
        raise FileNotFoundError(
            f"Matching frozen 10m baseline is missing: {baseline_manifest_path}"
        )
    baseline_run = json.loads(baseline_manifest_path.read_text(encoding="utf-8"))
    baseline_metrics = baseline_run["execution_metrics"]
    baseline_curve = pd.read_csv(baseline_curve_path, parse_dates=["exit_date"])

    runs_by_key: dict[str, dict[str, Any]] = {}
    for run in run_manifests:
        variant_id = str(run["strategy_variant_id"])
        if "decision-5m@" in variant_id:
            key = "5m"
        elif "decision-15m@" in variant_id:
            key = "15m"
        elif "decision-20m@" in variant_id:
            key = "20m"
        elif "decision-30m@" in variant_id:
            key = "30m"
        elif "10m-control" in variant_id:
            key = "10m"
        elif "60m" in variant_id:
            key = "60m"
        elif "90m" in variant_id:
            key = "90m"
        else:
            raise ValueError(f"Unknown timing variant id: {variant_id}")
        runs_by_key[key] = run
    required_keys = {"5m", "10m", "15m", "20m", "30m", "60m", "90m"}
    if set(runs_by_key) != required_keys:
        raise RuntimeError(
            "Comparison requires 5m/10m/15m/20m/30m/60m/90m runs, "
            f"got {sorted(runs_by_key)}"
        )
    control_metrics = runs_by_key["10m"]["execution_metrics"]

    curves: list[tuple[str, pd.DataFrame, str, str]] = []
    rows: list[dict[str, Any]] = []
    variant_labels = {
        "5m": "5m (09:30 bar / 09:35 available)",
        "10m": "10m normalized control (09:35 bar / 09:40 available)",
        "15m": "15m (09:40 bar / 09:45 available)",
        "20m": "20m (09:45 bar / 09:50 available)",
        "30m": "30m (09:55 bar / 10:00 available)",
        "60m": "60m (10:25 bar / 10:30 available)",
        "90m": "90m (10:55 bar / 11:00 available)",
    }
    variant_colors = {
        "5m": "#d97706",
        "10m": "#7c3aed",
        "15m": "#16a34a",
        "20m": "#0891b2",
        "30m": "#a16207",
        "60m": "#2563eb",
        "90m": "#dc2626",
    }
    for key in ("5m", "10m", "15m", "20m", "30m", "60m", "90m"):
        run = runs_by_key[key]
        curve = pd.read_csv(
            Path(run["artifact_directory"]) / "equity_curve.csv",
            parse_dates=["exit_date"],
        )
        curves.append((variant_labels[key], curve, variant_colors[key], "-"))
        rows.append(
            comparison_metrics_row(
                f"{key} normalized decision",
                str(run["strategy_variant_id"]),
                run["execution_metrics"],
                control_metrics,
            )
        )
    rows.append(
        comparison_metrics_row(
            "Frozen production V1.0.0 reference",
            BASELINE_VARIANT_ID,
            baseline_metrics,
            control_metrics,
        )
    )

    fig, ax = plt.subplots(figsize=(15, 8))
    for label, curve, color, linestyle in curves:
        ax.plot(
            curve["exit_date"],
            curve["equity"],
            linewidth=2.0,
            label=label,
            color=color,
            linestyle=linestyle,
        )
    ax.axhline(START_CAPITAL, color="#6b7280", linestyle="--", linewidth=1.0)
    ax.set_title(
        "Manual-Regime normalized decision-time comparison: "
        "5 vs 10 vs 15 vs 20 vs 30 vs 60 vs 90 minutes"
    )
    ax.set_xlabel("Exit date")
    ax.set_ylabel("Equity (USD)")
    ax.grid(True, alpha=0.25)
    ax.legend(loc="upper left")
    fig.tight_layout()
    plot_path = (
        comparison_dir
        / "equity_comparison_v1_5m_10m_15m_20m_30m_60m_90m.png"
    )
    fig.savefig(plot_path, dpi=180)
    plt.close(fig)

    metrics_frame = pd.DataFrame(rows)
    metrics_path = comparison_dir / "comparison_metrics.csv"
    metrics_frame.to_csv(metrics_path, index=False)
    comparison_manifest = {
        "created_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "period_start": PERIOD_START.date().isoformat(),
        "period_end_inclusive": end_inclusive.date().isoformat(),
        "baseline_run_id": baseline_run["run_id"],
        "normalized_control_run_id": runs_by_key["10m"]["run_id"],
        "variant_run_ids": [run["run_id"] for run in run_manifests],
        "metrics": rows,
        "artifacts": {
            "plot": str(plot_path),
            "metrics_csv": str(metrics_path),
        },
        "methodology": {
            "basket": "same frozen Top50 basket",
            "keep_pair_count": 318,
            "risk_per_trade": RISK_PER_TRADE,
            "fee_model": "zero@1.0.0",
            "slippage_model": "zero@1.0.0",
            "portfolio_vetos": "same baseline weak-retest and causal LHF loss-cluster veto",
            "timestamp_semantics": "bar-start timestamp; signal can be acted upon after five-minute bar close",
            "changed_causal_inputs": (
                "later decision price/bar and larger pre-decision intraday "
                "value-profile/price-acceptance context"
            ),
            "frozen_v1_reference_caveat": (
                "Frozen V1 derives the decision timestamp from the first raw-data day. "
                "CEG therefore uses 10:25 rather than 09:35; it is shown only as a "
                "legacy reference. Timing deltas use the normalized 10m control."
            ),
        },
    }
    (comparison_dir / "comparison_manifest.json").write_text(
        json.dumps(comparison_manifest, indent=2, ensure_ascii=False, allow_nan=False),
        encoding="utf-8",
    )
    return comparison_dir


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=24)
    parser.add_argument(
        "--end-exclusive",
        default=DEFAULT_END_EXCLUSIVE.date().isoformat(),
    )
    parser.add_argument(
        "--variants",
        nargs="+",
        choices=sorted(TIMING_VARIANTS),
        default=sorted(TIMING_VARIANTS),
    )
    parser.add_argument(
        "--reuse-existing",
        action="store_true",
        help="validate and reuse complete variant artifacts instead of recomputing them",
    )
    parser.add_argument(
        "--reuse-symbol-cache",
        action="store_true",
        help=(
            "validate per-symbol engine caches and submit only missing/invalid symbols; "
            "newly completed symbols are always cached atomically"
        ),
    )
    args = parser.parse_args()
    if args.workers < 1:
        raise ValueError("--workers must be positive")
    end_exclusive = pd.Timestamp(args.end_exclusive).normalize()
    if end_exclusive <= PERIOD_START:
        raise ValueError("--end-exclusive must be after period start")

    symbols = live_config.load_optimized_live_top50_symbols()
    keep_pairs = live_config.load_live_keep_pairs()
    if len(symbols) != 50:
        raise RuntimeError(f"Expected frozen Top50 basket, got {len(symbols)} symbols")
    if len(keep_pairs) != 318:
        raise RuntimeError(f"Expected 318 frozen keep-pairs, got {len(keep_pairs)}")
    pair_symbols = {symbol for symbol, _rule in keep_pairs}
    if set(symbols) != pair_symbols:
        raise RuntimeError(
            "Top50 basket and keep-pairs disagree: "
            f"missing={sorted(set(symbols) - pair_symbols)} "
            f"extra={sorted(pair_symbols - set(symbols))}"
        )

    print("Hashing reproducibility inputs once for all timing variants...", flush=True)
    common_inputs = build_common_input_manifest(symbols, end_exclusive)
    manifests = [
        run_variant(
            TIMING_VARIANTS[key],
            symbols=symbols,
            keep_pairs=keep_pairs,
            common_inputs=common_inputs,
            end_exclusive=end_exclusive,
            workers=args.workers,
            reuse_existing=args.reuse_existing,
            reuse_symbol_cache=args.reuse_symbol_cache,
        )
        for key in args.variants
    ]
    manifests_by_id = {run["strategy_variant_id"]: run for run in manifests}
    for variant in TIMING_VARIANTS.values():
        if variant.strategy_variant_id in manifests_by_id:
            continue
        (
            artifact_dir,
            run_id,
            config_hash,
            _config_payload,
            _strategy_manifest,
        ) = expected_variant_run(
            variant,
            symbols=symbols,
            keep_pairs=keep_pairs,
            common_inputs=common_inputs,
            end_exclusive=end_exclusive,
            workers=args.workers,
        )
        existing = load_valid_existing_run(
            artifact_dir,
            expected_run_id=run_id,
            expected_config_hash=config_hash,
            expected_data_revision=str(common_inputs["data_revision"]),
            expected_variant_id=variant.strategy_variant_id,
            expected_manifest_hash=timing_manifest.manifest_hash(variant),
        )
        if existing is not None:
            manifests_by_id[variant.strategy_variant_id] = existing
    if len(manifests_by_id) == len(TIMING_VARIANTS):
        comparison_dir = write_comparison(list(manifests_by_id.values()), end_exclusive)
        print(f"comparison artifacts: {comparison_dir}", flush=True)


if __name__ == "__main__":
    main()
