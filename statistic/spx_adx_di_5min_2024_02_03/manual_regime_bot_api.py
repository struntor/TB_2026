from __future__ import annotations

import concurrent.futures as cf
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass
import os
from pathlib import Path
import pickle
import subprocess
import sys
import time
from typing import Any, Protocol
import uuid

import pandas as pd

import decision_records as decision_model
import manual_regime_rules_backtest as rules

OUT_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True)
class BasketRequest:
    symbol: str
    start: str
    end_exclusive: str
    structure_lookback: int = 10
    bull_continuation_mode: str = "combo"
    learn_long_mode: str = "off"
    learn_long_window: int = 5
    learn_long_min_trades: int = 3
    learn_long_min_win_rate: float = 0.45
    learn_long_cooldown_days: int = 15


@dataclass(frozen=True)
class DailyTradeBatch:
    day: pd.Timestamp
    signals: pd.DataFrame
    trades: pd.DataFrame
    daily: pd.DataFrame
    decision_records: tuple[decision_model.DecisionRecord, ...] = ()


@dataclass(frozen=True)
class SymbolTradeBatch:
    symbol: str
    batch: DailyTradeBatch


class MarketDataAdapter(Protocol):
    """Adapter interface for the bot-owned market data layer."""

    def load_bars(self, request: BasketRequest) -> pd.DataFrame:
        """Return intraday bars needed by the rule engine for the requested basket/day."""
        ...


class MissingDecisionDataError(ValueError):
    """ValueError-compatible validation failure carrying its audit record."""

    def __init__(self, message: str, decision_record: decision_model.DecisionRecord) -> None:
        super().__init__(message)
        self.decision_record = decision_record


class SubprocessInfrastructureError(RuntimeError):
    """The pipe-free subprocess fallback itself could not create a worker."""


def _process_pool_probe_worker(_: int) -> int:
    """Small picklable task used to verify Windows worker creation at startup."""
    time.sleep(0.25)
    return os.getpid()


def is_process_pool_infrastructure_error(exc: BaseException) -> bool:
    """Return True only for executor/Windows IPC failures that permit a thread fallback."""
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, (PermissionError, BrokenProcessPool)):
            return True
        if isinstance(current, OSError) and getattr(current, "winerror", None) in {
            5,     # access denied while creating a named pipe
            6,     # invalid/closed Windows handle
            8,     # insufficient memory for process/handle creation
            1450,  # insufficient system resources
            1455,  # paging file too small
        }:
            return True
        current = current.__cause__ or current.__context__
    return False


def probe_process_pool(max_workers: int) -> tuple[bool, str]:
    """Exercise ProcessPool construction and worker execution before market time."""
    worker_count = max(1, int(max_workers))
    try:
        with cf.ProcessPoolExecutor(max_workers=worker_count) as executor:
            pids = list(executor.map(_process_pool_probe_worker, range(worker_count)))
    except Exception as exc:
        if not is_process_pool_infrastructure_error(exc):
            raise
        return False, f"{type(exc).__name__}: {exc}"
    unique_workers = len(set(pids))
    return True, f"workers_requested={worker_count} workers_observed={unique_workers}"


def _probe_one_subprocess(path: Path) -> int:
    code = "from pathlib import Path; Path(__import__('sys').argv[1]).write_text('ok')"
    try:
        completed = subprocess.run(
            [sys.executable, "-c", code, str(path)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except OSError as exc:
        raise SubprocessInfrastructureError(f"{type(exc).__name__}: {exc}") from exc
    if completed.returncode != 0 or not path.exists():
        raise SubprocessInfrastructureError(
            f"worker probe failed returncode={completed.returncode} output_created={path.exists()}"
        )
    return completed.returncode


def probe_subprocess_pool(max_workers: int, work_dir: str | Path) -> tuple[bool, str]:
    """Verify the pipe-free worker fallback with the configured concurrency."""
    worker_count = max(1, int(max_workers))
    root = Path(work_dir)
    root.mkdir(parents=True, exist_ok=True)
    probe_dir = root / f".subprocess_permission_probe_{uuid.uuid4().hex}"
    probe_dir.mkdir()
    paths = [probe_dir / f"worker_{index}.ok" for index in range(worker_count)]
    try:
        with cf.ThreadPoolExecutor(max_workers=worker_count) as executor:
            list(executor.map(_probe_one_subprocess, paths))
    except Exception as exc:
        if isinstance(exc, SubprocessInfrastructureError) or is_process_pool_infrastructure_error(exc):
            return False, f"{type(exc).__name__}: {exc}"
        raise
    finally:
        for path in paths:
            path.unlink(missing_ok=True)
        try:
            probe_dir.rmdir()
        except OSError:
            pass
    return True, f"workers_requested={worker_count} workers_completed={len(paths)}"


def build_basket_request(
    symbol: str,
    current_day: str | pd.Timestamp,
    start: str = "2023-01-01",
    history_lookback_days: int | None = 540,
    structure_lookback: int = 10,
    bull_continuation_mode: str = "combo",
    learn_long_mode: str = "off",
    learn_long_window: int = 5,
    learn_long_min_trades: int = 3,
    learn_long_min_win_rate: float = 0.45,
    learn_long_cooldown_days: int = 15,
) -> BasketRequest:
    day = pd.Timestamp(current_day).normalize()
    if history_lookback_days is not None:
        history_floor = (day - pd.Timedelta(days=history_lookback_days)).normalize()
        start = max(pd.Timestamp(start).normalize(), history_floor).strftime("%Y-%m-%d")
    end_exclusive = (day + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    return BasketRequest(
        symbol=symbol.upper(),
        start=start,
        end_exclusive=end_exclusive,
        structure_lookback=structure_lookback,
        bull_continuation_mode=bull_continuation_mode,
        learn_long_mode=learn_long_mode,
        learn_long_window=learn_long_window,
        learn_long_min_trades=learn_long_min_trades,
        learn_long_min_win_rate=learn_long_min_win_rate,
        learn_long_cooldown_days=learn_long_cooldown_days,
    )


def get_trade_openings_from_bars(
    bars: pd.DataFrame,
    current_day: str | pd.Timestamp,
    request: BasketRequest,
    decision_log_path: str | Path | None = None,
    runtime_log_path: str | Path | None = None,
    validation_decision_records_out: list[decision_model.DecisionRecord] | None = None,
) -> DailyTradeBatch:
    """
    Evaluate the rule engine for a single day on preloaded bars.

    The caller owns data loading and only passes the bars that are already
    available at decision time. The rule engine then returns the concrete
    trades opened on that day.
    """
    day = pd.Timestamp(current_day).normalize()
    if bars.empty:
        record = decision_model.build_missing_data_record(
            symbol=request.symbol,
            decision_day=day,
            primary_reason="no_market_bars_provided",
            source="api_validation",
            available_bar_count=0,
            context={
                "request_start": request.start,
                "request_end_exclusive": request.end_exclusive,
            },
        )
        if validation_decision_records_out is not None:
            validation_decision_records_out.append(record)
        raise MissingDecisionDataError(
            f"No market bars provided for {request.symbol} on or before {day.date()}.",
            record,
        )
    bar_days = pd.to_datetime(bars["date"]).dt.normalize()
    day_bars = bars[bar_days == day].copy()
    if day_bars.empty:
        record = decision_model.build_missing_data_record(
            symbol=request.symbol,
            decision_day=day,
            primary_reason="missing_current_day_bars",
            source="api_validation",
            available_bar_count=0,
            context={"loaded_bar_count": len(bars)},
        )
        if validation_decision_records_out is not None:
            validation_decision_records_out.append(record)
        raise MissingDecisionDataError(
            f"Missing bars for {request.symbol} on {day.date()}. "
            "The rule engine cannot answer trades for a day whose data has not been loaded yet.",
            record,
        )
    if len(day_bars) < 2:
        record = decision_model.build_missing_data_record(
            symbol=request.symbol,
            decision_day=day,
            primary_reason="insufficient_current_day_bars",
            source="api_validation",
            available_bar_count=len(day_bars),
            context={"loaded_bar_count": len(bars)},
        )
        if validation_decision_records_out is not None:
            validation_decision_records_out.append(record)
        raise MissingDecisionDataError(
            f"Need at least the first two 5-minute bars for {request.symbol} on {day.date()}. "
            "The rule engine decides on the second bar, so the first two bars must already be loaded.",
            record,
        )
    decision_records: list[decision_model.DecisionRecord] = []
    signals, trades, daily = rules.run_backtest(
        request.symbol,
        request.start,
        request.end_exclusive,
        structure_lookback=request.structure_lookback,
        bull_continuation_mode=request.bull_continuation_mode,
        learn_long_mode=request.learn_long_mode,
        learn_long_window=request.learn_long_window,
        learn_long_min_trades=request.learn_long_min_trades,
        learn_long_min_win_rate=request.learn_long_min_win_rate,
        learn_long_cooldown_days=request.learn_long_cooldown_days,
        bars=bars,
        decision_log_path=decision_log_path,
        runtime_log_path=runtime_log_path,
        decision_records_out=decision_records,
    )
    if signals.empty or "day" not in signals.columns:
        day_signals = pd.DataFrame()
    else:
        day_signals = signals[pd.to_datetime(signals["day"]).dt.normalize() == day].copy()
    if trades.empty or "entry_date" not in trades.columns:
        day_trades = pd.DataFrame(columns=trades.columns if len(trades.columns) else None)
    else:
        day_trades = trades[pd.to_datetime(trades["entry_date"]).dt.normalize() == day].copy()
    if not day_trades.empty:
        day_trades = day_trades.copy()
        day_trades["symbol"] = request.symbol
        day_trades = day_trades.sort_values(["entry_date", "rule"]).reset_index(drop=True)
    day_records = tuple(
        record
        for record in decision_records
        if (
            pd.Timestamp(record.decision_time_utc)
            .tz_convert(decision_model.DECISION_TIMEZONE)
            .tz_localize(None)
            .normalize()
            == day
        )
    )
    day_trades = decision_model.attach_record_keys_to_trades(day_trades, day_records)
    return DailyTradeBatch(
        day=day,
        signals=day_signals,
        trades=day_trades,
        daily=daily,
        decision_records=day_records,
    )


def get_trade_openings_from_adapter(
    adapter: MarketDataAdapter,
    current_day: str | pd.Timestamp,
    request: BasketRequest,
    decision_log_path: str | Path | None = None,
    runtime_log_path: str | Path | None = None,
) -> DailyTradeBatch:
    """
    Evaluate the rule engine through an external market-data adapter.

    This is the preferred bot entry point:
      1. build the request for the basket/day
      2. let the adapter load the needed bars
      3. pass the bars into the rule engine
      4. hand the returned trades to money management
    """
    bars = adapter.load_bars(request)
    return get_trade_openings_from_bars(
        bars,
        current_day,
        request,
        decision_log_path=decision_log_path,
        runtime_log_path=runtime_log_path,
    )


def get_trade_openings_for_day(
    symbol: str,
    current_day: str | pd.Timestamp,
    start: str = "2023-01-01",
    history_lookback_days: int | None = 540,
    structure_lookback: int = 10,
    bull_continuation_mode: str = "combo",
    learn_long_mode: str = "off",
    learn_long_window: int = 5,
    learn_long_min_trades: int = 3,
    learn_long_min_win_rate: float = 0.45,
    learn_long_cooldown_days: int = 15,
    decision_log_path: str | Path | None = None,
    runtime_log_path: str | Path | None = None,
) -> DailyTradeBatch:
    """
    Return the no-lookahead trades opened on a single decision day.

    The bot can use this as:
      1. call the function for the current day
      2. inspect `batch.trades`
      3. hand the trade rows to its own money-management layer

    The returned `trades` rows are already the concrete order specs:
    entry, stop, target, size, regime, labels.
    """
    request = build_basket_request(
        symbol=symbol,
        current_day=current_day,
        start=start,
        history_lookback_days=history_lookback_days,
        structure_lookback=structure_lookback,
        bull_continuation_mode=bull_continuation_mode,
        learn_long_mode=learn_long_mode,
        learn_long_window=learn_long_window,
        learn_long_min_trades=learn_long_min_trades,
        learn_long_min_win_rate=learn_long_min_win_rate,
        learn_long_cooldown_days=learn_long_cooldown_days,
    )
    bars = rules.load_bars(request.symbol, request.start, request.end_exclusive)
    return get_trade_openings_from_bars(
        bars,
        current_day,
        request,
        decision_log_path=decision_log_path,
        runtime_log_path=runtime_log_path,
    )


def get_trade_openings_for_day_only(
    symbol: str,
    current_day: str | pd.Timestamp,
    start: str = "2023-01-01",
    history_lookback_days: int | None = 540,
    structure_lookback: int = 10,
    bull_continuation_mode: str = "combo",
    learn_long_mode: str = "off",
    learn_long_window: int = 5,
    learn_long_min_trades: int = 3,
    learn_long_min_win_rate: float = 0.45,
    learn_long_cooldown_days: int = 15,
    decision_log_path: str | Path | None = None,
    runtime_log_path: str | Path | None = None,
    ) -> DailyTradeBatch:
    """
    Day-only bot entry point.

    This keeps the engine bounded to the requested decision day from the bot's
    point of view: the request window ends at current_day + 1 day, and only the
    day's signals and trades are returned.
    """
    return get_trade_openings_for_day(
        symbol,
        current_day,
        start=start,
        history_lookback_days=history_lookback_days,
        structure_lookback=structure_lookback,
        bull_continuation_mode=bull_continuation_mode,
        learn_long_mode=learn_long_mode,
        learn_long_window=learn_long_window,
        learn_long_min_trades=learn_long_min_trades,
        learn_long_min_win_rate=learn_long_min_win_rate,
        learn_long_cooldown_days=learn_long_cooldown_days,
        decision_log_path=decision_log_path,
        runtime_log_path=runtime_log_path,
    )


def _evaluate_symbol_trade_request(
    symbol: str,
    current_day: str | pd.Timestamp,
    start: str,
    history_lookback_days: int | None,
    structure_lookback: int,
    bull_continuation_mode: str,
    learn_long_mode: str,
    learn_long_window: int,
    learn_long_min_trades: int,
    learn_long_min_win_rate: float,
    learn_long_cooldown_days: int,
    log_dir: str | Path | None,
) -> SymbolTradeBatch:
    day = pd.Timestamp(current_day).normalize()
    safe_symbol = symbol.upper()
    log_root = Path(log_dir) if log_dir is not None else OUT_DIR / "logs" / "parallel"
    runtime_log_path = log_root / f"Trading_Engine_{safe_symbol}_{day.date()}.log"
    decision_log_path = log_root / f"Trading_Engine_{safe_symbol}_{day.date()}_decision.log"
    batch = get_trade_openings_for_day(
        safe_symbol,
        current_day,
        start=start,
        history_lookback_days=history_lookback_days,
        structure_lookback=structure_lookback,
        bull_continuation_mode=bull_continuation_mode,
        learn_long_mode=learn_long_mode,
        learn_long_window=learn_long_window,
        learn_long_min_trades=learn_long_min_trades,
        learn_long_min_win_rate=learn_long_min_win_rate,
        learn_long_cooldown_days=learn_long_cooldown_days,
        decision_log_path=decision_log_path,
        runtime_log_path=runtime_log_path,
    )
    return SymbolTradeBatch(symbol=safe_symbol, batch=batch)


def _evaluate_symbol_trade_request_from_payload(
    payload: tuple[
        str,
        str | pd.Timestamp,
        str,
        int | None,
        int,
        str,
        str,
        int,
        int,
        float,
        int,
        str | Path | None,
    ],
) -> SymbolTradeBatch:
    return _evaluate_symbol_trade_request(*payload)


def run_parallel_worker(input_path: str | Path, output_path: str | Path) -> None:
    """Run one serialized symbol job with import-stable result class names."""
    with Path(input_path).open("rb") as handle:
        worker_payload = pickle.load(handle)
    worker_result = _evaluate_symbol_trade_request_from_payload(worker_payload)
    with Path(output_path).open("wb") as handle:
        pickle.dump(worker_result, handle, protocol=pickle.HIGHEST_PROTOCOL)


def _run_subprocess_worker(input_path: Path, output_path: Path, error_path: Path) -> None:
    worker_code = (
        "import sys; sys.path.insert(0, sys.argv[1]); "
        "import manual_regime_bot_api as worker; "
        "worker.run_parallel_worker(sys.argv[2], sys.argv[3])"
    )
    with error_path.open("wb") as error_handle:
        try:
            completed = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    worker_code,
                    str(Path(__file__).resolve().parent),
                    str(input_path),
                    str(output_path),
                ],
                stdin=subprocess.DEVNULL,
                stdout=error_handle,
                stderr=error_handle,
                check=False,
            )
        except OSError as exc:
            raise SubprocessInfrastructureError(f"{type(exc).__name__}: {exc}") from exc
    if completed.returncode != 0 or not output_path.exists():
        detail = error_path.read_text(encoding="utf-8", errors="replace")[-4000:]
        raise RuntimeError(
            "parallel strategy subprocess failed "
            f"returncode={completed.returncode} input={input_path.name}: {detail}"
        )


def _run_subprocess_pool(
    payloads: list[tuple[Any, ...]],
    worker_count: int,
    log_dir: str | Path | None,
) -> list[SymbolTradeBatch]:
    root = Path(log_dir) if log_dir is not None else OUT_DIR / "logs" / "parallel"
    root.mkdir(parents=True, exist_ok=True)
    scratch = root / f".subprocess_pool_{uuid.uuid4().hex}"
    scratch.mkdir()
    jobs: list[tuple[Path, Path, Path]] = []
    try:
        for index, payload in enumerate(payloads):
            input_path = scratch / f"{index:04d}.input.pkl"
            output_path = scratch / f"{index:04d}.output.pkl"
            error_path = scratch / f"{index:04d}.worker.log"
            with input_path.open("wb") as handle:
                pickle.dump(payload, handle, protocol=pickle.HIGHEST_PROTOCOL)
            jobs.append((input_path, output_path, error_path))
        with cf.ThreadPoolExecutor(max_workers=worker_count) as executor:
            list(executor.map(lambda job: _run_subprocess_worker(*job), jobs))
        results: list[SymbolTradeBatch] = []
        for _, output_path, _ in jobs:
            with output_path.open("rb") as handle:
                results.append(pickle.load(handle))
        return results
    finally:
        for path in scratch.glob("*"):
            try:
                path.unlink()
            except OSError:
                pass
        try:
            scratch.rmdir()
        except OSError:
            pass


def get_trade_openings_for_symbols(
    symbols: list[str],
    current_day: str | pd.Timestamp,
    start: str = "2023-01-01",
    history_lookback_days: int | None = 540,
    structure_lookback: int = 10,
    bull_continuation_mode: str = "combo",
    learn_long_mode: str = "off",
    learn_long_window: int = 5,
    learn_long_min_trades: int = 3,
    learn_long_min_win_rate: float = 0.45,
    learn_long_cooldown_days: int = 15,
    max_workers: int | None = None,
    log_dir: str | Path | None = None,
    executor_backend: str = "process",
    executor_fallback_callback: Any | None = None,
) -> list[SymbolTradeBatch]:
    """
    Evaluate the rule engine for many symbols in parallel.

    The return value preserves the input order. Each list element contains the
    symbol and the per-symbol `DailyTradeBatch`.
    """
    if not symbols:
        return []
    worker_count = max_workers if max_workers is not None else min(len(symbols), os.cpu_count() or 1)
    worker_count = max(1, worker_count)
    payloads = [
        (
            symbol,
            current_day,
            start,
            history_lookback_days,
            structure_lookback,
            bull_continuation_mode,
            learn_long_mode,
            learn_long_window,
            learn_long_min_trades,
            learn_long_min_win_rate,
            learn_long_cooldown_days,
            log_dir,
        )
        for symbol in symbols
    ]
    if executor_backend not in {"process", "subprocess", "thread"}:
        raise ValueError("executor_backend must be 'process', 'subprocess', or 'thread'")

    def run(executor_type: type[cf.Executor]) -> list[SymbolTradeBatch]:
        with executor_type(max_workers=worker_count) as executor:
            return list(executor.map(_evaluate_symbol_trade_request_from_payload, payloads))

    if executor_backend == "thread":
        return run(cf.ThreadPoolExecutor)
    if executor_backend == "subprocess":
        return _run_subprocess_pool(payloads, worker_count, log_dir)
    try:
        return run(cf.ProcessPoolExecutor)
    except Exception as exc:
        if not is_process_pool_infrastructure_error(exc):
            raise
        message = (
            "process executor unavailable during live calculation; "
            f"falling back to {worker_count} pipe-free subprocess workers: "
            f"{type(exc).__name__}: {exc}"
        )
        if executor_fallback_callback is not None:
            executor_fallback_callback(message)
        try:
            return _run_subprocess_pool(payloads, worker_count, log_dir)
        except SubprocessInfrastructureError as subprocess_exc:
            if executor_fallback_callback is not None:
                executor_fallback_callback(
                    "subprocess worker creation also unavailable; last-resort fallback to "
                    f"{worker_count} threads: {type(subprocess_exc).__name__}: {subprocess_exc}"
                )
            return run(cf.ThreadPoolExecutor)


def trades_as_dicts(batch: DailyTradeBatch) -> list[dict[str, Any]]:
    return batch.trades.to_dict(orient="records")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--day", required=True)
    parser.add_argument("--start", default="2023-01-01")
    parser.add_argument("--structure-lookback", type=int, default=10)
    parser.add_argument("--bull-continuation-mode", default="combo")
    parser.add_argument("--learn-long-mode", default="off")
    parser.add_argument("--learn-long-window", type=int, default=5)
    parser.add_argument("--learn-long-min-trades", type=int, default=3)
    parser.add_argument("--learn-long-min-win-rate", type=float, default=0.45)
    parser.add_argument("--learn-long-cooldown-days", type=int, default=15)
    parser.add_argument("--decision-log", default="")
    parser.add_argument("--runtime-log", default="")
    args = parser.parse_args()

    batch = get_trade_openings_for_day(
        args.symbol,
        args.day,
        start=args.start,
        structure_lookback=args.structure_lookback,
        bull_continuation_mode=args.bull_continuation_mode,
        learn_long_mode=args.learn_long_mode,
        learn_long_window=args.learn_long_window,
        learn_long_min_trades=args.learn_long_min_trades,
        learn_long_min_win_rate=args.learn_long_min_win_rate,
        learn_long_cooldown_days=args.learn_long_cooldown_days,
        decision_log_path=args.decision_log or None,
        runtime_log_path=args.runtime_log or None,
    )
    print(batch.trades.to_string(index=False) if not batch.trades.empty else "No trades")
