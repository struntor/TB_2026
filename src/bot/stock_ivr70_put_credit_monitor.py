from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Callable

import pandas as pd
from ib_insync import IB

from src.execution.stock_put_credit import (
    StockPutCreditSelectionError,
    StockPutCreditSelection,
    build_entry_order,
    build_exit_order,
    require_paper_account,
    select_stock_put_expiry,
    select_stock_put_credit,
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
from src.strategies.stock_ivr70_journal import decision_payload, register_manifest
from src.strategies.stock_ivr70_put_credit import (
    STRATEGY_ID,
    STRATEGY_VARIANT_ID,
    STRATEGY_VERSION,
    CandidateEvaluation,
    StockIvr70Config,
    apply_expiry_and_earnings_gate,
    apply_quote_gates,
    config_hash,
    evaluate_candidate,
)


DEFAULT_STATE_PATH = Path(r"D:\Trading\state\stock_ivr70_put_credit_v1.json")
DONE_ORDER_STATUSES = frozenset({"Filled", "Cancelled", "ApiCancelled", "Inactive"})
ACTIVE_STATUSES = frozenset(
    {"planned", "entry_submitted", "open", "exit_submitted", "assignment_attention"}
)
EXPECTED_SELECTION_VETOES = frozenset(
    {
        "no_expiration_in_dte_window",
        "no_exact_width_pair",
        "no_positive_conservative_credit",
        "no_short_delta_in_range",
    }
)


@dataclass(frozen=True, slots=True)
class MonitorConfig:
    day: pd.Timestamp
    account: str
    client_id: int
    place_orders: bool
    journal_path: Path
    candidates_path: Path
    state_path: Path
    poll_seconds: float
    entry_work_minutes: int


@dataclass(slots=True)
class PositionState:
    symbol: str
    signal_day: str
    entry_day: str
    predicted_earnings: str
    expiry: str
    short_strike: float
    long_strike: float
    short_con_id: int
    long_con_id: int
    entry_credit_cons: float
    entry_credit_mid: float
    maximum_loss_dollars: float
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
    exit_reason: str | None = None
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
    exit_evaluated_days: dict[str, list[str]] = field(default_factory=dict)
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


def _window_elapsed(submitted_at_utc: str, now_utc: pd.Timestamp, minutes: int) -> bool:
    submitted = pd.Timestamp(submitted_at_utc)
    if submitted.tzinfo is None:
        submitted = submitted.tz_localize("UTC")
    return now_utc.tz_convert("UTC") >= submitted.tz_convert("UTC") + pd.Timedelta(minutes=minutes)


class StockIvr70PutCreditMonitor:
    def __init__(
        self,
        ib: IB,
        monitor_config: MonitorConfig,
        *,
        options_risk_budget: OptionsRiskBudget,
        strategy_config: StockIvr70Config = StockIvr70Config(),
        logger: Callable[[str], None] = print,
    ) -> None:
        require_paper_account(monitor_config.account)
        self.ib = ib
        self.monitor_config = monitor_config
        self.options_risk_budget = options_risk_budget
        self.strategy_config = strategy_config
        self.log = logger
        self.store = JournalStore(monitor_config.journal_path)
        register_manifest(self.store, strategy_config)
        self.decisions = DecisionJournalIngestor(self.store)
        self.executions = ExecutionJournalIngestor(self.store)
        self.options_risk_budget.register(STRATEGY_ID, self._active_defined_risk)

    def close(self) -> None:
        self.store.close()

    def _active_defined_risk(self) -> float:
        state = load_state(self.monitor_config.state_path)
        return sum(
            position.maximum_loss_dollars
            for position in state.positions
            if position.status in ACTIVE_STATUSES
        )

    def _all_trades(self) -> tuple[Any, ...]:
        trades = list(self.ib.trades())
        try:
            trades.extend(self.ib.reqCompletedOrders(False))
        except Exception as exc:
            self.log(f"stock IVR70 completed-order lookup unavailable: {exc}")
        unique: dict[tuple[int, str], Any] = {}
        for trade in trades:
            order = getattr(trade, "order", None)
            unique[(int(getattr(order, "orderId", 0) or 0), str(getattr(order, "orderRef", "") or ""))] = trade
        return tuple(unique.values())

    def _find_trade(self, order_ref: str | None, order_id: int | None) -> Any | None:
        if order_id:
            for trade in self._all_trades():
                order = getattr(trade, "order", None)
                if int(getattr(order, "orderId", 0) or 0) == order_id:
                    return trade
        for trade in self._all_trades():
            order = getattr(trade, "order", None)
            if order_ref and str(getattr(order, "orderRef", "") or "") == order_ref:
                return trade
        return None

    def _unknown_symbol_exposure(self, state: MonitorState, symbol: str) -> bool:
        known = {
            con_id
            for position in state.positions
            for con_id in (position.short_con_id, position.long_con_id)
        }
        for broker_position in self.ib.positions():
            if str(getattr(broker_position, "account", "") or "") != self.monitor_config.account:
                continue
            contract = getattr(broker_position, "contract", None)
            if (
                str(getattr(contract, "secType", "") or "").upper() == "OPT"
                and str(getattr(contract, "symbol", "") or "").upper() == symbol
                and int(getattr(contract, "conId", 0) or 0) not in known
                and float(getattr(broker_position, "position", 0.0) or 0.0) != 0
            ):
                return True
        for trade in self._all_trades():
            ref = str(getattr(getattr(trade, "order", None), "orderRef", "") or "")
            if ref.startswith("IVR70-") and _order_status(trade) not in DONE_ORDER_STATUSES:
                if not any(ref in {item.entry_order_ref, item.exit_order_ref} for item in state.positions):
                    return True
        return False

    def _selection(self, position: PositionState) -> StockPutCreditSelection:
        selection = selection_for_identity(
            self.ib,
            symbol=position.symbol,
            expiry=position.expiry,
            short_strike=position.short_strike,
            long_strike=position.long_strike,
            short_con_id=position.short_con_id,
            long_con_id=position.long_con_id,
        )
        observed = (int(selection.short_contract.conId), int(selection.long_contract.conId))
        expected = (position.short_con_id, position.long_con_id)
        if observed != expected:
            raise RuntimeError(
                f"{position.symbol} option identity changed after restart: expected={expected} observed={observed}"
            )
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

    def _start_run(self, evaluation: CandidateEvaluation) -> tuple[str, DecisionIngestContext]:
        run_id = deterministic_id(
            "run", STRATEGY_VARIANT_ID, evaluation.symbol, evaluation.signal_day,
            "paper", self.monitor_config.account,
        )
        started_at = pd.Timestamp(evaluation.decision_time)
        if started_at.tzinfo is None:
            started_at = started_at.tz_localize("America/New_York")
        started = started_at.tz_convert("UTC").isoformat()
        self.store.start_run(
            StrategyRunRecord(
                run_id, STRATEGY_ID, STRATEGY_VERSION, config_hash(self.strategy_config),
                "paper", "paper", started, None, "running", asdict(self.strategy_config),
                {"account": self.monitor_config.account, "paper_only": True}, STRATEGY_VARIANT_ID,
            )
        )
        return run_id, DecisionIngestContext(run_id, "paper")

    def _finish_run(self, run_id: str, evaluation: CandidateEvaluation, outcome: str) -> None:
        started_at = pd.Timestamp(evaluation.decision_time)
        if started_at.tzinfo is None:
            started_at = started_at.tz_localize("America/New_York")
        started = started_at.tz_convert("UTC").isoformat()
        self.store.start_run(
            StrategyRunRecord(
                run_id, STRATEGY_ID, STRATEGY_VERSION, config_hash(self.strategy_config),
                "paper", "paper", started, now_utc_iso(), "completed", asdict(self.strategy_config),
                {"account": self.monitor_config.account, "paper_only": True, "outcome": outcome},
                STRATEGY_VARIANT_ID,
            )
        )

    def _journal_decision(
        self,
        evaluation: CandidateEvaluation,
        selection: StockPutCreditSelection | None,
        *,
        structure: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], str, DecisionIngestContext, str]:
        observed_structure: dict[str, Any] = dict(structure or {})
        if selection is not None:
            observed_structure.update({
                "expiry": selection.expiry,
                "spot": selection.spot,
                "short_strike": selection.short_strike,
                "long_strike": selection.long_strike,
                "short_delta": selection.short_quote.delta,
                "short_iv": selection.short_quote.implied_vol,
                "entry_credit_cons": selection.conservative_credit,
                "entry_credit_mid": selection.mid_credit,
                "maximum_loss_dollars": selection.maximum_loss_dollars,
                "selection_priority": "lowest_maximum_loss_then_liquidity",
                "short_greeks": asdict(selection.short_quote),
                "long_greeks": asdict(selection.long_quote),
            })
        payload = decision_payload(
            evaluation,
            self.strategy_config,
            structure=observed_structure,
        )
        run_id, context = self._start_run(evaluation)
        decision = self.decisions.ingest(payload, context)
        return payload, run_id, context, decision.decision_id

    def _new_position(
        self,
        evaluation: CandidateEvaluation,
        selection: StockPutCreditSelection,
    ) -> PositionState:
        payload, run_id, context, decision_id = self._journal_decision(evaluation, selection)
        legs = tuple(
            TradeIntentLeg(
                asset_class="OPT",
                symbol=evaluation.symbol,
                con_id=int(contract.conId),
                local_symbol=str(getattr(contract, "localSymbol", "") or "") or None,
                exchange=str(getattr(contract, "exchange", "") or "SMART"),
                currency="USD",
                side=side,
                ratio=1,
                quantity=1.0,
                expiry=selection.expiry,
                strike=strike,
                option_right="P",
                multiplier=float(selection.multiplier),
                open_price=quote.mid,
                metadata={"role": role, "greeks": asdict(quote)},
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
                asset_class="BAG",
                symbol=evaluation.symbol,
                side="short",
                status="planned",
                opened_at_utc=None,
                quantity=1.0,
                currency="USD",
                entry_price=selection.conservative_credit,
                stop_price=None,
                target_price=None,
                exchange="SMART",
                legs=legs,
                metadata={
                    "paper_only": True,
                    "maximum_loss_dollars": selection.maximum_loss_dollars,
                    "selection_priority": "lowest_maximum_loss_then_liquidity",
                },
            ),
        )
        token = f"{evaluation.symbol}-{evaluation.signal_day.replace('-', '')}"
        return PositionState(
            evaluation.symbol, evaluation.signal_day, evaluation.entry_day,
            evaluation.predicted_earnings, selection.expiry,
            selection.short_strike, selection.long_strike,
            int(selection.short_contract.conId), int(selection.long_contract.conId),
            selection.conservative_credit, selection.mid_credit,
            selection.maximum_loss_dollars
            + self.strategy_config.estimated_round_trip_fees_dollars,
            run_id, str(payload["signal_key"]),
            decision_id, trade_id, f"IVR70-E-{token}"[:45],
        )

    def _submit_entry(self, position: PositionState, selection: StockPutCreditSelection) -> None:
        if self._find_trade(position.entry_order_ref, position.entry_order_id):
            raise RuntimeError(f"duplicate {position.symbol} IVR70 entry order")
        if not self.monitor_config.place_orders:
            self.executions.record_blocker(
                self._execution_context(position),
                status="execution_blocked",
                reason_code="dry_run_no_order",
                reason="stock IVR70 signal accepted but --place-orders is absent",
                requested_quantity=1,
                occurred_at=now_utc_iso(),
                details={
                    "entry_credit": selection.conservative_credit,
                    "maximum_loss": position.maximum_loss_dollars,
                    "risk_amount": position.maximum_loss_dollars,
                    "risk_currency": "USD",
                },
            )
            position.status = "dry_run"
            evaluation = self._evaluation_for_position(position)
            self._finish_run(position.run_id, evaluation, "dry_run_no_order")
            return
        order = build_entry_order(selection, account=self.monitor_config.account, order_ref=position.entry_order_ref)
        trade = self.ib.placeOrder(selection.combo_contract, order)
        self.ib.sleep(1.0)
        position.status = "entry_submitted"
        position.entry_order_id = int(getattr(trade.order, "orderId", 0) or 0)
        position.entry_submitted_at_utc = now_utc_iso()
        self.executions.ingest_trade(trade, self._execution_context(position), role="entry")
        self.log(
            f"stock IVR70 entry submitted {position.symbol} expiry={position.expiry} "
            f"strikes={position.short_strike:g}/{position.long_strike:g} "
            f"credit={selection.conservative_credit:.2f}"
        )

    def _evaluation_for_position(self, position: PositionState) -> CandidateEvaluation:
        return CandidateEvaluation(
            position.symbol, position.signal_day, position.entry_day, "accepted",
            "position_lifecycle", pd.Timestamp(f"{position.entry_day} {self.strategy_config.entry_start_time}"),
            0.0, 0.0, 0.0, position.predicted_earnings, (),
        )

    def _service_orders(self, state: MonitorState) -> None:
        for position in state.positions:
            if position.status not in {"entry_submitted", "exit_submitted"}:
                continue
            is_entry = position.status == "entry_submitted"
            trade = self._find_trade(
                position.entry_order_ref if is_entry else position.exit_order_ref,
                position.entry_order_id if is_entry else position.exit_order_id,
            )
            if trade is None:
                continue
            role = "entry" if is_entry else "exit"
            self.executions.ingest_trade(trade, self._execution_context(position), role=role)
            status = _order_status(trade)
            if status == "Filled" and is_entry:
                position.status = "open"
                position.entry_fill_price = _fill_price(trade)
                position.entry_filled_at_utc = now_utc_iso()
            elif status == "Filled":
                position.status = "closed"
                position.exit_fill_price = _fill_price(trade)
                position.closed_at_utc = now_utc_iso()
                self._finish_run(position.run_id, self._evaluation_for_position(position), position.exit_reason or "exit_filled")
            elif status in DONE_ORDER_STATUSES:
                position.last_error = f"terminal broker status {status}"
                if is_entry:
                    position.status = "entry_not_filled"
                    self._finish_run(
                        position.run_id,
                        self._evaluation_for_position(position),
                        position.status,
                    )
                else:
                    # A rejected/cancelled closing order does not remove the
                    # broker exposure. Keep the position open, retain its risk,
                    # and allow a fresh exit attempt on the next session.
                    position.status = "open"
            elif is_entry and position.entry_submitted_at_utc and _window_elapsed(
                position.entry_submitted_at_utc, pd.Timestamp.now(tz="UTC"),
                self.monitor_config.entry_work_minutes,
            ):
                self.ib.cancelOrder(trade.order)
                self.ib.sleep(1.0)
                self.executions.ingest_trade(trade, self._execution_context(position), role="entry")
                position.status = "entry_not_filled"
                position.last_error = "entry work window elapsed"
                self._finish_run(position.run_id, self._evaluation_for_position(position), "entry_work_window_elapsed")

    def _service_exits(self, state: MonitorState, now_et: pd.Timestamp) -> None:
        clock = now_et.strftime("%H:%M")
        if not (self.strategy_config.exit_start_time <= clock <= self.strategy_config.exit_end_time):
            return
        today = now_et.normalize().tz_localize(None)
        for position in state.positions:
            if position.status != "open":
                continue
            evaluated = state.exit_evaluated_days.setdefault(position.trade_id, [])
            today_text = today.strftime("%Y-%m-%d")
            if today_text in evaluated:
                continue
            evaluated.append(today_text)
            expiry = pd.Timestamp(position.expiry).normalize()
            remaining_dte = int((expiry - today).days)
            selection = self._selection(position)
            entry_credit = (
                abs(position.entry_fill_price)
                if position.entry_fill_price is not None
                else position.entry_credit_cons
            )
            target_debit = entry_credit * (1.0 - self.strategy_config.take_profit_fraction)
            reason = None
            if selection.conservative_exit_debit <= target_debit:
                reason = "take_profit_50"
            elif remaining_dte <= self.strategy_config.mandatory_exit_dte:
                reason = "mandatory_21dte_exit"
            if reason is None:
                continue
            if not self.monitor_config.place_orders:
                self.log(f"stock IVR70 exit blocked without --place-orders {position.symbol} reason={reason}")
                continue
            ref = (
                f"IVR70-X-{position.symbol}-"
                f"{position.signal_day.replace('-', '')}-{today_text.replace('-', '')}"
            )[:45]
            order = build_exit_order(selection, account=self.monitor_config.account, order_ref=ref)
            trade = self.ib.placeOrder(selection.inverse_combo_contract, order)
            self.ib.sleep(1.0)
            position.status = "exit_submitted"
            position.exit_reason = reason
            position.exit_order_ref = ref
            position.exit_order_id = int(getattr(trade.order, "orderId", 0) or 0)
            position.exit_submitted_at_utc = now_utc_iso()
            self.executions.ingest_trade(trade, self._execution_context(position), role="exit")
            self.log(
                f"stock IVR70 exit submitted {position.symbol} reason={reason} "
                f"debit={selection.conservative_exit_debit:.2f}"
            )

    def _load_candidates(self, today: str) -> pd.DataFrame:
        if not self.monitor_config.candidates_path.exists():
            raise FileNotFoundError(self.monitor_config.candidates_path)
        frame = pd.read_csv(self.monitor_config.candidates_path)
        required = {
            "symbol", "signal_date", "entry_date", "modeled_iv_rank",
            "previous_modeled_iv_rank", "median_dollar_volume60", "predicted_earnings",
        }
        missing = required - set(frame.columns)
        if missing:
            raise ValueError(f"live candidate file missing columns: {sorted(missing)}")
        return frame[frame["entry_date"].astype(str).eq(today)].copy()

    def _record_blocked(
        self,
        evaluation: CandidateEvaluation,
        selection: StockPutCreditSelection | None,
        reason: str,
    ) -> None:
        blocked = replace(evaluation, status="blocked_risk", reason=reason)
        payload, run_id, _, decision_id = self._journal_decision(blocked, selection)
        risk_amount = (
            selection.maximum_loss_dollars
            + self.strategy_config.estimated_round_trip_fees_dollars
            if selection is not None
            else None
        )
        self.executions.record_blocker(
            ExecutionContext(
                run_id=run_id,
                signal_key=str(payload["signal_key"]),
                trade_id=None,
                decision_id=decision_id,
                account=self.monitor_config.account,
                client_id=self.monitor_config.client_id,
                observed_at_utc=now_utc_iso(),
            ),
            status="blocked_risk",
            reason_code=reason,
            reason=f"stock IVR70 entry blocked: {reason}",
            requested_quantity=1,
            occurred_at=now_utc_iso(),
            details={
                "risk_amount": risk_amount,
                "risk_currency": "USD" if risk_amount is not None else None,
                "available_risk_usd": self.options_risk_budget.available_risk_usd,
            },
        )
        self._finish_run(run_id, blocked, reason)

    def _evaluate_entries(self, state: MonitorState, now_et: pd.Timestamp) -> None:
        today = now_et.strftime("%Y-%m-%d")
        clock = now_et.strftime("%H:%M")
        if today in state.evaluated_days or not (
            self.strategy_config.entry_start_time <= clock <= self.strategy_config.entry_end_time
        ):
            return
        state.evaluated_days.append(today)
        candidates = self._load_candidates(today)
        if candidates.empty:
            self.log(f"stock IVR70 no candidates for {today}")
            return
        active_symbols = {
            position.symbol for position in state.positions if position.status in ACTIVE_STATUSES
        }
        quoted: list[tuple[CandidateEvaluation, StockPutCreditSelection]] = []
        for row in candidates.to_dict(orient="records"):
            evaluation = evaluate_candidate(row, self.strategy_config)
            if evaluation.symbol in active_symbols:
                self._record_blocked(evaluation, None, "symbol_already_open")
                continue
            try:
                expiry_selection = select_stock_put_expiry(
                    self.ib,
                    symbol=evaluation.symbol,
                    decision_day=now_et.tz_localize(None),
                    target_dte=self.strategy_config.target_calendar_dte,
                    min_dte=self.strategy_config.minimum_calendar_dte,
                    max_dte=self.strategy_config.maximum_calendar_dte,
                )
                after_expiry = apply_expiry_and_earnings_gate(
                    evaluation,
                    expiry=expiry_selection.expiry,
                    config=self.strategy_config,
                )
                if not after_expiry.accepted:
                    _, run_id, _, _ = self._journal_decision(
                        after_expiry,
                        None,
                        structure={
                            "expiry": expiry_selection.expiry,
                            "spot": expiry_selection.spot,
                            "selection_stage": "pre_quote_earnings_gate",
                        },
                    )
                    self._finish_run(run_id, after_expiry, after_expiry.reason)
                    continue
                selection = select_stock_put_credit(
                    self.ib,
                    symbol=evaluation.symbol,
                    decision_day=now_et.tz_localize(None),
                    target_dte=self.strategy_config.target_calendar_dte,
                    min_dte=self.strategy_config.minimum_calendar_dte,
                    max_dte=self.strategy_config.maximum_calendar_dte,
                    target_delta=self.strategy_config.target_short_delta,
                    minimum_abs_delta=self.strategy_config.minimum_short_abs_delta,
                    maximum_abs_delta=self.strategy_config.maximum_short_abs_delta,
                    width=self.strategy_config.spread_width_points,
                    expiry_selection=expiry_selection,
                )
                final = apply_quote_gates(
                    after_expiry,
                    conservative_credit=selection.conservative_credit,
                    maximum_loss_dollars=selection.maximum_loss_dollars,
                    config=self.strategy_config,
                )
                if not final.accepted:
                    _, run_id, _, _ = self._journal_decision(final, selection)
                    self._finish_run(run_id, final, final.reason)
                    continue
                quoted.append((final, selection))
            except StockPutCreditSelectionError as exc:
                status = (
                    "vetoed"
                    if exc.reason_code in EXPECTED_SELECTION_VETOES
                    else "missing_data"
                )
                factor = {
                    "factor_code": exc.reason_code,
                    "observed_value": str(exc),
                    "operator": "is_available",
                    "threshold": True,
                    "passed": False,
                }
                blocked = replace(
                    evaluation,
                    status=status,
                    reason=exc.reason_code,
                    factors=evaluation.factors + (factor,),
                )
                _, run_id, _, _ = self._journal_decision(blocked, None)
                self._finish_run(run_id, blocked, blocked.reason)
                self.log(
                    f"stock IVR70 selection rejected {evaluation.symbol}: "
                    f"{exc.reason_code}: {exc}"
                )
            except Exception as exc:
                blocked = replace(
                    evaluation,
                    status="missing_data",
                    reason=f"unexpected_option_selection_error:{type(exc).__name__}",
                )
                _, run_id, _, _ = self._journal_decision(blocked, None)
                self._finish_run(run_id, blocked, blocked.reason)
                self.log(f"stock IVR70 quote failed {evaluation.symbol}: {type(exc).__name__}: {exc}")
        quoted.sort(
            key=lambda item: (
                item[1].maximum_loss_dollars
                + self.strategy_config.estimated_round_trip_fees_dollars,
                -item[0].median_dollar_volume60,
                item[0].symbol,
            )
        )
        remaining = self.options_risk_budget.available_risk_usd
        for evaluation, selection in quoted:
            if self._unknown_symbol_exposure(state, evaluation.symbol):
                self._record_blocked(evaluation, selection, "unknown_symbol_option_exposure")
                continue
            defined_risk = (
                selection.maximum_loss_dollars
                + self.strategy_config.estimated_round_trip_fees_dollars
            )
            if defined_risk > remaining + 1e-9:
                self._record_blocked(evaluation, selection, "shared_options_risk_budget")
                continue
            position = self._new_position(evaluation, selection)
            state.positions.append(position)
            remaining -= defined_risk
            self._submit_entry(position, selection)

    def _mark_expiry_attention(self, state: MonitorState, now_et: pd.Timestamp) -> None:
        for position in state.positions:
            if (
                position.status == "open"
                and now_et.date() >= pd.Timestamp(position.expiry).date()
                and now_et.strftime("%H:%M") >= "16:00"
            ):
                position.status = "assignment_attention"
                position.last_error = "stock option reached expiry; broker assignment reconciliation required"

    def tick(self, now_et: pd.Timestamp | None = None) -> MonitorState:
        now_et = now_et or pd.Timestamp.now(tz="America/New_York")
        if now_et.tzinfo is None:
            now_et = now_et.tz_localize("America/New_York")
        state = load_state(self.monitor_config.state_path)
        self._service_orders(state)
        self._service_exits(state, now_et)
        self._evaluate_entries(state, now_et)
        self._mark_expiry_attention(state, now_et)
        atomic_write_state(self.monitor_config.state_path, state)
        return state

    def safe_tick(self, now_et: pd.Timestamp | None = None) -> MonitorState:
        try:
            return self.tick(now_et)
        except Exception as exc:
            state = load_state(self.monitor_config.state_path)
            state.last_error = f"{type(exc).__name__}: {exc}"
            atomic_write_state(self.monitor_config.state_path, state)
            self.log(f"stock IVR70 monitor error (other strategies continue): {type(exc).__name__}: {exc}")
            return state
