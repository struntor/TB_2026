from __future__ import annotations

from dataclasses import dataclass
import math
import time
from typing import Any

import pandas as pd
from ib_insync import ComboLeg, Contract, IB, Index, LimitOrder, Option

from src.execution.spxw_put_credit import OptionRiskQuote, _chains, _finite, _quotes_many, round_credit_down


@dataclass(frozen=True, slots=True)
class BwbLegSelection:
    role: str
    action: str
    ratio: int
    strike: float
    contract: Contract
    quote: OptionRiskQuote


@dataclass(frozen=True, slots=True)
class SpxwPutBwbSelection:
    expiry: str
    spot: float
    opening_vix: float
    upper_strike: float
    body_strike: float
    lower_strike: float
    legs: tuple[BwbLegSelection, ...]
    combo_contract: Contract
    inverse_combo_contract: Contract
    min_tick: float
    multiplier: int
    entry_slippage_points: float

    @property
    def mid_credit(self) -> float:
        by_role = {leg.role: leg for leg in self.legs}
        return 2.0 * by_role["body"].quote.mid - by_role["upper"].quote.mid - by_role["lower"].quote.mid

    @property
    def natural_credit(self) -> float:
        by_role = {leg.role: leg for leg in self.legs}
        return 2.0 * by_role["body"].quote.bid - by_role["upper"].quote.ask - by_role["lower"].quote.ask

    @property
    def entry_limit_credit(self) -> float:
        return round_credit_down(self.mid_credit - self.entry_slippage_points, self.min_tick)

    @property
    def maximum_loss_dollars(self) -> float:
        return maximum_loss_dollars_for_credit(self, self.entry_limit_credit)


def maximum_loss_dollars_for_credit(selection: SpxwPutBwbSelection, credit: float) -> float:
    downside_width_difference = selection.lower_strike - 2.0 * selection.body_strike + selection.upper_strike
    worst_points = max(0.0, -(downside_width_difference + float(credit)))
    return worst_points * selection.multiplier


def require_paper_account(account: str) -> None:
    if not str(account).upper().startswith("DU"):
        raise RuntimeError("the SPXW 75DTE put-BWB strategy may only use a DU paper account")


def _expiration_floor(expirations: list[str], day: pd.Timestamp, minimum_dte: int) -> str:
    normalized = pd.Timestamp(day).normalize()
    parsed = sorted({pd.Timestamp(str(value).replace("-", "")).normalize() for value in expirations})
    eligible = [expiry for expiry in parsed if (expiry - normalized).days >= minimum_dte]
    if not eligible:
        raise RuntimeError(f"SPXW chain has no expiration with at least {minimum_dte} DTE")
    return eligible[0].strftime("%Y%m%d")


def _option(expiry: str, strike: float, chain: Any) -> Option:
    return Option(
        "SPX", expiry, float(strike), "P",
        str(getattr(chain, "exchange", "SMART") or "SMART"),
        multiplier=str(getattr(chain, "multiplier", "100") or "100"),
        currency="USD",
        tradingClass=str(getattr(chain, "tradingClass", "SPXW") or "SPXW"),
    )


def build_combo(legs: tuple[BwbLegSelection, ...], *, inverse: bool = False) -> Contract:
    if any(int(getattr(leg.contract, "conId", 0) or 0) <= 0 for leg in legs):
        raise ValueError("qualified option conIds are required")
    combo = Contract(symbol="SPX", secType="BAG", currency="USD", exchange="SMART")
    combo.comboLegs = [
        ComboLeg(
            conId=int(leg.contract.conId), ratio=int(leg.ratio),
            action=("SELL" if leg.action == "BUY" else "BUY") if inverse else leg.action,
            exchange="SMART",
        )
        for leg in legs
    ]
    return combo


def _spot(ib: IB, underlying: Contract, timeout: float = 4.0) -> float:
    ticker = ib.reqMktData(underlying, "", False, False)
    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline:
            ib.sleep(0.2)
            values = (ticker.marketPrice(), getattr(ticker, "last", None), getattr(ticker, "close", None))
            observed = next((value for value in (_finite(item) for item in values) if value and value > 0), None)
            if observed is not None:
                return float(observed)
    finally:
        ib.cancelMktData(underlying)
    raise RuntimeError("no valid live SPX price")


def select_spxw_put_bwb(
    ib: IB,
    *,
    decision_day: pd.Timestamp,
    opening_vix: float,
    minimum_dte: int = 75,
    body_spot_fraction: float = 0.99,
    upper_width: float = 35.0,
    lower_width: float = 55.0,
    entry_slippage_points: float = 0.20,
) -> SpxwPutBwbSelection:
    underlying = ib.qualifyContracts(Index("SPX", "CBOE", "USD"))[0]
    spot = _spot(ib, underlying)
    chain = _chains(ib, underlying)[0]
    expiry = _expiration_floor(list(chain.expirations), decision_day, minimum_dte)
    available = {float(value) for value in chain.strikes}
    body_target = spot * body_spot_fraction
    body = min(available, key=lambda strike: (abs(strike - body_target), strike))
    upper, lower = body + upper_width, body - lower_width
    missing = sorted({upper, body, lower} - available)
    if missing:
        raise RuntimeError(f"SPXW chain is missing required BWB strikes: {missing}")
    descriptors = (
        ("upper", "BUY", 1, upper),
        ("body", "SELL", 2, body),
        ("lower", "BUY", 1, lower),
    )
    contracts = list(ib.qualifyContracts(*[_option(expiry, strike, chain) for _, _, _, strike in descriptors]))
    if len(contracts) != 3:
        raise RuntimeError("not all SPXW BWB legs could be qualified")
    quote_map = {int(contract.conId): quote for contract, quote in _quotes_many(ib, contracts)}
    legs = []
    for (role, action, ratio, strike), contract in zip(descriptors, contracts):
        quote = quote_map.get(int(contract.conId))
        if quote is None:
            raise RuntimeError(f"no valid SPXW BWB quote for {role} strike {strike:g}")
        legs.append(BwbLegSelection(role, action, ratio, strike, contract, quote))
    details = [detail for contract in contracts for detail in ib.reqContractDetails(contract)]
    min_tick = max([float(getattr(detail, "minTick", 0.05) or 0.05) for detail in details] or [0.05])
    multiplier = int(float(getattr(chain, "multiplier", 100) or 100))
    selection = SpxwPutBwbSelection(
        expiry, spot, float(opening_vix), upper, body, lower, tuple(legs),
        build_combo(tuple(legs)), build_combo(tuple(legs), inverse=True),
        min_tick, multiplier, float(entry_slippage_points),
    )
    if not math.isfinite(selection.entry_limit_credit) or selection.entry_limit_credit <= 0:
        raise RuntimeError(f"invalid SPXW BWB entry credit {selection.entry_limit_credit}")
    if selection.entry_limit_credit >= 20.0:
        raise RuntimeError(f"SPXW BWB credit exceeds defined downside width difference: {selection.entry_limit_credit}")
    return selection


def selection_for_identity(
    ib: IB,
    *,
    expiry: str,
    upper_strike: float,
    body_strike: float,
    lower_strike: float,
    opening_vix: float,
    entry_slippage_points: float = 0.20,
) -> SpxwPutBwbSelection:
    underlying = ib.qualifyContracts(Index("SPX", "CBOE", "USD"))[0]
    chain = _chains(ib, underlying)[0]
    descriptors = (
        ("upper", "BUY", 1, upper_strike),
        ("body", "SELL", 2, body_strike),
        ("lower", "BUY", 1, lower_strike),
    )
    contracts = list(ib.qualifyContracts(*[_option(expiry, strike, chain) for _, _, _, strike in descriptors]))
    if len(contracts) != 3:
        raise RuntimeError("saved SPXW BWB identity could not be qualified")
    quote_map = {int(contract.conId): quote for contract, quote in _quotes_many(ib, contracts)}
    legs = []
    for (role, action, ratio, strike), contract in zip(descriptors, contracts):
        quote = quote_map.get(int(contract.conId))
        if quote is None:
            raise RuntimeError(f"saved SPXW BWB identity has no quote for {role}")
        legs.append(BwbLegSelection(role, action, ratio, strike, contract, quote))
    details = [detail for contract in contracts for detail in ib.reqContractDetails(contract)]
    min_tick = max([float(getattr(detail, "minTick", 0.05) or 0.05) for detail in details] or [0.05])
    selection = SpxwPutBwbSelection(
        expiry, float("nan"), float(opening_vix), upper_strike, body_strike, lower_strike,
        tuple(legs), build_combo(tuple(legs)), build_combo(tuple(legs), inverse=True),
        min_tick, int(float(getattr(chain, "multiplier", 100) or 100)), entry_slippage_points,
    )
    return selection


def build_entry_order(
    selection: SpxwPutBwbSelection,
    *,
    account: str,
    order_ref: str,
    limit_credit: float | None = None,
) -> LimitOrder:
    require_paper_account(account)
    credit = selection.entry_limit_credit if limit_credit is None else round_credit_down(limit_credit, selection.min_tick)
    if not math.isfinite(credit) or credit <= 0:
        raise ValueError("entry limit credit must be positive and finite")
    order = LimitOrder("BUY", 1, -credit)
    order.account, order.orderRef, order.tif = account, order_ref[:45], "DAY"
    order.outsideRth, order.transmit = False, True
    return order


def build_profit_target_order(
    selection: SpxwPutBwbSelection,
    *,
    entry_credit: float,
    profit_target_fraction: float,
    account: str,
    order_ref: str,
) -> LimitOrder:
    require_paper_account(account)
    target_debit = round_credit_down(entry_credit * (1.0 - profit_target_fraction), selection.min_tick)
    if target_debit <= 0:
        raise ValueError("profit-target debit must be positive")
    order = LimitOrder("BUY", 1, target_debit)
    order.account, order.orderRef, order.tif = account, order_ref[:45], "GTC"
    order.outsideRth, order.transmit = False, True
    return order
