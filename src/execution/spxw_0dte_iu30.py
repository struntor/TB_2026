from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd
from ib_insync import ComboLeg, Contract, IB, Index, LimitOrder, Option

from src.execution.spxw_put_credit import (
    OptionRiskQuote,
    _chains,
    _quotes_many,
    round_debit_up,
)


@dataclass(frozen=True, slots=True)
class ComboLegSelection:
    role: str
    action: str
    ratio: int
    right: str
    strike: float
    contract: Contract
    quote: OptionRiskQuote


@dataclass(frozen=True, slots=True)
class Spxw0DteSelection:
    expiry: str
    decision_price: float
    prior_atr14: float
    short_put_strike: float
    long_put_strike: float
    fly_lower: float | None
    fly_body: float | None
    fly_upper: float | None
    legs: tuple[ComboLegSelection, ...]
    combo_contract: Contract
    min_tick: float
    multiplier: int

    @property
    def conservative_put_credit(self) -> float:
        by_role = {leg.role: leg for leg in self.legs}
        return by_role["short_put"].quote.bid - by_role["long_put"].quote.ask

    @property
    def conservative_fly_debit(self) -> float:
        by_role = {leg.role: leg for leg in self.legs}
        if "fly_body" not in by_role:
            return 0.0
        return (
            by_role["fly_lower"].quote.ask
            - 2.0 * by_role["fly_body"].quote.bid
            + by_role["fly_upper"].quote.ask
        )

    @property
    def conservative_net_debit(self) -> float:
        """Signed BAG cost: positive debit, negative credit."""
        return self.conservative_fly_debit - self.conservative_put_credit

    @property
    def maximum_loss_dollars(self) -> float:
        width = self.short_put_strike - self.long_put_strike
        return max(0.0, width + self.conservative_net_debit) * self.multiplier


def require_paper_account(account: str) -> None:
    if not str(account).upper().startswith("DU"):
        raise RuntimeError("the SPXW 0DTE IU30 strategy may only use a DU paper account")


def _round5(value: float) -> float:
    return round(value / 5.0) * 5.0


def _option(expiry: str, strike: float, right: str, chain: Any) -> Option:
    return Option(
        "SPX",
        expiry,
        float(strike),
        right,
        str(getattr(chain, "exchange", "SMART") or "SMART"),
        multiplier=str(getattr(chain, "multiplier", "100") or "100"),
        currency="USD",
        tradingClass=str(getattr(chain, "tradingClass", "SPXW") or "SPXW"),
    )


def build_combo(legs: tuple[ComboLegSelection, ...]) -> Contract:
    if any(int(getattr(leg.contract, "conId", 0) or 0) <= 0 for leg in legs):
        raise ValueError("qualified option conIds are required")
    combo = Contract(symbol="SPX", secType="BAG", currency="USD", exchange="SMART")
    combo.comboLegs = [
        ComboLeg(
            conId=int(leg.contract.conId),
            ratio=int(leg.ratio),
            action=leg.action,
            exchange="SMART",
        )
        for leg in legs
    ]
    return combo


def select_spxw_0dte_combo(
    ib: IB,
    *,
    decision_day: pd.Timestamp,
    decision_price: float,
    prior_atr14: float,
    include_iu30: bool,
    put_width: float = 20.0,
    fly_width: float = 30.0,
) -> Spxw0DteSelection:
    underlying = ib.qualifyContracts(Index("SPX", "CBOE", "USD"))[0]
    chain = _chains(ib, underlying)[0]
    expiry = pd.Timestamp(decision_day).strftime("%Y%m%d")
    if expiry not in {str(value).replace("-", "") for value in chain.expirations}:
        raise RuntimeError(f"SPXW chain has no same-day expiration {expiry}")
    available = {float(value) for value in chain.strikes}
    short_put = _round5(decision_price - prior_atr14)
    long_put = short_put - put_width
    requested: list[tuple[str, str, int, str, float]] = [
        ("short_put", "SELL", 1, "P", short_put),
        ("long_put", "BUY", 1, "P", long_put),
    ]
    fly_lower = fly_body = fly_upper = None
    if include_iu30:
        fly_body = _round5(decision_price - 0.25 * prior_atr14)
        fly_lower, fly_upper = fly_body - fly_width, fly_body + fly_width
        requested.extend(
            [
                ("fly_lower", "BUY", 1, "C", fly_lower),
                ("fly_body", "SELL", 2, "C", fly_body),
                ("fly_upper", "BUY", 1, "C", fly_upper),
            ]
        )
    missing = sorted({strike for _, _, _, _, strike in requested} - available)
    if missing:
        raise RuntimeError(f"SPXW chain is missing required strikes: {missing}")
    contracts = ib.qualifyContracts(
        *[_option(expiry, strike, right, chain) for _, _, _, right, strike in requested]
    )
    if len(contracts) != len(requested):
        raise RuntimeError("not all SPXW 0DTE legs could be qualified")
    quote_map = {
        int(contract.conId): quote for contract, quote in _quotes_many(ib, list(contracts))
    }
    legs: list[ComboLegSelection] = []
    for descriptor, contract in zip(requested, contracts):
        role, action, ratio, right, strike = descriptor
        quote = quote_map.get(int(contract.conId))
        if quote is None:
            raise RuntimeError(f"no valid bid/ask and model Greeks for {role} strike {strike:g}")
        legs.append(ComboLegSelection(role, action, ratio, right, strike, contract, quote))
    details = [detail for contract in contracts for detail in ib.reqContractDetails(contract)]
    ticks = [float(getattr(detail, "minTick", 0.05) or 0.05) for detail in details]
    selection = Spxw0DteSelection(
        expiry=expiry,
        decision_price=float(decision_price),
        prior_atr14=float(prior_atr14),
        short_put_strike=short_put,
        long_put_strike=long_put,
        fly_lower=fly_lower,
        fly_body=fly_body,
        fly_upper=fly_upper,
        legs=tuple(legs),
        combo_contract=build_combo(tuple(legs)),
        min_tick=max([tick for tick in ticks if tick > 0] or [0.05]),
        multiplier=int(float(getattr(chain, "multiplier", 100) or 100)),
    )
    if selection.conservative_put_credit <= 0 or selection.conservative_put_credit >= put_width:
        raise RuntimeError(f"invalid natural put credit {selection.conservative_put_credit:.2f}")
    if include_iu30 and not 0 < selection.conservative_fly_debit < fly_width:
        raise RuntimeError(f"invalid natural IU30 debit {selection.conservative_fly_debit:.2f}")
    return selection


def build_entry_order(
    selection: Spxw0DteSelection,
    *,
    account: str,
    order_ref: str,
) -> LimitOrder:
    require_paper_account(account)
    signed_limit = round_debit_up(selection.conservative_net_debit, selection.min_tick)
    order = LimitOrder("BUY", 1, signed_limit)
    order.account = account
    order.orderRef = order_ref[:45]
    order.tif = "DAY"
    order.outsideRth = False
    order.transmit = True
    return order
