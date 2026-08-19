from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Callable

import pandas as pd
from ib_insync import IB, Stock, util

from src.execution.spy_poc_fly import (
    SpyPocFlySelection,
    build_entry_order,
    build_exit_order,
    refresh_fly_quote,
    round_down_to_tick,
    select_spy_poc_put_fly,
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
from src.strategies.failed_auction_journal import (
    config_hash,
    decision_payload,
    register_manifest,
)
from src.strategies.failed_auction_poc_fly import (
    STRATEGY_ID,
    STRATEGY_VARIANT_ID,
    STRATEGY_VERSION,
    FailedAuctionConfig,
    FailedAuctionEvaluation,
    evaluate_day,
)


RAW_SPY_PATH = Path(r"D:\Trading\Rohdaten\Rohdaten_SPY.csv")
DEFAULT_STATE_PATH = Path(r"D:\Trading\state\spy_failed_auction_poc_fly.json")
DONE_STATUSES = frozenset({"Filled", "Cancelled", "ApiCancelled", "Inactive"})
TERMINAL_STATE_STATUSES = frozenset(
    {"closed", "dry_run", "entry_not_filled", "exit_failed", "expired"}
)


@dataclass(frozen=True, slots=True)
class FlyMonitorConfig:
    day: pd.Timestamp
    account: str
    client_id: int
    place_orders: bool
    journal_path: Path
    state_path: Path = DEFAULT_STATE_PATH
    raw_spy_path: Path = RAW_SPY_PATH
    poll_seconds: float = 15.0
    entry_lateness_minutes: int = 5
    entry_work_minutes: int = 5
    exit_work_minutes: int = 30


@dataclass(slots=True)
class FlyMonitorState:
    schema_version: int
    status: str
    signal_day: str
    run_id: str
    signal_key: str
    decision_id: str
    trade_id: str
    poc: float
    body_strike: float
    upper_strike: float
    lower_strike: float
    expiry: str
    upper_con_id: int
    body_con_id: int
    lower_con_id: int
    entry_order_ref: str
    entry_order_id: int | None = None
    entry_submitted_at_utc: str | None = None
    entry_fill_price: float | None = None
    entry_filled_at_utc: str | None = None
    exit_order_ref: str | None = None
    exit_order_id: int | None = None
    exit_submitted_at_utc: str | None = None
    exit_fallback_submitted: bool = False
    exit_fill_price: float | None = None
    closed_at_utc: str | None = None
    last_error: str | None = None


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def require_paper_account(account: str) -> None:
    if not str(account).upper().startswith("DU"):
        raise RuntimeError(
            "the SPY failed-auction fly is research-only and may only use a DU paper account"
        )


def atomic_write_state(path: Path, state: FlyMonitorState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(asdict(state), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    temporary.replace(path)


def load_state(path: Path) -> FlyMonitorState | None:
    if not path.exists():
        return None
    return FlyMonitorState(**json.loads(path.read_text(encoding="utf-8")))


def completed_bars(bars: pd.DataFrame, now_et: pd.Timestamp) -> pd.DataFrame:
    if bars.empty:
        return bars.copy()
    stamps = pd.to_datetime(bars["date"], errors="coerce")
    if getattr(stamps.dt, "tz", None) is not None:
        stamps = stamps.dt.tz_convert("America/New_York").dt.tz_localize(None)
    cutoff = now_et.tz_convert("America/New_York").tz_localize(None)
    return bars.loc[stamps.add(pd.Timedelta(minutes=5)).le(cutoff)].copy()


def first_confirmed_poc_touch(
    bars: pd.DataFrame,
    *,
    poc: float,
    after: pd.Timestamp,
    now_et: pd.Timestamp,
) -> pd.Timestamp | None:
    done = completed_bars(bars, now_et)
    if done.empty:
        return None
    stamps = pd.to_datetime(done["date"], errors="coerce")
    after_naive = pd.Timestamp(after)
    if after_naive.tzinfo is not None:
        after_naive = after_naive.tz_convert("America/New_York").tz_localize(None)
    eligible = done.loc[stamps.ge(after_naive) & pd.to_numeric(done["low"]).le(poc)]
    if eligible.empty:
        return None
    return pd.Timestamp(eligible.iloc[0]["date"])


def exit_fallback_due(submitted_at_utc: str, now_utc: pd.Timestamp, minutes: int = 30) -> bool:
    submitted = pd.Timestamp(submitted_at_utc)
    if submitted.tzinfo is None:
        submitted = submitted.tz_localize("UTC")
    return now_utc.tz_convert("UTC") >= submitted.tz_convert("UTC") + pd.Timedelta(minutes=minutes)


def _normalize_ib_bars(rows: Any) -> pd.DataFrame:
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


def _order_fill_price(trade: Any) -> float | None:
    status = getattr(trade, "orderStatus", None)
    value = float(getattr(status, "avgFillPrice", 0.0) or 0.0)
    return value if value > 0 else None


def _order_fill_time(trade: Any) -> str:
    fills = tuple(getattr(trade, "fills", ()) or ())
    if fills:
        value = getattr(getattr(fills[-1], "execution", None), "time", None)
        if value is not None:
            stamp = pd.Timestamp(value)
            if stamp.tzinfo is None:
                stamp = stamp.tz_localize("UTC")
            return stamp.tz_convert("UTC").isoformat().replace("+00:00", "Z")
    return now_utc_iso()


class FailedAuctionFlyMonitor:
    def __init__(
        self,
        ib: IB,
        config: FlyMonitorConfig,
        *,
        strategy_config: FailedAuctionConfig = FailedAuctionConfig(),
        logger: Callable[[str], None] = print,
    ) -> None:
        require_paper_account(config.account)
        self.ib = ib
        self.config = config
        self.strategy_config = strategy_config
        self.log = logger
        self.store = JournalStore(config.journal_path)
        register_manifest(self.store, strategy_config)
        self.decisions = DecisionJournalIngestor(self.store)
        self.executions = ExecutionJournalIngestor(self.store)
        self.spy = self.ib.qualifyContracts(Stock("SPY", "SMART", "USD"))[0]

    def close(self) -> None:
        self.store.close()

    def _fetch_bars(self) -> pd.DataFrame:
        historical = pd.read_csv(self.config.raw_spy_path)
        live = _normalize_ib_bars(
            self.ib.reqHistoricalData(
                self.spy,
                endDateTime="",
                durationStr="2 D",
                barSizeSetting="5 mins",
                whatToShow="TRADES",
                useRTH=True,
                formatDate=1,
                keepUpToDate=False,
            )
        )
        combined = pd.concat([historical, live], ignore_index=True)
        combined["date"] = pd.to_datetime(combined["date"], errors="coerce")
        return (
            combined.dropna(subset=["date"])
            .drop_duplicates("date", keep="last")
            .sort_values("date")
            .reset_index(drop=True)
        )

    def _snapshot_price(self) -> float:
        ticker = self.ib.reqMktData(self.spy, "", True, False)
        self.ib.sleep(1.0)
        candidates = (ticker.marketPrice(), ticker.last, ticker.close)
        self.ib.cancelMktData(self.spy)
        for value in candidates:
            try:
                price = float(value)
            except (TypeError, ValueError):
                continue
            if price > 0 and pd.notna(price):
                return price
        raise RuntimeError("no finite SPY snapshot price available")

    def _all_broker_trades(self) -> tuple[Any, ...]:
        trades = list(self.ib.trades())
        try:
            trades.extend(self.ib.reqCompletedOrders(False))
        except Exception as exc:
            self.log(f"failed-auction fly: completed-order lookup unavailable: {exc}")
        unique: dict[tuple[int, str], Any] = {}
        for trade in trades:
            order = getattr(trade, "order", None)
            key = (
                int(getattr(order, "orderId", 0) or 0),
                str(getattr(order, "orderRef", "") or ""),
            )
            unique[key] = trade
        return tuple(unique.values())

    def _find_trade(self, order_ref: str | None, order_id: int | None) -> Any | None:
        for trade in self._all_broker_trades():
            order = getattr(trade, "order", None)
            if order_ref and str(getattr(order, "orderRef", "") or "") == order_ref:
                return trade
            if order_id and int(getattr(order, "orderId", 0) or 0) == order_id:
                return trade
        return None

    def _strategy_exposure_exists(self, selection: SpyPocFlySelection) -> bool:
        con_ids = {
            int(selection.upper_contract.conId),
            int(selection.body_contract.conId),
            int(selection.lower_contract.conId),
        }
        for position in self.ib.positions():
            if str(getattr(position, "account", "") or "") != self.config.account:
                continue
            contract = getattr(position, "contract", None)
            if (
                int(getattr(contract, "conId", 0) or 0) in con_ids
                and abs(float(getattr(position, "position", 0.0) or 0.0)) > 0
            ):
                return True
        for trade in self._all_broker_trades():
            order = getattr(trade, "order", None)
            order_ref = str(getattr(order, "orderRef", "") or "")
            if order_ref.startswith("FAFLY-") and _order_status(trade) not in DONE_STATUSES:
                return True
        return False

    def _selection(self, state: FlyMonitorState) -> SpyPocFlySelection:
        selection = select_spy_poc_put_fly(
            self.ib,
            decision_day=pd.Timestamp(state.signal_day),
            body_strike=state.body_strike,
            upper_width=state.upper_strike - state.body_strike,
            lower_width=state.body_strike - state.lower_strike,
        )
        observed = (
            int(selection.upper_contract.conId),
            int(selection.body_contract.conId),
            int(selection.lower_contract.conId),
        )
        expected = (state.upper_con_id, state.body_con_id, state.lower_con_id)
        if observed != expected:
            raise RuntimeError(
                f"option identity changed across restart: expected={expected} observed={observed}"
            )
        return selection

    def _execution_context(self, state: FlyMonitorState) -> ExecutionContext:
        return ExecutionContext(
            run_id=state.run_id,
            signal_key=state.signal_key,
            trade_id=state.trade_id,
            decision_id=state.decision_id,
            account=self.config.account,
            client_id=self.config.client_id,
            observed_at_utc=now_utc_iso(),
        )

    def _journal_new_trade(
        self,
        evaluation: FailedAuctionEvaluation,
        selection: SpyPocFlySelection,
    ) -> FlyMonitorState:
        payload = decision_payload(evaluation, self.strategy_config)
        signal_key = str(payload["signal_key"])
        run_id = deterministic_id(
            "run", STRATEGY_VARIANT_ID, evaluation.day, "paper", self.config.account
        )
        started = (
            pd.Timestamp(evaluation.day)
            .tz_localize("America/New_York")
            .tz_convert("UTC")
            .isoformat()
        )
        self.store.start_run(
            StrategyRunRecord(
                run_id=run_id,
                strategy_id=STRATEGY_ID,
                strategy_version=STRATEGY_VERSION,
                config_hash=config_hash(self.strategy_config),
                run_type="paper",
                environment="paper",
                started_at_utc=started,
                ended_at_utc=None,
                status="running",
                parameters=asdict(self.strategy_config),
                metadata={"account": self.config.account, "paper_only": True},
                variant_id=STRATEGY_VARIANT_ID,
            )
        )
        context = DecisionIngestContext(run_id=run_id, run_mode="paper")
        decision = self.decisions.ingest(payload, context)
        contracts = (
            ("upper_long", selection.upper_contract, selection.upper_strike, "BUY", 1),
            ("body_short", selection.body_contract, selection.body_strike, "SELL", 2),
            ("lower_long", selection.lower_contract, selection.lower_strike, "BUY", 1),
        )
        legs = tuple(
            TradeIntentLeg(
                asset_class="OPT",
                symbol="SPY",
                con_id=int(contract.conId),
                local_symbol=str(getattr(contract, "localSymbol", "") or "") or None,
                exchange=str(getattr(contract, "exchange", "") or "SMART"),
                currency="USD",
                side=side,
                ratio=ratio,
                quantity=float(ratio),
                expiry=selection.expiry,
                strike=strike,
                option_right="P",
                multiplier=float(selection.multiplier),
                open_price=None,
                metadata={"role": role},
            )
            for role, contract, strike, side, ratio in contracts
        )
        trade_id = self.decisions.create_trade_intent(
            payload,
            context,
            TradeIntent(
                asset_class="BAG",
                symbol="SPY",
                side="long",
                status="planned",
                opened_at_utc=None,
                quantity=1.0,
                currency="USD",
                entry_price=selection.entry_marketable_limit,
                stop_price=None,
                target_price=None,
                exchange="SMART",
                legs=legs,
                metadata={
                    "poc": evaluation.poc,
                    "paper_only": True,
                    "exit_policy": "confirmed_poc_touch_mid_then_marketable_after_30m",
                },
            ),
        )
        order_ref = f"FAFLY-E-{evaluation.day.replace('-', '')}"[:45]
        state = FlyMonitorState(
            schema_version=1,
            status="planned",
            signal_day=evaluation.day,
            run_id=run_id,
            signal_key=signal_key,
            decision_id=decision.decision_id,
            trade_id=trade_id,
            poc=float(evaluation.poc),
            body_strike=selection.body_strike,
            upper_strike=selection.upper_strike,
            lower_strike=selection.lower_strike,
            expiry=selection.expiry,
            upper_con_id=int(selection.upper_contract.conId),
            body_con_id=int(selection.body_contract.conId),
            lower_con_id=int(selection.lower_contract.conId),
            entry_order_ref=order_ref,
        )
        atomic_write_state(self.config.state_path, state)
        return state

    def _submit_entry(
        self, state: FlyMonitorState, selection: SpyPocFlySelection
    ) -> FlyMonitorState:
        if self._find_trade(state.entry_order_ref, state.entry_order_id) is not None:
            raise RuntimeError("duplicate entry order detected before submission")
        if not self.config.place_orders:
            self.executions.record_blocker(
                self._execution_context(state),
                status="execution_blocked",
                reason_code="dry_run_no_order",
                reason="signal and option structure accepted, but --place-orders is absent",
                requested_quantity=1,
                occurred_at=now_utc_iso(),
                details={
                    "entry_limit": selection.entry_marketable_limit,
                    "risk_amount": selection.maximum_loss_dollars,
                    "risk_currency": "USD",
                },
            )
            state.status = "dry_run"
            atomic_write_state(self.config.state_path, state)
            return state
        order = build_entry_order(
            selection,
            account=self.config.account,
            order_ref=state.entry_order_ref,
        )
        trade = self.ib.placeOrder(selection.combo_contract, order)
        self.ib.sleep(1.0)
        state.status = "entry_submitted"
        state.entry_order_id = int(getattr(trade.order, "orderId", 0) or 0)
        state.entry_submitted_at_utc = now_utc_iso()
        atomic_write_state(self.config.state_path, state)
        self.executions.ingest_trade(
            trade, self._execution_context(state), role="entry"
        )
        self.log(
            f"failed-auction fly entry submitted orderId={state.entry_order_id} "
            f"limit={order.lmtPrice:.2f} expiry={state.expiry} "
            f"strikes={state.upper_strike:g}/{state.body_strike:g}/{state.lower_strike:g}"
        )
        return state

    def _service_entry(self, state: FlyMonitorState) -> FlyMonitorState:
        trade = self._find_trade(state.entry_order_ref, state.entry_order_id)
        if trade is None:
            self.log("failed-auction fly: entry order not yet visible at broker")
            return state
        self.executions.ingest_trade(
            trade, self._execution_context(state), role="entry"
        )
        status = _order_status(trade)
        if status == "Filled":
            state.status = "open"
            state.entry_fill_price = _order_fill_price(trade)
            state.entry_filled_at_utc = _order_fill_time(trade)
            atomic_write_state(self.config.state_path, state)
            self.log(
                f"failed-auction fly entry filled at {state.entry_fill_price}; "
                f"watching POC={state.poc:g}"
            )
        elif status in DONE_STATUSES:
            state.status = "entry_not_filled"
            state.last_error = f"entry terminal status {status}"
            atomic_write_state(self.config.state_path, state)
        elif (
            state.entry_submitted_at_utc
            and exit_fallback_due(
                state.entry_submitted_at_utc,
                pd.Timestamp.now(tz="UTC"),
                self.config.entry_work_minutes,
            )
        ):
            self.ib.cancelOrder(trade.order)
            self.ib.sleep(1.0)
            self.executions.ingest_trade(
                trade, self._execution_context(state), role="entry"
            )
            state.status = "entry_not_filled"
            state.last_error = "entry was not filled inside the five-minute window"
            atomic_write_state(self.config.state_path, state)
        return state

    def _submit_exit(
        self,
        state: FlyMonitorState,
        selection: SpyPocFlySelection,
        *,
        marketable: bool,
    ) -> FlyMonitorState:
        quote = refresh_fly_quote(self.ib, selection)
        raw_limit = quote.exit_conservative if marketable else quote.mid
        limit_price = max(round_down_to_tick(raw_limit, selection.min_tick), 0.01)
        suffix = "M" if marketable else "L"
        order_ref = f"FAFLY-X-{state.signal_day.replace('-', '')}-{suffix}"[:45]
        order = build_exit_order(
            selection,
            account=self.config.account,
            order_ref=order_ref,
            limit_price=limit_price,
        )
        trade = self.ib.placeOrder(selection.combo_contract, order)
        self.ib.sleep(1.0)
        state.status = "exit_submitted"
        state.exit_order_ref = order_ref
        state.exit_order_id = int(getattr(trade.order, "orderId", 0) or 0)
        state.exit_submitted_at_utc = now_utc_iso()
        state.exit_fallback_submitted = marketable
        atomic_write_state(self.config.state_path, state)
        self.executions.ingest_trade(
            trade, self._execution_context(state), role="exit"
        )
        self.log(
            f"failed-auction fly exit submitted orderId={state.exit_order_id} "
            f"limit={order.lmtPrice:.2f} marketable={marketable}"
        )
        return state

    def _service_exit(
        self, state: FlyMonitorState, now_utc: pd.Timestamp
    ) -> FlyMonitorState:
        trade = self._find_trade(state.exit_order_ref, state.exit_order_id)
        if trade is None:
            self.log("failed-auction fly: exit order not yet visible at broker")
            return state
        self.executions.ingest_trade(
            trade, self._execution_context(state), role="exit"
        )
        status = _order_status(trade)
        if status == "Filled":
            state.status = "closed"
            state.exit_fill_price = _order_fill_price(trade)
            state.closed_at_utc = now_utc_iso()
            atomic_write_state(self.config.state_path, state)
            self.log(f"failed-auction fly exit filled at {state.exit_fill_price}")
            return state
        if (
            not state.exit_fallback_submitted
            and state.exit_submitted_at_utc
            and exit_fallback_due(
                state.exit_submitted_at_utc,
                now_utc,
                self.config.exit_work_minutes,
            )
        ):
            selection = self._selection(state)
            quote = refresh_fly_quote(self.ib, selection)
            marketable = max(
                round_down_to_tick(quote.exit_conservative, selection.min_tick),
                0.01,
            )
            trade.order.lmtPrice = marketable
            amended = self.ib.placeOrder(selection.combo_contract, trade.order)
            self.ib.sleep(1.0)
            state.exit_fallback_submitted = True
            atomic_write_state(self.config.state_path, state)
            self.executions.ingest_trade(
                amended, self._execution_context(state), role="exit"
            )
            self.log(
                f"failed-auction fly exit amended to marketable limit={marketable:.2f} "
                f"orderId={state.exit_order_id}"
            )
            return state
        if status in DONE_STATUSES:
            state.status = "exit_failed"
            state.last_error = f"exit terminal status {status} without a fill"
            atomic_write_state(self.config.state_path, state)
        return state

    def safe_tick(
        self, now_et: pd.Timestamp | None = None
    ) -> FlyMonitorState | None:
        try:
            return self.tick(now_et)
        except Exception as exc:
            state = load_state(self.config.state_path)
            if state is not None:
                state.last_error = f"{type(exc).__name__}: {exc}"
                atomic_write_state(self.config.state_path, state)
            self.log(
                f"failed-auction fly monitor error (stock bot continues): "
                f"{type(exc).__name__}: {exc}"
            )
            return state

    def tick(self, now_et: pd.Timestamp | None = None) -> FlyMonitorState | None:
        now_et = now_et or pd.Timestamp.now(tz="America/New_York")
        if now_et.tzinfo is None:
            now_et = now_et.tz_localize("America/New_York")
        now_utc = now_et.tz_convert("UTC")
        state = load_state(self.config.state_path)
        if (
            state is not None
            and state.status in TERMINAL_STATE_STATUSES
            and state.signal_day != self.config.day.strftime("%Y-%m-%d")
        ):
            archive = self.config.state_path.with_name(
                f"{self.config.state_path.stem}.{state.signal_day}.{state.status}.json"
            )
            self.config.state_path.replace(archive)
            state = None
        if state is not None and state.status == "entry_submitted":
            return self._service_entry(state)
        if state is not None and state.status == "exit_submitted":
            return self._service_exit(state, now_utc)
        bars = self._fetch_bars()
        if state is not None and state.status == "open":
            entered = pd.Timestamp(
                state.entry_filled_at_utc or state.entry_submitted_at_utc
            ).tz_convert(
                "America/New_York"
            )
            touch = first_confirmed_poc_touch(
                bars, poc=state.poc, after=entered, now_et=now_et
            )
            if touch is not None:
                return self._submit_exit(
                    state, self._selection(state), marketable=False
                )
            expiry_day = pd.Timestamp(state.expiry).date()
            if now_et.date() >= expiry_day and now_et.strftime("%H:%M") >= "16:00":
                state.status = "expired"
                state.closed_at_utc = now_utc_iso()
                atomic_write_state(self.config.state_path, state)
                self.log(
                    "failed-auction fly reached expiration without a confirmed POC touch; "
                    "paper account settlement will determine the result"
                )
            return state
        if state is not None:
            return state

        completed = completed_bars(bars, now_et)
        evaluation = evaluate_day(
            completed,
            self.config.day,
            self.strategy_config,
            allow_pending_entry=True,
        )
        if evaluation.status != "pending_entry":
            return None
        entry_time = evaluation.entry_time
        if entry_time is None:
            return None
        deadline = entry_time + pd.Timedelta(
            minutes=self.config.entry_lateness_minutes
        )
        now_naive = now_et.tz_localize(None)
        if now_naive < entry_time or now_naive > deadline:
            return None
        price = self._snapshot_price()
        if price <= float(evaluation.poc):
            evaluation = replace(
                evaluation,
                status="blocked",
                reason="entry_at_or_below_poc",
                entry_price=price,
            )
            return None
        evaluation = replace(
            evaluation,
            status="accepted",
            reason="failed_auction_hard12_rvol_accepted",
            entry_price=price,
            factors=evaluation.factors
            + (
                {
                    "factor_code": "next_bar_live_price_above_poc",
                    "observed_value": price,
                    "operator": ">",
                    "threshold": evaluation.poc,
                    "passed": True,
                },
            ),
        )
        selection = select_spy_poc_put_fly(
            self.ib,
            decision_day=self.config.day,
            body_strike=float(evaluation.body_strike),
            upper_width=self.strategy_config.upper_width_points,
            lower_width=self.strategy_config.lower_width_points,
        )
        if self._strategy_exposure_exists(selection):
            raise RuntimeError(
                "failed-auction fly exposure/order already exists but no active state "
                "was found; refusing a duplicate entry"
            )
        state = self._journal_new_trade(evaluation, selection)
        return self._submit_entry(state, selection)

    def run_until_close(self) -> None:
        while True:
            now_et = pd.Timestamp.now(tz="America/New_York")
            self.safe_tick(now_et)
            state = load_state(self.config.state_path)
            if state is not None and state.status in TERMINAL_STATE_STATUSES:
                return
            if now_et.strftime("%H:%M") >= "16:00":
                return
            self.ib.sleep(max(self.config.poll_seconds, 1.0))
