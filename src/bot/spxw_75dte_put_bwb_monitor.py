from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Callable

import pandas as pd
from ib_insync import IB, Index

from src.execution.spxw_put_bwb import (
    SpxwPutBwbSelection,
    build_entry_order,
    build_profit_target_order,
    maximum_loss_dollars_for_credit,
    require_paper_account,
    round_credit_down,
    select_spxw_put_bwb,
    selection_for_identity,
)
from src.journal.decision_ingest import DecisionIngestContext, DecisionJournalIngestor, TradeIntent, TradeIntentLeg
from src.journal.execution_ingest import ExecutionContext, ExecutionJournalIngestor
from src.journal.models import StrategyRunRecord, deterministic_id
from src.journal.store import JournalStore
from src.risk.options_risk_budget import OptionsRiskBudget
from src.strategies.spxw_75dte_put_bwb import (
    STRATEGY_ID,
    STRATEGY_VARIANT_ID,
    STRATEGY_VERSION,
    EntryEvaluation,
    Spxw75DteBwbConfig,
    config_hash,
    evaluate_entry,
    maximum_concurrent_positions_for_day,
    paper_weekday_experiment_active,
)
from src.strategies.spxw_75dte_put_bwb_journal import decision_payload, register_manifest


DEFAULT_STATE_PATH = Path(r"D:\Trading\state\spxw_75dte_put_bwb_thursday_v1_2.json")
DONE_ORDER_STATUSES = frozenset({"Filled", "Cancelled", "ApiCancelled", "Inactive"})


@dataclass(frozen=True, slots=True)
class MonitorConfig:
    day: pd.Timestamp
    account: str
    client_id: int
    place_orders: bool
    journal_path: Path
    state_path: Path = DEFAULT_STATE_PATH
    poll_seconds: float = 15.0
    initial_entry_work_minutes: int = 5
    entry_work_minutes: int = 5
    final_entry_work_minutes: int = 5


@dataclass(slots=True)
class PositionState:
    signal_day: str
    expiry: str
    opening_vix: float
    spot: float
    upper_strike: float
    body_strike: float
    lower_strike: float
    upper_con_id: int
    body_con_id: int
    lower_con_id: int
    planned_credit: float
    maximum_loss_dollars: float
    run_id: str
    signal_key: str
    decision_id: str
    trade_id: str
    entry_order_ref: str
    target_order_ref: str
    status: str = "planned"
    entry_price_stage: int = 1
    entry_stage_started_at_utc: str | None = None
    initial_mid_credit: float | None = None
    repriced_mid_credit: float | None = None
    entry_repriced_at_utc: str | None = None
    final_mid_credit: float | None = None
    final_repriced_at_utc: str | None = None
    entry_order_id: int | None = None
    entry_submitted_at_utc: str | None = None
    entry_fill_credit: float | None = None
    entry_filled_at_utc: str | None = None
    target_debit: float | None = None
    target_order_id: int | None = None
    target_submitted_at_utc: str | None = None
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


def _fill_time_utc(trade: Any) -> str:
    fills = tuple(getattr(trade, "fills", ()) or ())
    times = [getattr(getattr(fill, "execution", None), "time", None) for fill in fills]
    valid = [pd.Timestamp(value) for value in times if value is not None]
    if not valid:
        return now_utc_iso()
    latest = max(value.tz_localize("UTC") if value.tzinfo is None else value.tz_convert("UTC") for value in valid)
    return latest.isoformat().replace("+00:00", "Z")


def order_work_window_elapsed(submitted_at_utc: str, now_utc: pd.Timestamp, minutes: int) -> bool:
    submitted = pd.Timestamp(submitted_at_utc)
    if submitted.tzinfo is None:
        submitted = submitted.tz_localize("UTC")
    return now_utc.tz_convert("UTC") >= submitted.tz_convert("UTC") + pd.Timedelta(minutes=minutes)


class Spxw75DtePutBwbMonitor:
    def __init__(
        self,
        ib: IB,
        monitor_config: MonitorConfig,
        *,
        strategy_config: Spxw75DteBwbConfig = Spxw75DteBwbConfig(),
        options_risk_budget: OptionsRiskBudget | None = None,
        logger: Callable[[str], None] = print,
    ) -> None:
        require_paper_account(monitor_config.account)
        if monitor_config.initial_entry_work_minutes <= 0:
            raise ValueError("initial_entry_work_minutes must be positive")
        if monitor_config.entry_work_minutes <= 0:
            raise ValueError("entry_work_minutes must be positive")
        if monitor_config.final_entry_work_minutes <= 0:
            raise ValueError("final_entry_work_minutes must be positive")
        self.ib, self.monitor_config, self.strategy_config = ib, monitor_config, strategy_config
        self.options_risk_budget, self.log = options_risk_budget, logger
        self.store = JournalStore(monitor_config.journal_path)
        register_manifest(self.store, strategy_config)
        self.decisions = DecisionJournalIngestor(self.store)
        self.executions = ExecutionJournalIngestor(self.store)
        self.vix = self.ib.qualifyContracts(Index("VIX", "CBOE", "USD"))[0]
        if options_risk_budget is not None:
            options_risk_budget.register(STRATEGY_ID, self._active_defined_risk)

    def close(self) -> None:
        self.store.close()

    def _active_defined_risk(self) -> float:
        active = {
            "planned", "entry_submitted", "entry_cancel_requested", "open",
            "target_submitted", "target_cancelled",
        }
        return sum(p.maximum_loss_dollars for p in load_state(self.monitor_config.state_path).positions if p.status in active)

    def _opening_vix(self, session_day: str) -> float:
        ticker = self.ib.reqMktData(self.vix, "", False, False)
        try:
            self.ib.sleep(1.0)
            value = getattr(ticker, "open", None)
            try:
                parsed = float(value)
            except (TypeError, ValueError):
                parsed = float("nan")
            if pd.notna(parsed) and parsed > 0:
                return parsed
        finally:
            self.ib.cancelMktData(self.vix)
        bars = self.ib.reqHistoricalData(
            self.vix, endDateTime="", durationStr="2 D", barSizeSetting="1 day",
            whatToShow="TRADES", useRTH=True, formatDate=1, keepUpToDate=False,
        )
        expected_day = pd.Timestamp(session_day).date()
        for bar in reversed(list(bars)):
            if pd.Timestamp(getattr(bar, "date", None)).date() == expected_day:
                parsed = float(getattr(bar, "open", float("nan")))
                if pd.notna(parsed) and parsed > 0:
                    return parsed
        raise RuntimeError("no valid live VIX opening value")

    def _all_trades(self) -> tuple[Any, ...]:
        trades = list(self.ib.trades())
        try:
            trades.extend(self.ib.reqAllOpenOrders())
        except Exception as exc:
            self.log(f"SPXW 75DTE all-open-order lookup unavailable: {exc}")
        try:
            trades.extend(self.ib.reqCompletedOrders(False))
        except Exception as exc:
            self.log(f"SPXW 75DTE completed-order lookup unavailable: {exc}")
        unique = {}
        for trade in trades:
            order = getattr(trade, "order", None)
            unique[(int(getattr(order, "orderId", 0) or 0), str(getattr(order, "orderRef", "") or ""))] = trade
        return tuple(unique.values())

    def _find_trade(self, order_ref: str | None, order_id: int | None) -> Any | None:
        for trade in self._all_trades():
            order = getattr(trade, "order", None)
            if order_ref and str(getattr(order, "orderRef", "") or "") == order_ref:
                return trade
            if order_id and int(getattr(order, "orderId", 0) or 0) == order_id:
                return trade
        return None

    def _selection(self, position: PositionState) -> SpxwPutBwbSelection:
        selection = selection_for_identity(
            self.ib, expiry=position.expiry, upper_strike=position.upper_strike,
            body_strike=position.body_strike, lower_strike=position.lower_strike,
            opening_vix=position.opening_vix,
            entry_slippage_points=self.strategy_config.entry_slippage_points,
        )
        observed = tuple(int(leg.contract.conId) for leg in selection.legs)
        expected = (position.upper_con_id, position.body_con_id, position.lower_con_id)
        if observed != expected:
            raise RuntimeError(f"SPXW BWB identity changed after restart: expected={expected} observed={observed}")
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
                {"account": self.monitor_config.account, "paper_only": True, "outcome": outcome},
                STRATEGY_VARIANT_ID,
            )
        )

    def _journal_decision(self, evaluation: EntryEvaluation, selection: SpxwPutBwbSelection | None):
        structure = {}
        if selection is not None:
            structure = {
                "expiry": selection.expiry, "actual_dte": (pd.Timestamp(selection.expiry) - pd.Timestamp(evaluation.day)).days,
                "spot": selection.spot, "upper_strike": selection.upper_strike,
                "body_strike": selection.body_strike, "lower_strike": selection.lower_strike,
                "mid_credit": selection.mid_credit, "natural_credit": selection.natural_credit,
                "entry_limit_credit": selection.entry_limit_credit,
                "maximum_loss_dollars": selection.maximum_loss_dollars,
                "legs": [
                    {"role": leg.role, "action": leg.action, "ratio": leg.ratio, "strike": leg.strike, "quote": asdict(leg.quote)}
                    for leg in selection.legs
                ],
            }
        payload = decision_payload(evaluation, self.strategy_config, structure=structure)
        run_id, context = self._start_run(evaluation)
        decision = self.decisions.ingest(payload, context)
        return payload, run_id, context, decision.decision_id

    def _new_position(self, evaluation: EntryEvaluation, selection: SpxwPutBwbSelection) -> PositionState:
        payload, run_id, context, decision_id = self._journal_decision(evaluation, selection)
        legs = tuple(
            TradeIntentLeg(
                "OPT", "SPXW", int(leg.contract.conId), str(getattr(leg.contract, "localSymbol", "") or "") or None,
                str(getattr(leg.contract, "exchange", "") or "SMART"), "USD", leg.action, leg.ratio, 1.0,
                selection.expiry, leg.strike, "P", float(selection.multiplier), leg.quote.mid,
                {"role": leg.role, "greeks": asdict(leg.quote)},
            )
            for leg in selection.legs
        )
        trade_id = self.decisions.create_trade_intent(
            payload, context,
            TradeIntent(
                "BAG", "SPXW", "short", "planned", None, 1.0, "USD", selection.entry_limit_credit,
                None, None, "SMART", legs,
                {"paper_only": True, "maximum_loss_dollars": selection.maximum_loss_dollars},
            ),
        )
        key = evaluation.day.replace("-", "")
        return PositionState(
            evaluation.day, selection.expiry, selection.opening_vix, selection.spot,
            selection.upper_strike, selection.body_strike, selection.lower_strike,
            *(int(leg.contract.conId) for leg in selection.legs),
            selection.entry_limit_credit, selection.maximum_loss_dollars,
            run_id, str(payload["signal_key"]), decision_id, trade_id,
            f"SPX75-E-{key}"[:45], f"SPX75-X-{key}"[:45],
            initial_mid_credit=selection.mid_credit,
        )

    def _submit_entry(self, position: PositionState, selection: SpxwPutBwbSelection) -> None:
        if self._find_trade(position.entry_order_ref, position.entry_order_id):
            raise RuntimeError("duplicate SPXW 75DTE BWB entry order")
        if not self.monitor_config.place_orders:
            self.executions.record_blocker(
                self._execution_context(position), status="execution_blocked", reason_code="dry_run_no_order",
                reason="SPXW 75DTE BWB accepted but --place-orders is absent", requested_quantity=1,
                occurred_at=now_utc_iso(), details={"entry_credit": selection.entry_limit_credit, "maximum_loss": selection.maximum_loss_dollars},
            )
            position.status = "dry_run"
            self._finish_run(position.run_id, position.signal_day, "dry_run_no_order")
            return
        order = build_entry_order(selection, account=self.monitor_config.account, order_ref=position.entry_order_ref)
        trade = self.ib.placeOrder(selection.combo_contract, order)
        self.ib.sleep(1.0)
        position.status, position.entry_order_id = "entry_submitted", int(getattr(trade.order, "orderId", 0) or 0)
        position.entry_submitted_at_utc = now_utc_iso()
        position.entry_stage_started_at_utc = position.entry_submitted_at_utc
        self.executions.ingest_trade(trade, self._execution_context(position), role="entry")
        self.log(
            f"SPXW 75DTE BWB entry submitted expiry={position.expiry} "
            f"strikes={position.upper_strike:g}/{position.body_strike:g}/{position.lower_strike:g} "
            f"credit={position.planned_credit:.2f}"
        )

    def _reprice_entry(self, position: PositionState, trade: Any, *, target_stage: int) -> None:
        if target_stage not in {2, 3}:
            raise ValueError(f"unsupported SPXW BWB entry price stage {target_stage}")
        selection = self._selection(position)
        if _order_status(trade) == "Filled":
            self._capture_entry_fill(position, trade)
            return
        current_mid = selection.mid_credit
        slippage_points = (
            self.strategy_config.repriced_entry_slippage_points
            if target_stage == 2
            else self.strategy_config.final_entry_slippage_points
        )
        proposed = round_credit_down(
            current_mid - slippage_points,
            selection.min_tick,
        )
        credit = min(float(position.planned_credit), float(proposed))
        if credit <= 0:
            raise RuntimeError(f"invalid repriced SPXW BWB credit {credit}")
        new_risk = maximum_loss_dollars_for_credit(selection, credit)
        experiment_ignores_budget = (
            paper_weekday_experiment_active(position.signal_day, self.strategy_config)
            and self.strategy_config.paper_experiment_ignore_dedicated_risk_budget
        )
        if self.options_risk_budget is not None and not experiment_ignores_budget:
            projected = self.options_risk_budget.open_risk_usd - position.maximum_loss_dollars + new_risk
            if projected > self.options_risk_budget.maximum_open_risk_usd + 1e-9:
                self._request_entry_cancel(
                    position,
                    trade,
                    reason="repriced entry exceeds dedicated risk budget",
                )
                return
        trade.order.lmtPrice = -credit
        modified = self.ib.placeOrder(selection.combo_contract, trade.order)
        self.ib.sleep(1.0)
        position.planned_credit = credit
        position.maximum_loss_dollars = new_risk
        position.entry_price_stage = target_stage
        repriced_at = now_utc_iso()
        if target_stage == 2:
            position.repriced_mid_credit = current_mid
            position.entry_repriced_at_utc = repriced_at
            work_minutes = self.monitor_config.entry_work_minutes
        else:
            position.final_mid_credit = current_mid
            position.final_repriced_at_utc = repriced_at
            work_minutes = self.monitor_config.final_entry_work_minutes
        position.entry_stage_started_at_utc = repriced_at
        self.executions.ingest_trade(modified, self._execution_context(position), role="entry")
        self.log(
            f"SPXW 75DTE BWB entry repriced stage={target_stage} current_mid={current_mid:.2f} "
            f"slippage={slippage_points:.2f} limit_credit={credit:.2f}; work window={work_minutes}m"
        )

    def _capture_entry_fill(self, position: PositionState, trade: Any) -> None:
        fill = _fill_price(trade)
        position.entry_fill_credit = abs(float(fill)) if fill is not None else position.planned_credit
        position.entry_filled_at_utc = _fill_time_utc(trade)
        position.status = "open"
        position.last_error = None
        self._ensure_target(position)

    def _request_entry_cancel(self, position: PositionState, trade: Any, *, reason: str) -> None:
        self.ib.cancelOrder(trade.order)
        self.ib.sleep(1.0)
        self.executions.ingest_trade(trade, self._execution_context(position), role="entry")
        if _order_status(trade) == "Filled":
            self._capture_entry_fill(position, trade)
            return
        position.status = "entry_cancel_requested"
        position.last_error = reason

    def _ensure_target(self, position: PositionState) -> None:
        existing = self._find_trade(position.target_order_ref, position.target_order_id)
        if existing is not None:
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
        self.log(f"SPXW 75DTE BWB GTC 40% target submitted debit={position.target_debit:.2f}")

    def _service_orders(self, state: MonitorState) -> None:
        for position in state.positions:
            if position.status == "open":
                self._ensure_target(position)
                continue
            if position.status not in {"entry_submitted", "entry_cancel_requested", "target_submitted"}:
                continue
            entry = position.status in {"entry_submitted", "entry_cancel_requested"}
            trade = self._find_trade(
                position.entry_order_ref if entry else position.target_order_ref,
                position.entry_order_id if entry else position.target_order_id,
            )
            if trade is None:
                continue
            self.executions.ingest_trade(trade, self._execution_context(position), role="entry" if entry else "exit")
            status = _order_status(trade)
            if status == "Filled" and entry:
                self._capture_entry_fill(position, trade)
            elif status == "Filled":
                position.exit_fill_price, position.closed_at_utc, position.status = _fill_price(trade), now_utc_iso(), "closed"
                self._finish_run(position.run_id, position.signal_day, "profit_target_filled")
            elif status in DONE_ORDER_STATUSES:
                position.status = "entry_not_filled" if entry else "target_cancelled"
                prior_reason = position.last_error
                position.last_error = prior_reason or f"terminal broker status {status}"
                if entry:
                    outcome = (
                        "entry_work_window_elapsed_after_reprice"
                        if prior_reason == "final repriced entry work window elapsed"
                        else "repriced_entry_risk_budget"
                        if prior_reason == "repriced entry exceeds dedicated risk budget"
                        else position.status
                    )
                    self._finish_run(position.run_id, position.signal_day, outcome)
                else:
                    self.log(
                        "SPXW 75DTE BWB target is no longer active; "
                        f"status={status} order_ref={position.target_order_ref}. Manual review required."
                    )
            elif entry and position.status == "entry_submitted":
                stage_started = position.entry_stage_started_at_utc or position.entry_submitted_at_utc
                if position.entry_price_stage == 1 and stage_started and order_work_window_elapsed(
                    stage_started, pd.Timestamp.now(tz="UTC"), self.monitor_config.initial_entry_work_minutes
                ):
                    self._reprice_entry(position, trade, target_stage=2)
                elif position.entry_price_stage == 2 and stage_started and order_work_window_elapsed(
                    stage_started, pd.Timestamp.now(tz="UTC"), self.monitor_config.entry_work_minutes
                ):
                    self._reprice_entry(position, trade, target_stage=3)
                elif position.entry_price_stage >= 3 and stage_started and order_work_window_elapsed(
                    stage_started, pd.Timestamp.now(tz="UTC"), self.monitor_config.final_entry_work_minutes
                ):
                    self._request_entry_cancel(
                        position,
                        trade,
                        reason="final repriced entry work window elapsed",
                    )

    def _unknown_exposure_exists(
        self,
        state: MonitorState,
        selection: SpxwPutBwbSelection,
        *,
        signal_day: str,
    ) -> bool:
        known = {con_id for p in state.positions for con_id in (p.upper_con_id, p.body_con_id, p.lower_con_id)}
        selected = {int(leg.contract.conId) for leg in selection.legs}
        experiment_active = paper_weekday_experiment_active(signal_day, self.strategy_config)
        allowed_test_con_ids: set[int] = set()
        all_trades = self._all_trades()
        if experiment_active:
            for trade in all_trades:
                order = getattr(trade, "order", None)
                ref = str(getattr(order, "orderRef", "") or "")
                if not ref.startswith("SPX75-TEST-"):
                    continue
                combo_legs = tuple(getattr(getattr(trade, "contract", None), "comboLegs", ()) or ())
                allowed_test_con_ids.update(int(getattr(leg, "conId", 0) or 0) for leg in combo_legs)
        for position in self.ib.positions():
            contract = getattr(position, "contract", None)
            con_id = int(getattr(contract, "conId", 0) or 0)
            if (
                str(getattr(position, "account", "") or "") == self.monitor_config.account
                and str(getattr(contract, "tradingClass", "") or "").upper() == "SPXW"
                and con_id in selected
                and con_id not in known
                and con_id not in allowed_test_con_ids
                and float(getattr(position, "position", 0.0) or 0.0) != 0
            ):
                return True
        for trade in all_trades:
            ref = str(getattr(getattr(trade, "order", None), "orderRef", "") or "")
            if experiment_active and ref.startswith("SPX75-TEST-"):
                continue
            if ref.startswith("SPX75-") and _order_status(trade) not in DONE_ORDER_STATUSES:
                if not any(ref in {p.entry_order_ref, p.target_order_ref} for p in state.positions):
                    return True
        return False

    def _evaluate_entry(self, state: MonitorState, now_et: pd.Timestamp) -> None:
        today, clock = now_et.strftime("%Y-%m-%d"), now_et.strftime("%H:%M:%S")
        if clock < self.strategy_config.entry_time_et or clock > self.strategy_config.latest_submission_time_et or today in state.evaluated_days:
            return
        try:
            opening_vix = self._opening_vix(today)
        except Exception as exc:
            opening_vix = None
            self.log(f"SPXW 75DTE BWB opening VIX unavailable: {type(exc).__name__}: {exc}")
        evaluation = evaluate_entry(today, opening_vix=opening_vix, config=self.strategy_config)
        state.evaluated_days.append(today)
        if not evaluation.accepted:
            _, run_id, _, _ = self._journal_decision(evaluation, None)
            self._finish_run(run_id, today, evaluation.status)
            self.log(f"SPXW 75DTE BWB no trade: {evaluation.reason}")
            return
        active = [
            p for p in state.positions
            if p.status in {
                "planned", "entry_submitted", "entry_cancel_requested", "open",
                "target_submitted", "target_cancelled",
            }
        ]
        maximum_positions = maximum_concurrent_positions_for_day(today, self.strategy_config)
        if len(active) >= maximum_positions:
            blocked = EntryEvaluation(**{**asdict(evaluation), "status": "blocked_risk", "reason": "maximum_concurrent_positions"})
            _, run_id, _, _ = self._journal_decision(blocked, None)
            self._finish_run(run_id, today, "maximum_concurrent_positions")
            return
        selection = select_spxw_put_bwb(
            self.ib, decision_day=pd.Timestamp(today), opening_vix=opening_vix,
            minimum_dte=self.strategy_config.target_calendar_dte_floor,
            body_spot_fraction=self.strategy_config.body_spot_fraction,
            upper_width=self.strategy_config.upper_width_points,
            lower_width=self.strategy_config.lower_width_points,
            entry_slippage_points=self.strategy_config.entry_slippage_points,
        )
        experiment_ignores_budget = (
            paper_weekday_experiment_active(today, self.strategy_config)
            and self.strategy_config.paper_experiment_ignore_dedicated_risk_budget
        )
        if (
            self.options_risk_budget is not None
            and not experiment_ignores_budget
            and not self.options_risk_budget.can_allocate(selection.maximum_loss_dollars)
        ):
            blocked = EntryEvaluation(**{**asdict(evaluation), "status": "blocked_risk", "reason": "shared_options_risk_budget"})
            _, run_id, _, _ = self._journal_decision(blocked, selection)
            self._finish_run(run_id, today, "shared_options_risk_budget")
            self.log(f"SPXW 75DTE BWB blocked by shared risk budget requested={selection.maximum_loss_dollars:.2f}")
            return
        if self._unknown_exposure_exists(state, selection, signal_day=today):
            raise RuntimeError("unknown SPXW strategy exposure/order exists; refusing duplicate BWB entry")
        position = self._new_position(evaluation, selection)
        state.positions.append(position)
        self._submit_entry(position, selection)

    def _expire(self, state: MonitorState, now_et: pd.Timestamp) -> None:
        for position in state.positions:
            if position.status in {"open", "target_submitted", "target_cancelled"} and now_et.date() >= pd.Timestamp(position.expiry).date() and now_et.strftime("%H:%M") >= "16:00":
                target = self._find_trade(position.target_order_ref, position.target_order_id)
                if target is not None and _order_status(target) not in DONE_ORDER_STATUSES:
                    self.ib.cancelOrder(target.order)
                position.status, position.closed_at_utc = "expired", now_utc_iso()
                self._finish_run(position.run_id, position.signal_day, "expired")

    def tick(self, now_et: pd.Timestamp | None = None) -> MonitorState:
        now_et = now_et or pd.Timestamp.now(tz="America/New_York")
        if now_et.tzinfo is None:
            now_et = now_et.tz_localize("America/New_York")
        state = load_state(self.monitor_config.state_path)
        self._service_orders(state)
        self._evaluate_entry(state, now_et)
        self._expire(state, now_et)
        atomic_write_state(self.monitor_config.state_path, state)
        return state

    def safe_tick(self, now_et: pd.Timestamp | None = None) -> MonitorState:
        try:
            return self.tick(now_et)
        except Exception as exc:
            state = load_state(self.monitor_config.state_path)
            state.last_error = f"{type(exc).__name__}: {exc}"
            atomic_write_state(self.monitor_config.state_path, state)
            self.log(f"SPXW 75DTE BWB monitor error (other strategies continue): {type(exc).__name__}: {exc}")
            return state
