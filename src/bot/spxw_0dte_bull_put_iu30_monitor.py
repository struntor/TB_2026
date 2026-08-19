from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Callable, Iterable

import pandas as pd
from ib_insync import IB

from src.execution.spxw_0dte_iu30 import (
    Spxw0DteSelection,
    build_entry_order,
    require_paper_account,
    select_spxw_0dte_combo,
)
from src.journal.decision_ingest import (
    DecisionIngestContext,
    DecisionJournalIngestor,
    TradeIntent,
    TradeIntentLeg,
)
from src.journal.execution_ingest import ExecutionContext, ExecutionJournalIngestor
from src.journal.models import StrategyRunRecord, deterministic_id
from src.journal.store import JournalStore
from src.risk.options_risk_budget import OptionsRiskBudget
from src.strategies.spxw_0dte_bull_put_iu30 import (
    STRATEGY_ID,
    STRATEGY_VARIANT_ID,
    STRATEGY_VERSION,
    EntryEvaluation,
    Spxw0DteConfig,
    config_hash,
    evaluate_entry,
)
from src.strategies.spxw_0dte_bull_put_iu30_journal import (
    decision_payload,
    register_manifest,
)


RAW_SPX_PATH = Path(r"D:\Trading\Rohdaten\Rohdaten_SPX.csv")
DEFAULT_STATE_PATH = Path(r"D:\Trading\state\spxw_0dte_bull_put_iu30_0940_v1.json")
DONE_ORDER_STATUSES = frozenset({"Filled", "Cancelled", "ApiCancelled", "Inactive"})


@dataclass(frozen=True, slots=True)
class MonitorConfig:
    day: pd.Timestamp
    account: str
    client_id: int
    place_orders: bool
    journal_path: Path
    state_path: Path = DEFAULT_STATE_PATH
    raw_spx_path: Path = RAW_SPX_PATH
    entry_work_minutes: int = 5


@dataclass(slots=True)
class PositionState:
    signal_day: str
    expiry: str
    run_id: str
    signal_key: str
    decision_id: str
    trade_id: str
    order_ref: str
    maximum_loss_dollars: float
    net_debit: float
    leg_con_ids: list[int]
    status: str = "planned"
    order_id: int | None = None
    submitted_at_utc: str | None = None
    fill_price: float | None = None
    filled_at_utc: str | None = None
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


class Spxw0DteBullPutIu30Monitor:
    def __init__(
        self,
        ib: IB,
        monitor_config: MonitorConfig,
        *,
        strategy_config: Spxw0DteConfig = Spxw0DteConfig(),
        options_risk_budget: OptionsRiskBudget | None = None,
        logger: Callable[[str], None] = print,
    ) -> None:
        require_paper_account(monitor_config.account)
        self.ib = ib
        self.monitor_config = monitor_config
        self.strategy_config = strategy_config
        self.options_risk_budget = options_risk_budget
        self.log = logger
        self.store = JournalStore(monitor_config.journal_path)
        register_manifest(self.store, strategy_config)
        self.decisions = DecisionJournalIngestor(self.store)
        self.executions = ExecutionJournalIngestor(self.store)
        if options_risk_budget is not None:
            options_risk_budget.register(STRATEGY_ID, self._active_defined_risk)

    def close(self) -> None:
        self.store.close()

    def _active_defined_risk(self) -> float:
        active = {"planned", "entry_submitted", "open"}
        return sum(
            position.maximum_loss_dollars
            for position in load_state(self.monitor_config.state_path).positions
            if position.status in active
        )

    def _all_trades(self) -> tuple[Any, ...]:
        trades = list(self.ib.trades())
        try:
            trades.extend(self.ib.reqCompletedOrders(False))
        except Exception as exc:
            self.log(f"SPXW 0DTE completed-order lookup unavailable: {exc}")
        unique: dict[tuple[int, str], Any] = {}
        for trade in trades:
            order = getattr(trade, "order", None)
            key = (int(getattr(order, "orderId", 0) or 0), str(getattr(order, "orderRef", "") or ""))
            unique[key] = trade
        return tuple(unique.values())

    def _find_trade(self, order_ref: str, order_id: int | None) -> Any | None:
        for trade in self._all_trades():
            order = getattr(trade, "order", None)
            if str(getattr(order, "orderRef", "") or "") == order_ref:
                return trade
            if order_id and int(getattr(order, "orderId", 0) or 0) == order_id:
                return trade
        return None

    def _unknown_own_order(self, state: MonitorState) -> bool:
        known = {position.order_ref for position in state.positions}
        return any(
            str(getattr(getattr(trade, "order", None), "orderRef", "") or "").startswith("SPX0-")
            and str(getattr(getattr(trade, "order", None), "orderRef", "") or "") not in known
            and _order_status(trade) not in DONE_ORDER_STATUSES
            for trade in self._all_trades()
        )

    def _start_run(self, evaluation: EntryEvaluation) -> tuple[str, DecisionIngestContext]:
        run_id = deterministic_id("run", STRATEGY_VARIANT_ID, evaluation.day, "paper", self.monitor_config.account)
        started = evaluation.decision_time.tz_localize("America/New_York").tz_convert("UTC").isoformat()
        self.store.start_run(
            StrategyRunRecord(
                run_id, STRATEGY_ID, STRATEGY_VERSION, config_hash(self.strategy_config),
                "paper", "paper", started, None, "running", asdict(self.strategy_config),
                {"account": self.monitor_config.account, "paper_only": True}, STRATEGY_VARIANT_ID,
            )
        )
        return run_id, DecisionIngestContext(run_id, "paper")

    def _finish_run(self, run_id: str, evaluation: EntryEvaluation, outcome: str) -> None:
        self._complete_run(run_id, evaluation.day, outcome)

    def _complete_run(self, run_id: str, day: str, outcome: str) -> None:
        started = pd.Timestamp(
            f"{day} {self.strategy_config.decision_time_et}"
        ).tz_localize("America/New_York").tz_convert("UTC").isoformat()
        self.store.start_run(
            StrategyRunRecord(
                run_id, STRATEGY_ID, STRATEGY_VERSION, config_hash(self.strategy_config),
                "paper", "paper", started, now_utc_iso(), "completed", asdict(self.strategy_config),
                {"account": self.monitor_config.account, "paper_only": True, "outcome": outcome},
                STRATEGY_VARIANT_ID,
            )
        )

    def _journal_decision(
        self, evaluation: EntryEvaluation, selection: Spxw0DteSelection | None
    ) -> tuple[dict[str, Any], str, DecisionIngestContext, str]:
        structure: dict[str, Any] = {}
        if selection is not None:
            structure = {
                "expiry": selection.expiry,
                "decision_price": selection.decision_price,
                "prior_atr14": selection.prior_atr14,
                "short_put": selection.short_put_strike,
                "long_put": selection.long_put_strike,
                "fly_lower": selection.fly_lower,
                "fly_body": selection.fly_body,
                "fly_upper": selection.fly_upper,
                "put_credit_natural": selection.conservative_put_credit,
                "fly_debit_natural": selection.conservative_fly_debit,
                "net_debit_natural": selection.conservative_net_debit,
                "maximum_loss_dollars": selection.maximum_loss_dollars,
                "legs": [
                    {"role": leg.role, "action": leg.action, "ratio": leg.ratio, "right": leg.right, "strike": leg.strike, "greeks": asdict(leg.quote)}
                    for leg in selection.legs
                ],
            }
        payload = decision_payload(evaluation, self.strategy_config, structure=structure)
        run_id, context = self._start_run(evaluation)
        decision = self.decisions.ingest(payload, context)
        return payload, run_id, context, decision.decision_id

    def _new_position(
        self, evaluation: EntryEvaluation, selection: Spxw0DteSelection
    ) -> PositionState:
        payload, run_id, context, decision_id = self._journal_decision(evaluation, selection)
        legs = tuple(
            TradeIntentLeg(
                asset_class="OPT", symbol="SPXW", con_id=int(leg.contract.conId),
                local_symbol=str(getattr(leg.contract, "localSymbol", "") or "") or None,
                exchange=str(getattr(leg.contract, "exchange", "") or "SMART"), currency="USD",
                side=leg.action, ratio=leg.ratio, quantity=1.0, expiry=selection.expiry,
                strike=leg.strike, option_right=leg.right, multiplier=float(selection.multiplier),
                open_price=leg.quote.mid, metadata={"role": leg.role, "greeks": asdict(leg.quote)},
            )
            for leg in selection.legs
        )
        trade_id = self.decisions.create_trade_intent(
            payload,
            context,
            TradeIntent(
                "BAG", "SPXW", "short", "planned", None, 1.0, "USD",
                selection.conservative_net_debit, None, None, "SMART", legs,
                {"paper_only": True, "maximum_loss_dollars": selection.maximum_loss_dollars},
            ),
        )
        return PositionState(
            signal_day=evaluation.day, expiry=selection.expiry, run_id=run_id,
            signal_key=str(payload["signal_key"]), decision_id=decision_id, trade_id=trade_id,
            order_ref=f"SPX0-E-{evaluation.day.replace('-', '')}"[:45],
            maximum_loss_dollars=selection.maximum_loss_dollars,
            net_debit=selection.conservative_net_debit,
            leg_con_ids=[int(leg.contract.conId) for leg in selection.legs],
        )

    def _execution_context(self, position: PositionState) -> ExecutionContext:
        return ExecutionContext(
            run_id=position.run_id, signal_key=position.signal_key, trade_id=position.trade_id,
            decision_id=position.decision_id, account=self.monitor_config.account,
            client_id=self.monitor_config.client_id, observed_at_utc=now_utc_iso(),
        )

    def evaluate_and_submit(
        self,
        records: Iterable[object],
        top50_symbols: Iterable[str],
        now_et: pd.Timestamp | None = None,
    ) -> MonitorState:
        now_et = now_et or pd.Timestamp.now(tz="America/New_York")
        state = load_state(self.monitor_config.state_path)
        today = self.monitor_config.day.strftime("%Y-%m-%d")
        if today in state.evaluated_days:
            self._service_orders(state, now_et)
            atomic_write_state(self.monitor_config.state_path, state)
            return state
        evaluation = evaluate_entry(
            records, top50_symbols, self.monitor_config.day,
            self.monitor_config.raw_spx_path, self.strategy_config,
        )
        clock = now_et.strftime("%H:%M:%S")
        timing_ok = (
            self.strategy_config.decision_time_et
            <= clock
            <= self.strategy_config.latest_submission_time_et
        )
        if evaluation.accepted and not timing_ok:
            evaluation = replace(
                evaluation,
                status="vetoed",
                reason="outside_0940_submission_window",
                factors=evaluation.factors
                + (
                    {
                        "factor_code": "0940_submission_window",
                        "observed_value": clock,
                        "operator": "between_inclusive",
                        "threshold": {
                            "start": self.strategy_config.decision_time_et,
                            "end": self.strategy_config.latest_submission_time_et,
                        },
                        "passed": False,
                    },
                ),
            )
        state.evaluated_days.append(today)
        if not evaluation.accepted:
            _, run_id, _, _ = self._journal_decision(evaluation, None)
            self._finish_run(run_id, evaluation, evaluation.status)
            self.log(f"SPXW 0DTE 09:40 no trade: {evaluation.reason}")
            atomic_write_state(self.monitor_config.state_path, state)
            return state
        selection = select_spxw_0dte_combo(
            self.ib, decision_day=self.monitor_config.day,
            decision_price=float(evaluation.decision_price),
            prior_atr14=float(evaluation.prior_atr14),
            include_iu30=evaluation.iu30_overlay,
            put_width=self.strategy_config.spread_width_points,
            fly_width=self.strategy_config.fly_width_points,
        )
        if self.options_risk_budget is not None and not self.options_risk_budget.can_allocate(selection.maximum_loss_dollars):
            blocked = EntryEvaluation(**{**asdict(evaluation), "status": "blocked_risk", "reason": "shared_options_risk_budget"})
            _, run_id, _, _ = self._journal_decision(blocked, selection)
            self._finish_run(run_id, blocked, blocked.reason)
            self.log(f"SPXW 0DTE blocked by shared options risk budget requested={selection.maximum_loss_dollars:.2f}")
            atomic_write_state(self.monitor_config.state_path, state)
            return state
        if self._unknown_own_order(state):
            raise RuntimeError("unknown SPXW 0DTE strategy order exists; refusing duplicate entry")
        position = self._new_position(evaluation, selection)
        state.positions.append(position)
        if not self.monitor_config.place_orders:
            self.executions.record_blocker(
                self._execution_context(position), status="execution_blocked",
                reason_code="dry_run_no_order",
                reason="SPXW 0DTE signal accepted but --place-orders is absent",
                requested_quantity=1, occurred_at=now_utc_iso(),
                details={"net_debit": selection.conservative_net_debit, "maximum_loss": selection.maximum_loss_dollars},
            )
            position.status = "dry_run"
            self._finish_run(position.run_id, evaluation, "dry_run_no_order")
        else:
            order = build_entry_order(selection, account=self.monitor_config.account, order_ref=position.order_ref)
            trade = self.ib.placeOrder(selection.combo_contract, order)
            self.ib.sleep(1.0)
            position.status = "entry_submitted"
            position.order_id = int(getattr(trade.order, "orderId", 0) or 0)
            position.submitted_at_utc = now_utc_iso()
            self.executions.ingest_trade(trade, self._execution_context(position), role="entry")
            self.log(
                f"SPXW 0DTE 09:40 combo submitted put={selection.short_put_strike:g}/{selection.long_put_strike:g} "
                f"iu30={evaluation.iu30_overlay} signed_limit={order.lmtPrice:.2f}"
            )
        atomic_write_state(self.monitor_config.state_path, state)
        return state

    def _service_orders(self, state: MonitorState, now_et: pd.Timestamp) -> None:
        for position in state.positions:
            if position.status != "entry_submitted":
                continue
            trade = self._find_trade(position.order_ref, position.order_id)
            if trade is None:
                continue
            self.executions.ingest_trade(trade, self._execution_context(position), role="entry")
            status = _order_status(trade)
            if status == "Filled":
                position.status = "open"
                position.fill_price = _fill_price(trade)
                position.filled_at_utc = now_utc_iso()
            elif status in DONE_ORDER_STATUSES:
                position.status = "entry_not_filled"
                position.last_error = f"terminal broker status {status}"
                self._complete_run(position.run_id, position.signal_day, position.status)
            elif position.submitted_at_utc:
                submitted = pd.Timestamp(position.submitted_at_utc)
                if now_et.tz_convert("UTC") >= submitted + pd.Timedelta(minutes=self.monitor_config.entry_work_minutes):
                    self.ib.cancelOrder(trade.order)
                    self.ib.sleep(1.0)
                    self.executions.ingest_trade(trade, self._execution_context(position), role="entry")
                    position.status = "entry_not_filled"
                    position.last_error = "entry work window elapsed"
                    self._complete_run(
                        position.run_id, position.signal_day, "entry_work_window_elapsed"
                    )

    def tick(self, now_et: pd.Timestamp | None = None) -> MonitorState:
        now_et = now_et or pd.Timestamp.now(tz="America/New_York")
        if now_et.tzinfo is None:
            now_et = now_et.tz_localize("America/New_York")
        state = load_state(self.monitor_config.state_path)
        self._service_orders(state, now_et)
        for position in state.positions:
            if position.status == "open" and now_et.strftime("%Y%m%d") >= position.expiry and now_et.strftime("%H:%M") >= "16:05":
                position.status = "expired"
                position.closed_at_utc = now_utc_iso()
                self._complete_run(position.run_id, position.signal_day, "expired")
        atomic_write_state(self.monitor_config.state_path, state)
        return state

    def safe_tick(self, now_et: pd.Timestamp | None = None) -> MonitorState:
        try:
            return self.tick(now_et)
        except Exception as exc:
            state = load_state(self.monitor_config.state_path)
            state.last_error = f"{type(exc).__name__}: {exc}"
            atomic_write_state(self.monitor_config.state_path, state)
            self.log(f"SPXW 0DTE monitor error (other strategies continue): {type(exc).__name__}: {exc}")
            return state

    def safe_evaluate_and_submit(
        self, records: Iterable[object], top50_symbols: Iterable[str], now_et: pd.Timestamp | None = None
    ) -> MonitorState:
        try:
            return self.evaluate_and_submit(records, top50_symbols, now_et)
        except Exception as exc:
            state = load_state(self.monitor_config.state_path)
            state.last_error = f"{type(exc).__name__}: {exc}"
            atomic_write_state(self.monitor_config.state_path, state)
            self.log(f"SPXW 0DTE evaluation error (other strategies continue): {type(exc).__name__}: {exc}")
            return state
