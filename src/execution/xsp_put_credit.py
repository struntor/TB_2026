from __future__ import annotations

from dataclasses import dataclass
import math
import time
from typing import Any

import pandas as pd
from ib_insync import ComboLeg, Contract, IB, Index, LimitOrder, Option

from src.execution.spxw_put_credit import (
    OptionRiskQuote,
    _finite,
    _quote,
    _quotes_many,
    round_credit_down,
    round_debit_up,
)


@dataclass(frozen=True, slots=True)
class XspPutCreditSelection:
    expiry: str
    spot: float
    short_strike: float
    long_strike: float
    short_contract: Contract
    long_contract: Contract
    short_quote: OptionRiskQuote
    long_quote: OptionRiskQuote
    combo_contract: Contract
    inverse_combo_contract: Contract
    min_tick: float
    multiplier: int
    opening_fees_dollars: float = 3.30

    @property
    def conservative_credit(self) -> float:
        return self.short_quote.bid - self.long_quote.ask

    @property
    def entry_limit_credit(self) -> float:
        return round_credit_down(self.conservative_credit, self.min_tick)

    @property
    def conservative_exit_debit(self) -> float:
        return self.short_quote.ask - self.long_quote.bid

    @property
    def maximum_loss_dollars(self) -> float:
        return (
            (self.short_strike - self.long_strike - self.entry_limit_credit) * self.multiplier
            + self.opening_fees_dollars
        )

    @property
    def relative_leg_spreads(self) -> tuple[float, float]:
        return tuple(
            (quote.ask - quote.bid) / ((quote.ask + quote.bid) / 2.0)
            for quote in (self.short_quote, self.long_quote)
        )


def quote_quality_passes(
    selection: XspPutCreditSelection,
    maximum_relative_leg_spread: float,
) -> bool:
    """Return whether both option legs have finite, non-negative spreads within the limit."""
    return all(
        math.isfinite(value) and 0.0 <= value <= maximum_relative_leg_spread
        for value in selection.relative_leg_spreads
    )


def require_paper_account(account: str) -> None:
    if not str(account).upper().startswith("DU"):
        raise RuntimeError("the XSP VIX-pullback strategy may only use a DU paper account")


def _chains(ib: IB, underlying: Contract) -> list[Any]:
    chains = ib.reqSecDefOptParams(underlying.symbol, "", underlying.secType, underlying.conId)
    preferred = [c for c in chains if str(getattr(c, "tradingClass", "")).upper() == "XSP"]
    if not preferred:
        raise RuntimeError("IB returned no standard XSP option chain")
    exchange_rank = {"SMART": 0, "CBOE": 1, "IBUSOPT": 2}
    preferred.sort(
        key=lambda c: (
            exchange_rank.get(str(getattr(c, "exchange", "")).upper(), 3),
            -len(c.expirations),
        )
    )
    return preferred


def expiration_floor(expirations: list[str], day: pd.Timestamp, minimum_dte: int) -> str:
    normalized = pd.Timestamp(day).normalize()
    target = normalized + pd.Timedelta(days=minimum_dte)
    listed = {pd.Timestamp(str(value).replace("-", "")).normalize() for value in expirations}
    for month_start in pd.date_range(target.replace(day=1), periods=12, freq="MS"):
        month_days = pd.date_range(month_start, month_start + pd.offsets.MonthEnd(0), freq="D")
        third_friday = month_days[month_days.weekday == 4][2]
        if third_friday < target:
            continue
        # A holiday-shifted standard monthly expires on the immediately
        # preceding Thursday. An arbitrary weekly Thursday is never selected.
        for candidate in (third_friday, third_friday - pd.Timedelta(days=1)):
            if candidate in listed:
                return candidate.strftime("%Y%m%d")
    raise RuntimeError(f"XSP chain has no standard monthly expiration with at least {minimum_dte} DTE")


def _option(expiry: str, strike: float, chain: Any) -> Option:
    return Option(
        "XSP", expiry, float(strike), "P", str(getattr(chain, "exchange", "SMART") or "SMART"),
        multiplier=str(getattr(chain, "multiplier", "100") or "100"), currency="USD",
        tradingClass=str(getattr(chain, "tradingClass", "XSP") or "XSP"),
    )


def _listed_puts(ib: IB, expiry: str, chain: Any) -> dict[float, Contract]:
    """Return contracts actually listed for one expiry, not the chain-wide strike union."""
    template = _option(expiry, 0.0, chain)
    details = ib.reqContractDetails(template)
    contracts: dict[float, Contract] = {}
    for detail in details:
        contract = getattr(detail, "contract", None)
        if (
            contract is not None
            and str(getattr(contract, "right", "")).upper() == "P"
            and str(getattr(contract, "lastTradeDateOrContractMonth", "")).replace("-", "")[:8] == expiry
            and int(getattr(contract, "conId", 0) or 0) > 0
        ):
            contracts[float(contract.strike)] = contract
    if not contracts:
        raise RuntimeError(f"IB returned no actually listed XSP puts for {expiry}")
    return contracts


def build_combo(short_contract: Contract, long_contract: Contract, *, inverse: bool = False) -> Contract:
    if any(int(getattr(c, "conId", 0) or 0) <= 0 for c in (short_contract, long_contract)):
        raise ValueError("qualified XSP option conIds are required")
    combo = Contract(symbol="XSP", secType="BAG", currency="USD", exchange="SMART")
    short_action, long_action = (("BUY", "SELL") if inverse else ("SELL", "BUY"))
    combo.comboLegs = [
        ComboLeg(conId=int(short_contract.conId), ratio=1, action=short_action, exchange="SMART"),
        ComboLeg(conId=int(long_contract.conId), ratio=1, action=long_action, exchange="SMART"),
    ]
    return combo


def _spot(ib: IB, underlying: Contract, timeout: float = 4.0) -> float:
    ticker = ib.reqMktData(underlying, "", False, False)
    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline:
            ib.sleep(0.2)
            bid, ask = getattr(ticker, "bid", None), getattr(ticker, "ask", None)
            try:
                midpoint = (float(bid) + float(ask)) / 2.0 if float(bid) > 0 and float(ask) >= float(bid) else None
            except (TypeError, ValueError):
                midpoint = None
            values = (getattr(ticker, "last", None), midpoint)
            spot = next((v for v in (_finite(x) for x in values) if v is not None and v > 0), None)
            if spot is not None:
                return float(spot)
    finally:
        ib.cancelMktData(underlying)
    raise RuntimeError("no valid live XSP price")


def _selection(
    ib: IB,
    *,
    expiry: str,
    short_strike: float,
    long_strike: float,
    chain: Any,
    spot: float,
    listed_puts: dict[float, Contract] | None = None,
) -> XspPutCreditSelection:
    listed = listed_puts or _listed_puts(ib, expiry, chain)
    short_contract, long_contract = listed.get(short_strike), listed.get(long_strike)
    if short_contract is None or long_contract is None:
        raise RuntimeError("saved XSP vertical strikes are not both listed")
    contracts = [short_contract, long_contract]
    quote_map = {int(c.conId): q for c, q in _quotes_many(ib, contracts)}
    short_quote = quote_map.get(int(short_contract.conId)) or _quote(ib, short_contract)
    long_quote = quote_map.get(int(long_contract.conId)) or _quote(ib, long_contract)
    details = [d for c in contracts for d in ib.reqContractDetails(c)]
    min_tick = max([float(getattr(d, "minTick", 0.05) or 0.05) for d in details] or [0.05])
    selection = XspPutCreditSelection(
        expiry, spot, short_strike, long_strike, short_contract, long_contract,
        short_quote, long_quote, build_combo(short_contract, long_contract),
        build_combo(short_contract, long_contract, inverse=True), min_tick,
        int(float(getattr(chain, "multiplier", 100) or 100)),
    )
    if not math.isfinite(selection.conservative_credit) or not 0 < selection.conservative_credit < short_strike - long_strike:
        raise RuntimeError(f"invalid conservative XSP credit {selection.conservative_credit}")
    return selection


def select_xsp_put_credit(
    ib: IB,
    *,
    decision_day: pd.Timestamp,
    minimum_dte: int,
    target_delta: float,
    maximum_delta_deviation: float,
    width: float,
) -> XspPutCreditSelection:
    underlying = ib.qualifyContracts(Index("XSP", "CBOE", "USD"))[0]
    spot = _spot(ib, underlying)
    chain = _chains(ib, underlying)[0]
    expiry = expiration_floor(list(chain.expirations), decision_day, minimum_dte)
    listed = _listed_puts(ib, expiry, chain)
    strikes = sorted(listed)
    strike_set = set(strikes)
    candidates = [x for x in strikes if x - width in strike_set and spot - 100 <= x <= spot]
    if not candidates:
        raise RuntimeError(f"XSP chain contains no exact {width:g}-point put verticals")
    requested = [listed[x] for x in candidates[-80:]]
    quoted = [(float(c.strike), c, q) for c, q in _quotes_many(ib, requested) if q.delta is not None and q.delta < 0]
    if not quoted:
        raise RuntimeError("no XSP puts with valid model delta and bid/ask")
    short_strike, _, short_quote = min(quoted, key=lambda x: abs(float(x[2].delta) - target_delta))
    deviation = abs(float(short_quote.delta) - target_delta)
    if deviation > maximum_delta_deviation:
        raise RuntimeError(f"nearest XSP short delta {short_quote.delta:.4f} misses target by {deviation:.4f}")
    return _selection(
        ib, expiry=expiry, short_strike=short_strike, long_strike=short_strike - width,
        chain=chain, spot=spot, listed_puts=listed,
    )


def selection_for_identity(ib: IB, *, expiry: str, short_strike: float, long_strike: float) -> XspPutCreditSelection:
    underlying = ib.qualifyContracts(Index("XSP", "CBOE", "USD"))[0]
    chain = _chains(ib, underlying)[0]
    return _selection(ib, expiry=expiry, short_strike=short_strike, long_strike=long_strike, chain=chain, spot=float("nan"))


def build_entry_order(selection: XspPutCreditSelection, *, account: str, order_ref: str) -> LimitOrder:
    require_paper_account(account)
    order = LimitOrder("BUY", 1, -selection.entry_limit_credit)
    order.account, order.orderRef, order.tif = account, order_ref[:45], "DAY"
    order.outsideRth, order.transmit = False, True
    return order


def build_profit_target_order(
    selection: XspPutCreditSelection,
    *,
    entry_credit: float,
    profit_target_fraction: float,
    account: str,
    order_ref: str,
) -> LimitOrder:
    require_paper_account(account)
    target = round_credit_down(entry_credit * (1.0 - profit_target_fraction), selection.min_tick)
    if target <= 0:
        raise ValueError("XSP profit-target debit must be positive")
    order = LimitOrder("BUY", 1, target)
    order.account, order.orderRef, order.tif = account, order_ref[:45], "GTC"
    order.outsideRth, order.transmit = False, True
    return order


def build_time_exit_order(selection: XspPutCreditSelection, *, account: str, order_ref: str) -> LimitOrder:
    require_paper_account(account)
    debit = round_debit_up(selection.conservative_exit_debit, selection.min_tick)
    if debit <= 0:
        raise ValueError("XSP time-exit debit must be positive")
    order = LimitOrder("BUY", 1, debit)
    order.account, order.orderRef, order.tif = account, order_ref[:45], "DAY"
    order.outsideRth, order.transmit = False, True
    return order
