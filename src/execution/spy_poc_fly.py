from __future__ import annotations

from dataclasses import dataclass
import math

import pandas as pd
from ib_insync import ComboLeg, Contract, IB, LimitOrder

from src.execution.spx_zebra import (
    DEFAULT_COMBO_EXCHANGE,
    DEFAULT_OPTION_EXCHANGE,
    OPTION_MULTIPLIER,
    ZebraOptionQuote,
    _load_underlying_contract,
    _option_contract,
    _quote_option,
    _resolve_option_chains,
    _safe_option_multiplier,
)


@dataclass(frozen=True, slots=True)
class FlyStructureQuote:
    upper: ZebraOptionQuote
    body: ZebraOptionQuote
    lower: ZebraOptionQuote

    @property
    def mid(self) -> float:
        return self.upper.mid - 2.0 * self.body.mid + self.lower.mid

    @property
    def entry_conservative(self) -> float:
        return self.upper.ask - 2.0 * self.body.bid + self.lower.ask

    @property
    def exit_conservative(self) -> float:
        return self.upper.bid - 2.0 * self.body.ask + self.lower.bid


@dataclass(frozen=True, slots=True)
class SpyPocFlySelection:
    symbol: str
    expiry: str
    upper_strike: float
    body_strike: float
    lower_strike: float
    quote: FlyStructureQuote
    min_tick: float
    multiplier: int
    upper_contract: Contract
    body_contract: Contract
    lower_contract: Contract
    combo_contract: Contract

    @property
    def entry_mid(self) -> float:
        return self.quote.mid

    @property
    def entry_marketable_limit(self) -> float:
        return round_up_to_tick(self.quote.entry_conservative, self.min_tick)

    @property
    def maximum_loss_dollars(self) -> float:
        return self.entry_marketable_limit * self.multiplier


def round_up_to_tick(value: float, tick: float) -> float:
    if tick <= 0:
        raise ValueError("tick must be positive")
    return round(math.ceil((float(value) - 1e-12) / tick) * tick, 8)


def round_down_to_tick(value: float, tick: float) -> float:
    if tick <= 0:
        raise ValueError("tick must be positive")
    return round(math.floor((float(value) + 1e-12) / tick) * tick, 8)


def build_put_fly_combo(
    upper: Contract,
    body: Contract,
    lower: Contract,
    *,
    exchange: str = DEFAULT_COMBO_EXCHANGE,
) -> Contract:
    contracts = (upper, body, lower)
    if any(int(getattr(contract, "conId", 0) or 0) <= 0 for contract in contracts):
        raise ValueError("all fly legs require qualified conIds")
    combo = Contract()
    combo.symbol = "SPY"
    combo.secType = "BAG"
    combo.currency = "USD"
    combo.exchange = exchange
    combo.comboLegs = [
        ComboLeg(
            conId=int(upper.conId),
            ratio=1,
            action="BUY",
            exchange=str(getattr(upper, "exchange", "") or exchange),
        ),
        ComboLeg(
            conId=int(body.conId),
            ratio=2,
            action="SELL",
            exchange=str(getattr(body, "exchange", "") or exchange),
        ),
        ComboLeg(
            conId=int(lower.conId),
            ratio=1,
            action="BUY",
            exchange=str(getattr(lower, "exchange", "") or exchange),
        ),
    ]
    return combo


def _minimum_tick(ib: IB, contracts: tuple[Contract, ...]) -> float:
    ticks: list[float] = []
    for contract in contracts:
        details = ib.reqContractDetails(contract)
        if details:
            tick = float(getattr(details[0], "minTick", 0.01) or 0.01)
            if math.isfinite(tick) and tick > 0:
                ticks.append(tick)
    return max(ticks or [0.01])


def select_spy_poc_put_fly(
    ib: IB,
    *,
    decision_day: pd.Timestamp,
    body_strike: float,
    upper_width: float = 10.0,
    lower_width: float = 10.0,
) -> SpyPocFlySelection:
    underlying = _load_underlying_contract(ib, "SPY")
    target = (pd.Timestamp(decision_day).normalize() + pd.Timedelta(days=1)).strftime(
        "%Y%m%d"
    )
    desired = {
        float(body_strike + upper_width),
        float(body_strike),
        float(body_strike - lower_width),
    }
    for chain in _resolve_option_chains(ib, underlying):
        expirations = {str(value).replace("-", "") for value in chain.expirations}
        strikes = {float(value) for value in chain.strikes}
        if target not in expirations or not desired.issubset(strikes):
            continue
        trading_class = str(getattr(chain, "tradingClass", "SPY") or "SPY")
        exchange = str(
            getattr(chain, "exchange", "") or DEFAULT_OPTION_EXCHANGE
        )
        multiplier = _safe_option_multiplier(
            getattr(chain, "multiplier", OPTION_MULTIPLIER)
        )
        contracts = tuple(
            _option_contract(
                "SPY",
                target,
                strike,
                "P",
                trading_class=trading_class,
                exchange=exchange,
                multiplier=multiplier,
            )
            for strike in (
                body_strike + upper_width,
                body_strike,
                body_strike - lower_width,
            )
        )
        qualified = ib.qualifyContracts(*contracts)
        if len(qualified) != 3:
            continue
        upper, body, lower = qualified
        quote = FlyStructureQuote(
            upper=_quote_option(ib, upper),
            body=_quote_option(ib, body),
            lower=_quote_option(ib, lower),
        )
        if not math.isfinite(quote.mid) or quote.mid <= 0:
            raise RuntimeError(f"SPY POC fly has invalid mid debit {quote.mid}")
        if (
            not math.isfinite(quote.entry_conservative)
            or quote.entry_conservative <= 0
            or quote.entry_conservative >= upper_width
        ):
            raise RuntimeError(
                f"SPY POC fly has invalid marketable debit {quote.entry_conservative}"
            )
        return SpyPocFlySelection(
            symbol="SPY",
            expiry=target,
            upper_strike=float(body_strike + upper_width),
            body_strike=float(body_strike),
            lower_strike=float(body_strike - lower_width),
            quote=quote,
            min_tick=_minimum_tick(ib, (upper, body, lower)),
            multiplier=multiplier,
            upper_contract=upper,
            body_contract=body,
            lower_contract=lower,
            combo_contract=build_put_fly_combo(upper, body, lower),
        )
    raise RuntimeError(
        f"no exact next-calendar-day SPY U{upper_width:g}/L{lower_width:g} "
        f"put fly exists for {target} body={body_strike:g}"
    )


def refresh_fly_quote(ib: IB, selection: SpyPocFlySelection) -> FlyStructureQuote:
    return FlyStructureQuote(
        upper=_quote_option(ib, selection.upper_contract),
        body=_quote_option(ib, selection.body_contract),
        lower=_quote_option(ib, selection.lower_contract),
    )


def build_entry_order(
    selection: SpyPocFlySelection,
    *,
    account: str,
    order_ref: str,
    quantity: int = 1,
) -> LimitOrder:
    if quantity != 1:
        raise ValueError("the frozen paper candidate trades exactly one fly")
    order = LimitOrder("BUY", quantity, selection.entry_marketable_limit)
    order.account = account
    order.orderRef = order_ref[:45]
    order.tif = "DAY"
    order.outsideRth = False
    order.transmit = True
    return order


def build_exit_order(
    selection: SpyPocFlySelection,
    *,
    account: str,
    order_ref: str,
    limit_price: float,
    quantity: int = 1,
) -> LimitOrder:
    if quantity != 1:
        raise ValueError("the frozen paper candidate closes exactly one fly")
    order = LimitOrder(
        "SELL",
        quantity,
        round_down_to_tick(limit_price, selection.min_tick),
    )
    order.account = account
    order.orderRef = order_ref[:45]
    order.tif = "DAY"
    order.outsideRth = False
    order.transmit = True
    return order
