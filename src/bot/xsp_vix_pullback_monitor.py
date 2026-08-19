from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
import time
from typing import Any, Callable

import pandas as pd
from ib_insync import IB, Index

from src.execution.xsp_put_credit import (
    XspPutCreditSelection,
    build_entry_order,
    build_profit_target_order,
    build_time_exit_order,
    quote_quality_passes,
    require_paper_account,
    select_xsp_put_credit,
    selection_for_identity,
)
from src.journal.decision_ingest import DecisionIngestContext, DecisionJournalIngestor, TradeIntent, TradeIntentLeg
from src.journal.execution_ingest import ExecutionContext, ExecutionJournalIngestor
from src.journal.models import StrategyRunRecord, deterministic_id
from src.journal.store import JournalStore
from src.risk.options_risk_budget import OptionsRiskBudget
from src.strategies.xsp_vix_pullback import (
    STRATEGY_ID,
    STRATEGY_VARIANT_ID,
    STRATEGY_VERSION,
    EntryEvaluation,
    XspVixPullbackConfig,
    config_hash,
    evaluate_entry,
    load_daily_close_frame,
    load_intraday_value_frame,
)
from src.strategies.xsp_vix_pullback_journal import decision_payload, register_manifest


DEFAULT_STATE_PATH = Path(r"D:\Trading\state\xsp_vix_pullback_100dte_v1_0_1.json")
DEFAULT_VIX_PATH = Path(r"D:\Trading\Rohdaten\Rohdaten_VIX.csv")
DEFAULT_SPX_PATH = Path(r"D:\Trading\Rohdaten\Rohdaten_SPX.csv")
DONE_ORDER_STATUSES = frozenset({"Filled", "Cancelled", "ApiCancelled", "Inactive"})


@dataclass(frozen=True, slots=True)
class MonitorConfig:
    day: pd.Timestamp
    account: str
    client_id: int
    place_orders: bool
    journal_path: Path
    state_path: Path = DEFAULT_STATE_PATH
    vix_path: Path = DEFAULT_VIX_PATH
    spx_path: Path = DEFAULT_SPX_PATH
    poll_seconds: float = 15.0
    entry_work_minutes: int = 10


@dataclass(slots=True)
class PositionState:
    signal_day: str
    expiry: str
    entry_vix: float
    peak_vix: float
    momentum_20_sessions: float
    spot: float
    short_strike: float
    long_strike: float
    short_con_id: int
    long_con_id: int
    planned_credit: float
    maximum_loss_dollars: float
    run_id: str
    signal_key: str
    decision_id: str
    trade_id: str
    entry_order_ref: str
    target_order_ref: str
    time_exit_order_ref: str
    status: str = "planned"
    entry_order_id: int | None = None
    entry_submitted_at_utc: str | None = None
    entry_fill_credit: float | None = None
    entry_filled_at_utc: str | None = None
    target_debit: float | None = None
    target_order_id: int | None = None
    target_submitted_at_utc: str | None = None
    time_exit_order_id: int | None = None
    time_exit_submitted_at_utc: str | None = None
    time_exit_attempts: int = 0
    exit_fill_price: float | None = None
    closed_at_utc: str | None = None
    last_error: str | None = None


@dataclass(slots=True)
class MonitorState:
    schema_version: int = 1
    evaluated_days: list[str] = field(default_factory=list)
    positions: list[PositionState] = field(default_factory=list)
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
    payload["positions"] = [PositionState(**item) for item in payload.get("positions", [])]
    return MonitorState(**payload)


def _order_status(trade: Any) -> str:
    return str(getattr(getattr(trade, "orderStatus", None), "status", "") or "")


def _fill_price(trade: Any) -> float | None:
    value = float(getattr(getattr(trade, "orderStatus", None), "avgFillPrice", 0.0) or 0.0)
    return value if value != 0 else None


def order_work_window_elapsed(submitted_at_utc: str, now_utc: pd.Timestamp, minutes: int) -> bool:
    submitted = pd.Timestamp(submitted_at_utc)
    if submitted.tzinfo is None:
        submitted = submitted.tz_localize("UTC")
    return now_utc.tz_convert("UTC") >= submitted.tz_convert("UTC") + pd.Timedelta(minutes=minutes)


class XspVixPullbackMonitor:
    def __init__(
        self,
        ib: IB,
        monitor_config: MonitorConfig,
        *,
        strategy_config: XspVixPullbackConfig = XspVixPullbackConfig(),
        options_risk_budget: OptionsRiskBudget | None = None,
        logger: Callable[[str], None] = print,
    ) -> None:
        require_paper_account(monitor_config.account)
        self.ib, self.monitor_config, self.strategy_config = ib, monitor_config, strategy_config
        self.options_risk_budget, self.log = options_risk_budget, logger
        self.store = JournalStore(monitor_config.journal_path)
        register_manifest(self.store, strategy_config)
        self.decisions = DecisionJournalIngestor(self.store)
        self.executions = ExecutionJournalIngestor(self.store)
        self.vix, self.spx = self.ib.qualifyContracts(Index("VIX", "CBOE", "USD"), Index("SPX", "CBOE", "USD"))
        if options_risk_budget is not None:
            options_risk_budget.register(STRATEGY_ID, self._active_defined_risk)

    def close(self) -> None:
        self.store.close()

    def _active_defined_risk(self) -> float:
        active = {"planned", "entry_submitted", "open", "target_submitted", "target_cancelled", "time_exit_pending", "time_exit_submitted"}
        return sum(p.maximum_loss_dollars for p in load_state(self.monitor_config.state_path).positions if p.status in active)

    def _live_value(self, contract: Any, label: str, timeout: float = 4.0) -> float:
        ticker = self.ib.reqMktData(contract, "", False, False)
        deadline = time.monotonic() + timeout
        try:
            while time.monotonic() < deadline:
                self.ib.sleep(0.2)
                bid, ask = getattr(ticker, "bid", None), getattr(ticker, "ask", None)
                try:
                    midpoint = (float(bid) + float(ask)) / 2.0 if float(bid) > 0 and float(ask) >= float(bid) else None
                except (TypeError, ValueError):
                    midpoint = None
                candidates = (getattr(ticker, "last", None), midpoint)
                for value in candidates:
                    try:
                        parsed = float(value)
                    except (TypeError, ValueError):
                        continue
                    if pd.notna(parsed) and parsed > 0:
                        return parsed
        finally:
            self.ib.cancelMktData(contract)
        raise RuntimeError(f"no valid live {label} value")

    def _all_trades(self) -> tuple[Any, ...]:
        trades = list(self.ib.trades())
        try:
            trades.extend(self.ib.reqCompletedOrders(False))
        except Exception as exc:
            self.log(f"XSP completed-order lookup unavailable: {exc}")
        unique = {}
        for trade in trades:
            order = getattr(trade, "order", None)
            unique[(int(getattr(order, "orderId", 0) or 0), str(getattr(order, "orderRef", "") or ""))] = trade
        return tuple(unique.values())

    def _find_trade(self, order_ref: str | None, order_id: int | None) -> Any | None:
        matches: list[Any] = []
        for trade in self._all_trades():
            order = getattr(trade, "order", None)
            if order_ref and str(getattr(order, "orderRef", "") or "") == order_ref:
                matches.append(trade)
            elif order_id and int(getattr(order, "orderId", 0) or 0) == order_id:
                matches.append(trade)
        if not matches:
            return None
        # A deterministic orderRef may be reused after a cancelled GTC target.
        # Prefer an active replacement, then the newest broker order identity.
        return max(
            matches,
            key=lambda trade: (
                _order_status(trade) not in DONE_ORDER_STATUSES,
                int(getattr(getattr(trade, "order", None), "orderId", 0) or 0),
            ),
        )

    def _selection(self, position: PositionState) -> XspPutCreditSelection:
        selection = selection_for_identity(
            self.ib, expiry=position.expiry, short_strike=position.short_strike, long_strike=position.long_strike
        )
        observed = (int(selection.short_contract.conId), int(selection.long_contract.conId))
        expected = (position.short_con_id, position.long_con_id)
        if observed != expected:
            raise RuntimeError(f"XSP spread identity changed after restart: expected={expected} observed={observed}")
        return selection

    def _execution_context(self, position: PositionState) -> ExecutionContext:
        return ExecutionContext(
            position.run_id, position.signal_key, position.trade_id, position.decision_id,
            self.monitor_config.account, self.monitor_config.client_id, now_utc_iso(),
        )

    def _start_run(self, evaluation: EntryEvaluation) -> tuple[str, DecisionIngestContext]:
        run_id = deterministic_id("run", STRATEGY_VARIANT_ID, evaluation.day, "paper", self.monitor_config.account)
        started = pd.Timestamp(evaluation.day).tz_localize("America/New_York").tz_convert("UTC").isoformat()
        self.store.start_run(
            StrategyRunRecord(
                run_id, STRATEGY_ID, STRATEGY_VERSION, config_hash(self.strategy_config),
                "paper", "paper", started, None, "running", asdict(self.strategy_config),
                {"account": self.monitor_config.account, "paper_only": True}, STRATEGY_VARIANT_ID,
            )
        )
        return run_id, DecisionIngestContext(run_id, "paper")

    def _finish_run(self, run_id: str, day: str, outcome: str) -> None:
        started = pd.Timestamp(day).tz_localize("America/New_York").tz_convert("UTC").isoformat()
        self.store.start_run(
            StrategyRunRecord(
                run_id, STRATEGY_ID, STRATEGY_VERSION, config_hash(self.strategy_config),
                "paper", "paper", started, now_utc_iso(), "completed", asdict(self.strategy_config),
                {"account": self.monitor_config.account, "paper_only": True, "outcome": outcome}, STRATEGY_VARIANT_ID,
            )
        )

    def _journal_decision(self, evaluation: EntryEvaluation, selection: XspPutCreditSelection | None):
        structure: dict[str, Any] = {}
        if selection is not None:
            structure = {
                "expiry": selection.expiry,
                "actual_dte": (pd.Timestamp(selection.expiry) - pd.Timestamp(evaluation.day)).days,
                "spot": selection.spot,
                "short_strike": selection.short_strike,
                "long_strike": selection.long_strike,
                "short_delta": selection.short_quote.delta,
                "delta_deviation": abs(float(selection.short_quote.delta) - self.strategy_config.target_short_delta),
                "conservative_credit": selection.conservative_credit,
                "entry_limit_credit": selection.entry_limit_credit,
                "maximum_loss_dollars": selection.maximum_loss_dollars,
                "quotes": {"short": asdict(selection.short_quote), "long": asdict(selection.long_quote)},
            }
        payload = decision_payload(evaluation, self.strategy_config, structure=structure)
        run_id, context = self._start_run(evaluation)
        decision = self.decisions.ingest(payload, context)
        return payload, run_id, context, decision.decision_id

    def _new_position(self, evaluation: EntryEvaluation, selection: XspPutCreditSelection) -> PositionState:
        payload, run_id, context, decision_id = self._journal_decision(evaluation, selection)
        leg_data = (
            ("short_put", "SELL", selection.short_contract, selection.short_quote, selection.short_strike),
            ("long_put", "BUY", selection.long_contract, selection.long_quote, selection.long_strike),
        )
        legs = tuple(
            TradeIntentLeg(
                "OPT", "XSP", int(contract.conId), str(getattr(contract, "localSymbol", "") or "") or None,
                str(getattr(contract, "exchange", "") or "SMART"), "USD", side, 1, 1.0,
                selection.expiry, strike, "P", float(selection.multiplier), quote.mid,
                {"role": role, "greeks": asdict(quote)},
            )
            for role, side, contract, quote, strike in leg_data
        )
        trade_id = self.decisions.create_trade_intent(
            payload, context,
            TradeIntent(
                "BAG", "XSP", "short", "planned", None, 1.0, "USD", selection.entry_limit_credit,
                None, None, "SMART", legs,
                {"paper_only": True, "maximum_loss_dollars": selection.maximum_loss_dollars},
            ),
        )
        key = evaluation.day.replace("-", "")
        return PositionState(
            evaluation.day, selection.expiry, float(evaluation.vix), float(evaluation.peak_vix),
            float(evaluation.momentum_20_sessions), selection.spot, selection.short_strike, selection.long_strike,
            int(selection.short_contract.conId), int(selection.long_contract.conId),
            selection.entry_limit_credit, selection.maximum_loss_dollars,
            run_id, str(payload["signal_key"]), decision_id, trade_id,
            f"XSPVX-E-{key}"[:45], f"XSPVX-TP-{key}"[:45], f"XSPVX-T60-{key}"[:45],
        )

    def _submit_entry(self, position: PositionState, selection: XspPutCreditSelection) -> None:
        if self._find_trade(position.entry_order_ref, position.entry_order_id):
            raise RuntimeError("duplicate XSP VIX-pullback entry order")
        if not self.monitor_config.place_orders:
            self.executions.record_blocker(
                self._execution_context(position), status="execution_blocked", reason_code="dry_run_no_order",
                reason="XSP VIX-pullback accepted but --place-orders is absent", requested_quantity=1,
                occurred_at=now_utc_iso(),
                details={"entry_credit": selection.entry_limit_credit, "maximum_loss": selection.maximum_loss_dollars},
            )
            position.status = "dry_run"
            self._finish_run(position.run_id, position.signal_day, "dry_run_no_order")
            return
        order = build_entry_order(selection, account=self.monitor_config.account, order_ref=position.entry_order_ref)
        trade = self.ib.placeOrder(selection.combo_contract, order)
        self.ib.sleep(1.0)
        position.status, position.entry_order_id = "entry_submitted", int(getattr(trade.order, "orderId", 0) or 0)
        position.entry_submitted_at_utc = now_utc_iso()
        self.executions.ingest_trade(trade, self._execution_context(position), role="entry")
        self.log(
            f"XSP VIX-pullback entry submitted expiry={position.expiry} "
            f"strikes={position.short_strike:g}/{position.long_strike:g} credit={position.planned_credit:.2f}"
        )

    def _ensure_target(self, position: PositionState) -> None:
        existing = self._find_trade(position.target_order_ref, position.target_order_id)
        if existing is not None:
            status = _order_status(existing)
            self.executions.ingest_trade(existing, self._execution_context(position), role="exit")
            if status == "Filled":
                position.exit_fill_price = _fill_price(existing)
                position.closed_at_utc, position.status = now_utc_iso(), "closed"
                self._finish_run(position.run_id, position.signal_day, "profit_target_filled")
                return
            if status not in DONE_ORDER_STATUSES:
                position.target_order_id = int(getattr(existing.order, "orderId", 0) or 0)
                position.status = "target_submitted"
                return
        selection = self._selection(position)
        credit = float(position.entry_fill_credit or position.planned_credit)
        order = build_profit_target_order(
            selection, entry_credit=credit, profit_target_fraction=self.strategy_config.profit_target_fraction,
            account=self.monitor_config.account, order_ref=position.target_order_ref,
        )
        trade = self.ib.placeOrder(selection.inverse_combo_contract, order)
        self.ib.sleep(1.0)
        position.target_debit = float(order.lmtPrice)
        position.target_order_id = int(getattr(trade.order, "orderId", 0) or 0)
        position.target_submitted_at_utc = now_utc_iso()
        position.status = "target_submitted"
        self.executions.ingest_trade(trade, self._execution_context(position), role="exit")
        self.log(f"XSP VIX-pullback GTC TP60 submitted debit={position.target_debit:.2f}")

    def _service_orders(self, state: MonitorState) -> None:
        for position in state.positions:
            if position.status == "open":
                self._ensure_target(position)
                continue
            mapping = {
                "entry_submitted": (position.entry_order_ref, position.entry_order_id, "entry"),
                "target_submitted": (position.target_order_ref, position.target_order_id, "exit"),
                "time_exit_submitted": (position.time_exit_order_ref, position.time_exit_order_id, "exit"),
            }
            if position.status not in mapping:
                continue
            ref, order_id, role = mapping[position.status]
            trade = self._find_trade(ref, order_id)
            if trade is None:
                continue
            self.executions.ingest_trade(trade, self._execution_context(position), role=role)
            status = _order_status(trade)
            if status == "Filled" and position.status == "entry_submitted":
                fill = _fill_price(trade)
                position.entry_fill_credit = abs(float(fill)) if fill is not None else position.planned_credit
                position.entry_filled_at_utc, position.status = now_utc_iso(), "open"
                self._ensure_target(position)
            elif status == "Filled":
                outcome = "profit_target_filled" if position.status == "target_submitted" else "time60_exit_filled"
                position.exit_fill_price, position.closed_at_utc, position.status = _fill_price(trade), now_utc_iso(), "closed"
                self._finish_run(position.run_id, position.signal_day, outcome)
            elif status in DONE_ORDER_STATUSES:
                if position.status == "entry_submitted":
                    position.status, position.last_error = "entry_not_filled", f"terminal broker status {status}"
                    self._finish_run(position.run_id, position.signal_day, position.status)
                elif position.status == "target_submitted":
                    position.status, position.last_error = "target_cancelled", f"terminal broker status {status}"
                else:
                    position.status, position.last_error = "time_exit_pending", f"terminal broker status {status}"
            elif position.status == "entry_submitted" and position.entry_submitted_at_utc and order_work_window_elapsed(
                position.entry_submitted_at_utc, pd.Timestamp.now(tz="UTC"), self.monitor_config.entry_work_minutes
            ):
                self.ib.cancelOrder(trade.order)
                self.ib.sleep(1.0)
                self.executions.ingest_trade(trade, self._execution_context(position), role="entry")
                position.status, position.last_error = "entry_not_filled", "entry work window elapsed"
                self._finish_run(position.run_id, position.signal_day, "entry_work_window_elapsed")

    def _cancel_target(self, position: PositionState) -> None:
        target = self._find_trade(position.target_order_ref, position.target_order_id)
        if target is None:
            if position.status == "target_cancelled":
                return
            raise RuntimeError(f"cannot verify resting target before time exit: {position.target_order_ref}")
        if _order_status(target) == "Filled":
            self.executions.ingest_trade(target, self._execution_context(position), role="exit")
            position.exit_fill_price, position.closed_at_utc, position.status = _fill_price(target), now_utc_iso(), "closed"
            self._finish_run(position.run_id, position.signal_day, "profit_target_filled")
            return
        if _order_status(target) not in DONE_ORDER_STATUSES:
            self.ib.cancelOrder(target.order)
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline and _order_status(target) not in DONE_ORDER_STATUSES:
                self.ib.sleep(0.2)
            self.executions.ingest_trade(target, self._execution_context(position), role="exit")
            if _order_status(target) not in DONE_ORDER_STATUSES:
                raise RuntimeError("XSP target cancellation was not acknowledged; time exit withheld")
        position.status = "target_cancelled"

    def _service_time_exits(self, state: MonitorState, now_et: pd.Timestamp) -> None:
        for position in state.positions:
            if position.status not in {"target_submitted", "target_cancelled", "time_exit_pending"}:
                continue
            remaining = (pd.Timestamp(position.expiry).date() - now_et.date()).days
            if remaining > self.strategy_config.time_exit_remaining_dte:
                if position.status == "target_cancelled":
                    self._ensure_target(position)
                continue
            selection = self._selection(position)
            if not quote_quality_passes(selection, self.strategy_config.maximum_relative_leg_spread):
                position.last_error = "time60 quote-quality gate failed; resting TP retained"
                continue
            if position.status == "target_submitted":
                self._cancel_target(position)
                if position.status == "closed":
                    continue
            if not self.monitor_config.place_orders:
                position.status = "dry_run_time_exit"
                continue
            existing = self._find_trade(position.time_exit_order_ref, position.time_exit_order_id)
            if existing is not None:
                existing_status = _order_status(existing)
                self.executions.ingest_trade(existing, self._execution_context(position), role="exit")
                if existing_status == "Filled":
                    position.exit_fill_price = _fill_price(existing)
                    position.closed_at_utc, position.status = now_utc_iso(), "closed"
                    self._finish_run(position.run_id, position.signal_day, "time60_exit_filled")
                    continue
                if existing_status not in DONE_ORDER_STATUSES:
                    position.time_exit_order_id = int(getattr(existing.order, "orderId", 0) or 0)
                    position.status = "time_exit_submitted"
                    continue
                position.time_exit_attempts += 1
                key = position.signal_day.replace("-", "")
                position.time_exit_order_ref = f"XSPVX-T60-{key}-{position.time_exit_attempts}"[:45]
            order = build_time_exit_order(selection, account=self.monitor_config.account, order_ref=position.time_exit_order_ref)
            trade = self.ib.placeOrder(selection.inverse_combo_contract, order)
            self.ib.sleep(1.0)
            position.time_exit_order_id = int(getattr(trade.order, "orderId", 0) or 0)
            position.time_exit_submitted_at_utc = now_utc_iso()
            position.status = "time_exit_submitted"
            self.executions.ingest_trade(trade, self._execution_context(position), role="exit")
            self.log(f"XSP VIX-pullback 60-DTE time exit submitted debit={float(order.lmtPrice):.2f}")

    def _unknown_exposure_exists(self, state: MonitorState, selection: XspPutCreditSelection) -> bool:
        known = {con_id for p in state.positions for con_id in (p.short_con_id, p.long_con_id)}
        selected = {int(selection.short_contract.conId), int(selection.long_contract.conId)}
        for item in self.ib.positions():
            contract = getattr(item, "contract", None)
            if (
                str(getattr(item, "account", "") or "") == self.monitor_config.account
                and str(getattr(contract, "tradingClass", "") or "").upper() == "XSP"
                and int(getattr(contract, "conId", 0) or 0) in selected - known
                and float(getattr(item, "position", 0.0) or 0.0) != 0
            ):
                return True
        for trade in self._all_trades():
            ref = str(getattr(getattr(trade, "order", None), "orderRef", "") or "")
            if ref.startswith("XSPVX-") and _order_status(trade) not in DONE_ORDER_STATUSES:
                if not any(ref in {p.entry_order_ref, p.target_order_ref, p.time_exit_order_ref} for p in state.positions):
                    return True
        return False

    def _evaluate_entry(self, state: MonitorState, now_et: pd.Timestamp) -> None:
        today, clock = now_et.strftime("%Y-%m-%d"), now_et.strftime("%H:%M:%S")
        if clock < self.strategy_config.entry_time_et or clock > self.strategy_config.latest_submission_time_et or today in state.evaluated_days:
            return
        # IB stores CBOE index bars in exchange-local US Central time. The
        # 12:35 CT bar is the same instant as the 13:35 ET live decision.
        vix_frame = load_intraday_value_frame(
            self.monitor_config.vix_path,
            self.strategy_config.raw_cboe_entry_time_ct,
            "vix",
        )
        spx_frame = load_daily_close_frame(self.monitor_config.spx_path, "spx")
        evaluation = evaluate_entry(
            today, vix_frame=vix_frame, spx_frame=spx_frame,
            current_vix=self._live_value(self.vix, "VIX"), current_spx=self._live_value(self.spx, "SPX"),
            config=self.strategy_config,
        )
        state.evaluated_days.append(today)
        if not evaluation.accepted:
            _, run_id, _, _ = self._journal_decision(evaluation, None)
            self._finish_run(run_id, today, evaluation.reason)
            self.log(f"XSP VIX-pullback no trade: {evaluation.reason}")
            return
        active = {"planned", "entry_submitted", "open", "target_submitted", "target_cancelled", "time_exit_pending", "time_exit_submitted"}
        if sum(p.status in active for p in state.positions) >= self.strategy_config.maximum_concurrent_positions:
            blocked = EntryEvaluation(**{**asdict(evaluation), "status": "blocked_risk", "reason": "maximum_concurrent_positions"})
            _, run_id, _, _ = self._journal_decision(blocked, None)
            self._finish_run(run_id, today, blocked.reason)
            return
        selection = select_xsp_put_credit(
            self.ib, decision_day=pd.Timestamp(today), minimum_dte=self.strategy_config.target_calendar_dte_floor,
            target_delta=self.strategy_config.target_short_delta,
            maximum_delta_deviation=self.strategy_config.maximum_delta_deviation,
            width=self.strategy_config.width_points,
        )
        if not quote_quality_passes(selection, self.strategy_config.maximum_relative_leg_spread):
            blocked = EntryEvaluation(
                **{
                    **asdict(evaluation),
                    "status": "blocked_execution",
                    "reason": "entry_quote_quality_gate",
                }
            )
            _, run_id, _, _ = self._journal_decision(blocked, selection)
            self._finish_run(run_id, today, blocked.reason)
            self.log(
                "XSP VIX-pullback blocked by entry quote-quality gate "
                f"relative_leg_spreads={selection.relative_leg_spreads}"
            )
            return
        if self.options_risk_budget is not None and not self.options_risk_budget.can_allocate(selection.maximum_loss_dollars):
            blocked = EntryEvaluation(**{**asdict(evaluation), "status": "blocked_risk", "reason": "shared_options_risk_budget"})
            _, run_id, _, _ = self._journal_decision(blocked, selection)
            self._finish_run(run_id, today, blocked.reason)
            self.log(f"XSP VIX-pullback blocked by shared risk budget requested={selection.maximum_loss_dollars:.2f}")
            return
        if self._unknown_exposure_exists(state, selection):
            raise RuntimeError("unknown XSP strategy exposure/order exists; refusing duplicate entry")
        position = self._new_position(evaluation, selection)
        state.positions.append(position)
        self._submit_entry(position, selection)

    def tick(self, now_et: pd.Timestamp | None = None) -> MonitorState:
        now_et = now_et or pd.Timestamp.now(tz="America/New_York")
        if now_et.tzinfo is None:
            now_et = now_et.tz_localize("America/New_York")
        state = load_state(self.monitor_config.state_path)
        self._service_orders(state)
        self._service_time_exits(state, now_et)
        self._evaluate_entry(state, now_et)
        atomic_write_state(self.monitor_config.state_path, state)
        return state

    def safe_tick(self, now_et: pd.Timestamp | None = None) -> MonitorState:
        try:
            return self.tick(now_et)
        except Exception as exc:
            state = load_state(self.monitor_config.state_path)
            state.last_error = f"{type(exc).__name__}: {exc}"
            atomic_write_state(self.monitor_config.state_path, state)
            self.log(f"XSP VIX-pullback monitor error (other strategies continue): {type(exc).__name__}: {exc}")
            return state
