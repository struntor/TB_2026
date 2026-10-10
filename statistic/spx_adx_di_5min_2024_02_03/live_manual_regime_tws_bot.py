from __future__ import annotations

import argparse
import math
import os
from pathlib import Path
import sys
import tempfile
from dataclasses import dataclass, replace
from typing import Callable

import pandas as pd
from ib_insync import IB, Index, Stock, util


HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parents[2]
PYTHON_PROJECT_ROOT = HERE.parents[1]
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
if str(PYTHON_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_PROJECT_ROOT))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
IBKR_BOT_ROOT = PROJECT_ROOT / "IBKR_TradingBot"
if str(IBKR_BOT_ROOT) not in sys.path:
    sys.path.insert(0, str(IBKR_BOT_ROOT))

import decision_records as decision_model
from lhf_cluster_rank_score import (
    input_from_record_and_trade,
    rank_cluster_choices,
    score_cluster,
)
import manual_regime_rules_backtest as rules
from manual_regime_failed_countermove_fresh_stop import (
    POLICY_VERSION as FRESH_STOP_POLICY_VERSION,
    TARGET_RULE as FRESH_STOP_TARGET_RULE,
    apply_fresh_structure_stop,
)
from manual_regime_journal_registry import register_gap_quarter_v4_journal_manifest
import manual_regime_fresh_stop_v3_manifest as fresh_stop_v3_manifest
import manual_regime_gap_quarter_v4_manifest as gap_quarter_v4_manifest
import manual_regime_sizing_v2_manifest as sizing_v2_manifest
from manual_regime_gap_breakdown_quarter_sizing import (
    POLICY_VERSION as GAP_BREAKDOWN_QUARTER_POLICY_VERSION,
    apply_gap_breakdown_quarter_sizing,
)
from manual_regime_position_sizing import (
    LEGACY_SHADOW_VARIANT_ID,
    POLICY_VERSION as POSITION_SIZING_POLICY_VERSION,
    PositionSizingContext,
    apply_position_sizing,
    build_position_sizing_context,
)
from current_live_baseline_config import (
    LIVE_PAIR_SELECTION_PATH,
    LIVE_VARIANT,
    OPTIMIZED_LIVE_TOP50_PATH,
)
from manual_regime_bot_api import (
    get_trade_openings_for_symbols,
    probe_process_pool,
    probe_subprocess_pool,
)
from src.execution.safe_bracket_order import BracketOrderSpec, SafeBracketError, SafeBracketOrderManager
from src.bot.failed_auction_poc_fly_monitor import (
    DEFAULT_STATE_PATH as DEFAULT_FAILED_AUCTION_STATE_PATH,
    FailedAuctionFlyMonitor,
    FlyMonitorConfig,
    TERMINAL_STATE_STATUSES as FAILED_AUCTION_TERMINAL_STATUSES,
    load_state as load_failed_auction_state,
)
from src.bot.spxw_15dte_put_credit_monitor import (
    DEFAULT_STATE_PATH as DEFAULT_SPXW_15DTE_STATE_PATH,
    MonitorConfig as Spxw15DteMonitorConfig,
    Spxw15DtePutCreditMonitor,
)
from src.bot.spxw_0dte_bull_put_iu30_monitor import (
    DEFAULT_STATE_PATH as DEFAULT_SPXW_0DTE_IU30_STATE_PATH,
    MonitorConfig as Spxw0DteIu30MonitorConfig,
    Spxw0DteBullPutIu30Monitor,
)
from src.bot.spxw_75dte_put_bwb_monitor import (
    DEFAULT_STATE_PATH as DEFAULT_SPXW_75DTE_BWB_STATE_PATH,
    MonitorConfig as Spxw75DteBwbMonitorConfig,
    Spxw75DtePutBwbMonitor,
)
from src.bot.xsp_vix_pullback_monitor import (
    DEFAULT_STATE_PATH as DEFAULT_XSP_VIX_PULLBACK_STATE_PATH,
    MonitorConfig as XspVixPullbackMonitorConfig,
    XspVixPullbackMonitor,
)
from src.bot.stock_ivr70_put_credit_monitor import (
    DEFAULT_STATE_PATH as DEFAULT_STOCK_IVR70_STATE_PATH,
    MonitorConfig as StockIvr70MonitorConfig,
    StockIvr70PutCreditMonitor,
)
from src.bot.manual_regime_lhf_eod_monitor import (
    DEFAULT_STATE_PATH as DEFAULT_LHF_EOD_STATE_PATH,
    ManualRegimeLhfEodMonitor,
    MonitorConfig as LhfEodMonitorConfig,
)
from src.journal.realized_history import ModelHistorySnapshot, load_model_history
from src.journal.store import DEFAULT_JOURNAL_PATH, JournalStore
from src.risk.options_risk_budget import OptionsRiskBudget
from research.stock_ivr70_live_candidates import LiveScanConfig, build_live_candidates
from live_trading_journal import ManualRegimeJournalSession


TOP50_PATH = OPTIMIZED_LIVE_TOP50_PATH
PAIR_SELECTION_PATH = LIVE_PAIR_SELECTION_PATH
LOG_ROOT = Path(r"D:\Trading\logs")
OVERLAY_ROOT = LOG_ROOT / "manual_regime_live_overlay"
STOCK_IVR70_RESEARCH_ROOT = (
    Path(r"D:\Trading\research\single_stock_drawdown_recovery_put_credit")
)
STOCK_IVR70_UNIVERSE_PATH = (
    STOCK_IVR70_RESEARCH_ROOT / "equity_screen_v1" / "asset_diagnostics.csv"
)
STOCK_IVR70_SEC_CACHE = STOCK_IVR70_RESEARCH_ROOT / "sec_earnings_cache_all"

NON_ROUTABLE = {"SPX", "SPX_FUT"}
CORE_SHORT_RULES = {
    "bearish_gap_breakdown_continuation_short",
    "bearish_lower_high_failure_short",
    "bearish_failed_countermove_short_baseline",
}
ROUTABLE_LHF_CLUSTER = frozenset({"SPY", "IVV", "VOO", "VTI", "OEF"})


@dataclass(frozen=True)
class LiveConfig:
    day: pd.Timestamp
    risk_per_trade: float
    host: str
    port: int
    client_id: int
    max_workers: int
    executor_backend: str
    cutoff_variant: str
    place_orders: bool
    require_paper_account: bool
    bracket_ack_timeout_seconds: float


def log(message: str) -> None:
    ts = pd.Timestamp.now(tz="Europe/Berlin").strftime("%Y-%m-%d %H:%M:%S %Z")
    print(f"[{ts}] {message}", flush=True)


def verify_writable_directory(path: str | Path) -> None:
    """Fail at startup if a directory required later cannot be written."""
    directory = Path(path)
    directory.mkdir(parents=True, exist_ok=True)
    probe_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", prefix=".bot_permission_probe_", dir=directory, delete=False
        ) as handle:
            handle.write(b"permission-probe")
            handle.flush()
            os.fsync(handle.fileno())
            probe_path = Path(handle.name)
        probe_path.unlink()
    except Exception as exc:
        if probe_path is not None:
            try:
                probe_path.unlink(missing_ok=True)
            except OSError:
                pass
        raise PermissionError(f"required bot directory is not writable: {directory}") from exc


def run_startup_permission_preflight(
    *,
    max_workers: int,
    writable_directories: list[str | Path],
) -> str:
    for directory in writable_directories:
        verify_writable_directory(directory)
    process_ok, detail = probe_process_pool(max_workers)
    if process_ok:
        log(f"startup permission preflight passed: filesystem=ok process_pool=ok {detail}")
        return "process"
    subprocess_ok, subprocess_detail = probe_subprocess_pool(max_workers, writable_directories[0])
    if subprocess_ok:
        log(
            "startup permission preflight: filesystem=ok process_pool=unavailable; "
            "using pipe-free subprocess workers for this run "
            f"(process_pool={detail}; subprocess_pool={subprocess_detail})"
        )
        return "subprocess"
    log(
        "startup permission preflight: filesystem=ok process_pool=unavailable and "
        "subprocess_pool=unavailable; using last-resort thread fallback for this run "
        f"(process_pool={detail}; subprocess_pool={subprocess_detail})"
    )
    return "thread"


def load_top50_symbols() -> list[str]:
    df = pd.read_csv(TOP50_PATH)
    if "symbol" not in df.columns:
        raise ValueError(f"{TOP50_PATH} has no symbol column")
    return [str(s).upper() for s in df["symbol"].dropna().tolist()]


def load_keep_pairs(variant: str) -> set[tuple[str, str]]:
    df = pd.read_csv(PAIR_SELECTION_PATH)
    df = df[df["variant"].astype(str).eq(variant)].copy()
    if df.empty:
        raise ValueError(f"No pair selection rows for {variant}")
    kept = df[df["keep"].astype(bool)].copy()
    return {(str(r.symbol).upper(), str(r.rule)) for r in kept.itertuples(index=False)}


def wait_until_local_time(day: pd.Timestamp, hhmm: str) -> None:
    hour, minute = [int(part) for part in hhmm.split(":", 1)]
    target = pd.Timestamp(
        year=day.year,
        month=day.month,
        day=day.day,
        hour=hour,
        minute=minute,
        tz="Europe/Berlin",
    )
    now = pd.Timestamp.now(tz="Europe/Berlin")
    if now < target:
        secs = (target - now).total_seconds()
        log(f"waiting until bot start {target:%Y-%m-%d %H:%M %Z} ({secs:.0f}s)")
        util.sleep(secs)
    else:
        log(f"bot start time {target:%Y-%m-%d %H:%M %Z} already passed; continuing")


def wait_until_et(
    day: pd.Timestamp,
    hhmm: str,
    *,
    while_waiting: Callable[[], None] | None = None,
    poll_seconds: float = 60.0,
) -> None:
    hour, minute = [int(part) for part in hhmm.split(":", 1)]
    target = pd.Timestamp(
        year=day.year,
        month=day.month,
        day=day.day,
        hour=hour,
        minute=minute,
        tz="America/New_York",
    )
    now = pd.Timestamp.now(tz="America/New_York")
    if now < target:
        secs = (target - now).total_seconds()
        log(f"waiting until decision fetch {target.tz_convert('Europe/Berlin'):%Y-%m-%d %H:%M %Z} ({secs:.0f}s)")
        while pd.Timestamp.now(tz="America/New_York") < target:
            if while_waiting is not None:
                while_waiting()
            remaining = (
                target - pd.Timestamp.now(tz="America/New_York")
            ).total_seconds()
            if remaining > 0:
                util.sleep(min(max(poll_seconds, 1.0), remaining))
    else:
        log(f"decision fetch time {target:%Y-%m-%d %H:%M %Z} already passed; continuing")


def contract_for_data(symbol: str):
    if symbol == "SPX":
        return Index("SPX", "CBOE", "USD")
    if symbol in NON_ROUTABLE:
        return None
    return Stock(symbol, "SMART", "USD")


def contract_for_order(symbol: str):
    if symbol in NON_ROUTABLE:
        return None
    return Stock(symbol, "SMART", "USD")


def normalize_ib_dates(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    dates = pd.to_datetime(out["date"], errors="coerce")
    if getattr(dates.dt, "tz", None) is not None:
        dates = dates.dt.tz_convert("America/New_York").dt.tz_localize(None)
    out["date"] = dates
    return out


def fetch_first_two_bars(
    ib: IB,
    symbol: str,
    day: pd.Timestamp,
    missing_data_records_out: list[decision_model.DecisionRecord] | None = None,
) -> pd.DataFrame:
    def missing(reason: str, count: int | None, detail: str) -> pd.DataFrame:
        if missing_data_records_out is not None:
            missing_data_records_out.append(
                decision_model.build_missing_data_record(
                    symbol=symbol,
                    decision_day=day,
                    primary_reason=reason,
                    source="live_market_data",
                    available_bar_count=count,
                    context={"detail": detail},
                )
            )
        return pd.DataFrame()

    contract = contract_for_data(symbol)
    if contract is None:
        log(f"{symbol}: no live data contract mapping; skipped")
        return missing(
            "no_live_data_contract_mapping",
            None,
            "contract_for_data returned None",
        )
    try:
        [qualified] = ib.qualifyContracts(contract)
    except Exception as exc:
        log(f"{symbol}: contract qualification failed: {exc}")
        return missing(
            "contract_qualification_failed",
            None,
            f"{type(exc).__name__}: {exc}",
        )
    what = "TRADES"
    try:
        bars = ib.reqHistoricalData(
            qualified,
            endDateTime="",
            durationStr="1 D",
            barSizeSetting="5 mins",
            whatToShow=what,
            useRTH=True,
            formatDate=1,
            keepUpToDate=False,
        )
    except Exception as exc:
        log(f"{symbol}: historical fetch failed: {exc}")
        return missing(
            "historical_fetch_failed",
            None,
            f"{type(exc).__name__}: {exc}",
        )
    df = util.df(bars)
    if df is None or df.empty:
        log(f"{symbol}: no bars returned")
        return missing(
            "no_current_day_bars_returned",
            0,
            "IB historical request returned no rows",
        )
    df = normalize_ib_dates(df)
    df = df[["date", "open", "high", "low", "close", "volume"]].copy()
    day_naive = day.tz_localize(None)
    day_rows = df[df["date"].dt.normalize().eq(day_naive)].copy()
    first_two = day_rows.sort_values("date").head(2)
    if len(first_two) < 2:
        log(f"{symbol}: only {len(first_two)} current-day bars available")
        return missing(
            "insufficient_current_day_bars",
            len(first_two),
            "fewer than two current-day 5-minute bars",
        )
    log(
        f"{symbol}: got first two bars "
        f"{first_two['date'].iloc[0]:%H:%M}, {first_two['date'].iloc[1]:%H:%M}"
    )
    return first_two


def write_overlay(symbol: str, bars: pd.DataFrame, overlay_dir: Path) -> None:
    if bars.empty:
        return
    overlay_dir.mkdir(parents=True, exist_ok=True)
    path = overlay_dir / f"Rohdaten_{symbol.upper()}.csv"
    bars.to_csv(path, index=False)


def build_live_overlay(
    ib: IB,
    symbols: list[str],
    day: pd.Timestamp,
    overlay_dir: Path,
    missing_data_records_out: list[decision_model.DecisionRecord] | None = None,
) -> list[str]:
    usable: list[str] = []
    for symbol in symbols:
        bars = fetch_first_two_bars(ib, symbol, day, missing_data_records_out)
        if bars.empty:
            continue
        write_overlay(symbol, bars, overlay_dir)
        usable.append(symbol)
        ib.sleep(0.2)
    return usable


def collect_trades(
    symbols: list[str],
    cfg: LiveConfig,
    overlay_dir: Path,
    decision_records_out: list[decision_model.DecisionRecord] | None = None,
) -> pd.DataFrame:
    os.environ[rules.LIVE_OVERLAY_ENV] = str(overlay_dir)
    results = get_trade_openings_for_symbols(
        symbols,
        cfg.day.strftime("%Y-%m-%d"),
        start="2023-01-01",
        history_lookback_days=548,
        structure_lookback=10,
        bull_continuation_mode="combo",
        learn_long_mode="off",
        max_workers=cfg.max_workers,
        log_dir=LOG_ROOT / "manual_regime_live_engine",
        executor_backend=cfg.executor_backend,
        executor_fallback_callback=log,
    )
    parts = []
    for result in results:
        if decision_records_out is not None:
            decision_records_out.extend(result.batch.decision_records)
        trades = result.batch.trades.copy()
        if not trades.empty:
            parts.append(trades)
    if not parts:
        return pd.DataFrame()
    out = pd.concat(parts, ignore_index=True, sort=False)
    out["symbol"] = out["symbol"].astype(str).str.upper()
    out["rule"] = out["rule"].astype(str)
    return out


def load_fresh_stop_context_bars(
    trades: pd.DataFrame,
    day: pd.Timestamp,
) -> dict[str, pd.DataFrame]:
    if trades.empty:
        return {}
    affected = trades[
        (trades["rule"].astype(str) == FRESH_STOP_TARGET_RULE)
        & (trades["side"].astype(str).str.lower() == "short")
    ]
    start = (pd.Timestamp(day).normalize() - pd.Timedelta(days=10)).strftime(
        "%Y-%m-%d"
    )
    end_exclusive = (
        pd.Timestamp(day).normalize() + pd.Timedelta(days=1)
    ).strftime("%Y-%m-%d")
    return {
        symbol: rules.load_bars(symbol, start, end_exclusive)
        for symbol in sorted(affected["symbol"].astype(str).str.upper().unique())
    }


def apply_live_baseline_filters(
    trades: pd.DataFrame,
    keep_pairs: set[tuple[str, str]],
    day: pd.Timestamp,
    decision_records: tuple[decision_model.DecisionRecord, ...]
    | list[decision_model.DecisionRecord]
    | None = None,
    final_records_out: list[decision_model.DecisionRecord] | None = None,
    journal_path: str | Path = DEFAULT_JOURNAL_PATH,
    model_history: ModelHistorySnapshot | None = None,
    model_history_run_id: str | None = None,
    lhf_cluster_choice_mode: str = "shadow",
) -> pd.DataFrame:
    if lhf_cluster_choice_mode not in {"shadow", "top1"}:
        raise ValueError("lhf_cluster_choice_mode must be shadow or top1")
    filtered = trades.copy()
    steps_by_key: dict[str, list[dict[str, object]]] = {}

    if decision_records is not None:
        record_keys = [record.signal_key for record in decision_records]
        if len(record_keys) != len(set(record_keys)):
            raise ValueError("Live DecisionRecords contain duplicate signal_key values.")
        if not filtered.empty and "signal_key" not in filtered.columns:
            raise ValueError("Live trade rows require signal_key for auditable filtering.")
        if not filtered.empty and filtered["signal_key"].duplicated().any():
            raise ValueError("Live trade rows contain duplicate signal_key values.")
        raw_keys = set(
            filtered.get("signal_key", pd.Series(dtype="object")).astype(str)
        )
        accepted_keys = {
            record.signal_key
            for record in decision_records
            if record.status == "accepted"
        }
        if raw_keys != accepted_keys:
            raise RuntimeError(
                "DecisionRecord/trade mismatch: "
                f"rows={sorted(raw_keys)} records={sorted(accepted_keys)}"
            )
        steps_by_key = {key: [] for key in raw_keys}
        records_by_key = {record.signal_key: record for record in decision_records}

        # DecisionRecord is the auditable source of truth for every contextual
        # live filter.  The engine's compact trade rows are not guaranteed to
        # carry these fields (the 2026-07-23 INTU mismatch exposed this).
        context_sources = {
            "daily_structure_phase_fresh": (
                "daily_structure_phase_fresh",
                "daily_structure_phase",
            ),
            "volume_price_phase": ("volume_price_phase",),
            "bearish_countermove_subregime": (
                "bearish_countermove_subregime",
            ),
        }

        def context_text(record: decision_model.DecisionRecord, names: tuple[str, ...]) -> str:
            for name in names:
                value = record.context.get(name)
                if value is not None and str(value).strip():
                    return str(value).strip()
            return ""

        if not filtered.empty:
            for column, source_names in context_sources.items():
                authoritative = filtered["signal_key"].astype(str).map(
                    lambda key: context_text(records_by_key[key], source_names)
                )
                if column in filtered.columns:
                    supplied = filtered[column].fillna("").astype(str).str.strip()
                    conflicts = supplied.ne("") & authoritative.ne("") & supplied.ne(authoritative)
                    if conflicts.any():
                        keys = filtered.loc[conflicts, "signal_key"].astype(str).tolist()
                        raise RuntimeError(
                            f"Trade row/DecisionRecord context mismatch for {column}: {keys}"
                        )
                filtered[column] = authoritative

    def add_step(
        row: pd.Series,
        code: str,
        passed: bool,
        observed: object,
        operator: str,
        threshold: object,
        reason: str,
    ) -> None:
        if decision_records is None:
            return
        steps_by_key[str(row["signal_key"])].append(
            {
                "factor_code": code,
                "passed": passed,
                "observed_value": observed,
                "operator": operator,
                "threshold": threshold,
                "reason": reason,
            }
        )

    if "track" in filtered.columns:
        for _, row in filtered.iterrows():
            passed = str(row["track"]) == "baseline"
            add_step(
                row,
                "live_baseline_track_selection",
                passed,
                str(row["track"]),
                "equals",
                "baseline",
                "live runner routes the baseline track only",
            )
        filtered = filtered[filtered["track"].astype(str).eq("baseline")].copy()

    if not filtered.empty:
        pair_pass = filtered.apply(
            lambda row: (
                str(row["symbol"]).upper(),
                str(row["rule"]),
            )
            in keep_pairs,
            axis=1,
        )
        for index, row in filtered.iterrows():
            pair = [str(row["symbol"]).upper(), str(row["rule"])]
            add_step(
                row,
                "live_symbol_rule_keep_pair",
                bool(pair_pass.loc[index]),
                pair,
                "is_in",
                sorted(keep_pairs),
                "symbol/rule pair must be enabled for the configured cutoff variant",
            )
        filtered = filtered.loc[pair_pass].copy()

    def text_col(name: str) -> pd.Series:
        if name in filtered.columns:
            return filtered[name].astype(str)
        return pd.Series([""] * len(filtered), index=filtered.index, dtype="object")

    if not filtered.empty:
        structure_phase = text_col("daily_structure_phase_fresh")
        volume_phase = text_col("volume_price_phase")
        countermove_regime = text_col("bearish_countermove_subregime")
        weak_retest_candidates = (
            filtered["rule"].astype(str).eq(
                "bearish_failed_countermove_short_baseline"
            )
            & filtered["side"].astype(str).eq("short")
        )
        missing_context = weak_retest_candidates & (
            structure_phase.str.strip().eq("")
            | volume_phase.str.strip().eq("")
            | countermove_regime.str.strip().eq("")
        )
        if missing_context.any():
            keys = filtered.loc[missing_context, "signal_key"].astype(str).tolist()
            raise RuntimeError(
                "Missing DecisionRecord context for weak-retest live veto: "
                f"{keys}"
            )
        weak_retest = (
            weak_retest_candidates
            & structure_phase.eq("trend_or_unclassified")
            & volume_phase.eq("normal_volume_trend")
            & countermove_regime.eq("bearish_countermove_weak_retest")
        )
        for index, row in filtered.iterrows():
            veto = bool(weak_retest.loc[index])
            add_step(
                row,
                "failed_countermove_weak_retest_veto",
                not veto,
                {
                    "rule": str(row["rule"]),
                    "side": str(row["side"]),
                    "daily_structure_phase_fresh": structure_phase.loc[index],
                    "volume_price_phase": volume_phase.loc[index],
                    "bearish_countermove_subregime": countermove_regime.loc[index],
                },
                "does_not_match_all",
                "weak_retest_veto_tuple",
                "weak failed-countermove retest combination is disabled live",
            )
        if weak_retest.any():
            log(
                "failed_countermove_weak_retest_veto removed "
                f"{int(weak_retest.sum())} live trade(s)"
            )
        filtered = filtered.loc[~weak_retest].copy()

    history = model_history
    if history is None:
        history = load_model_history(
            journal_path,
            strategy_variant_id=decision_model.STRATEGY_VARIANT_ID,
            rule_ids=CORE_SHORT_RULES,
            trading_day=day.date(),
            lookback_calendar_days=2,
            model_run_id=model_history_run_id,
        )
    if history.strategy_variant_id != decision_model.STRATEGY_VARIANT_ID:
        raise ValueError(
            "model history strategy variant does not match the live strategy"
        )
    if not filtered.empty:
        open_symbols = history.open_symbols
        occupied = filtered["symbol"].astype(str).str.upper().isin(open_symbols)
        positions_by_symbol: dict[str, list[dict[str, object]]] = {}
        for position in history.open_positions:
            positions_by_symbol.setdefault(position.symbol.upper(), []).append(
                {
                    "signal_key": position.signal_key,
                    "rule": position.rule_id,
                    "side": position.side,
                    "track": position.track,
                    "opened_at_utc": position.opened_at_utc,
                    "entry_price": position.entry_price,
                    "stop_price": position.stop_price,
                    "target_price": position.target_price,
                    "quantity": position.quantity,
                    "source_status": position.source_status,
                }
            )
        for index, row in filtered.iterrows():
            symbol = str(row["symbol"]).upper()
            blocked = bool(occupied.loc[index])
            add_step(
                row,
                "model_open_position_guard",
                not blocked,
                {
                    "symbol": symbol,
                    "open_positions": positions_by_symbol.get(symbol, []),
                    "selected_run_id": history.selected_run_id,
                    "open_position_count": len(history.open_positions),
                    "open_position_source_count": history.open_position_source_count,
                    "open_position_duplicate_count": history.open_position_duplicate_count,
                },
                "symbol_not_in_open_model_positions",
                sorted(open_symbols),
                "an open model/shadow position blocks a new signal for the same symbol",
            )
        if occupied.any():
            blocked_symbols = sorted(
                filtered.loc[occupied, "symbol"].astype(str).str.upper().unique()
            )
            log(
                "model_open_position_guard removed "
                f"{int(occupied.sum())} live trade(s); symbols={blocked_symbols}"
            )
        filtered = filtered.loc[~occupied].copy()
    loss_count = history.loss_count
    loss_net = history.loss_net
    cluster_on = loss_count >= 5 and loss_net <= -350.0

    if not filtered.empty:
        lhf = (
            filtered["rule"].astype(str).eq(
                "bearish_lower_high_failure_short"
            )
            & filtered["side"].astype(str).eq("short")
        )
        veto = lhf & cluster_on
        for index, row in filtered.iterrows():
            is_veto = bool(veto.loc[index])
            add_step(
                row,
                "hard_lhf_cluster_veto_lb2_loss5_net350",
                not is_veto,
                {
                    "history_source": "model_backtest_history",
                    "journal_path": history.database_path,
                    "strategy_variant_id": history.strategy_variant_id,
                    "selected_run_id": history.selected_run_id,
                    "run_selection": history.run_selection,
                    "window_start_utc": history.window_start_utc,
                    "window_end_utc": history.window_end_utc,
                    "counted_trade_count": len(history.trades),
                    "counted_signal_keys": [
                        trade.signal_key for trade in history.trades
                    ],
                    "recent_losses": loss_count,
                    "loss_net": loss_net,
                    "cluster_on": cluster_on,
                    "data_quality": {
                        "status": history.data_quality_status,
                        "source_candidate_count": history.source_candidate_count,
                        "duplicate_signal_count": history.duplicate_signal_count,
                        "run_candidate_count": history.run_candidate_count,
                    },
                    "rule": str(row["rule"]),
                    "side": str(row["side"]),
                },
                "does_not_match_veto",
                {
                    "min_losses": 5,
                    "max_net": -350.0,
                    "rule": "bearish_lower_high_failure_short",
                },
                "LHF shorts are disabled during the configured recent loss cluster",
            )
        if veto.any():
            log(
                "hard_lhf_cluster_veto_lb2_loss5_net350 removed "
                f"{int(veto.sum())} live trade(s); "
                f"recent_losses={loss_count}, net={loss_net:.2f}"
            )
        filtered = filtered.loc[~veto].copy()

    if decision_records is not None and not filtered.empty:
        cluster_candidates = filtered[
            filtered["rule"].astype(str).eq("bearish_lower_high_failure_short")
            & filtered["side"].astype(str).eq("short")
            & filtered["symbol"].astype(str).str.upper().isin(ROUTABLE_LHF_CLUSTER)
        ].copy()
        if len(cluster_candidates) >= 2:
            score_inputs = [
                input_from_record_and_trade(
                    records_by_key[str(row["signal_key"])],
                    row.to_dict(),
                )
                for _, row in cluster_candidates.iterrows()
            ]
            ranked = rank_cluster_choices(score_cluster(score_inputs))
            result_by_key = {result.signal_key: result for result in ranked}
            rank_by_key = {
                result.signal_key: rank
                for rank, result in enumerate(ranked, start=1)
            }
            candidate_symbols = [result.symbol for result in ranked]
            for _, row in cluster_candidates.iterrows():
                key = str(row["signal_key"])
                result = result_by_key[key]
                rank = rank_by_key[key]
                observed = {
                    "choice_score_version": result.choice_score_version,
                    "choice_score": result.choice_score,
                    "rank": rank,
                    "candidate_count": len(ranked),
                    "candidate_symbols_by_rank": candidate_symbols,
                    "diagnostic_setup_score_version": result.score_version,
                    "diagnostic_setup_score": result.score,
                    "components": {
                        "structural_alignment": result.structural_alignment,
                        "setup_quality": result.setup_quality,
                        "reward_quality": result.reward_quality,
                        "relative_weakness": result.relative_weakness,
                    },
                }
                if lhf_cluster_choice_mode == "shadow":
                    add_step(
                        row,
                        "lhf_cluster_choice_crv_shadow_v1",
                        True,
                        {**observed, "selected_shadow": rank == 1},
                        "shadow_rank_only",
                        {"selected_rank": 1, "routing_effect": "none"},
                        "planned-CRV cluster choice is journaled in shadow mode only",
                    )
                else:
                    add_step(
                        row,
                        "lhf_cluster_choice_crv_top1_v1",
                        rank == 1,
                        {
                            **observed,
                            "selected_base": rank == 1,
                            "legacy_shadow_accepted": True,
                            "legacy_shadow_variant_id": LEGACY_SHADOW_VARIANT_ID,
                        },
                        "rank_equals",
                        1,
                        "only the highest planned-CRV routable LHF cluster candidate is traded",
                    )
            if lhf_cluster_choice_mode == "top1":
                selected_key = ranked[0].signal_key
                removed = len(cluster_candidates) - 1
                filtered = filtered.loc[
                    ~filtered["signal_key"].astype(str).isin(
                        set(cluster_candidates["signal_key"].astype(str)) - {selected_key}
                    )
                ].copy()
                log(
                    "lhf_cluster_choice_crv_top1_v1 selected "
                    f"{ranked[0].symbol}; removed={removed} "
                    f"candidates={candidate_symbols}"
                )

    for _, row in filtered.iterrows():
        add_step(
            row,
            "live_pipeline_final_acceptance",
            True,
            "survived",
            "equals",
            "survived",
            "signal survived every live baseline filter",
        )

    if decision_records is not None and final_records_out is not None:
        surviving_keys = set(
            filtered.get("signal_key", pd.Series(dtype="object")).astype(str)
        )
        for record in decision_records:
            steps = steps_by_key.get(record.signal_key, [])
            failed = next(
                (step for step in steps if not bool(step["passed"])),
                None,
            )
            final_records_out.append(
                decision_model.finalize_live_record(
                    record,
                    steps=steps,
                    accepted=record.signal_key in surviving_keys,
                    primary_reason=(
                        str(failed["factor_code"])
                        if failed is not None
                        else record.primary_reason
                    ),
                )
            )
    return filtered.reset_index(drop=True)


def open_order_or_position_exists(ib: IB, symbol: str, account: str) -> bool:
    sym = symbol.upper()
    for pos in ib.positions():
        if str(getattr(pos, "account", "") or "") != account:
            continue
        if getattr(pos.contract, "symbol", "").upper() == sym and float(pos.position or 0) != 0:
            return True
    ib.reqAllOpenOrders()
    ib.sleep(0.5)
    for trade in ib.trades():
        order_account = str(getattr(trade.order, "account", "") or "")
        if order_account and order_account != account:
            continue
        if getattr(trade.contract, "symbol", "").upper() != sym:
            continue
        status = str(getattr(trade.orderStatus, "status", "") or "").lower()
        if status not in {"filled", "cancelled", "inactive"}:
            return True
    return False


def quantity_for_trade(row: pd.Series, risk_per_trade: float) -> int:
    entry = float(row["entry"])
    stop = float(row["stop"])
    distance = abs(entry - stop)
    if not math.isfinite(distance) or distance <= 0:
        return 0
    size_mult = float(row.get("size", 1.0) or 1.0)
    return max(0, int(math.floor((risk_per_trade * size_mult) / distance)))


def legacy_shadow_quantity_for_trade(row: pd.Series, risk_per_trade: float) -> int:
    shadow = row.copy()
    shadow["size"] = float(
        row.get("legacy_shadow_size", row.get("size", 1.0)) or 1.0
    )
    legacy_stop = row.get("legacy_v2_stop")
    if legacy_stop is not None and pd.notna(legacy_stop):
        shadow["stop"] = float(legacy_stop)
    return quantity_for_trade(shadow, risk_per_trade)


def parent_v2_shadow_quantity_for_trade(
    row: pd.Series,
    risk_per_trade: float,
) -> int | None:
    legacy_stop = row.get("legacy_v2_stop")
    if legacy_stop is None or pd.isna(legacy_stop):
        return None
    shadow = row.copy()
    shadow["stop"] = float(legacy_stop)
    return quantity_for_trade(shadow, risk_per_trade)


def _version_record_for_sizing_v2(
    record: decision_model.DecisionRecord,
) -> decision_model.DecisionRecord:
    context = dict(record.context)
    provenance = dict(context.get("version_provenance") or {})
    provenance.update(
        {
            "strategy_variant_id": sizing_v2_manifest.STRATEGY_VARIANT_ID,
            "engine_version": sizing_v2_manifest.ENGINE_VERSION,
            "money_management_version": sizing_v2_manifest.MONEY_MANAGEMENT_VERSION,
            "manifest_hash": sizing_v2_manifest.manifest_hash(),
        }
    )
    context["version_provenance"] = provenance
    return replace(
        record,
        strategy_version=sizing_v2_manifest.STRATEGY_VERSION,
        strategy_variant_id=sizing_v2_manifest.STRATEGY_VARIANT_ID,
        engine_version=sizing_v2_manifest.ENGINE_VERSION,
        money_management_version=sizing_v2_manifest.MONEY_MANAGEMENT_VERSION,
        context=context,
    )


def _version_record_for_fresh_stop_v3(
    record: decision_model.DecisionRecord,
) -> decision_model.DecisionRecord:
    context = dict(record.context)
    provenance = dict(context.get("version_provenance") or {})
    provenance.update(
        {
            "strategy_variant_id": fresh_stop_v3_manifest.STRATEGY_VARIANT_ID,
            "engine_version": fresh_stop_v3_manifest.ENGINE_VERSION,
            "money_management_version": (
                fresh_stop_v3_manifest.MONEY_MANAGEMENT_VERSION
            ),
            "manifest_hash": fresh_stop_v3_manifest.manifest_hash(),
            "parent_variant_id": sizing_v2_manifest.STRATEGY_VARIANT_ID,
        }
    )
    context["version_provenance"] = provenance
    rule_id = str(record.rule_id or record.primary_rule)
    return replace(
        record,
        strategy_version=fresh_stop_v3_manifest.STRATEGY_VERSION,
        strategy_variant_id=fresh_stop_v3_manifest.STRATEGY_VARIANT_ID,
        engine_version=fresh_stop_v3_manifest.ENGINE_VERSION,
        money_management_version=fresh_stop_v3_manifest.MONEY_MANAGEMENT_VERSION,
        rule_version=fresh_stop_v3_manifest.RULE_VERSIONS.get(
            rule_id,
            record.rule_version,
        ),
        context=context,
    )


def _version_record_for_gap_quarter_v4(
    record: decision_model.DecisionRecord,
) -> decision_model.DecisionRecord:
    context = dict(record.context)
    provenance = dict(context.get("version_provenance") or {})
    provenance.update(
        {
            "strategy_variant_id": gap_quarter_v4_manifest.STRATEGY_VARIANT_ID,
            "engine_version": gap_quarter_v4_manifest.ENGINE_VERSION,
            "money_management_version": (
                gap_quarter_v4_manifest.MONEY_MANAGEMENT_VERSION
            ),
            "manifest_hash": gap_quarter_v4_manifest.manifest_hash(),
            "parent_variant_id": fresh_stop_v3_manifest.STRATEGY_VARIANT_ID,
        }
    )
    context["version_provenance"] = provenance
    rule_id = str(record.rule_id or record.primary_rule)
    return replace(
        record,
        strategy_version=gap_quarter_v4_manifest.STRATEGY_VERSION,
        strategy_variant_id=gap_quarter_v4_manifest.STRATEGY_VARIANT_ID,
        engine_version=gap_quarter_v4_manifest.ENGINE_VERSION,
        money_management_version=gap_quarter_v4_manifest.MONEY_MANAGEMENT_VERSION,
        rule_version=gap_quarter_v4_manifest.RULE_VERSIONS.get(
            rule_id,
            record.rule_version,
        ),
        context=context,
    )


def apply_live_gap_breakdown_quarter_sizing(
    trades: pd.DataFrame,
    final_records: list[decision_model.DecisionRecord],
    trading_day: pd.Timestamp,
    risk_per_trade: float,
    *,
    raw_dir: Path,
) -> tuple[pd.DataFrame, list[decision_model.DecisionRecord]]:
    sized = apply_gap_breakdown_quarter_sizing(
        trades,
        trading_day,
        raw_dir=raw_dir,
    )
    rows_by_key = {
        str(row.signal_key): pd.Series(row._asdict())
        for row in sized.itertuples(index=False)
    }
    versioned: list[decision_model.DecisionRecord] = []
    parent_quantities: dict[str, int] = {}
    base_quantities: dict[str, int] = {}
    for source in final_records:
        record = _version_record_for_gap_quarter_v4(source)
        row = rows_by_key.get(record.signal_key)
        if row is None:
            versioned.append(record)
            continue
        parent_row = row.copy()
        parent_row["size"] = float(row["v3_parent_shadow_size"])
        parent_quantity = quantity_for_trade(parent_row, risk_per_trade)
        base_quantity = quantity_for_trade(row, risk_per_trade)
        parent_quantities[record.signal_key] = parent_quantity
        base_quantities[record.signal_key] = base_quantity
        observed = {
            "policy_version": GAP_BREAKDOWN_QUARTER_POLICY_VERSION,
            "parent_variant_id": fresh_stop_v3_manifest.STRATEGY_VARIANT_ID,
            "parent_size_multiplier": float(row["v3_parent_shadow_size"]),
            "risk_multiplier": float(
                row["gap_breakdown_quarter_risk_multiplier"]
            ),
            "base_size_multiplier": float(row["size"]),
            "base_quantity": base_quantity,
            "v3_parent_shadow_quantity": parent_quantity,
            "quantity_delta": base_quantity - parent_quantity,
            "reasons": str(row["gap_breakdown_quarter_reasons"]).split("|"),
            "context": dict(row["gap_breakdown_quarter_context"]),
        }
        factor = decision_model.DecisionFactor(
            factor_code="gap_breakdown_regime_volume_quarter_sizing_v1",
            category="risk",
            observed_value=observed,
            operator="multiplies_v3_parent_size",
            threshold={
                "affected_rule": "bearish_gap_breakdown_continuation_short",
                "triggered_multiplier": 0.25,
                "spx_strong_bull_threshold_net_atr_20": 2.0,
                "relative_strength_threshold_pct": 0.0,
            },
            passed=True,
            is_veto=False,
            reason=(
                "V4 routes the causal quarter-size quantity; V3 remains a virtual shadow"
            ),
            as_of_time_utc=record.decision_time_utc,
        )
        versioned.append(
            replace(
                record,
                factors=record.factors + (factor,),
                context={
                    **dict(record.context),
                    "gap_breakdown_quarter_sizing": observed,
                },
            )
        )
    if not sized.empty:
        sized["v3_parent_shadow_variant_id"] = (
            fresh_stop_v3_manifest.STRATEGY_VARIANT_ID
        )
        sized["v3_parent_shadow_quantity"] = sized["signal_key"].astype(str).map(
            parent_quantities
        )
        sized["base_quantity"] = sized["signal_key"].astype(str).map(
            base_quantities
        )
    return sized, versioned


def apply_live_position_sizing(
    trades: pd.DataFrame,
    final_records: list[decision_model.DecisionRecord],
    context: PositionSizingContext,
    risk_per_trade: float,
    base_variant_id: str,
) -> tuple[pd.DataFrame, list[decision_model.DecisionRecord]]:
    sized = apply_position_sizing(trades, context)
    rows_by_key = {
        str(row.signal_key): pd.Series(row._asdict())
        for row in sized.itertuples(index=False)
    }
    versioned: list[decision_model.DecisionRecord] = []
    for source in final_records:
        record = _version_record_for_sizing_v2(source)
        row = rows_by_key.get(record.signal_key)
        if row is None:
            versioned.append(record)
            continue
        base_quantity = quantity_for_trade(row, risk_per_trade)
        shadow_quantity = legacy_shadow_quantity_for_trade(row, risk_per_trade)
        parent_v2_quantity = parent_v2_shadow_quantity_for_trade(
            row,
            risk_per_trade,
        )
        observed = {
            "policy_version": POSITION_SIZING_POLICY_VERSION,
            "base_variant_id": base_variant_id,
            "legacy_shadow_variant_id": LEGACY_SHADOW_VARIANT_ID,
            "engine_size_multiplier": float(row["legacy_shadow_size"]),
            "risk_multiplier": float(row["regime_volume_risk_multiplier"]),
            "base_size_multiplier": float(row["size"]),
            "base_quantity": base_quantity,
            "legacy_shadow_quantity": shadow_quantity,
            "quantity_delta": base_quantity - shadow_quantity,
            "parent_v2_variant_id": sizing_v2_manifest.STRATEGY_VARIANT_ID,
            "parent_v2_quantity": parent_v2_quantity,
            "reasons": str(row["position_sizing_reasons"]).split("|"),
            "context": context.to_dict(),
        }
        factor = decision_model.DecisionFactor(
            factor_code="regime_volume_position_sizing_v1",
            category="risk",
            observed_value=observed,
            operator="multiplies_engine_size",
            threshold={
                "range_forming": 0.75,
                "sideways_confirmed": 0.50,
                "volume_risk_cap": 0.50,
            },
            passed=True,
            is_veto=False,
            reason=(
                "Base quantity is reduced causally; V1 full quantity remains virtual shadow"
            ),
            as_of_time_utc=record.decision_time_utc,
        )
        versioned.append(
            replace(
                record,
                factors=record.factors + (factor,),
                context={
                    **dict(record.context),
                    "position_sizing": observed,
                },
            )
        )
    if not sized.empty:
        sized["base_quantity"] = [
            quantity_for_trade(pd.Series(row._asdict()), risk_per_trade)
            for row in sized.itertuples(index=False)
        ]
        sized["legacy_shadow_quantity"] = [
            legacy_shadow_quantity_for_trade(pd.Series(row._asdict()), risk_per_trade)
            for row in sized.itertuples(index=False)
        ]
        sized["fresh_stop_parent_v2_quantity"] = [
            parent_v2_shadow_quantity_for_trade(
                pd.Series(row._asdict()),
                risk_per_trade,
            )
            for row in sized.itertuples(index=False)
        ]
    return sized, versioned


def build_live_order_ref(cfg: LiveConfig, symbol: str, rule: str) -> str:
    return f"MRB:{cfg.day:%Y%m%d}:{symbol.upper()}:{rule}"[:45]


def route_trade(
    ib: IB,
    row: pd.Series,
    cfg: LiveConfig,
    bracket_manager: SafeBracketOrderManager,
    journal: ManualRegimeJournalSession | None = None,
):
    symbol = str(row["symbol"]).upper()

    def blocked(
        status: str,
        reason_code: str,
        reason: str,
        requested_quantity: float | None,
        details: dict[str, object],
    ) -> None:
        if journal is not None:
            journal.record_blocker(
                row,
                account=bracket_manager.account,
                client_id=cfg.client_id,
                status=status,
                reason_code=reason_code,
                reason=reason,
                requested_quantity=requested_quantity,
                details={"symbol": symbol, **details},
            )

    contract = contract_for_order(symbol)
    if contract is None:
        log(f"{symbol}: trade generated but symbol is non-routable in this runner; skipped")
        blocked(
            "execution_blocked",
            "non_routable_symbol",
            "trade symbol has no order-contract mapping",
            None,
            {},
        )
        return None
    if open_order_or_position_exists(ib, symbol, bracket_manager.account):
        log(f"{symbol}: existing position/open order found; skipped duplicate")
        blocked(
            "duplicate_exposure",
            "existing_position_or_open_order",
            "existing position or open order blocks duplicate exposure",
            None,
            {"risk_amount": cfg.risk_per_trade, "risk_currency": "USD"},
        )
        return None
    try:
        [qualified] = ib.qualifyContracts(contract)
    except Exception as exc:
        log(f"{symbol}: order contract qualification failed: {exc}")
        blocked(
            "execution_blocked",
            "order_contract_qualification_failed",
            "order contract could not be qualified",
            None,
            {"error": f"{type(exc).__name__}: {exc}"},
        )
        return None

    qty = quantity_for_trade(row, cfg.risk_per_trade)
    shadow_qty = legacy_shadow_quantity_for_trade(row, cfg.risk_per_trade)
    v3_shadow_qty = int(row.get("v3_parent_shadow_quantity", qty) or 0)
    if "gap_breakdown_quarter_risk_multiplier" in row:
        log(
            f"{symbol}: V4 base qty={qty}, V3 shadow qty={v3_shadow_qty}, "
            f"V1 shadow qty={shadow_qty}, "
            "gap_breakdown_multiplier="
            f"{float(row['gap_breakdown_quarter_risk_multiplier']):.2f}, "
            f"reason={row.get('gap_breakdown_quarter_reasons', '')}"
        )
    elif "regime_volume_risk_multiplier" in row:
        log(
            f"{symbol}: V2 base qty={qty}, V1 shadow qty={shadow_qty}, "
            f"risk_multiplier={float(row['regime_volume_risk_multiplier']):.2f}, "
            f"reason={row.get('position_sizing_reasons', '')}"
        )
    if qty <= 0:
        log(f"{symbol}: qty=0 from risk sizing; skipped")
        blocked(
            "blocked_risk",
            "qty_zero",
            "risk sizing produced a zero quantity",
            qty,
            {
                "risk_amount": cfg.risk_per_trade,
                "risk_currency": "USD",
                "entry": float(row["entry"]),
                "stop": float(row["stop"]),
                "v3_parent_shadow_quantity": v3_shadow_qty,
                "legacy_shadow_quantity": shadow_qty,
            },
        )
        return None

    side = str(row["side"]).lower()
    action = "BUY" if side == "long" else "SELL"
    if not cfg.place_orders:
        log(
            f"DRY RUN {symbol}: would send {action} {qty} MKT; "
            f"V3-shadow={v3_shadow_qty}; V1-shadow={shadow_qty}; "
            f"stop={row['stop']} "
            f"target={row['target']} rule={row['rule']}"
        )
        blocked(
            "execution_blocked",
            "dry_run_no_order",
            "order placement is disabled for this run",
            qty,
            {
                "action": action,
                "order_type": "MKT",
                "v3_parent_shadow_quantity": v3_shadow_qty,
                "legacy_shadow_quantity": shadow_qty,
            },
        )
        return None

    try:
        details = ib.reqContractDetails(qualified)
    except Exception as exc:
        blocked(
            "execution_blocked",
            "contract_details_request_failed",
            "contract details request failed",
            qty,
            {"error": f"{type(exc).__name__}: {exc}"},
        )
        raise
    if not details:
        log(f"{symbol}: no contract details/minTick; bracket skipped")
        blocked(
            "execution_blocked",
            "missing_contract_details",
            "no contract details or minTick were returned",
            qty,
            {},
        )
        return None
    tick_size = float(details[0].minTick or 0.01)
    order_ref = build_live_order_ref(cfg, symbol, str(row["rule"]))
    spec = BracketOrderSpec(
        action=action,
        quantity=qty,
        reference_entry_price=float(row["entry"]),
        take_profit_price=float(row["target"]),
        stop_loss_price=float(row["stop"]),
        entry_order_type="MKT",
        limit_price=None,
        parent_tif="DAY",
        exit_tif="GTC",
        outside_rth=False,
        order_ref=order_ref,
        tick_size=tick_size,
    )
    try:
        submission = bracket_manager.submit(qualified, spec)
    except (SafeBracketError, ValueError) as exc:
        log(f"{symbol}: safe bracket rejected: {type(exc).__name__}: {exc}")
        blocked(
            "execution_rejected",
            "safe_bracket_rejected",
            "safe bracket submission or validation failed",
            qty,
            {"error": f"{type(exc).__name__}: {exc}", "order_ref": order_ref},
        )
        raise RuntimeError(f"{symbol}: safe bracket submission failed; routing aborted") from exc
    if journal is not None:
        journal.ingest_submission(
            submission,
            row,
            account=bracket_manager.account,
            client_id=cfg.client_id,
        )
    states = bracket_manager.states(submission)
    state_text = " ".join(
        f"{state.role}=oid{state.order_id}/perm{state.perm_id}/{state.status}"
        for state in states
    )
    log(f"{symbol}: safe bracket acknowledged ref={order_ref} qty={qty} {state_text}")
    return submission


def route_trades(
    ib: IB,
    trades: pd.DataFrame,
    cfg: LiveConfig,
    bracket_manager: SafeBracketOrderManager,
    journal: ManualRegimeJournalSession | None = None,
) -> tuple[object, ...]:
    if trades.empty:
        log("no live baseline trades after filters")
        return ()
    trades = trades.sort_values(["symbol", "rule"]).reset_index(drop=True)
    log(f"routing {len(trades)} live trade(s)")
    submissions = []
    for row in trades.itertuples(index=False):
        submission = route_trade(
            ib,
            pd.Series(row._asdict()),
            cfg,
            bracket_manager,
            journal=journal,
        )
        if submission is not None:
            submissions.append(submission)
    return tuple(submissions)


def resolve_account(ib: IB, requested_account: str | None) -> str:
    accounts = ib.managedAccounts()
    if requested_account:
        if requested_account not in accounts:
            raise RuntimeError("requested account is not managed by this TWS session")
        return requested_account
    if len(accounts) != 1:
        raise RuntimeError("--account is required when TWS exposes zero or multiple accounts")
    return accounts[0]


def require_exact_production_account(ib: IB, expected_account: str) -> str:
    if not ib.isConnected():
        raise RuntimeError("production TWS is not connected")
    try:
        server_time = ib.reqCurrentTime()
    except BaseException as exc:
        raise RuntimeError("production TWS health check failed") from exc
    if server_time is None:
        raise RuntimeError("production TWS health check returned no server time")
    account = resolve_account(ib, expected_account)
    if account.upper().startswith("DU"):
        raise RuntimeError("production-account guard rejected a DU paper account")
    if account != expected_account:
        raise RuntimeError("production account identity does not match configuration")
    return account


def validate_broker_arguments(args: argparse.Namespace) -> None:
    production_values = {
        "--production-host": args.production_host,
        "--production-port": args.production_port,
        "--production-client-id": args.production_client_id,
        "--production-account": args.production_account,
    }
    if args.dual_broker:
        missing = [name for name, value in production_values.items() if value is None]
        if missing:
            raise ValueError("--dual-broker requires " + ", ".join(missing))
        if not args.account:
            raise ValueError("--dual-broker requires the exact paper --account")
        if not str(args.account).upper().startswith("DU"):
            raise ValueError("dual-broker paper --account must start with DU")
        if str(args.production_account).upper().startswith("DU"):
            raise ValueError("--production-account must be a non-DU account")
        if args.account == args.production_account:
            raise ValueError("paper and production accounts must differ")
        if int(args.production_port) <= 0:
            raise ValueError("--production-port must be positive")
        if int(args.production_client_id) < 0:
            raise ValueError("--production-client-id must be non-negative")
        if args.allow_live_account:
            raise ValueError(
                "--allow-live-account cannot be combined with --dual-broker"
            )
        if (
            args.host == args.production_host
            and int(args.port) == int(args.production_port)
            and int(args.client_id) == int(args.production_client_id)
        ):
            raise ValueError("paper and production endpoints need distinct client IDs")
    elif args.production_place_orders or any(
        value is not None for value in production_values.values()
    ):
        raise ValueError("production options require --dual-broker")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--day", default=pd.Timestamp.now(tz="Europe/Berlin").date().isoformat())
    parser.add_argument("--start-local", default="15:00")
    parser.add_argument("--decision-fetch-et", default="09:40")
    parser.add_argument("--risk-per-trade", type=float, default=100.0)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7496)
    parser.add_argument("--client-id", type=int, default=62)
    parser.add_argument("--max-workers", type=int, default=8)
    parser.add_argument(
        "--preflight-only",
        action="store_true",
        help="test required filesystem and multiprocessing permissions, then exit before TWS connection",
    )
    parser.add_argument("--cutoff-variant", default=LIVE_VARIANT)
    parser.add_argument("--account")
    parser.add_argument("--allow-live-account", action="store_true")
    parser.add_argument("--bracket-ack-timeout-seconds", type=float, default=8.0)
    parser.add_argument("--place-orders", action="store_true")
    parser.add_argument("--dual-broker", action="store_true")
    parser.add_argument("--production-host")
    parser.add_argument("--production-port", type=int)
    parser.add_argument("--production-client-id", type=int)
    parser.add_argument("--production-account")
    parser.add_argument("--production-place-orders", action="store_true")
    parser.add_argument(
        "--enable-failed-auction-fly",
        action="store_true",
        help=(
            "monitor and paper-trade the frozen SPY Hard-12 1DTE U10/L10 fly; "
            "this path always rejects non-DU accounts"
        ),
    )
    parser.add_argument(
        "--failed-auction-state-path",
        default=str(DEFAULT_FAILED_AUCTION_STATE_PATH),
    )
    parser.add_argument(
        "--failed-auction-poll-seconds",
        type=float,
        default=15.0,
    )
    parser.add_argument(
        "--enable-spxw-15dte-put-credit",
        action="store_true",
        help=(
            "monitor and paper-trade the causal SPXW 15DTE put-credit spread; "
            "this path always rejects non-DU accounts"
        ),
    )
    parser.add_argument(
        "--spxw-15dte-state-path",
        default=str(DEFAULT_SPXW_15DTE_STATE_PATH),
    )
    parser.add_argument(
        "--spxw-15dte-poll-seconds",
        type=float,
        default=15.0,
    )
    parser.add_argument(
        "--enable-spxw-0dte-bull-put-iu30",
        action="store_true",
        help=(
            "paper-trade the frozen causal 09:40 SPXW 0DTE 20-point bull-put "
            "with strict IU30 butterfly overlay; this path always rejects non-DU accounts"
        ),
    )
    parser.add_argument(
        "--spxw-0dte-state-path",
        default=str(DEFAULT_SPXW_0DTE_IU30_STATE_PATH),
    )
    parser.add_argument("--spxw-0dte-poll-seconds", type=float, default=15.0)
    parser.add_argument("--spxw-0dte-entry-work-minutes", type=int, default=5)
    parser.add_argument(
        "--enable-spxw-75dte-put-bwb",
        action="store_true",
        help=(
            "paper-trade the Thursday 09:32 SPXW 75DTE +35/-55 put broken-wing "
            "butterfly with VIX >= 15 and a resting 40%% profit target; "
            "this path always rejects non-DU accounts"
        ),
    )
    parser.add_argument(
        "--spxw-75dte-bwb-state-path",
        default=str(DEFAULT_SPXW_75DTE_BWB_STATE_PATH),
    )
    parser.add_argument("--spxw-75dte-bwb-poll-seconds", type=float, default=15.0)
    parser.add_argument("--spxw-75dte-bwb-initial-entry-work-minutes", type=int, default=5)
    parser.add_argument("--spxw-75dte-bwb-entry-work-minutes", type=int, default=5)
    parser.add_argument("--spxw-75dte-bwb-final-entry-work-minutes", type=int, default=5)
    parser.add_argument("--spxw-75dte-bwb-max-risk-usd", type=float, default=10_500.0)
    parser.add_argument(
        "--enable-xsp-vix-pullback",
        action="store_true",
        help=(
            "paper-trade the monitored XSP VIX-pullback 100DTE delta-20 "
            "10-point put-credit spread with TP60 and a 60-DTE time exit; "
            "this path always rejects non-DU accounts"
        ),
    )
    parser.add_argument(
        "--xsp-vix-pullback-state-path",
        default=str(DEFAULT_XSP_VIX_PULLBACK_STATE_PATH),
    )
    parser.add_argument("--xsp-vix-pullback-poll-seconds", type=float, default=15.0)
    parser.add_argument("--xsp-vix-pullback-entry-work-minutes", type=int, default=10)
    parser.add_argument(
        "--enable-stock-ivr70-put-credit",
        action="store_true",
        help=(
            "scan and paper-trade the frozen multi-stock IVR-cross-above-70 "
            "35-50 DTE put-credit strategy; this path always rejects non-DU accounts"
        ),
    )
    parser.add_argument(
        "--stock-ivr70-state-path",
        default=str(DEFAULT_STOCK_IVR70_STATE_PATH),
    )
    parser.add_argument("--stock-ivr70-candidates-path")
    parser.add_argument("--stock-ivr70-poll-seconds", type=float, default=15.0)
    parser.add_argument("--stock-ivr70-entry-work-minutes", type=int, default=5)
    parser.add_argument(
        "--enable-lhf-eod-profit-protection",
        action="store_true",
        help=(
            "paper-only: close open bearish_lower_high_failure_short stock trades "
            "at 15:55 ET when the completed 15:50 bar has at least +1R; retain "
            "the original TP/SL path as a virtual shadow"
        ),
    )
    parser.add_argument("--lhf-eod-state-path", default=str(DEFAULT_LHF_EOD_STATE_PATH))
    parser.add_argument("--lhf-eod-poll-seconds", type=float, default=15.0)
    parser.add_argument("--options-account-value-usd", type=float, default=25_000.0)
    parser.add_argument("--options-max-risk-fraction", type=float, default=0.10)
    parser.add_argument("--journal-path", default=str(DEFAULT_JOURNAL_PATH))
    parser.add_argument(
        "--model-history-run-id",
        help=(
            "completed backtest/shadow run used exclusively for model state; "
            "required when the journal has no single canonical model-history run"
        ),
    )
    args = parser.parse_args()
    validate_broker_arguments(args)

    day = pd.Timestamp(args.day).normalize()
    cfg = LiveConfig(
        day=day,
        risk_per_trade=float(args.risk_per_trade),
        host=args.host,
        port=int(args.port),
        client_id=int(args.client_id),
        max_workers=int(args.max_workers),
        executor_backend="process",
        cutoff_variant=args.cutoff_variant,
        place_orders=bool(args.place_orders),
        require_paper_account=not bool(args.allow_live_account),
        bracket_ack_timeout_seconds=float(args.bracket_ack_timeout_seconds),
    )

    LOG_ROOT.mkdir(parents=True, exist_ok=True)
    overlay_dir = OVERLAY_ROOT / day.strftime("%Y%m%d")
    overlay_dir.mkdir(parents=True, exist_ok=True)

    state_paths = [
        args.failed_auction_state_path,
        args.spxw_15dte_state_path,
        args.spxw_0dte_state_path,
        args.spxw_75dte_bwb_state_path,
        args.xsp_vix_pullback_state_path,
        args.stock_ivr70_state_path,
        args.lhf_eod_state_path,
    ]
    permission_directories = [
        LOG_ROOT,
        overlay_dir,
        Path(args.journal_path).parent,
        *(Path(path).parent for path in state_paths),
    ]
    executor_backend = run_startup_permission_preflight(
        max_workers=cfg.max_workers,
        writable_directories=permission_directories,
    )
    cfg = replace(cfg, executor_backend=executor_backend)
    if args.preflight_only:
        log(f"startup permission preflight-only completed executor_backend={executor_backend}")
        return

    log(
        "manual-regime live bot starting "
        f"variant={gap_quarter_v4_manifest.STRATEGY_VARIANT_ID} "
        f"day={day.date()} place_orders={cfg.place_orders} "
        f"risk_per_trade={cfg.risk_per_trade:.2f} "
        f"legacy_shadow={LEGACY_SHADOW_VARIANT_ID}"
    )
    wait_until_local_time(day, args.start_local)

    journal_store = JournalStore(args.journal_path)
    register_gap_quarter_v4_journal_manifest(journal_store)
    journal_mode = "live" if args.allow_live_account and cfg.place_orders else "paper"
    journal_parameters = {
        "cutoff_variant": cfg.cutoff_variant,
        "risk_per_trade": cfg.risk_per_trade,
        "place_orders": cfg.place_orders,
        "require_paper_account": cfg.require_paper_account,
        "client_id": cfg.client_id,
        "model_history_run_id": args.model_history_run_id,
        "lhf_eod_profit_protection_1r": bool(args.enable_lhf_eod_profit_protection),
        "position_sizing_policy_version": POSITION_SIZING_POLICY_VERSION,
        "fresh_stop_policy_version": FRESH_STOP_POLICY_VERSION,
        "fresh_stop_parent_variant_id": sizing_v2_manifest.STRATEGY_VARIANT_ID,
        "gap_breakdown_quarter_policy_version": (
            GAP_BREAKDOWN_QUARTER_POLICY_VERSION
        ),
        "gap_breakdown_quarter_parent_variant_id": (
            fresh_stop_v3_manifest.STRATEGY_VARIANT_ID
        ),
        "legacy_shadow_variant_id": LEGACY_SHADOW_VARIANT_ID,
        "lhf_cluster_choice_mode": "top1",
    }
    journal: ManualRegimeJournalSession | None = None
    production_journal: ManualRegimeJournalSession | None = None
    failed_auction_monitor: FailedAuctionFlyMonitor | None = None
    spxw_15dte_monitor: Spxw15DtePutCreditMonitor | None = None
    spxw_0dte_monitor: Spxw0DteBullPutIu30Monitor | None = None
    spxw_75dte_bwb_monitor: Spxw75DtePutBwbMonitor | None = None
    xsp_vix_pullback_monitor: XspVixPullbackMonitor | None = None
    stock_ivr70_monitor: StockIvr70PutCreditMonitor | None = None
    lhf_eod_monitor: ManualRegimeLhfEodMonitor | None = None
    run_status = "failed"
    run_error: str | None = None
    production_run_status = "failed"
    production_run_error: str | None = None
    ib = IB()
    production_ib = IB()
    production_cfg: LiveConfig | None = None
    production_account: str | None = None
    production_bracket_manager: SafeBracketOrderManager | None = None
    try:
        log(f"connecting TWS {cfg.host}:{cfg.port} clientId={cfg.client_id}")
        ib.connect(cfg.host, cfg.port, clientId=cfg.client_id, timeout=10)
        account = resolve_account(ib, args.account)
        if cfg.require_paper_account and not account.upper().startswith("DU"):
            raise RuntimeError("paper-account guard rejected the connected primary account")
        if args.dual_broker:
            production_cfg = replace(
                cfg,
                host=str(args.production_host),
                port=int(args.production_port),
                client_id=int(args.production_client_id),
                place_orders=bool(args.production_place_orders),
                require_paper_account=False,
            )
            try:
                log(
                    "checking production TWS "
                    f"{production_cfg.host}:{production_cfg.port} "
                    f"clientId={production_cfg.client_id}"
                )
                production_ib.connect(
                    production_cfg.host,
                    production_cfg.port,
                    clientId=production_cfg.client_id,
                    timeout=10,
                )
                production_account = require_exact_production_account(
                    production_ib,
                    str(args.production_account),
                )
                production_bracket_manager = SafeBracketOrderManager(
                    production_ib,
                    account=production_account,
                    require_paper_account=False,
                    ack_timeout_seconds=production_cfg.bracket_ack_timeout_seconds,
                )
                production_bracket_manager.validate_connection()
                log(
                    "production TWS validated; only manual-regime stock trades are "
                    f"eligible place_orders={production_cfg.place_orders}"
                )
            except BaseException as exc:
                production_run_error = f"{type(exc).__name__}: {exc}"
                log(
                    "production TWS unavailable or rejected; production is disabled "
                    f"for this cycle ({production_run_error})"
                )
                production_account = None
                production_bracket_manager = None
                if production_ib.isConnected():
                    production_ib.disconnect()
        if args.enable_lhf_eod_profit_protection:
            lhf_eod_monitor = ManualRegimeLhfEodMonitor(
                ib,
                LhfEodMonitorConfig(
                    account=account,
                    client_id=cfg.client_id,
                    place_orders=cfg.place_orders,
                    journal_path=Path(args.journal_path),
                    state_path=Path(args.lhf_eod_state_path),
                    poll_seconds=float(args.lhf_eod_poll_seconds),
                    profit_threshold_r=1.0,
                    cancel_timeout_seconds=cfg.bracket_ack_timeout_seconds,
                    strategy_variant_id=gap_quarter_v4_manifest.STRATEGY_VARIANT_ID,
                ),
                logger=log,
            )
            log(
                "paper-only LHF EOD profit protection enabled "
                "threshold=1.00R decision_bar=15:50 exit_from=15:55 shadow=original_TP_SL"
            )
        options_risk_budget: OptionsRiskBudget | None = None
        if (
            args.enable_spxw_15dte_put_credit
            or args.enable_spxw_0dte_bull_put_iu30
            or args.enable_xsp_vix_pullback
            or args.enable_stock_ivr70_put_credit
        ):
            if args.options_account_value_usd <= 0:
                raise ValueError("--options-account-value-usd must be positive")
            if not 0 < args.options_max_risk_fraction <= 1:
                raise ValueError("--options-max-risk-fraction must be in (0, 1]")
            options_risk_budget = OptionsRiskBudget(
                float(args.options_account_value_usd)
                * float(args.options_max_risk_fraction)
            )
            log(
                "shared options defined-risk budget enabled "
                f"maximum=${options_risk_budget.maximum_open_risk_usd:,.2f}"
            )
        if args.enable_failed_auction_fly:
            failed_auction_monitor = FailedAuctionFlyMonitor(
                ib,
                FlyMonitorConfig(
                    day=day,
                    account=account,
                    client_id=cfg.client_id,
                    place_orders=cfg.place_orders,
                    journal_path=Path(args.journal_path),
                    state_path=Path(args.failed_auction_state_path),
                    poll_seconds=float(args.failed_auction_poll_seconds),
                ),
                logger=log,
            )
            log(
                "failed-auction SPY 1DTE U10/L10 fly monitor enabled "
                f"paper_only=true place_orders={cfg.place_orders}"
            )
        if args.enable_stock_ivr70_put_credit:
            assert options_risk_budget is not None
            candidate_path = (
                Path(args.stock_ivr70_candidates_path)
                if args.stock_ivr70_candidates_path
                else LOG_ROOT / f"stock_ivr70_live_candidates_{day:%Y%m%d}.csv"
            )
            diagnostics_path = candidate_path.with_name(
                f"{candidate_path.stem}_diagnostics.csv"
            )
            candidates, diagnostics = build_live_candidates(
                day,
                LiveScanConfig(
                    universe_path=STOCK_IVR70_UNIVERSE_PATH,
                    raw_dir=Path(r"D:\Trading\Rohdaten"),
                    vix_path=Path(r"D:\Trading\Rohdaten\Rohdaten_VIX.csv"),
                    spy_path=Path(r"D:\Trading\Rohdaten\Rohdaten_SPY.csv"),
                    sec_cache_dir=STOCK_IVR70_SEC_CACHE,
                    min_latest_price=80.0,
                    min_median_dollar_volume=100_000_000.0,
                    min_sessions=750,
                    min_last_session=day - pd.Timedelta(days=10),
                    ivr_threshold=70.0,
                    ivr_lookback=252,
                    min_ivr_observations=126,
                    iv_rank_dte=42,
                    market_weight=0.5,
                    earnings_jump=0.0,
                    earnings_cycle_lookback=6,
                    vrp_min=0.7,
                    vrp_max=1.8,
                    maximum_expiry_dte=50,
                    earnings_buffer_days=7,
                ),
            )
            candidate_path.parent.mkdir(parents=True, exist_ok=True)
            candidates.to_csv(candidate_path, index=False)
            diagnostics.to_csv(diagnostics_path, index=False)
            stock_ivr70_monitor = StockIvr70PutCreditMonitor(
                ib,
                StockIvr70MonitorConfig(
                    day=day,
                    account=account,
                    client_id=cfg.client_id,
                    place_orders=cfg.place_orders,
                    journal_path=Path(args.journal_path),
                    candidates_path=candidate_path,
                    state_path=Path(args.stock_ivr70_state_path),
                    poll_seconds=float(args.stock_ivr70_poll_seconds),
                    entry_work_minutes=int(args.stock_ivr70_entry_work_minutes),
                ),
                options_risk_budget=options_risk_budget,
                logger=log,
            )
            log(
                "multi-stock IVR70 put-credit monitor enabled "
                f"paper_only=true place_orders={cfg.place_orders} "
                f"candidates={len(candidates)} candidate_file={candidate_path}"
            )
        if args.enable_spxw_15dte_put_credit:
            spxw_15dte_monitor = Spxw15DtePutCreditMonitor(
                ib,
                Spxw15DteMonitorConfig(
                    day=day,
                    account=account,
                    client_id=cfg.client_id,
                    place_orders=cfg.place_orders,
                    journal_path=Path(args.journal_path),
                    state_path=Path(args.spxw_15dte_state_path),
                    poll_seconds=float(args.spxw_15dte_poll_seconds),
                ),
                options_risk_budget=options_risk_budget,
                logger=log,
            )
            log(
                "SPXW causal 15DTE put-credit monitor enabled "
                f"paper_only=true place_orders={cfg.place_orders}"
            )
        if args.enable_spxw_0dte_bull_put_iu30:
            assert options_risk_budget is not None
            spxw_0dte_monitor = Spxw0DteBullPutIu30Monitor(
                ib,
                Spxw0DteIu30MonitorConfig(
                    day=day,
                    account=account,
                    client_id=cfg.client_id,
                    place_orders=cfg.place_orders,
                    journal_path=Path(args.journal_path),
                    state_path=Path(args.spxw_0dte_state_path),
                    entry_work_minutes=int(args.spxw_0dte_entry_work_minutes),
                ),
                options_risk_budget=options_risk_budget,
                logger=log,
            )
            log(
                "SPXW 0DTE bull-put + IU30 09:40 monitor enabled "
                f"paper_only=true place_orders={cfg.place_orders}"
            )
        if args.enable_spxw_75dte_put_bwb:
            if args.spxw_75dte_bwb_max_risk_usd <= 0:
                raise ValueError("--spxw-75dte-bwb-max-risk-usd must be positive")
            if args.spxw_75dte_bwb_initial_entry_work_minutes <= 0:
                raise ValueError("--spxw-75dte-bwb-initial-entry-work-minutes must be positive")
            if args.spxw_75dte_bwb_entry_work_minutes <= 0:
                raise ValueError("--spxw-75dte-bwb-entry-work-minutes must be positive")
            if args.spxw_75dte_bwb_final_entry_work_minutes <= 0:
                raise ValueError("--spxw-75dte-bwb-final-entry-work-minutes must be positive")
            bwb_risk_budget = OptionsRiskBudget(float(args.spxw_75dte_bwb_max_risk_usd))
            spxw_75dte_bwb_monitor = Spxw75DtePutBwbMonitor(
                ib,
                Spxw75DteBwbMonitorConfig(
                    day=day,
                    account=account,
                    client_id=cfg.client_id,
                    place_orders=cfg.place_orders,
                    journal_path=Path(args.journal_path),
                    state_path=Path(args.spxw_75dte_bwb_state_path),
                    poll_seconds=float(args.spxw_75dte_bwb_poll_seconds),
                    initial_entry_work_minutes=int(args.spxw_75dte_bwb_initial_entry_work_minutes),
                    entry_work_minutes=int(args.spxw_75dte_bwb_entry_work_minutes),
                    final_entry_work_minutes=int(args.spxw_75dte_bwb_final_entry_work_minutes),
                ),
                options_risk_budget=bwb_risk_budget,
                logger=log,
            )
            log(
                "SPXW Thursday 75DTE put-BWB monitor enabled "
                f"paper_only=true place_orders={cfg.place_orders} "
                f"dedicated_max_risk=${bwb_risk_budget.maximum_open_risk_usd:,.2f}"
            )
        if args.enable_xsp_vix_pullback:
            assert options_risk_budget is not None
            xsp_vix_pullback_monitor = XspVixPullbackMonitor(
                ib,
                XspVixPullbackMonitorConfig(
                    day=day,
                    account=account,
                    client_id=cfg.client_id,
                    place_orders=cfg.place_orders,
                    journal_path=Path(args.journal_path),
                    state_path=Path(args.xsp_vix_pullback_state_path),
                    poll_seconds=float(args.xsp_vix_pullback_poll_seconds),
                    entry_work_minutes=int(args.xsp_vix_pullback_entry_work_minutes),
                ),
                options_risk_budget=options_risk_budget,
                logger=log,
            )
            log(
                "XSP VIX-pullback 100DTE put-credit monitor enabled "
                f"paper_only=true place_orders={cfg.place_orders} "
                f"shared_max_risk=${options_risk_budget.maximum_open_risk_usd:,.2f}"
            )
        journal = ManualRegimeJournalSession(
            journal_store,
            day=day,
            run_mode=journal_mode,
            strategy_id=gap_quarter_v4_manifest.STRATEGY_ID,
            strategy_version=gap_quarter_v4_manifest.STRATEGY_VERSION,
            strategy_variant_id=gap_quarter_v4_manifest.STRATEGY_VARIANT_ID,
            engine_version=gap_quarter_v4_manifest.ENGINE_VERSION,
            money_management_version=gap_quarter_v4_manifest.MONEY_MANAGEMENT_VERSION,
            config_hash=decision_model.stable_config_hash(journal_parameters),
            account=account,
            parameters=journal_parameters,
            metadata={"runner": "live_manual_regime_tws_bot.py", "account": account},
        )
        journal.start()
        if (
            args.dual_broker
            and production_cfg is not None
            and production_account is not None
            and production_bracket_manager is not None
        ):
            production_parameters = {
                **journal_parameters,
                "place_orders": production_cfg.place_orders,
                "require_paper_account": False,
                "client_id": production_cfg.client_id,
                "broker_role": "production",
                "approved_strategy_scope": [
                    gap_quarter_v4_manifest.STRATEGY_VARIANT_ID
                ],
                "paper_only_option_monitors_enabled": False,
            }
            production_journal = ManualRegimeJournalSession(
                journal_store,
                day=day,
                run_mode="live",
                strategy_id=gap_quarter_v4_manifest.STRATEGY_ID,
                strategy_version=gap_quarter_v4_manifest.STRATEGY_VERSION,
                strategy_variant_id=gap_quarter_v4_manifest.STRATEGY_VARIANT_ID,
                engine_version=gap_quarter_v4_manifest.ENGINE_VERSION,
                money_management_version=gap_quarter_v4_manifest.MONEY_MANAGEMENT_VERSION,
                config_hash=decision_model.stable_config_hash(production_parameters),
                account=production_account,
                parameters=production_parameters,
                metadata={
                    "runner": "live_manual_regime_tws_bot.py",
                    "account": production_account,
                    "broker_role": "production",
                    "shared_decision_source": "primary_paper_connection",
                },
            )
            production_journal.start()
        bracket_manager = SafeBracketOrderManager(
            ib,
            account=account,
            require_paper_account=cfg.require_paper_account,
            ack_timeout_seconds=cfg.bracket_ack_timeout_seconds,
        )
        bracket_manager.validate_connection()
        masked_account = f"{'*' * max(0, len(account) - 4)}{account[-4:]}"
        log(
            f"order account={masked_account} "
            f"paper_guard={cfg.require_paper_account} safe_attached_bracket=true"
        )
        def service_option_monitors() -> None:
            if failed_auction_monitor is not None:
                state = load_failed_auction_state(Path(args.failed_auction_state_path))
                if state is not None and state.status not in FAILED_AUCTION_TERMINAL_STATUSES:
                    failed_auction_monitor.safe_tick()
            if spxw_15dte_monitor is not None:
                spxw_15dte_monitor.safe_tick()
            if spxw_0dte_monitor is not None:
                spxw_0dte_monitor.safe_tick()
            if spxw_75dte_bwb_monitor is not None:
                spxw_75dte_bwb_monitor.safe_tick()
            if xsp_vix_pullback_monitor is not None:
                xsp_vix_pullback_monitor.safe_tick()
            if stock_ivr70_monitor is not None:
                stock_ivr70_monitor.safe_tick()
            if lhf_eod_monitor is not None:
                lhf_eod_monitor.safe_tick()

        def run_option_monitors_until_close() -> None:
            if (
                failed_auction_monitor is None
                and spxw_15dte_monitor is None
                and spxw_0dte_monitor is None
                and spxw_75dte_bwb_monitor is None
                and xsp_vix_pullback_monitor is None
                and stock_ivr70_monitor is None
                and lhf_eod_monitor is None
            ):
                return
            poll = min(
                [
                    value
                    for value in (
                        float(args.failed_auction_poll_seconds) if failed_auction_monitor else None,
                        float(args.spxw_15dte_poll_seconds) if spxw_15dte_monitor else None,
                        float(args.spxw_0dte_poll_seconds) if spxw_0dte_monitor else None,
                        float(args.spxw_75dte_bwb_poll_seconds) if spxw_75dte_bwb_monitor else None,
                        float(args.xsp_vix_pullback_poll_seconds) if xsp_vix_pullback_monitor else None,
                        float(args.stock_ivr70_poll_seconds) if stock_ivr70_monitor else None,
                        float(args.lhf_eod_poll_seconds) if lhf_eod_monitor else None,
                    )
                    if value is not None
                ]
            )
            while True:
                now_et = pd.Timestamp.now(tz="America/New_York")
                if failed_auction_monitor is not None:
                    failed_auction_monitor.safe_tick(now_et)
                if spxw_15dte_monitor is not None:
                    spxw_15dte_monitor.safe_tick(now_et)
                if spxw_0dte_monitor is not None:
                    spxw_0dte_monitor.safe_tick(now_et)
                if spxw_75dte_bwb_monitor is not None:
                    spxw_75dte_bwb_monitor.safe_tick(now_et)
                if xsp_vix_pullback_monitor is not None:
                    xsp_vix_pullback_monitor.safe_tick(now_et)
                if stock_ivr70_monitor is not None:
                    stock_ivr70_monitor.safe_tick(now_et)
                if lhf_eod_monitor is not None:
                    lhf_eod_monitor.safe_tick(now_et)
                if now_et.strftime("%H:%M") >= "16:05":
                    return
                ib.sleep(max(poll, 1.0))

        wait_until_et(
            day,
            args.decision_fetch_et,
            while_waiting=service_option_monitors,
        )

        top50 = load_top50_symbols()
        engine_symbols = list(top50)
        if spxw_0dte_monitor is not None and "SPX" not in engine_symbols:
            # The option strategy requires its own SPX regime context even if a
            # future stock-universe revision no longer contains SPX.
            engine_symbols.append("SPX")
        keep_pairs = load_keep_pairs(cfg.cutoff_variant)
        log(
            f"loaded top50={len(top50)} engine_symbols={len(engine_symbols)} "
            f"keep_pairs={len(keep_pairs)}"
        )

        pipeline_inputs: list[decision_model.DecisionRecord] = []
        decision_path = (
            LOG_ROOT / f"manual_regime_live_decisions_{day:%Y%m%d}.jsonl"
        )
        usable_symbols = build_live_overlay(
            ib,
            engine_symbols,
            day,
            overlay_dir,
            missing_data_records_out=pipeline_inputs,
        )
        if not usable_symbols:
            versioned_inputs = [
                _version_record_for_fresh_stop_v3(
                    _version_record_for_sizing_v2(record)
                )
                for record in pipeline_inputs
            ]
            decision_model.write_jsonl(decision_path, versioned_inputs)
            journal.ingest_decisions(versioned_inputs)
            if production_journal is not None:
                production_journal.ingest_decisions(versioned_inputs)
            log(
                f"wrote final live decisions {decision_path} "
                f"records={len(versioned_inputs)}"
            )
            log("no symbols had first two bars; stopping")
            if any(
                monitor is not None
                for monitor in (
                    failed_auction_monitor,
                    spxw_15dte_monitor,
                    spxw_0dte_monitor,
                    spxw_75dte_bwb_monitor,
                    xsp_vix_pullback_monitor,
                    stock_ivr70_monitor,
                    lhf_eod_monitor,
                )
            ):
                log("starting option monitors despite empty stock universe")
                run_option_monitors_until_close()
            run_status = "completed"
            if production_journal is not None:
                production_run_status = "completed"
            return

        raw_trades = collect_trades(
            usable_symbols,
            cfg,
            overlay_dir,
            decision_records_out=pipeline_inputs,
        )
        log(f"raw live engine trades={len(raw_trades)}")
        if spxw_0dte_monitor is not None:
            spxw_0dte_monitor.safe_evaluate_and_submit(
                pipeline_inputs,
                top50,
                pd.Timestamp.now(tz="America/New_York"),
            )
        final_records: list[decision_model.DecisionRecord] = []
        live_trades = apply_live_baseline_filters(
            raw_trades,
            keep_pairs,
            day,
            decision_records=pipeline_inputs,
            final_records_out=final_records,
            journal_path=args.journal_path,
            model_history_run_id=args.model_history_run_id,
            lhf_cluster_choice_mode="top1",
        )
        fresh_stop_bars = load_fresh_stop_context_bars(live_trades, day)
        live_trades, final_records = apply_fresh_structure_stop(
            live_trades,
            final_records,
            day,
            fresh_stop_bars,
        )
        sizing_context = build_position_sizing_context(day)
        live_trades, final_records = apply_live_position_sizing(
            live_trades,
            final_records,
            sizing_context,
            cfg.risk_per_trade,
            fresh_stop_v3_manifest.STRATEGY_VARIANT_ID,
        )
        final_records = [
            _version_record_for_fresh_stop_v3(record) for record in final_records
        ]
        live_trades, final_records = apply_live_gap_breakdown_quarter_sizing(
            live_trades,
            final_records,
            day,
            cfg.risk_per_trade,
            raw_dir=rules.RAW_DIR,
        )
        log(
            "V4 fresh-stop plus regime/volume and gap-breakdown quarter sizing "
            f"structure={sizing_context.structure_score}/"
            f"{sizing_context.structure_phase} "
            f"volume_risk={sizing_context.vp_overhead_magnet_short_risk} "
            f"context_available="
            f"{sizing_context.structure_available and sizing_context.volume_available}"
        )
        decision_model.write_jsonl(decision_path, final_records)
        journal.ingest_decisions(final_records)
        if production_journal is not None:
            production_journal.ingest_decisions(final_records)
        log(
            f"wrote final live decisions {decision_path} "
            f"records={len(final_records)}"
        )
        log(f"active V4 baseline live trades={len(live_trades)}")

        out_path = LOG_ROOT / f"manual_regime_live_trades_{day:%Y%m%d}.csv"
        live_trades.to_csv(out_path, index=False)
        log(f"wrote live trade plan {out_path}")

        quantities_by_signal = {
            str(row.signal_key): quantity_for_trade(
                pd.Series(row._asdict()),
                cfg.risk_per_trade,
            )
            for row in live_trades.itertuples(index=False)
        }
        journal.create_stock_trade_intents(
            live_trades,
            final_records,
            quantities_by_signal,
        )
        if production_journal is not None:
            production_journal.create_stock_trade_intents(
                live_trades,
                final_records,
                quantities_by_signal,
            )
        submissions = route_trades(
            ib,
            live_trades,
            cfg,
            bracket_manager,
            journal=journal,
        )
        ib.sleep(5)
        journal.reingest_submissions(submissions)
        if production_journal is not None:
            assert production_cfg is not None
            assert production_account is not None
            assert production_bracket_manager is not None
            try:
                verified_account = require_exact_production_account(
                    production_ib,
                    str(args.production_account),
                )
                if verified_account != production_account:
                    raise RuntimeError(
                        "production account identity changed before order submission"
                    )
                production_bracket_manager.validate_connection()
                log(
                    "routing approved manual-regime stock plan to production "
                    f"place_orders={production_cfg.place_orders}"
                )
                production_submissions = route_trades(
                    production_ib,
                    live_trades,
                    production_cfg,
                    production_bracket_manager,
                    journal=production_journal,
                )
                production_ib.sleep(5)
                production_journal.reingest_submissions(production_submissions)
                production_run_status = "completed"
            except BaseException as exc:
                production_run_error = f"{type(exc).__name__}: {exc}"
                log(
                    "production routing failed closed; paper processing continues "
                    f"({production_run_error})"
                )
        if any(
            monitor is not None
            for monitor in (
                failed_auction_monitor,
                spxw_15dte_monitor,
                spxw_0dte_monitor,
                spxw_75dte_bwb_monitor,
                xsp_vix_pullback_monitor,
                stock_ivr70_monitor,
                lhf_eod_monitor,
            )
        ):
            log("starting persistent option monitors")
            run_option_monitors_until_close()
        run_status = "completed"
    except BaseException as exc:
        run_error = f"{type(exc).__name__}: {exc}"
        if production_journal is not None and production_run_error is None:
            production_run_error = (
                "shared pipeline failed before production completed: " + run_error
            )
        raise
    finally:
        try:
            if journal is not None:
                journal.finish(run_status, error=run_error)
            if production_journal is not None:
                production_journal.finish(
                    production_run_status,
                    error=production_run_error,
                )
        finally:
            if failed_auction_monitor is not None:
                failed_auction_monitor.close()
            if spxw_15dte_monitor is not None:
                spxw_15dte_monitor.close()
            if spxw_0dte_monitor is not None:
                spxw_0dte_monitor.close()
            if spxw_75dte_bwb_monitor is not None:
                spxw_75dte_bwb_monitor.close()
            if xsp_vix_pullback_monitor is not None:
                xsp_vix_pullback_monitor.close()
            if stock_ivr70_monitor is not None:
                stock_ivr70_monitor.close()
            if lhf_eod_monitor is not None:
                lhf_eod_monitor.close()
            journal_store.close()
            if production_ib.isConnected():
                log("disconnecting production TWS")
                production_ib.disconnect()
            if ib.isConnected():
                log("disconnecting primary TWS")
                ib.disconnect()


if __name__ == "__main__":
    main()
