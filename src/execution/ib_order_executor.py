from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import time
from typing import Any

import pandas as pd
from ib_insync import IB, LimitOrder, MarketOrder, OrderStatus, StopOrder

from src.data.ib_market_data import build_contract


DONE_STATES = set(getattr(OrderStatus, "DoneStates", {"Filled", "Cancelled", "ApiCancelled"}))


@dataclass(frozen=True)
class OpenTradeExposure:
    total_open: int
    long_open: int
    short_open: int
    symbols: tuple[str, ...]
    long_symbols: tuple[str, ...]
    short_symbols: tuple[str, ...]


@dataclass(frozen=True)
class OrderExecutionConfig:
    entry_order_type: str = "MKT"
    bracket_style: str = "manual_oca"
    allow_index_orders: bool = False
    order_ref_prefix: str = "IBKRBOT"
    tif: str = "DAY"
    outside_rth: bool = False
    bracket_ack_timeout_seconds: float = 5.0


class IBOrderExecutor:
    def __init__(
        self,
        ib: IB,
        *,
        entry_order_type: str = "MKT",
        bracket_style: str = "manual_oca",
        allow_index_orders: bool = False,
        order_ref_prefix: str = "IBKRBOT",
        tif: str = "DAY",
        outside_rth: bool = False,
        bracket_ack_timeout_seconds: float = 5.0,
        account: str | None = None,
    ) -> None:
        self.ib = ib
        self.config = OrderExecutionConfig(
            entry_order_type=entry_order_type.upper(),
            bracket_style=bracket_style,
            allow_index_orders=allow_index_orders,
            order_ref_prefix=order_ref_prefix,
            tif=tif,
            outside_rth=outside_rth,
            bracket_ack_timeout_seconds=bracket_ack_timeout_seconds,
        )
        self.account = account

    @staticmethod
    def _contract_symbol(contract: Any) -> str:
        return str(getattr(contract, "localSymbol", None) or getattr(contract, "symbol", "")).upper()

    @staticmethod
    def _trade_is_active(trade: Any) -> bool:
        status = str(getattr(getattr(trade, "orderStatus", None), "status", "") or "")
        return status not in DONE_STATES

    @staticmethod
    def _normalize_action(action: Any) -> str:
        return str(action or "").upper()

    @staticmethod
    def _round_to_tick(price: float, tick_size: float = 0.01) -> float:
        if not pd.notna(price):
            return float(price)
        tick = float(tick_size) if tick_size and tick_size > 0 else 0.01
        steps = round(float(price) / tick)
        return round(steps * tick, 2)

    @staticmethod
    def _format_good_till_date(value: Any, *, min_timestamp: pd.Timestamp | None = None) -> str | None:
        if value is None or pd.isna(value):
            return None
        timestamp = pd.Timestamp(value)
        if pd.isna(timestamp):
            return None
        if timestamp.tzinfo is not None:
            timestamp = timestamp.tz_convert("America/New_York").tz_localize(None)
        if min_timestamp is not None:
            floor = pd.Timestamp(min_timestamp)
            if floor.tzinfo is not None:
                floor = floor.tz_convert("America/New_York").tz_localize(None)
            if timestamp <= floor:
                return None
        return timestamp.strftime("%Y%m%d %H:%M:%S US/Eastern")

    @staticmethod
    def _apply_exit_leg_tif(order: Any, good_till_date: str | None) -> None:
        if not good_till_date:
            return
        order.tif = "GTD"
        order.goodTillDate = good_till_date

    @staticmethod
    def _trade_order_id(trade: Any) -> int:
        return int(getattr(getattr(trade, "order", None), "orderId", 0) or 0)

    @staticmethod
    def _trade_account(trade: Any) -> str:
        return str(getattr(getattr(trade, "order", None), "account", "") or "")

    def current_open_exposure(self, *, refresh: bool = True) -> OpenTradeExposure:
        if refresh and self.ib.isConnected():
            self.ib.reqAllOpenOrders()
            self.ib.sleep(0.2)

        position_qty_by_symbol: dict[str, float] = defaultdict(float)
        for position in self.ib.positions():
            if self.account:
                position_account = str(getattr(position, "account", "") or "")
                if position_account and position_account != self.account:
                    continue
            qty = float(getattr(position, "position", 0) or 0.0)
            if qty == 0.0:
                continue
            symbol = self._contract_symbol(position.contract)
            if symbol:
                position_qty_by_symbol[symbol] += qty

        open_trades_by_symbol: dict[str, list[Any]] = defaultdict(list)
        for trade in self.ib.openTrades():
            if not self._trade_is_active(trade):
                continue
            if self.account:
                trade_account = self._trade_account(trade)
                if trade_account and trade_account != self.account:
                    continue
            symbol = self._contract_symbol(getattr(trade, "contract", None))
            if symbol:
                open_trades_by_symbol[symbol].append(trade)

        long_symbols: list[str] = []
        short_symbols: list[str] = []
        for symbol in sorted(set(position_qty_by_symbol) | set(open_trades_by_symbol)):
            direction = self._infer_open_direction(position_qty_by_symbol.get(symbol, 0.0), open_trades_by_symbol.get(symbol, []))
            if direction == "long":
                long_symbols.append(symbol)
            elif direction == "short":
                short_symbols.append(symbol)

        symbols = tuple(sorted(set(long_symbols) | set(short_symbols)))
        return OpenTradeExposure(
            total_open=len(symbols),
            long_open=len(long_symbols),
            short_open=len(short_symbols),
            symbols=symbols,
            long_symbols=tuple(long_symbols),
            short_symbols=tuple(short_symbols),
        )

    @staticmethod
    def _infer_open_direction(position_qty: float, trades: list[Any]) -> str | None:
        if position_qty > 0:
            return "long"
        if position_qty < 0:
            return "short"

        active_trades = [trade for trade in trades if IBOrderExecutor._trade_is_active(trade)]
        if not active_trades:
            return None

        action_counts = Counter(
            action
            for action in (
                IBOrderExecutor._normalize_action(getattr(getattr(trade, "order", None), "action", ""))
                for trade in active_trades
            )
            if action in {"BUY", "SELL"}
        )
        if action_counts["BUY"] and not action_counts["SELL"]:
            return "long"
        if action_counts["SELL"] and not action_counts["BUY"]:
            return "short"
        if action_counts["BUY"] != action_counts["SELL"]:
            return "long" if action_counts["BUY"] < action_counts["SELL"] else "short"

        first_trade = min(active_trades, key=IBOrderExecutor._trade_order_id)
        first_action = IBOrderExecutor._normalize_action(getattr(getattr(first_trade, "order", None), "action", ""))
        if first_action == "BUY":
            return "long"
        if first_action == "SELL":
            return "short"
        return None

    def current_open_trade_symbols(self) -> set[str]:
        return set(self.current_open_exposure().symbols)

    def submit_plan(
        self,
        plan: pd.DataFrame,
        *,
        transmit: bool = False,
        existing_symbols: set[str] | None = None,
    ) -> pd.DataFrame:
        if plan.empty:
            return pd.DataFrame(
                columns=[
                    "symbol",
                    "track",
                    "source_tracks",
                    "rule",
                    "side",
                    "quantity",
                    "entry",
                    "stop",
                    "target",
                    "contract_secType",
                    "contract_localSymbol",
                    "contract_conId",
                    "parent_order_id",
                    "take_profit_order_id",
                    "stop_order_id",
                    "status",
                    "message",
                ]
            )

        frame = plan.copy()
        if "allowed" not in frame.columns:
            frame["allowed"] = True
        if "blocked_reason" not in frame.columns:
            frame["blocked_reason"] = ""
        frame["symbol"] = frame["symbol"].astype(str).str.upper()

        working_existing_symbols = {symbol.upper() for symbol in (existing_symbols or set()) if symbol}
        records: list[dict[str, object]] = []

        frame = frame.sort_values(["entry_date", "symbol", "track", "rule"], kind="mergesort").reset_index(drop=True)
        for _, row in frame.iterrows():
            symbol = str(row["symbol"]).upper()
            quantity = int(row.get("quantity", 0) or 0)
            entry = self._round_to_tick(float(row.get("entry", float("nan"))))
            stop = self._round_to_tick(float(row.get("stop", float("nan"))))
            target = self._round_to_tick(float(row.get("target", float("nan"))))
            record: dict[str, object] = {
                "symbol": symbol,
                "track": str(row.get("track", "")),
                "source_tracks": str(row.get("source_tracks", row.get("track", ""))),
                "rule": str(row.get("rule", "")),
                "side": str(row.get("side", "")).lower(),
                "quantity": quantity,
                "entry": entry,
                "stop": stop,
                "target": target,
                "contract_secType": "",
                "contract_localSymbol": "",
                "contract_conId": None,
                "parent_order_id": None,
                "take_profit_order_id": None,
                "stop_order_id": None,
                "status": "skipped",
                "message": "",
            }

            if not bool(row.get("allowed", True)):
                record["message"] = str(row.get("blocked_reason", "not_allowed") or "not_allowed")
                records.append(record)
                continue

            if quantity <= 0:
                record["message"] = "non_positive_quantity"
                records.append(record)
                continue

            if symbol in working_existing_symbols:
                record["message"] = "existing_position_or_open_order"
                records.append(record)
                continue

            try:
                contract, _ = build_contract(symbol, "US")
                qualified = self.ib.qualifyContracts(contract)
                if not qualified:
                    raise RuntimeError("unqualified_contract")
                contract = qualified[0]
                record["contract_secType"] = str(getattr(contract, "secType", ""))
                record["contract_localSymbol"] = str(getattr(contract, "localSymbol", "") or getattr(contract, "symbol", ""))
                record["contract_conId"] = getattr(contract, "conId", None)

                if str(getattr(contract, "secType", "")).upper() == "IND" and not self.config.allow_index_orders:
                    record["status"] = "blocked"
                    record["message"] = "unsupported_contract_type_IND"
                    records.append(record)
                    continue

                parent, take_profit, stop_loss = self._build_bracket_orders(row)
                if transmit:
                    parent_trade = self.ib.placeOrder(contract, parent)
                    self.ib.sleep(0.1)
                    if self.config.entry_order_type == "MKT" and self.config.bracket_style == "manual_oca":
                        parent_ack = self._await_bracket_ack(
                            [parent_trade],
                            timeout=self.config.bracket_ack_timeout_seconds,
                        )
                        filled_qty = float(getattr(parent_trade.orderStatus, "filled", 0.0) or 0.0)
                        if filled_qty <= 0:
                            record["parent_order_id"] = getattr(parent_trade.order, "orderId", parent.orderId)
                            record["status"] = "error"
                            record["message"] = (
                                f"parent={getattr(parent_trade.orderStatus, 'status', '')} "
                                f"ack={parent_ack} no_parent_fill"
                            ).strip()
                            records.append(record)
                            continue

                        oca_group = self._build_oca_group(symbol, getattr(parent_trade.order, "permId", 0) or parent.orderId)
                        for exit_order in (take_profit, stop_loss):
                            exit_order.parentId = 0
                            exit_order.totalQuantity = filled_qty
                            exit_order.ocaGroup = oca_group
                            exit_order.ocaType = 1

                        take_profit_trade = self.ib.placeOrder(contract, take_profit)
                        self.ib.sleep(0.1)
                        stop_loss_trade = self.ib.placeOrder(contract, stop_loss)
                        self.ib.reqAllOpenOrders()
                        self.ib.sleep(0.5)
                        ack_snapshot = self._await_bracket_ack(
                            [parent_trade, take_profit_trade, stop_loss_trade],
                            timeout=self.config.bracket_ack_timeout_seconds,
                        )
                        record["parent_order_id"] = getattr(parent_trade.order, "orderId", parent.orderId)
                        record["take_profit_order_id"] = getattr(take_profit_trade.order, "orderId", take_profit.orderId)
                        record["stop_order_id"] = getattr(stop_loss_trade.order, "orderId", stop_loss.orderId)
                        record["status"] = "submitted"
                        record["message"] = (
                            f"parent={getattr(parent_trade.orderStatus, 'status', '')} "
                            f"tp={getattr(take_profit_trade.orderStatus, 'status', '')} "
                            f"sl={getattr(stop_loss_trade.orderStatus, 'status', '')}"
                        ).strip()
                        if ack_snapshot:
                            record["message"] = f"{record['message']} ack={ack_snapshot}".strip()
                        exit_rejections = self._trade_error_messages([take_profit_trade, stop_loss_trade])
                        if exit_rejections:
                            record["status"] = "error"
                            record["message"] = f"{record['message']} exit_leg_rejected={' | '.join(exit_rejections)}".strip()
                        working_existing_symbols.add(symbol)
                        records.append(record)
                        continue

                    take_profit_trade = self.ib.placeOrder(contract, take_profit)
                    self.ib.sleep(0.1)
                    stop_loss_trade = self.ib.placeOrder(contract, stop_loss)
                    self.ib.reqAllOpenOrders()
                    self.ib.sleep(0.5)
                    ack_snapshot = self._await_bracket_ack(
                        [parent_trade, take_profit_trade, stop_loss_trade],
                        timeout=self.config.bracket_ack_timeout_seconds,
                    )
                    record["parent_order_id"] = getattr(parent_trade.order, "orderId", parent.orderId)
                    record["take_profit_order_id"] = getattr(take_profit_trade.order, "orderId", take_profit.orderId)
                    record["stop_order_id"] = getattr(stop_loss_trade.order, "orderId", stop_loss.orderId)
                    record["status"] = "submitted"
                    record["message"] = (
                        f"parent={getattr(parent_trade.orderStatus, 'status', '')} "
                        f"tp={getattr(take_profit_trade.orderStatus, 'status', '')} "
                        f"sl={getattr(stop_loss_trade.orderStatus, 'status', '')}"
                    ).strip()
                    if ack_snapshot:
                        record["message"] = f"{record['message']} ack={ack_snapshot}".strip()
                    exit_rejections = self._trade_error_messages([take_profit_trade, stop_loss_trade])
                    if exit_rejections:
                        record["status"] = "error"
                        record["message"] = f"{record['message']} exit_leg_rejected={' | '.join(exit_rejections)}".strip()
                    working_existing_symbols.add(symbol)
                else:
                    record["status"] = "dry_run"
                    record["message"] = "not_transmitted"
                    record["parent_order_id"] = parent.orderId
                    record["take_profit_order_id"] = take_profit.orderId
                    record["stop_order_id"] = stop_loss.orderId
            except Exception as exc:
                record["status"] = "error"
                record["message"] = f"{type(exc).__name__}: {exc}"

            records.append(record)

        return pd.DataFrame.from_records(records)

    def _build_bracket_orders(self, row: pd.Series) -> tuple[Any, Any, Any]:
        side = str(row.get("side", "")).lower()
        quantity = int(row.get("quantity", 0) or 0)
        entry = self._round_to_tick(float(row.get("entry", float("nan"))))
        stop = self._round_to_tick(float(row.get("stop", float("nan"))))
        target = self._round_to_tick(float(row.get("target", float("nan"))))
        symbol = str(row.get("symbol", "")).upper()
        track = str(row.get("track", ""))
        rule = str(row.get("rule", ""))
        min_exit_gtd = pd.Timestamp.now(tz="America/New_York").tz_localize(None) + pd.Timedelta(minutes=1)
        good_till_date = self._format_good_till_date(row.get("exit_date", None), min_timestamp=min_exit_gtd)

        if side == "long":
            action = "BUY"
            reverse_action = "SELL"
            if not (stop < entry < target):
                raise ValueError("inconsistent long levels")
        elif side == "short":
            action = "SELL"
            reverse_action = "BUY"
            if not (target < entry < stop):
                raise ValueError("inconsistent short levels")
        else:
            raise ValueError(f"unsupported side '{side}'")

        order_ref = self._build_order_ref(symbol, track, rule)
        entry_order_type = self.config.entry_order_type
        if entry_order_type == "LMT" and self.config.bracket_style == "manual_oca":
            parent = LimitOrder(action, quantity, entry)
            take_profit = LimitOrder(reverse_action, quantity, target)
            stop_loss = StopOrder(reverse_action, quantity, stop)

            parent.orderId = self.ib.client.getReqId()
            take_profit.orderId = self.ib.client.getReqId()
            stop_loss.orderId = self.ib.client.getReqId()
            take_profit.parentId = parent.orderId
            stop_loss.parentId = parent.orderId

            for order in (parent, take_profit, stop_loss):
                order.tif = self.config.tif
                order.transmit = True
                order.outsideRth = self.config.outside_rth
                if self.account:
                    order.account = self.account
                order.orderRef = order_ref
            self._apply_exit_leg_tif(take_profit, good_till_date)
            self._apply_exit_leg_tif(stop_loss, good_till_date)

            return parent, take_profit, stop_loss
        elif entry_order_type == "LMT":
            bracket = self.ib.bracketOrder(
                action,
                quantity,
                entry,
                target,
                stop,
                tif=self.config.tif,
                outsideRth=self.config.outside_rth,
            )
            parent = bracket.parent
            take_profit = bracket.takeProfit
            stop_loss = bracket.stopLoss
            self._apply_exit_leg_tif(take_profit, good_till_date)
            self._apply_exit_leg_tif(stop_loss, good_till_date)
        elif entry_order_type == "MKT":
            parent = MarketOrder(action, quantity)
            take_profit = LimitOrder(reverse_action, quantity, target)
            stop_loss = StopOrder(reverse_action, quantity, stop)

            parent.orderId = self.ib.client.getReqId()
            take_profit.orderId = self.ib.client.getReqId()
            stop_loss.orderId = self.ib.client.getReqId()
            take_profit.parentId = parent.orderId
            stop_loss.parentId = parent.orderId

            for order in (parent, take_profit, stop_loss):
                order.tif = self.config.tif
                order.transmit = True
                order.outsideRth = self.config.outside_rth
                if self.account:
                    order.account = self.account
                order.orderRef = order_ref
            self._apply_exit_leg_tif(take_profit, good_till_date)
            self._apply_exit_leg_tif(stop_loss, good_till_date)

            return parent, take_profit, stop_loss
        else:
            raise ValueError(f"unsupported entry_order_type '{entry_order_type}'")

        for order in (parent, take_profit, stop_loss):
            if self.account:
                order.account = self.account
            order.orderRef = order_ref

        return parent, take_profit, stop_loss

    def _await_bracket_ack(self, trades: list[Any], *, timeout: float) -> str:
        if timeout <= 0:
            return ""

        deadline = time.time() + timeout
        min_deadline = min(deadline, time.time() + 1.0)
        last_snapshot = ""
        while True:
            snapshot_parts: list[str] = []
            all_acknowledged = True
            for trade in trades:
                order_id = int(getattr(getattr(trade, "order", None), "orderId", 0) or 0)
                status = str(getattr(getattr(trade, "orderStatus", None), "status", "") or "")
                snapshot_parts.append(f"{order_id}:{status}")
                if status in {"", "PendingSubmit"}:
                    all_acknowledged = False
            last_snapshot = "|".join(snapshot_parts)
            if all_acknowledged and time.time() >= min_deadline:
                return last_snapshot
            if time.time() >= deadline:
                return last_snapshot
            self.ib.waitOnUpdate(timeout=0.5)

    @staticmethod
    def _trade_error_messages(trades: list[Any]) -> list[str]:
        messages: list[str] = []
        for trade in trades:
            order = getattr(trade, "order", None)
            order_id = int(getattr(order, "orderId", 0) or 0)
            status = str(getattr(getattr(trade, "orderStatus", None), "status", "") or "")
            for entry in getattr(trade, "log", []) or []:
                error_code = int(getattr(entry, "errorCode", 0) or 0)
                message = str(getattr(entry, "message", "") or "")
                if error_code:
                    messages.append(f"{order_id}:{status}:{error_code}:{message}")
            advanced_error = str(getattr(trade, "advancedError", "") or "")
            if advanced_error:
                messages.append(f"{order_id}:{status}:advanced:{advanced_error}")
        return messages

    def _build_order_ref(self, symbol: str, track: str, rule: str) -> str:
        parts = [self.config.order_ref_prefix, symbol.upper()]
        if track:
            parts.append(track.upper())
        if rule:
            parts.append(rule[:12].upper())
        return ":".join(parts)[:45]

    def _build_oca_group(self, symbol: str, parent_id: object) -> str:
        return f"{self.config.order_ref_prefix}_{symbol.upper()}_{parent_id}"[:32]
