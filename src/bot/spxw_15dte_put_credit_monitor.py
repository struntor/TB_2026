from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Callable

import pandas as pd
from ib_insync import IB, Stock, util

from src.execution.spxw_put_credit import (
    PutCreditSelection,
    build_entry_order,
    build_exit_order,
    require_paper_account,
    select_spxw_put_credit,
    selection_for_identity,
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
from src.strategies.spxw_15dte_journal import decision_payload, register_manifest
from src.strategies.spxw_15dte_put_credit import (
    STRATEGY_ID,
    STRATEGY_VARIANT_ID,
    STRATEGY_VERSION,
    EntryEvaluation,
    Spxw15DteConfig,
    apply_quote_gates,
    config_hash,
    evaluate_damage,
    evaluate_entry,
)


RAW_SPY_PATH = Path(r"D:\Trading\Rohdaten\Rohdaten_SPY.csv")
DEFAULT_STATE_PATH = Path(r"D:\Trading\state\spxw_15dte_put_credit_v1.json")
DONE_ORDER_STATUSES = frozenset({"Filled", "Cancelled", "ApiCancelled", "Inactive"})


@dataclass(frozen=True, slots=True)
class MonitorConfig:
    day: pd.Timestamp
    account: str
    client_id: int
    place_orders: bool
    journal_path: Path
    state_path: Path = DEFAULT_STATE_PATH
    raw_spy_path: Path = RAW_SPY_PATH
    poll_seconds: float = 15.0
    entry_work_minutes: int = 5


@dataclass(slots=True)
class PositionState:
    signal_day: str
    expiry: str
    short_strike: float
    long_strike: float
    short_con_id: int
    long_con_id: int
    entry_credit_cons: float
    entry_credit_mid: float
    run_id: str
    signal_key: str
    decision_id: str
    trade_id: str
    entry_order_ref: str
    status: str = "planned"
    entry_order_id: int | None = None
    entry_submitted_at_utc: str | None = None
    entry_fill_price: float | None = None
    entry_filled_at_utc: str | None = None
    damage_signal_day: str | None = None
    damage_rejected_friction: bool = False
    exit_order_ref: str | None = None
    exit_order_id: int | None = None
    exit_submitted_at_utc: str | None = None
    exit_fill_price: float | None = None
    closed_at_utc: str | None = None
    last_error: str | None = None


@dataclass(slots=True)
class MonitorState:
    schema_version: int = 1
    evaluated_days: list[str] = field(default_factory=list)
    damage_evaluated_days: list[str] = field(default_factory=list)
    reentry_activation_day: str | None = None
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


def _normalize_bars(rows: Any) -> pd.DataFrame:
    frame = util.df(rows)
    if frame is None or frame.empty:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
    frame = frame[["date", "open", "high", "low", "close", "volume"]].copy()
    stamps = pd.to_datetime(frame["date"], errors="coerce")
    if getattr(stamps.dt, "tz", None) is not None:
        stamps = stamps.dt.tz_convert("America/New_York").dt.tz_localize(None)
    frame["date"] = stamps
    return frame.dropna(subset=["date"]).sort_values("date")


def _order_status(trade: Any) -> str:
    return str(getattr(getattr(trade, "orderStatus", None), "status", "") or "")


def _fill_price(trade: Any) -> float | None:
    value = float(getattr(getattr(trade, "orderStatus", None), "avgFillPrice", 0.0) or 0.0)
    return value if value != 0 else None


def friction_gap_dollars(
    *,
    entry_credit_mid: float,
    entry_credit_cons: float,
    exit_debit_mid: float,
    exit_debit_cons: float,
) -> float:
    """Return historical P/L(mid) minus P/L(conservative)."""
    return (
        entry_credit_mid
        - entry_credit_cons
        + exit_debit_cons
        - exit_debit_mid
    ) * 100.0


def order_work_window_elapsed(
    submitted_at_utc: str,
    now_utc: pd.Timestamp,
    minutes: int,
) -> bool:
    submitted = pd.Timestamp(submitted_at_utc)
    if submitted.tzinfo is None:
        submitted = submitted.tz_localize("UTC")
    return now_utc.tz_convert("UTC") >= submitted.tz_convert("UTC") + pd.Timedelta(minutes=minutes)


class Spxw15DtePutCreditMonitor:
    def __init__(
        self,
        ib: IB,
        monitor_config: MonitorConfig,
        *,
        strategy_config: Spxw15DteConfig = Spxw15DteConfig(),
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
        self.spy = self.ib.qualifyContracts(Stock("SPY", "SMART", "USD"))[0]
        if self.options_risk_budget is not None:
            self.options_risk_budget.register(STRATEGY_ID, self._active_defined_risk)

    def close(self) -> None:
        self.store.close()

    def _active_defined_risk(self) -> float:
        state = load_state(self.monitor_config.state_path)
        active = {"planned", "entry_submitted", "open", "exit_submitted"}
        return sum(
            max(
                0.0,
                (
                    position.short_strike
                    - position.long_strike
                    - position.entry_credit_cons
                )
                * 100.0,
            )
            for position in state.positions
            if position.status in active
        )

    def _bars(self) -> pd.DataFrame:
        historical = pd.read_csv(self.monitor_config.raw_spy_path)
        live = _normalize_bars(
            self.ib.reqHistoricalData(
                self.spy,
                endDateTime="",
                durationStr="30 D",
                barSizeSetting="5 mins",
                whatToShow="TRADES",
                useRTH=True,
                formatDate=1,
                keepUpToDate=False,
            )
        )
        combined = pd.concat([historical, live], ignore_index=True, sort=False)
        combined["date"] = pd.to_datetime(combined["date"], errors="coerce")
        return combined.dropna(subset=["date"]).drop_duplicates("date", keep="last").sort_values("date")

    def _all_trades(self) -> tuple[Any, ...]:
        trades = list(self.ib.trades())
        try:
            trades.extend(self.ib.reqCompletedOrders(False))
        except Exception as exc:
            self.log(f"SPXW 15DTE completed-order lookup unavailable: {exc}")
        unique: dict[tuple[int, str], Any] = {}
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

    def _unknown_exposure_exists(self, state: MonitorState) -> bool:
        known = {position.short_con_id for position in state.positions} | {position.long_con_id for position in state.positions}
        for position in self.ib.positions():
            if str(getattr(position, "account", "") or "") != self.monitor_config.account:
                continue
            contract = getattr(position, "contract", None)
            if str(getattr(contract, "tradingClass", "") or "").upper() == "SPXW" and int(getattr(contract, "conId", 0) or 0) not in known and float(getattr(position, "position", 0.0) or 0.0) != 0:
                return True
        for trade in self._all_trades():
            ref = str(getattr(getattr(trade, "order", None), "orderRef", "") or "")
            if ref.startswith("SPX15-") and _order_status(trade) not in DONE_ORDER_STATUSES:
                if not any(ref in {item.entry_order_ref, item.exit_order_ref} for item in state.positions):
                    return True
        return False

    def _selection(self, position: PositionState) -> PutCreditSelection:
        selection = selection_for_identity(
            self.ib,
            expiry=position.expiry,
            short_strike=position.short_strike,
            long_strike=position.long_strike,
        )
        observed = (int(selection.short_contract.conId), int(selection.long_contract.conId))
        expected = (position.short_con_id, position.long_con_id)
        if observed != expected:
            raise RuntimeError(f"SPXW option identity changed after restart: expected={expected} observed={observed}")
        return selection

    def _execution_context(self, position: PositionState) -> ExecutionContext:
        return ExecutionContext(
            run_id=position.run_id,
            signal_key=position.signal_key,
            trade_id=position.trade_id,
            decision_id=position.decision_id,
            account=self.monitor_config.account,
            client_id=self.monitor_config.client_id,
            observed_at_utc=now_utc_iso(),
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
                "paper", "paper", started, now_utc_iso(), "completed",
                asdict(self.strategy_config),
                {
                    "account": self.monitor_config.account,
                    "paper_only": True,
                    "outcome": outcome,
                },
                STRATEGY_VARIANT_ID,
            )
        )

    def _journal_decision(
        self,
        evaluation: EntryEvaluation,
        selection: PutCreditSelection | None,
    ) -> tuple[dict[str, Any], str, DecisionIngestContext, str]:
        structure = {}
        if selection is not None:
            structure = {
                "expiry": selection.expiry,
                "short_strike": selection.short_strike,
                "long_strike": selection.long_strike,
                "short_delta": selection.short_quote.delta,
                "short_iv": selection.short_quote.implied_vol,
                "entry_credit_cons": selection.conservative_credit,
                "entry_credit_mid": selection.mid_credit,
                "short_greeks": asdict(selection.short_quote),
                "long_greeks": asdict(selection.long_quote),
            }
        payload = decision_payload(evaluation, self.strategy_config, structure=structure)
        run_id, context = self._start_run(evaluation)
        decision = self.decisions.ingest(payload, context)
        return payload, run_id, context, decision.decision_id

    def _new_position(
        self,
        evaluation: EntryEvaluation,
        selection: PutCreditSelection,
    ) -> PositionState:
        payload, run_id, context, decision_id = self._journal_decision(evaluation, selection)
        legs = tuple(
            TradeIntentLeg(
                asset_class="OPT", symbol="SPXW", con_id=int(contract.conId),
                local_symbol=str(getattr(contract, "localSymbol", "") or "") or None,
                exchange=str(getattr(contract, "exchange", "") or "SMART"), currency="USD",
                side=side, ratio=1, quantity=1.0, expiry=selection.expiry,
                strike=strike, option_right="P", multiplier=float(selection.multiplier),
                open_price=quote.mid, metadata={"role": role, "greeks": asdict(quote)},
            )
            for role, contract, strike, side, quote in (
                ("short_put", selection.short_contract, selection.short_strike, "SELL", selection.short_quote),
                ("long_put", selection.long_contract, selection.long_strike, "BUY", selection.long_quote),
            )
        )
        trade_id = self.decisions.create_trade_intent(
            payload,
            context,
            TradeIntent(
                "BAG", "SPXW", "short", "planned", None, 1.0, "USD",
                selection.conservative_credit, None, None, "SMART", legs,
                {"paper_only": True, "maximum_loss_dollars": selection.maximum_loss_dollars},
            ),
        )
        return PositionState(
            signal_day=evaluation.day, expiry=selection.expiry,
            short_strike=selection.short_strike, long_strike=selection.long_strike,
            short_con_id=int(selection.short_contract.conId), long_con_id=int(selection.long_contract.conId),
            entry_credit_cons=selection.conservative_credit, entry_credit_mid=selection.mid_credit,
            run_id=run_id, signal_key=str(payload["signal_key"]), decision_id=decision_id,
            trade_id=trade_id, entry_order_ref=f"SPX15-E-{evaluation.day.replace('-', '')}"[:45],
        )

    def _submit_entry(self, position: PositionState, selection: PutCreditSelection) -> None:
        if self._find_trade(position.entry_order_ref, position.entry_order_id):
            raise RuntimeError("duplicate SPXW 15DTE entry order")
        if not self.monitor_config.place_orders:
            self.executions.record_blocker(
                self._execution_context(position),
                status="execution_blocked",
                reason_code="dry_run_no_order",
                reason="SPXW 15DTE signal accepted but --place-orders is absent",
                requested_quantity=1,
                occurred_at=now_utc_iso(),
                details={
                    "entry_credit": selection.conservative_credit,
                    "maximum_loss": selection.maximum_loss_dollars,
                },
            )
            position.status = "dry_run"
            self._finish_run(position.run_id, position.signal_day, "dry_run_no_order")
            return
        order = build_entry_order(selection, account=self.monitor_config.account, order_ref=position.entry_order_ref)
        trade = self.ib.placeOrder(selection.combo_contract, order)
        self.ib.sleep(1.0)
        position.status = "entry_submitted"
        position.entry_order_id = int(getattr(trade.order, "orderId", 0) or 0)
        position.entry_submitted_at_utc = now_utc_iso()
        self.executions.ingest_trade(trade, self._execution_context(position), role="entry")
        self.log(f"SPXW 15DTE entry submitted expiry={position.expiry} strikes={position.short_strike:g}/{position.long_strike:g} credit={selection.conservative_credit:.2f}")

    def _service_orders(self, state: MonitorState) -> None:
        for position in state.positions:
            if position.status not in {"entry_submitted", "exit_submitted"}:
                continue
            ref = position.entry_order_ref if position.status == "entry_submitted" else position.exit_order_ref
            oid = position.entry_order_id if position.status == "entry_submitted" else position.exit_order_id
            trade = self._find_trade(ref, oid)
            if trade is None:
                continue
            role = "entry" if position.status == "entry_submitted" else "exit"
            self.executions.ingest_trade(trade, self._execution_context(position), role=role)
            status = _order_status(trade)
            if status == "Filled" and role == "entry":
                position.status = "open"
                position.entry_fill_price = _fill_price(trade)
                position.entry_filled_at_utc = now_utc_iso()
            elif status == "Filled":
                position.status = "closed"
                position.exit_fill_price = _fill_price(trade)
                position.closed_at_utc = now_utc_iso()
                state.reentry_activation_day = pd.Timestamp.now(tz="America/New_York").strftime("%Y-%m-%d")
                self._finish_run(position.run_id, position.signal_day, "damage_exit_filled")
            elif status in DONE_ORDER_STATUSES:
                position.status = "entry_not_filled" if role == "entry" else "exit_failed"
                position.last_error = f"terminal broker status {status}"
                self._finish_run(position.run_id, position.signal_day, position.status)
            elif (
                role == "entry"
                and position.entry_submitted_at_utc
                and order_work_window_elapsed(
                    position.entry_submitted_at_utc,
                    pd.Timestamp.now(tz="UTC"),
                    self.monitor_config.entry_work_minutes,
                )
            ):
                self.ib.cancelOrder(trade.order)
                self.ib.sleep(1.0)
                self.executions.ingest_trade(
                    trade, self._execution_context(position), role="entry"
                )
                position.status = "entry_not_filled"
                position.last_error = (
                    "entry was not filled inside the configured work window"
                )
                self._finish_run(
                    position.run_id, position.signal_day, "entry_work_window_elapsed"
                )

    def _service_damage_exits(self, state: MonitorState, now_et: pd.Timestamp) -> None:
        if now_et.strftime("%H:%M") < "09:35" or now_et.strftime("%H:%M") > "09:45":
            return
        today = now_et.strftime("%Y-%m-%d")
        for position in state.positions:
            if position.status != "open" or not position.damage_signal_day or position.damage_rejected_friction:
                continue
            if today <= position.damage_signal_day:
                continue
            selection = self._selection(position)
            friction = friction_gap_dollars(
                entry_credit_mid=position.entry_credit_mid,
                entry_credit_cons=position.entry_credit_cons,
                exit_debit_mid=selection.mid_exit_debit,
                exit_debit_cons=selection.conservative_exit_debit,
            )
            if friction > self.strategy_config.maximum_friction_gap_dollars:
                position.damage_rejected_friction = True
                self.log(f"SPXW 15DTE damage exit rejected by friction gap={friction:.2f}")
                continue
            ref = f"SPX15-X-{position.signal_day.replace('-', '')}"[:45]
            order = build_exit_order(selection, account=self.monitor_config.account, order_ref=ref)
            trade = self.ib.placeOrder(selection.inverse_combo_contract, order)
            self.ib.sleep(1.0)
            position.status = "exit_submitted"
            position.exit_order_ref = ref
            position.exit_order_id = int(getattr(trade.order, "orderId", 0) or 0)
            position.exit_submitted_at_utc = now_utc_iso()
            self.executions.ingest_trade(trade, self._execution_context(position), role="exit")
            self.log(f"SPXW 15DTE damage exit submitted debit={selection.conservative_exit_debit:.2f} friction_gap={friction:.2f}")

    def _evaluate_damage(self, state: MonitorState, bars: pd.DataFrame, now_et: pd.Timestamp) -> None:
        today = now_et.strftime("%Y-%m-%d")
        if now_et.strftime("%H:%M") < "16:00" or today in state.damage_evaluated_days:
            return
        evaluation = evaluate_damage(bars, today, self.strategy_config)
        state.damage_evaluated_days.append(today)
        if evaluation.triggered:
            for position in state.positions:
                expiry = pd.Timestamp(position.expiry).strftime("%Y-%m-%d")
                if position.status == "open" and today < expiry and position.damage_signal_day is None:
                    position.damage_signal_day = today
            self.log(f"SPXW 15DTE EOD damage signal accepted for {today}")

    def _expire(self, state: MonitorState, now_et: pd.Timestamp) -> None:
        for position in state.positions:
            expiry = pd.Timestamp(position.expiry).date()
            if position.status == "open" and now_et.date() >= expiry and now_et.strftime("%H:%M") >= "16:00":
                position.status = "expired"
                position.closed_at_utc = now_utc_iso()
                self._finish_run(position.run_id, position.signal_day, "expired")

    def _evaluate_entry(self, state: MonitorState, bars: pd.DataFrame, now_et: pd.Timestamp) -> None:
        today = now_et.strftime("%Y-%m-%d")
        if now_et.strftime("%H:%M") < "13:35" or now_et.strftime("%H:%M") > "13:40" or today in state.evaluated_days:
            return
        preliminary = evaluate_entry(
            bars, today, self.strategy_config,
            reentry_activation_day=state.reentry_activation_day,
        )
        state.evaluated_days.append(today)
        if not preliminary.accepted:
            _, run_id, _, _ = self._journal_decision(preliminary, None)
            self._finish_run(run_id, today, preliminary.status)
            self.log(f"SPXW 15DTE no trade: {preliminary.reason}")
            return
        active = [position for position in state.positions if position.status in {"planned", "entry_submitted", "open", "exit_submitted"}]
        if len(active) >= self.strategy_config.maximum_concurrent_positions:
            blocked = EntryEvaluation(**{**asdict(preliminary), "status": "blocked_risk", "reason": "maximum_concurrent_positions"})
            _, run_id, _, _ = self._journal_decision(blocked, None)
            self._finish_run(run_id, today, "maximum_concurrent_positions")
            return
        selection = select_spxw_put_credit(
            self.ib, decision_day=pd.Timestamp(today),
            target_dte=self.strategy_config.target_calendar_dte,
            target_delta=self.strategy_config.target_short_delta,
            width=self.strategy_config.spread_width_points,
        )
        final = apply_quote_gates(
            preliminary, conservative_credit=selection.conservative_credit,
            short_iv=selection.short_quote.implied_vol, config=self.strategy_config,
        )
        if not final.accepted:
            _, run_id, _, _ = self._journal_decision(final, selection)
            self._finish_run(run_id, today, final.status)
            self.log(f"SPXW 15DTE quote gate veto: {final.reason}")
            return
        if (
            self.options_risk_budget is not None
            and not self.options_risk_budget.can_allocate(selection.maximum_loss_dollars)
        ):
            blocked = EntryEvaluation(
                **{
                    **asdict(final),
                    "status": "blocked_risk",
                    "reason": "shared_options_risk_budget",
                }
            )
            _, run_id, _, _ = self._journal_decision(blocked, selection)
            self._finish_run(run_id, today, "shared_options_risk_budget")
            self.log(
                "SPXW 15DTE blocked by shared options risk budget "
                f"requested={selection.maximum_loss_dollars:.2f} "
                f"available={self.options_risk_budget.available_risk_usd:.2f}"
            )
            return
        if self._unknown_exposure_exists(state):
            raise RuntimeError("unknown SPXW strategy exposure/order exists; refusing duplicate entry")
        position = self._new_position(final, selection)
        state.positions.append(position)
        self._submit_entry(position, selection)

    def tick(self, now_et: pd.Timestamp | None = None) -> MonitorState:
        now_et = now_et or pd.Timestamp.now(tz="America/New_York")
        if now_et.tzinfo is None:
            now_et = now_et.tz_localize("America/New_York")
        state = load_state(self.monitor_config.state_path)
        self._service_orders(state)
        bars = self._bars()
        self._service_damage_exits(state, now_et)
        self._evaluate_entry(state, bars, now_et)
        self._evaluate_damage(state, bars, now_et)
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
            self.log(f"SPXW 15DTE monitor error (other strategies continue): {type(exc).__name__}: {exc}")
            return state

    def run_until_close(self) -> None:
        while True:
            now_et = pd.Timestamp.now(tz="America/New_York")
            self.safe_tick(now_et)
            if now_et.strftime("%H:%M") >= "16:05":
                return
            self.ib.sleep(max(self.monitor_config.poll_seconds, 1.0))
