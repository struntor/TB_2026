from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sys
from typing import Any

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
for path in (ENGINE_ROOT, BOT_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import current_live_baseline_config as live_config
import decision_records as decision_model
import live_manual_regime_tws_bot as live
import manual_regime_bot_api as executor_api
import manual_regime_rules_backtest as rules
import manual_regime_version_manifest as version_manifest
from manual_regime_journal_registry import register_baseline_journal_manifest
from src.journal.backtest_ingest import (
    BacktestJournalIngestor,
    BacktestRunSpec,
    BacktestTradeDefaults,
)
from src.journal.realized_history import ModelHistorySnapshot, ModelHistoryTrade
from src.journal.store import DEFAULT_JOURNAL_PATH, JournalStore


WARMUP_START = "2023-01-01"
PERIOD_START = pd.Timestamp("2024-01-01")
END_EXCLUSIVE = pd.Timestamp("2026-07-01")
START_CAPITAL = 10_000.0
RISK_PER_TRADE = 100.0
ARTIFACT_ROOT = Path(r"D:\Trading\journal\baselines")
ARTIFACT_DIR = ARTIFACT_ROOT / version_manifest.STRATEGY_VARIANT_ID / "2024-01-01_2026-06-30"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_payload_hash(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_input_manifest(symbols: list[str]) -> dict[str, Any]:
    files = {
        "engine": ENGINE_ROOT / "manual_regime_rules_backtest.py",
        "decision_records": ENGINE_ROOT / "decision_records.py",
        "live_pipeline": ENGINE_ROOT / "live_manual_regime_tws_bot.py",
        "live_config": ENGINE_ROOT / "current_live_baseline_config.py",
        "symbols": live_config.OPTIMIZED_LIVE_TOP50_PATH,
        "symbol_rule_pairs": live_config.LIVE_PAIR_SELECTION_PATH,
        "rule_stack": ENGINE_ROOT / "current_baseline_rule_stack_2026_07_04.json",
    }
    raw_files = {
        symbol: rules.RAW_DIR / f"Rohdaten_{symbol}.csv" for symbol in symbols
    }
    missing = [str(path) for path in (*files.values(), *raw_files.values()) if not path.exists()]
    if missing:
        raise FileNotFoundError(f"Missing baseline inputs: {missing}")
    payload = {
        "period_start": PERIOD_START.date().isoformat(),
        "period_end_exclusive": END_EXCLUSIVE.date().isoformat(),
        "warmup_start": WARMUP_START,
        "symbols": symbols,
        "live_variant": live_config.LIVE_VARIANT,
        "risk_per_trade": RISK_PER_TRADE,
        "files": {
            name: {"path": str(path), "sha256": sha256_file(path)}
            for name, path in files.items()
        },
        "raw_data": {
            symbol: {
                "path": str(path),
                "size": path.stat().st_size,
                "sha256": sha256_file(path),
            }
            for symbol, path in raw_files.items()
        },
    }
    payload["data_revision"] = stable_payload_hash(payload)
    return payload


def run_symbol(
    symbol: str,
    log_dir: str,
    end_exclusive: str,
) -> tuple[str, pd.DataFrame, list[Any]]:
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
    if not completed.empty:
        completed = completed.copy()
        completed["symbol"] = symbol
        completed = decision_model.attach_record_keys_to_trades(completed, openings)
    return symbol, completed, openings


def run_symbols_parallel(
    symbols: list[str],
    *,
    log_dir: Path,
    end_exclusive: str,
    workers: int,
    executor_backend: str,
) -> tuple[list[pd.DataFrame], list[decision_model.DecisionRecord]]:
    executor_type = (
        ProcessPoolExecutor if executor_backend == "process" else ThreadPoolExecutor
    )
    completed_parts: list[pd.DataFrame] = []
    opening_records: list[decision_model.DecisionRecord] = []
    with executor_type(max_workers=workers) as pool:
        futures = {
            pool.submit(run_symbol, symbol, str(log_dir), end_exclusive): symbol
            for symbol in symbols
        }
        done = 0
        for future in as_completed(futures):
            symbol, completed, openings = future.result()
            done += 1
            print(
                f"engine {done}/{len(symbols)} {symbol}: "
                f"completed={len(completed)} openings={len(openings)}",
                flush=True,
            )
            if not completed.empty:
                completed_parts.append(completed)
            opening_records.extend(openings)
    return completed_parts, opening_records


def local_day(record: decision_model.DecisionRecord) -> pd.Timestamp:
    return (
        pd.Timestamp(record.decision_time_utc)
        .tz_convert(decision_model.DECISION_TIMEZONE)
        .tz_localize(None)
        .normalize()
    )


def opening_row(record: decision_model.DecisionRecord) -> dict[str, Any]:
    context = dict(record.context)
    trade = dict(context.get("trade", {}))
    return {
        "symbol": record.symbol,
        "signal_key": record.signal_key,
        "decision_key": record.decision_key,
        "track": record.track,
        "rule": record.primary_rule,
        "side": record.side,
        "entry_date": trade.get("entry_date"),
        "entry": trade.get("entry_price"),
        "stop": trade.get("stop"),
        "target": trade.get("target"),
        "size": trade.get("size"),
        "daily_structure_phase_fresh": context.get("daily_structure_phase", ""),
        "volume_price_phase": context.get("volume_price_phase", ""),
        "bearish_countermove_subregime": context.get(
            "bearish_countermove_subregime", ""
        ),
    }


def routing_record(
    record: decision_model.DecisionRecord,
    *,
    accepted: bool,
    reason: str,
    quantity: int,
) -> decision_model.DecisionRecord:
    factor = decision_model.DecisionFactor(
        factor_code="production_routing_eligibility",
        category="routing",
        observed_value={"symbol": record.symbol, "quantity": quantity},
        operator="routable_and_quantity_positive",
        threshold={"non_routable": sorted(live.NON_ROUTABLE), "minimum_quantity": 1},
        passed=accepted,
        is_veto=not accepted,
        reason=reason,
        as_of_time_utc=record.decision_time_utc,
    )
    return replace(
        record,
        status="accepted" if accepted else "vetoed",
        trade_action="trade" if accepted else "no_trade",
        setup_state="production_routable" if accepted else "routing_blocked",
        primary_reason=record.primary_reason if accepted else reason,
        factors=record.factors + (factor,),
        context={
            **dict(record.context),
            "production_routing": {
                "accepted": accepted,
                "reason": reason,
                "quantity": quantity,
                "non_routable_symbols": sorted(live.NON_ROUTABLE),
            },
        },
    )


def quantity(row: pd.Series) -> int:
    return live.quantity_for_trade(row, RISK_PER_TRADE)


def causal_model_history(
    history_rows: list[dict[str, Any]],
    day: pd.Timestamp,
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
            timestamp = timestamp.tz_localize("America/New_York")
        return timestamp.tz_convert("UTC").isoformat().replace("+00:00", "Z")

    trades = tuple(
        ModelHistoryTrade(
            signal_key=str(row["signal_key"]),
            run_id="causal-manual-regime-v1-backtest",
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
        strategy_variant_id=decision_model.STRATEGY_VARIANT_ID,
        selected_run_id="causal-manual-regime-v1-backtest",
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
) -> tuple[list[decision_model.DecisionRecord], pd.DataFrame, pd.DataFrame]:
    period_records = [
        record
        for record in openings
        if PERIOD_START <= local_day(record) < END_EXCLUSIVE
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
            & (completed["entry_date"] < END_EXCLUSIVE)
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
        raw = pd.DataFrame([opening_row(record) for record in day_records])
        filtered_records: list[decision_model.DecisionRecord] = []
        surviving = live.apply_live_baseline_filters(
            raw,
            keep_pairs,
            day,
            decision_records=day_records,
            final_records_out=filtered_records,
            model_history=causal_model_history(history_rows, day),
        )
        surviving_keys = set(surviving.get("signal_key", pd.Series(dtype=str)).astype(str))
        filtered_by_key = {record.signal_key: record for record in filtered_records}

        for record in day_records:
            filtered = filtered_by_key[record.signal_key]
            if record.signal_key not in surviving_keys:
                final_records.append(filtered)
                continue
            closed = closed_by_key.get(record.signal_key)
            opening = pd.Series(opening_row(record))
            qty = quantity(opening)
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
                routing_record(filtered, accepted=routable, reason=reason, quantity=qty)
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


def metrics_and_curve(trades: pd.DataFrame) -> tuple[dict[str, Any], pd.DataFrame]:
    frame = trades.copy()
    if frame.empty:
        return {
            "trades": 0,
            "start_capital": START_CAPITAL,
            "ending_equity": START_CAPITAL,
            "net_pnl_dollars": 0.0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "max_drawdown_dollars": 0.0,
        }, pd.DataFrame(columns=["point", "exit_date", "pnl_dollars", "equity", "drawdown"])
    pnl = pd.to_numeric(frame["pnl_dollars"], errors="raise").astype(float)
    equity = START_CAPITAL + pnl.cumsum()
    drawdown = equity - equity.cummax()
    gross_profit = float(pnl[pnl > 0].sum())
    gross_loss = float(-pnl[pnl < 0].sum())
    curve = pd.DataFrame(
        {
            "point": range(1, len(frame) + 1),
            "exit_date": pd.to_datetime(frame["exit_date"]),
            "signal_key": frame["signal_key"],
            "pnl_dollars": pnl,
            "equity": equity,
            "drawdown": drawdown,
        }
    )
    metrics = {
        "trades": int(len(frame)),
        "wins": int((pnl > 0).sum()),
        "losses": int((pnl <= 0).sum()),
        "win_rate": float((pnl > 0).mean()),
        "start_capital": START_CAPITAL,
        "ending_equity": float(equity.iloc[-1]),
        "net_pnl_dollars": float(pnl.sum()),
        "profit_factor": (
            gross_profit / gross_loss
            if gross_loss > 0
            else (math.inf if gross_profit > 0 else 0.0)
        ),
        "max_drawdown_dollars": float(drawdown.min()),
    }
    return metrics, curve


def write_curve(curve: pd.DataFrame, path: Path, title: str) -> None:
    fig, ax = plt.subplots(figsize=(14, 7))
    if not curve.empty:
        ax.plot(curve["exit_date"], curve["equity"], linewidth=2.0, color="#0f766e")
    ax.axhline(START_CAPITAL, color="#6b7280", linestyle="--", linewidth=1.0)
    ax.set_title(title)
    ax.set_xlabel("Exit date")
    ax.set_ylabel("Equity (USD)")
    ax.grid(True, alpha=0.25)
    fig.tight_layout()
    fig.savefig(path, dpi=170)
    plt.close(fig)


def main() -> None:
    global END_EXCLUSIVE, ARTIFACT_DIR

    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--database", type=Path, default=DEFAULT_JOURNAL_PATH)
    parser.add_argument(
        "--end-exclusive",
        default=END_EXCLUSIVE.date().isoformat(),
        help="first trading date excluded from the canonical model run",
    )
    args = parser.parse_args()
    if args.workers < 1:
        raise ValueError("--workers must be positive")
    END_EXCLUSIVE = pd.Timestamp(args.end_exclusive).normalize()
    if END_EXCLUSIVE <= PERIOD_START:
        raise ValueError("--end-exclusive must be after the model period start")
    end_inclusive = END_EXCLUSIVE - pd.Timedelta(days=1)
    ARTIFACT_DIR = (
        ARTIFACT_ROOT
        / version_manifest.STRATEGY_VARIANT_ID
        / f"{PERIOD_START:%Y-%m-%d}_{end_inclusive:%Y-%m-%d}"
    )

    symbols = live_config.load_optimized_live_top50_symbols()
    keep_pairs = live_config.load_live_keep_pairs()
    pair_symbols = {symbol for symbol, _rule in keep_pairs}
    if set(symbols) != pair_symbols:
        raise RuntimeError(
            "Live symbol basket and enabled symbol/rule pairs do not cover the same symbols: "
            f"missing={sorted(set(symbols) - pair_symbols)} "
            f"extra={sorted(pair_symbols - set(symbols))}"
        )

    process_pool_available, process_pool_detail = executor_api.probe_process_pool(
        args.workers
    )
    executor_backend = "process" if process_pool_available else "thread"
    print(
        "startup permission preflight: "
        f"process_pool={'ok' if process_pool_available else 'unavailable'} "
        f"backend={executor_backend} detail={process_pool_detail}",
        flush=True,
    )

    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    log_dir = ARTIFACT_DIR / "engine_logs"
    log_dir.mkdir(exist_ok=True)
    inputs = build_input_manifest(symbols)
    data_revision = str(inputs["data_revision"])
    run_id = (
        f"backtest:{version_manifest.STRATEGY_VARIANT_ID}:"
        f"{PERIOD_START:%Y%m%d}-{end_inclusive:%Y%m%d}:{data_revision[:12]}"
    )
    manifest_path = ARTIFACT_DIR / "run_manifest.json"
    previous = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else None
    if previous and previous.get("run_id") != run_id:
        raise RuntimeError(
            f"Immutable V1 artifact directory already belongs to {previous.get('run_id')}"
        )
    started_at = (
        previous["started_at_utc"]
        if previous
        else datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    )

    try:
        completed_parts, opening_records = run_symbols_parallel(
            symbols,
            log_dir=log_dir,
            end_exclusive=END_EXCLUSIVE.date().isoformat(),
            workers=args.workers,
            executor_backend=executor_backend,
        )
    except BaseException as exc:
        if executor_backend != "process" or not executor_api.is_process_pool_infrastructure_error(
            exc
        ):
            raise
        print(
            "process pool failed after a successful preflight; restarting the complete "
            f"symbol calculation with the pipe-free thread fallback: {type(exc).__name__}: {exc}",
            flush=True,
        )
        completed_parts, opening_records = run_symbols_parallel(
            symbols,
            log_dir=log_dir,
            end_exclusive=END_EXCLUSIVE.date().isoformat(),
            workers=args.workers,
            executor_backend="thread",
        )
    all_completed = (
        pd.concat(completed_parts, ignore_index=True, sort=False)
        if completed_parts
        else pd.DataFrame()
    )
    final_records, model_trades, executable_trades = apply_portfolio_pipeline(
        opening_records,
        all_completed,
        keep_pairs,
    )
    model_metrics, model_curve = metrics_and_curve(model_trades)
    execution_metrics, execution_curve = metrics_and_curve(executable_trades)

    completed_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    config_payload = {
        "manifest_hash": version_manifest.manifest_hash(),
        "data_revision": data_revision,
        "period_start": PERIOD_START.date().isoformat(),
        "period_end_exclusive": END_EXCLUSIVE.date().isoformat(),
        "warmup_start": WARMUP_START,
        "symbols": symbols,
        "keep_pair_count": len(keep_pairs),
        "live_variant": live_config.LIVE_VARIANT,
        "risk_per_trade": RISK_PER_TRADE,
        "non_routable_symbols": sorted(live.NON_ROUTABLE),
        "fee_model": "zero@1.0.0",
        "slippage_model": "zero@1.0.0",
    }
    config_hash = stable_payload_hash(config_payload)
    spec = BacktestRunSpec(
        run_id=run_id,
        strategy_id=version_manifest.STRATEGY_ID,
        strategy_version=version_manifest.STRATEGY_VERSION,
        strategy_variant_id=version_manifest.STRATEGY_VARIANT_ID,
        engine_version=version_manifest.ENGINE_VERSION,
        money_management_version=version_manifest.MONEY_MANAGEMENT_VERSION,
        config_hash=config_hash,
        environment="historical-local",
        started_at_utc=started_at,
        parameters={
            **config_payload,
            "period_end": END_EXCLUSIVE.date().isoformat(),
            "starting_capital": START_CAPITAL,
            "capital_currency": "USD",
            "money_management_version": version_manifest.MONEY_MANAGEMENT_VERSION,
        },
        metadata={
            "model_history_role": "canonical_model_history",
            "artifact_directory": str(ARTIFACT_DIR),
            "input_manifest": inputs,
            "production_execution_scope": True,
            "model_metrics_including_non_routable": model_metrics,
            "execution_metrics": execution_metrics,
        },
        trade_defaults=BacktestTradeDefaults(
            symbol="MULTI",
            asset_class="STK",
            currency="USD",
            exchange="SMART",
            naive_timestamp_timezone=decision_model.DECISION_TIMEZONE,
        ),
    )
    with JournalStore(args.database) as store:
        register_baseline_journal_manifest(store)
        result = BacktestJournalIngestor(store).ingest_run(
            spec,
            final_records,
            executable_trades,
            completed_at_utc=completed_at,
        )

    executable_trades.to_csv(ARTIFACT_DIR / "trades.csv", index=False)
    model_trades.to_csv(
        ARTIFACT_DIR / "model_trades_including_non_routable.csv", index=False
    )
    execution_curve.to_csv(ARTIFACT_DIR / "equity_curve.csv", index=False)
    model_curve.to_csv(
        ARTIFACT_DIR / "model_equity_curve_including_non_routable.csv", index=False
    )
    decision_model.write_jsonl(ARTIFACT_DIR / "decision_records.jsonl", final_records)
    write_curve(
        execution_curve,
        ARTIFACT_DIR / "equity_curve.png",
        "Manual-Regime Production Baseline V1.0.0 (2024 through June 2026)",
    )
    write_curve(
        model_curve,
        ARTIFACT_DIR / "model_equity_curve_including_non_routable.png",
        "Manual-Regime V1.0.0 Model Signals incl. Non-Routable Symbols",
    )
    artifact_hashes = {
        name: sha256_file(ARTIFACT_DIR / name)
        for name in (
            "trades.csv",
            "equity_curve.csv",
            "equity_curve.png",
            "decision_records.jsonl",
        )
    }
    manifest = {
        "run_id": run_id,
        "strategy_variant_id": version_manifest.STRATEGY_VARIANT_ID,
        "started_at_utc": started_at,
        "completed_at_utc": completed_at,
        "config_hash": config_hash,
        "data_revision": data_revision,
        "runner_sha256": sha256_file(Path(__file__).resolve()),
        "artifact_hashes": artifact_hashes,
        "input_manifest": inputs,
        "config": config_payload,
        "journal": {
            "database": str(args.database),
            "decision_count": result.decision_count,
            "trade_count": result.trade_count,
        },
        "execution_metrics": execution_metrics,
        "model_metrics_including_non_routable": model_metrics,
    }
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False, allow_nan=False),
        encoding="utf-8",
    )
    print(json.dumps(manifest, indent=2, ensure_ascii=False, allow_nan=False), flush=True)


if __name__ == "__main__":
    main()
