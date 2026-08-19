from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import time
from typing import Any, Callable

import pandas as pd
from ib_insync import IB, MarketOrder, Stock, util

from src.execution.safe_bracket_order import DONE_STATES
from src.journal.execution_ingest import ExecutionContext, ExecutionJournalIngestor
from src.journal.models import TradeEventRecord, deterministic_id
from src.journal.store import JournalStore


DEFAULT_STATE_PATH = Path(r"D:\Trading\state\manual_regime_lhf_eod_1r_shadow_v1.json")
STRATEGY_VARIANT_ID = "manual-regime-baseline@1.0.0"
ENTRY_RULE = "bearish_lower_high_failure_short"
EXIT_EVENT = "lhf_eod_profit_protection_1r"
SHADOW_EXIT_EVENT = "lhf_shadow_baseline_closed"
DECISION_BAR_TIME = "15:50"
EXIT_START_TIME = "15:55"
EXIT_CUTOFF_TIME = "15:59"


@dataclass(frozen=True, slots=True)
class MonitorConfig:
    account: str
    client_id: int
    place_orders: bool
    journal_path: Path
    state_path: Path
    poll_seconds: float
    profit_threshold_r: float
    cancel_timeout_seconds: float
    strategy_variant_id: str = STRATEGY_VARIANT_ID


@dataclass(slots=True)
class ShadowPosition:
    trade_id: str
    run_id: str
    decision_id: str | None
    signal_key: str
    symbol: str
    quantity: float
    entry_price: float
    stop_price: float
    target_price: float
    risk_per_share: float
    decision_bar_at_et: str
    decision_bar_close: float
    decision_r: float
    status: str = "triggered_pending_exit"
    exit_order_ref: str | None = None
    exit_order_id: int | None = None
    managed_exit_price: float | None = None
    managed_closed_at_utc: str | None = None
    shadow_last_bar_at_et: str | None = None
    shadow_exit_price: float | None = None
    shadow_exit_reason: str | None = None
    shadow_closed_at_et: str | None = None
    managed_r: float | None = None
    shadow_r: float | None = None
    delta_r: float | None = None
    managed_gross_pnl: float | None = None
    shadow_gross_pnl: float | None = None
    delta_gross_pnl: float | None = None
    last_error: str | None = None


@dataclass(slots=True)
class MonitorState:
    schema_version: int = 1
    evaluated_trade_days: dict[str, list[str]] = field(default_factory=dict)
    positions: list[ShadowPosition] = field(default_factory=list)
    last_error: str | None = None


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def atomic_write_state(path: Path, state: MonitorState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(asdict(state), indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def load_state(path: Path) -> MonitorState:
    if not path.exists():
        return MonitorState()
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1:
        raise RuntimeError(f"unsupported LHF EOD monitor state schema: {path}")
    payload["positions"] = [ShadowPosition(**item) for item in payload.get("positions", [])]
    return MonitorState(**payload)


def normalize_ib_bars(rows: Any) -> pd.DataFrame:
    frame = util.df(rows)
    if frame is None or frame.empty:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
    frame = frame[["date", "open", "high", "low", "close", "volume"]].copy()
    stamps = pd.to_datetime(frame["date"], errors="coerce")
    if getattr(stamps.dt, "tz", None) is not None:
        stamps = stamps.dt.tz_convert("America/New_York").dt.tz_localize(None)
    frame["date"] = stamps
    return frame.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)


def completed_bars(bars: pd.DataFrame, now_et: pd.Timestamp) -> pd.DataFrame:
    local_now = now_et.tz_convert("America/New_York").tz_localize(None)
    stamps = pd.to_datetime(bars["date"], errors="coerce")
    return bars.loc[stamps.add(pd.Timedelta(minutes=5)).le(local_now)].copy()


def decision_bar_for_day(
    bars: pd.DataFrame,
    now_et: pd.Timestamp,
) -> pd.Series | None:
    done = completed_bars(bars, now_et)
    wanted = pd.Timestamp(f"{now_et.date().isoformat()} {DECISION_BAR_TIME}")
    matches = done.loc[pd.to_datetime(done["date"], errors="coerce").eq(wanted)]
    return None if matches.empty else matches.iloc[-1]


def short_profit_r(entry_price: float, stop_price: float, current_price: float) -> float:
    risk = stop_price - entry_price
    if not all(math.isfinite(value) for value in (entry_price, stop_price, current_price)):
        raise ValueError("entry, stop and current prices must be finite")
    if risk <= 0:
        raise ValueError("short trade requires stop_price > entry_price")
    return (entry_price - current_price) / risk


def shadow_exit_from_bars(
    bars: pd.DataFrame,
    *,
    after_bar_et: pd.Timestamp,
    now_et: pd.Timestamp,
    stop_price: float,
    target_price: float,
) -> tuple[pd.Timestamp, float, str] | None:
    done = completed_bars(bars, now_et)
    stamps = pd.to_datetime(done["date"], errors="coerce")
    eligible = done.loc[stamps.gt(after_bar_et)].copy()
    for _, bar in eligible.iterrows():
        stop_hit = float(bar["high"]) >= stop_price
        target_hit = float(bar["low"]) <= target_price
        if stop_hit and target_hit:
            return pd.Timestamp(bar["date"]), stop_price, "stop_same_bar_conservative"
        if stop_hit:
            return pd.Timestamp(bar["date"]), stop_price, "stop"
        if target_hit:
            return pd.Timestamp(bar["date"]), target_price, "target"
    return None


def _order_status(trade: Any) -> str:
    return str(getattr(getattr(trade, "orderStatus", None), "status", "") or "")


class ManualRegimeLhfEodMonitor:
    """Paper-only 1R EOD profit protection with a virtual legacy TP/SL shadow."""

    def __init__(
        self,
        ib: IB,
        config: MonitorConfig,
        *,
        logger: Callable[[str], None] = print,
    ) -> None:
        if not config.account.upper().startswith("DU"):
            raise RuntimeError("LHF EOD profit protection rejects non-paper accounts")
        if config.profit_threshold_r <= 0:
            raise ValueError("profit_threshold_r must be positive")
        if config.cancel_timeout_seconds <= 0:
            raise ValueError("cancel_timeout_seconds must be positive")
        if not config.strategy_variant_id.strip():
            raise ValueError("strategy_variant_id must not be empty")
        self.ib = ib
        self.config = config
        self.log = logger
        self.store = JournalStore(config.journal_path)
        self.executions = ExecutionJournalIngestor(self.store)
        self._contracts: dict[str, Any] = {}

    def close(self) -> None:
        self.store.close()

    def _contract(self, symbol: str) -> Any:
        cached = self._contracts.get(symbol)
        if cached is not None:
            return cached
        qualified = self.ib.qualifyContracts(Stock(symbol, "SMART", "USD"))
        if len(qualified) != 1:
            raise RuntimeError(f"could not qualify stock contract for {symbol}")
        self._contracts[symbol] = qualified[0]
        return qualified[0]

    def _fetch_bars(self, symbol: str) -> pd.DataFrame:
        return normalize_ib_bars(
            self.ib.reqHistoricalData(
                self._contract(symbol),
                endDateTime="",
                durationStr="10 D",
                barSizeSetting="5 mins",
                whatToShow="TRADES",
                useRTH=True,
                formatDate=1,
                keepUpToDate=False,
            )
        )

    def _all_broker_trades(self) -> tuple[Any, ...]:
        trades = list(self.ib.trades())
        try:
            trades.extend(self.ib.reqCompletedOrders(False))
        except Exception as exc:
            self.log(f"LHF EOD completed-order lookup unavailable: {exc}")
        unique: dict[tuple[str, int, int], Any] = {}
        for trade in trades:
            order = getattr(trade, "order", None)
            key = (
                str(getattr(order, "account", "") or ""),
                int(getattr(order, "clientId", 0) or 0),
                int(getattr(order, "orderId", 0) or 0),
            )
            unique[key] = trade
        return tuple(unique.values())

    def _find_broker_trade(self, order_ref: str | None, order_id: int | None) -> Any | None:
        for trade in self._all_broker_trades():
            order = getattr(trade, "order", None)
            if str(getattr(order, "account", "") or "") != self.config.account:
                continue
            if order_ref and str(getattr(order, "orderRef", "") or "") == order_ref:
                return trade
            if order_id and int(getattr(order, "orderId", 0) or 0) == order_id:
                return trade
        return None

    def _context(self, position: ShadowPosition) -> ExecutionContext:
        return ExecutionContext(
            run_id=position.run_id,
            signal_key=position.signal_key,
            trade_id=position.trade_id,
            decision_id=position.decision_id,
            account=self.config.account,
            client_id=self.config.client_id,
            observed_at_utc=now_utc_iso(),
        )

    def _add_event(
        self,
        position: ShadowPosition,
        event_type: str,
        payload: dict[str, object],
    ) -> None:
        occurred = now_utc_iso()
        self.store.add_event(
            TradeEventRecord(
                event_id=deterministic_id(
                    "event", position.trade_id, event_type, position.decision_bar_at_et
                ),
                run_id=position.run_id,
                trade_id=position.trade_id,
                order_id=None,
                signal_key=position.signal_key,
                event_type=event_type,
                occurred_at_utc=occurred,
                payload=payload,
            )
        )

    def _position_at_broker(self, position: ShadowPosition) -> float:
        matches = []
        for broker_position in self.ib.positions():
            contract = getattr(broker_position, "contract", None)
            if (
                str(getattr(broker_position, "account", "") or "") == self.config.account
                and str(getattr(contract, "secType", "") or "").upper() == "STK"
                and str(getattr(contract, "symbol", "") or "").upper() == position.symbol
                and abs(float(getattr(broker_position, "position", 0.0) or 0.0)) > 1e-9
            ):
                matches.append(float(broker_position.position))
        if len(matches) != 1:
            raise RuntimeError(
                f"expected exactly one broker position for {position.symbol}, found {len(matches)}"
            )
        return matches[0]

    @staticmethod
    def _order_identity(row: dict[str, object]) -> tuple[int, int, int]:
        return (
            int(row.get("broker_perm_id") or 0),
            int(row.get("broker_client_id") or 0),
            int(row.get("broker_client_order_id") or 0),
        )

    def _cancel_original_children(self, position: ShadowPosition) -> None:
        rows = self.store.list_orders_for_trade(position.trade_id)
        child_rows = []
        original_refs = set()
        for row in rows:
            metadata = json.loads(str(row["metadata_json"]))
            role = str(metadata.get("role") or "")
            action = str(row.get("action") or "").upper()
            order_type = str(row.get("order_type") or "").upper()
            structural_child = (
                row.get("parent_order_id") is not None
                and action == "BUY"
                and order_type in {"LMT", "STP"}
            )
            if row.get("order_ref"):
                original_refs.add(str(row["order_ref"]))
            if role in {"take_profit", "stop_loss"} or structural_child:
                child_rows.append(row)
        if len(child_rows) != 2:
            raise RuntimeError(
                f"journal does not contain exactly two original bracket children for {position.trade_id}"
            )
        identities = {self._order_identity(row) for row in child_rows}
        self.ib.reqAllOpenOrders()
        self.ib.sleep(0.25)
        matched = []
        for trade in self.ib.openTrades():
            order = getattr(trade, "order", None)
            if str(getattr(order, "account", "") or "") != self.config.account:
                continue
            identity = (
                int(getattr(order, "permId", 0) or 0),
                int(getattr(order, "clientId", 0) or 0),
                int(getattr(order, "orderId", 0) or 0),
            )
            if identity in identities and _order_status(trade) not in DONE_STATES:
                matched.append(trade)
        for trade in matched:
            self.ib.cancelOrder(trade.order)
        deadline = time.monotonic() + self.config.cancel_timeout_seconds
        while time.monotonic() < deadline:
            if all(_order_status(trade) in DONE_STATES for trade in matched):
                break
            self.ib.waitOnUpdate(timeout=0.25)
        if any(_order_status(trade) not in DONE_STATES for trade in matched):
            raise RuntimeError(f"original bracket cancellation was not acknowledged for {position.symbol}")
        self.ib.reqAllOpenOrders()
        self.ib.sleep(0.25)
        conflicting = [
            trade
            for trade in self.ib.openTrades()
            if str(getattr(trade.order, "account", "") or "") == self.config.account
            and _order_status(trade) not in DONE_STATES
            and (
                str(getattr(trade.order, "orderRef", "") or "") in original_refs
                or (
                    str(getattr(getattr(trade, "contract", None), "symbol", "") or "").upper()
                    == position.symbol
                    and str(getattr(trade.order, "action", "") or "").upper() == "BUY"
                )
            )
        ]
        if conflicting:
            raise RuntimeError(f"original bracket still active for {position.symbol}")

    def _submit_exit(self, position: ShadowPosition) -> None:
        order_ref = f"MRX:{position.symbol}:{position.signal_key[-12:]}:LHF1R"[:45]
        existing = self._find_broker_trade(order_ref, None)
        if existing is None:
            try:
                broker_quantity = self._position_at_broker(position)
            except RuntimeError:
                position.status = "activation_attention"
                raise
            if broker_quantity >= 0 or abs(abs(broker_quantity) - position.quantity) > 1e-9:
                position.status = "activation_attention"
                raise RuntimeError(
                    f"broker/journal quantity mismatch for {position.symbol}: "
                    f"broker={broker_quantity:g} journal=-{position.quantity:g}"
                )
            self._cancel_original_children(position)
            try:
                broker_quantity = self._position_at_broker(position)
            except RuntimeError:
                position.status = "activation_attention"
                raise RuntimeError(
                    f"broker position changed while cancelling the original bracket for "
                    f"{position.symbol}; managed market exit was not submitted"
                )
            if broker_quantity >= 0 or abs(abs(broker_quantity) - position.quantity) > 1e-9:
                position.status = "activation_attention"
                raise RuntimeError(
                    f"broker position changed while cancelling the original bracket for "
                    f"{position.symbol}: broker={broker_quantity:g} journal=-{position.quantity:g}; "
                    "managed market exit was not submitted"
                )
            order = MarketOrder("BUY", position.quantity)
            order.account = self.config.account
            order.orderRef = order_ref
            order.tif = "DAY"
            order.outsideRth = False
            existing = self.ib.placeOrder(self._contract(position.symbol), order)
            self.ib.sleep(1.0)
        position.exit_order_ref = order_ref
        position.exit_order_id = int(getattr(existing.order, "orderId", 0) or 0)
        position.status = "exit_submitted"
        self.executions.ingest_trade(existing, self._context(position), role="eod_exit")
        self._add_event(
            position,
            EXIT_EVENT,
            {
                "entry_rule": ENTRY_RULE,
                "profit_threshold_r": self.config.profit_threshold_r,
                "decision_bar_at_et": position.decision_bar_at_et,
                "decision_bar_close": position.decision_bar_close,
                "decision_r": position.decision_r,
                "managed_exit_order_ref": order_ref,
                "shadow_mode": "virtual_original_tp_sl",
                "shadow_stop_price": position.stop_price,
                "shadow_target_price": position.target_price,
            },
        )
        self.log(
            f"LHF EOD 1R exit submitted {position.symbol} qty={position.quantity:g} "
            f"decision_r={position.decision_r:.3f} orderId={position.exit_order_id}"
        )

    def _service_exit(self, position: ShadowPosition) -> None:
        trade = self._find_broker_trade(position.exit_order_ref, position.exit_order_id)
        if trade is None:
            self.log(f"LHF EOD exit not yet visible at broker: {position.symbol}")
            return
        self.executions.ingest_trade(trade, self._context(position), role="eod_exit")
        status = _order_status(trade)
        if status == "Filled":
            lifecycle = self.store.refresh_trade_lifecycle_from_executions(position.trade_id)
            if lifecycle != "closed":
                self.log(
                    f"LHF EOD exit is filled but executions are not complete in the journal yet: "
                    f"{position.symbol}"
                )
                return
            journal_trade = self.store.get_trade(position.trade_id)
            if journal_trade is None or journal_trade.get("exit_price") is None:
                raise RuntimeError(f"closed LHF journal trade has no exit price for {position.symbol}")
            fill_price = float(journal_trade["exit_price"])
            position.managed_exit_price = fill_price
            position.managed_closed_at_utc = now_utc_iso()
            position.managed_r = (position.entry_price - fill_price) / position.risk_per_share
            position.managed_gross_pnl = (
                position.entry_price - fill_price
            ) * position.quantity
            position.status = "managed_closed_shadow_open"
            self.log(f"LHF EOD exit filled {position.symbol} at {fill_price:.4f}")
        elif status in DONE_STATES:
            position.status = "exit_terminal_error"
            position.last_error = f"managed exit terminal status {status}"

    def _service_shadow(
        self,
        position: ShadowPosition,
        now_et: pd.Timestamp,
        bars: pd.DataFrame,
    ) -> None:
        after = pd.Timestamp(position.shadow_last_bar_at_et or position.decision_bar_at_et)
        result = shadow_exit_from_bars(
            bars,
            after_bar_et=after,
            now_et=now_et,
            stop_price=position.stop_price,
            target_price=position.target_price,
        )
        done = completed_bars(bars, now_et)
        if not done.empty:
            position.shadow_last_bar_at_et = pd.Timestamp(done.iloc[-1]["date"]).isoformat()
        if result is None:
            return
        closed_at, exit_price, reason = result
        position.shadow_exit_price = exit_price
        position.shadow_exit_reason = reason
        position.shadow_closed_at_et = closed_at.isoformat()
        position.shadow_r = (position.entry_price - exit_price) / position.risk_per_share
        if position.managed_r is not None:
            position.delta_r = position.managed_r - position.shadow_r
        position.shadow_gross_pnl = (
            position.entry_price - exit_price
        ) * position.quantity
        if position.managed_gross_pnl is not None:
            position.delta_gross_pnl = (
                position.managed_gross_pnl - position.shadow_gross_pnl
            )
        position.status = "complete"
        self._add_event(
            position,
            SHADOW_EXIT_EVENT,
            {
                "shadow_exit_price": exit_price,
                "shadow_exit_reason": reason,
                "shadow_closed_at_et": position.shadow_closed_at_et,
                "managed_exit_price": position.managed_exit_price,
                "managed_r": position.managed_r,
                "shadow_r": position.shadow_r,
                "delta_r": position.delta_r,
                "managed_gross_pnl": position.managed_gross_pnl,
                "shadow_gross_pnl": position.shadow_gross_pnl,
                "delta_gross_pnl": position.delta_gross_pnl,
            },
        )
        self.log(
            f"LHF shadow closed {position.symbol} reason={reason} "
            f"delta={position.delta_r:+.3f}R"
        )

    @staticmethod
    def _kill_switch_active(state: MonitorState) -> bool:
        completed = [position for position in state.positions if position.delta_r is not None]
        return len(completed) >= 5 and sum(float(position.delta_r) for position in completed) <= -2.0

    @staticmethod
    def _shadow_service_due(position: ShadowPosition, now_et: pd.Timestamp) -> bool:
        clock = now_et.strftime("%H:%M")
        if not "09:35" <= clock <= "16:05":
            return False
        expected = now_et.tz_convert("America/New_York").tz_localize(None).floor("5min")
        expected -= pd.Timedelta(minutes=5)
        if position.shadow_last_bar_at_et is None:
            return True
        return pd.Timestamp(position.shadow_last_bar_at_et) < expected

    def _new_position(self, row: dict[str, object], bar: pd.Series) -> ShadowPosition:
        metadata = json.loads(str(row["metadata_json"]))
        entry = float(row["entry_price"])
        stop = float(metadata["planned_stop_price"])
        target = float(metadata["planned_target_price"])
        close = float(bar["close"])
        return ShadowPosition(
            trade_id=str(row["trade_id"]),
            run_id=str(row["run_id"]),
            decision_id=str(row["decision_id"]) if row.get("decision_id") else None,
            signal_key=str(row["signal_key"]),
            symbol=str(row["symbol"]).upper(),
            quantity=float(row["quantity"]),
            entry_price=entry,
            stop_price=stop,
            target_price=target,
            risk_per_share=stop - entry,
            decision_bar_at_et=pd.Timestamp(bar["date"]).isoformat(),
            decision_bar_close=close,
            decision_r=short_profit_r(entry, stop, close),
        )

    def _evaluate_open_trades(self, state: MonitorState, now_et: pd.Timestamp) -> None:
        if not EXIT_START_TIME <= now_et.strftime("%H:%M") < EXIT_CUTOFF_TIME:
            return
        if self._kill_switch_active(state):
            self.log("LHF EOD 1R kill switch active; no new managed exits")
            return
        existing = {position.trade_id for position in state.positions}
        rows = self.store.list_open_trades_for_rule(
            run_type="paper",
            variant_id=self.config.strategy_variant_id,
            asset_class="STK",
            side="short",
            rule=ENTRY_RULE,
            broker_account=self.config.account,
        )
        session_day = now_et.date().isoformat()
        for row in rows:
            trade_id = str(row["trade_id"])
            evaluated_days = state.evaluated_trade_days.setdefault(trade_id, [])
            if session_day in evaluated_days or trade_id in existing:
                continue
            bars = self._fetch_bars(str(row["symbol"]))
            bar = decision_bar_for_day(bars, now_et)
            if bar is None:
                self.log(f"LHF EOD missing completed 15:50 bar for {row['symbol']}")
                continue
            position = self._new_position(row, bar)
            evaluated_days.append(session_day)
            if position.decision_r + 1e-12 < self.config.profit_threshold_r:
                continue
            state.positions.append(position)
            atomic_write_state(self.config.state_path, state)
            if self.config.place_orders:
                try:
                    self._submit_exit(position)
                finally:
                    atomic_write_state(self.config.state_path, state)
            else:
                position.status = "dry_run_triggered"

    def tick(self, now_et: pd.Timestamp | None = None) -> MonitorState:
        now_et = now_et or pd.Timestamp.now(tz="America/New_York")
        if now_et.tzinfo is None:
            now_et = now_et.tz_localize("America/New_York")
        else:
            now_et = now_et.tz_convert("America/New_York")
        state = load_state(self.config.state_path)
        bars_by_symbol: dict[str, pd.DataFrame] = {}
        for position in state.positions:
            try:
                if position.status == "triggered_pending_exit" and self.config.place_orders:
                    self._submit_exit(position)
                if position.status == "exit_submitted":
                    self._service_exit(position)
                if (
                    position.status == "managed_closed_shadow_open"
                    and self._shadow_service_due(position, now_et)
                ):
                    bars = bars_by_symbol.get(position.symbol)
                    if bars is None:
                        bars = self._fetch_bars(position.symbol)
                        bars_by_symbol[position.symbol] = bars
                    self._service_shadow(position, now_et, bars)
            except Exception as exc:
                position.last_error = f"{type(exc).__name__}: {exc}"
                self.log(
                    f"LHF EOD position error {position.symbol} "
                    f"(other strategies continue): {type(exc).__name__}: {exc}"
                )
        self._evaluate_open_trades(state, now_et)
        state.last_error = None
        atomic_write_state(self.config.state_path, state)
        return state

    def safe_tick(self, now_et: pd.Timestamp | None = None) -> MonitorState:
        try:
            return self.tick(now_et)
        except Exception as exc:
            state = load_state(self.config.state_path)
            state.last_error = f"{type(exc).__name__}: {exc}"
            atomic_write_state(self.config.state_path, state)
            self.log(f"LHF EOD monitor error (other strategies continue): {type(exc).__name__}: {exc}")
            return state
