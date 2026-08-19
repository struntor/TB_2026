from __future__ import annotations

from dataclasses import dataclass
import math
import time
from typing import Any

import pandas as pd
from ib_insync import ComboLeg, Contract, IB, Index, LimitOrder, Option


@dataclass(frozen=True, slots=True)
class OptionRiskQuote:
    bid: float
    ask: float
    delta: float | None
    gamma: float | None
    theta: float | None
    vega: float | None
    implied_vol: float | None

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2.0


@dataclass(frozen=True, slots=True)
class PutCreditSelection:
    symbol: str
    expiry: str
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

    @property
    def conservative_credit(self) -> float:
        return self.short_quote.bid - self.long_quote.ask

    @property
    def mid_credit(self) -> float:
        return self.short_quote.mid - self.long_quote.mid

    @property
    def conservative_exit_debit(self) -> float:
        return self.short_quote.ask - self.long_quote.bid

    @property
    def mid_exit_debit(self) -> float:
        return self.short_quote.mid - self.long_quote.mid

    @property
    def maximum_loss_dollars(self) -> float:
        return (
            self.short_strike - self.long_strike - self.conservative_credit
        ) * self.multiplier


def _finite(value: object) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def round_credit_down(value: float, tick: float) -> float:
    return round(math.floor((value + 1e-12) / tick) * tick, 8)


def round_debit_up(value: float, tick: float) -> float:
    return round(math.ceil((value - 1e-12) / tick) * tick, 8)


def require_paper_account(account: str) -> None:
    if not str(account).upper().startswith("DU"):
        raise RuntimeError(
            "the SPXW 15DTE put-credit strategy may only use a DU paper account"
        )


def _quote(ib: IB, contract: Contract, timeout: float = 4.0) -> OptionRiskQuote:
    ticker = ib.reqMktData(contract, "", False, False)
    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline:
            ib.sleep(0.2)
            bid = _finite(getattr(ticker, "bid", None))
            ask = _finite(getattr(ticker, "ask", None))
            greeks = getattr(ticker, "modelGreeks", None)
            if bid and ask and bid > 0 and ask > 0 and greeks is not None:
                break
        bid = _finite(getattr(ticker, "bid", None))
        ask = _finite(getattr(ticker, "ask", None))
        if bid is None or ask is None or bid <= 0 or ask <= 0 or ask < bid:
            raise RuntimeError(
                f"no valid live bid/ask for {getattr(contract, 'localSymbol', '') or contract}"
            )
        greeks = getattr(ticker, "modelGreeks", None)
        return OptionRiskQuote(
            bid=bid,
            ask=ask,
            delta=_finite(getattr(greeks, "delta", None)) if greeks else None,
            gamma=_finite(getattr(greeks, "gamma", None)) if greeks else None,
            theta=_finite(getattr(greeks, "theta", None)) if greeks else None,
            vega=_finite(getattr(greeks, "vega", None)) if greeks else None,
            implied_vol=_finite(getattr(greeks, "impliedVol", None)) if greeks else None,
        )
    finally:
        try:
            ib.cancelMktData(contract)
        except Exception:
            pass


def _quotes_many(
    ib: IB,
    contracts: list[Contract],
    timeout: float = 5.0,
) -> list[tuple[Contract, OptionRiskQuote]]:
    """Request a strike strip concurrently so selection fits the entry window."""
    tickers = [(contract, ib.reqMktData(contract, "", False, False)) for contract in contracts]
    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline:
            ib.sleep(0.2)
            ready = 0
            for _, ticker in tickers:
                bid = _finite(getattr(ticker, "bid", None))
                ask = _finite(getattr(ticker, "ask", None))
                greeks = getattr(ticker, "modelGreeks", None)
                if bid is not None and ask is not None and bid > 0 and ask >= bid and greeks is not None:
                    ready += 1
            if ready == len(tickers):
                break
        observed: list[tuple[Contract, OptionRiskQuote]] = []
        for contract, ticker in tickers:
            bid = _finite(getattr(ticker, "bid", None))
            ask = _finite(getattr(ticker, "ask", None))
            greeks = getattr(ticker, "modelGreeks", None)
            if bid is None or ask is None or bid <= 0 or ask < bid or greeks is None:
                continue
            observed.append(
                (
                    contract,
                    OptionRiskQuote(
                        bid=bid,
                        ask=ask,
                        delta=_finite(getattr(greeks, "delta", None)),
                        gamma=_finite(getattr(greeks, "gamma", None)),
                        theta=_finite(getattr(greeks, "theta", None)),
                        vega=_finite(getattr(greeks, "vega", None)),
                        implied_vol=_finite(getattr(greeks, "impliedVol", None)),
                    ),
                )
            )
        return observed
    finally:
        for contract, _ in tickers:
            try:
                ib.cancelMktData(contract)
            except Exception:
                pass


def _chains(ib: IB, underlying: Contract) -> list[Any]:
    chains = ib.reqSecDefOptParams(
        underlying.symbol, "", underlying.secType, underlying.conId
    )
    preferred = [
        chain
        for chain in chains
        if str(getattr(chain, "tradingClass", "")).upper() == "SPXW"
    ]
    if not preferred:
        raise RuntimeError("IB returned no SPXW option chain")
    preferred.sort(
        key=lambda chain: (
            str(getattr(chain, "exchange", "")).upper() not in {"SMART", "CBOE"},
            -len(getattr(chain, "expirations", [])),
        )
    )
    return preferred


def _expiry(expirations: list[str], day: pd.Timestamp, target_dte: int) -> str:
    target = pd.Timestamp(day).normalize() + pd.Timedelta(days=target_dte)
    parsed = sorted(
        {
            pd.Timestamp(str(value).replace("-", "")).normalize()
            for value in expirations
        }
    )
    if not parsed:
        raise RuntimeError("SPXW chain has no expirations")
    chosen = min(parsed, key=lambda value: (abs((value - target).days), value))
    return chosen.strftime("%Y%m%d")


def _option(
    expiry: str,
    strike: float,
    trading_class: str,
    exchange: str,
    multiplier: int,
) -> Option:
    return Option(
        "SPX",
        expiry,
        float(strike),
        "P",
        exchange,
        multiplier=str(multiplier),
        currency="USD",
        tradingClass=trading_class,
    )


def build_vertical_combo(
    short_contract: Contract,
    long_contract: Contract,
    *,
    inverse: bool = False,
) -> Contract:
    if any(int(getattr(contract, "conId", 0) or 0) <= 0 for contract in (short_contract, long_contract)):
        raise ValueError("qualified option conIds are required")
    combo = Contract(
        symbol="SPX",
        secType="BAG",
        currency="USD",
        exchange="SMART",
    )
    short_action, long_action = (("BUY", "SELL") if inverse else ("SELL", "BUY"))
    combo.comboLegs = [
        ComboLeg(
            conId=int(short_contract.conId), ratio=1, action=short_action, exchange="SMART"
        ),
        ComboLeg(
            conId=int(long_contract.conId), ratio=1, action=long_action, exchange="SMART"
        ),
    ]
    return combo


def select_spxw_put_credit(
    ib: IB,
    *,
    decision_day: pd.Timestamp,
    target_dte: int = 15,
    target_delta: float = -0.25,
    width: float = 10.0,
) -> PutCreditSelection:
    underlying = ib.qualifyContracts(Index("SPX", "CBOE", "USD"))[0]
    ticker = ib.reqMktData(underlying, "", False, False)
    ib.sleep(1.0)
    spot_candidates = (
        _finite(ticker.marketPrice()),
        _finite(getattr(ticker, "last", None)),
        _finite(getattr(ticker, "close", None)),
    )
    ib.cancelMktData(underlying)
    spot = next((value for value in spot_candidates if value is not None and value > 0), None)
    if spot is None:
        raise RuntimeError("no valid live SPX price")
    chain = _chains(ib, underlying)[0]
    expiry = _expiry(list(chain.expirations), decision_day, target_dte)
    strikes = sorted(float(value) for value in chain.strikes if value is not None)
    trading_class = str(getattr(chain, "tradingClass", "SPXW") or "SPXW")
    exchange = str(getattr(chain, "exchange", "SMART") or "SMART")
    multiplier = int(float(getattr(chain, "multiplier", 100) or 100))
    strike_set = set(strikes)
    candidates = [
        strike
        for strike in strikes
        if strike - width in strike_set and spot - 600.0 <= strike <= spot
    ]
    if not candidates:
        raise RuntimeError("SPXW chain contains no exact 10-point put verticals")
    # Query a broad but bounded OTM range. Delta, not spot distance, makes the final choice.
    candidates = candidates[-70:]
    requested = [
        _option(expiry, strike, trading_class, exchange, multiplier)
        for strike in candidates
    ]
    qualified = list(ib.qualifyContracts(*requested))
    quoted = [
        (float(contract.strike), contract, quote)
        for contract, quote in _quotes_many(ib, qualified)
        if quote.delta is not None and quote.delta < 0
    ]
    if not quoted:
        raise RuntimeError("no SPXW puts with valid model delta and bid/ask")
    short_strike, short_contract, short_quote = min(
        quoted, key=lambda item: abs(float(item[2].delta) - target_delta)
    )
    long_strike = short_strike - width
    quoted_by_strike = {strike: (contract, quote) for strike, contract, quote in quoted}
    cached_long = quoted_by_strike.get(long_strike)
    if cached_long is None:
        long_contracts = ib.qualifyContracts(
            _option(expiry, long_strike, trading_class, exchange, multiplier)
        )
        if not long_contracts:
            raise RuntimeError("exact long put strike could not be qualified")
        long_contract = long_contracts[0]
        long_quote = _quote(ib, long_contract)
    else:
        long_contract, long_quote = cached_long
    details = ib.reqContractDetails(short_contract) + ib.reqContractDetails(long_contract)
    ticks = [float(getattr(item, "minTick", 0.05) or 0.05) for item in details]
    min_tick = max([tick for tick in ticks if tick > 0] or [0.05])
    selection = PutCreditSelection(
        symbol="SPXW",
        expiry=expiry,
        short_strike=short_strike,
        long_strike=long_strike,
        short_contract=short_contract,
        long_contract=long_contract,
        short_quote=short_quote,
        long_quote=long_quote,
        combo_contract=build_vertical_combo(short_contract, long_contract),
        inverse_combo_contract=build_vertical_combo(short_contract, long_contract, inverse=True),
        min_tick=min_tick,
        multiplier=multiplier,
    )
    if selection.conservative_credit <= 0 or selection.conservative_credit >= width:
        raise RuntimeError(f"invalid conservative credit {selection.conservative_credit}")
    return selection


def selection_for_identity(
    ib: IB,
    *,
    expiry: str,
    short_strike: float,
    long_strike: float,
) -> PutCreditSelection:
    underlying = ib.qualifyContracts(Index("SPX", "CBOE", "USD"))[0]
    chain = _chains(ib, underlying)[0]
    trading_class = str(getattr(chain, "tradingClass", "SPXW") or "SPXW")
    exchange = str(getattr(chain, "exchange", "SMART") or "SMART")
    multiplier = int(float(getattr(chain, "multiplier", 100) or 100))
    contracts = ib.qualifyContracts(
        _option(expiry, short_strike, trading_class, exchange, multiplier),
        _option(expiry, long_strike, trading_class, exchange, multiplier),
    )
    if len(contracts) != 2:
        raise RuntimeError("saved SPXW spread identity could not be qualified")
    short_contract, long_contract = contracts
    short_quote, long_quote = _quote(ib, short_contract), _quote(ib, long_contract)
    details = ib.reqContractDetails(short_contract) + ib.reqContractDetails(long_contract)
    min_tick = max([float(getattr(item, "minTick", 0.05) or 0.05) for item in details] or [0.05])
    return PutCreditSelection(
        "SPXW", expiry, short_strike, long_strike, short_contract, long_contract,
        short_quote, long_quote, build_vertical_combo(short_contract, long_contract),
        build_vertical_combo(short_contract, long_contract, inverse=True), min_tick, multiplier,
    )


def build_entry_order(selection: PutCreditSelection, *, account: str, order_ref: str) -> LimitOrder:
    require_paper_account(account)
    credit = round_credit_down(selection.conservative_credit, selection.min_tick)
    order = LimitOrder("BUY", 1, -credit)
    order.account = account
    order.orderRef = order_ref[:45]
    order.tif = "DAY"
    order.outsideRth = False
    order.transmit = True
    return order


def build_exit_order(selection: PutCreditSelection, *, account: str, order_ref: str) -> LimitOrder:
    require_paper_account(account)
    debit = round_debit_up(selection.conservative_exit_debit, selection.min_tick)
    order = LimitOrder("BUY", 1, debit)
    order.account = account
    order.orderRef = order_ref[:45]
    order.tif = "DAY"
    order.outsideRth = False
    order.transmit = True
    return order
