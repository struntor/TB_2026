from __future__ import annotations

from dataclasses import dataclass
import math
import time
from typing import Any

import pandas as pd
from ib_insync import ComboLeg, Contract, IB, LimitOrder, Option, Stock


class StockPutCreditSelectionError(RuntimeError):
    """Expected live-selection rejection with a stable journal reason code."""

    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True, slots=True)
class StockPutExpirySelection:
    symbol: str
    expiry: str
    underlying: Contract
    spot: float
    strikes: tuple[float, ...]
    trading_class: str
    multiplier: int


@dataclass(frozen=True, slots=True)
class OptionQuote:
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
class StockPutCreditSelection:
    symbol: str
    expiry: str
    short_strike: float
    long_strike: float
    short_contract: Contract
    long_contract: Contract
    short_quote: OptionQuote
    long_quote: OptionQuote
    combo_contract: Contract
    inverse_combo_contract: Contract
    min_tick: float
    multiplier: int
    spot: float

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


def require_paper_account(account: str) -> None:
    if not str(account).upper().startswith("DU"):
        raise RuntimeError(
            "the stock IVR70 put-credit strategy may only use a DU paper account"
        )


def _quote_from_ticker(ticker: Any) -> OptionQuote | None:
    bid = _finite(getattr(ticker, "bid", None))
    ask = _finite(getattr(ticker, "ask", None))
    greeks = getattr(ticker, "modelGreeks", None)
    if bid is None or ask is None or bid <= 0 or ask < bid or greeks is None:
        return None
    return OptionQuote(
        bid,
        ask,
        _finite(getattr(greeks, "delta", None)),
        _finite(getattr(greeks, "gamma", None)),
        _finite(getattr(greeks, "theta", None)),
        _finite(getattr(greeks, "vega", None)),
        _finite(getattr(greeks, "impliedVol", None)),
    )


def _quotes_many(
    ib: IB, contracts: list[Contract], timeout: float = 5.0
) -> list[tuple[Contract, OptionQuote]]:
    tickers = [(contract, ib.reqMktData(contract, "", False, False)) for contract in contracts]
    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline:
            ib.sleep(0.2)
            if all(_quote_from_ticker(ticker) is not None for _, ticker in tickers):
                break
        return [
            (contract, quote)
            for contract, ticker in tickers
            if (quote := _quote_from_ticker(ticker)) is not None
        ]
    finally:
        for contract, _ in tickers:
            try:
                ib.cancelMktData(contract)
            except Exception:
                pass


def _chain(ib: IB, underlying: Contract) -> Any:
    chains = ib.reqSecDefOptParams(
        underlying.symbol, "", underlying.secType, underlying.conId
    )
    eligible = [
        chain
        for chain in chains
        if str(getattr(chain, "exchange", "")).upper() in {"SMART", "BOX", "CBOE"}
    ] or list(chains)
    if not eligible:
        raise StockPutCreditSelectionError(
            "missing_option_chain",
            f"IB returned no option chain for {underlying.symbol}",
        )
    eligible.sort(
        key=lambda chain: (
            str(getattr(chain, "exchange", "")).upper() != "SMART",
            -len(getattr(chain, "expirations", [])),
            -len(getattr(chain, "strikes", [])),
        )
    )
    return eligible[0]


def _expiry(
    expirations: list[str], day: pd.Timestamp, target_dte: int, min_dte: int, max_dte: int
) -> str:
    parsed = sorted(
        {
            pd.Timestamp(str(value).replace("-", "")).normalize()
            for value in expirations
        }
    )
    eligible = [value for value in parsed if min_dte <= (value - day).days <= max_dte]
    if not eligible:
        raise StockPutCreditSelectionError(
            "no_expiration_in_dte_window",
            f"option chain has no expiration between {min_dte} and {max_dte} DTE",
        )
    target = day + pd.Timedelta(days=target_dte)
    return min(eligible, key=lambda value: (abs((value - target).days), value)).strftime("%Y%m%d")


def _option(symbol: str, expiry: str, strike: float, trading_class: str, multiplier: int) -> Option:
    return Option(
        symbol,
        expiry,
        float(strike),
        "P",
        "SMART",
        multiplier=str(multiplier),
        currency="USD",
        tradingClass=trading_class,
    )


def build_vertical_combo(
    symbol: str,
    short_contract: Contract,
    long_contract: Contract,
    *,
    inverse: bool = False,
) -> Contract:
    if any(int(getattr(contract, "conId", 0) or 0) <= 0 for contract in (short_contract, long_contract)):
        raise ValueError("qualified option conIds are required")
    short_action, long_action = (("BUY", "SELL") if inverse else ("SELL", "BUY"))
    combo = Contract(symbol=symbol, secType="BAG", currency="USD", exchange="SMART")
    combo.comboLegs = [
        ComboLeg(conId=int(short_contract.conId), ratio=1, action=short_action, exchange="SMART"),
        ComboLeg(conId=int(long_contract.conId), ratio=1, action=long_action, exchange="SMART"),
    ]
    return combo


def select_stock_put_expiry(
    ib: IB,
    *,
    symbol: str,
    decision_day: pd.Timestamp,
    target_dte: int,
    min_dte: int,
    max_dte: int,
) -> StockPutExpirySelection:
    qualified = ib.qualifyContracts(Stock(symbol, "SMART", "USD"))
    if not qualified:
        raise StockPutCreditSelectionError(
            "missing_underlying_contract",
            f"IB could not qualify stock contract for {symbol}",
        )
    underlying = qualified[0]
    ticker = ib.reqMktData(underlying, "", False, False)
    ib.sleep(1.0)
    try:
        spot = next(
            (
                value
                for value in (
                    _finite(ticker.marketPrice()),
                    _finite(getattr(ticker, "last", None)),
                    _finite(getattr(ticker, "close", None)),
                )
                if value is not None and value > 0
            ),
            None,
        )
    finally:
        ib.cancelMktData(underlying)
    if spot is None:
        raise StockPutCreditSelectionError(
            "missing_underlying_quote",
            f"no valid live stock price for {symbol}",
        )
    chain = _chain(ib, underlying)
    expiry = _expiry(
        list(chain.expirations),
        decision_day.normalize(),
        target_dte,
        min_dte,
        max_dte,
    )
    return StockPutExpirySelection(
        symbol=symbol,
        expiry=expiry,
        underlying=underlying,
        spot=float(spot),
        strikes=tuple(sorted(float(value) for value in chain.strikes if value is not None)),
        trading_class=str(getattr(chain, "tradingClass", symbol) or symbol),
        multiplier=int(float(getattr(chain, "multiplier", 100) or 100)),
    )


def _select_quoted_vertical(
    quoted_by_strike: dict[float, tuple[Contract, OptionQuote]],
    *,
    target_delta: float,
    minimum_abs_delta: float,
    maximum_abs_delta: float,
    width: float,
) -> tuple[float, Contract, OptionQuote, float, Contract, OptionQuote]:
    complete = []
    for short_strike, (short_contract, short_quote) in quoted_by_strike.items():
        long_strike = short_strike - width
        long_item = quoted_by_strike.get(long_strike)
        if long_item is None or short_quote.delta is None or short_quote.delta >= 0:
            continue
        long_contract, long_quote = long_item
        complete.append(
            (
                short_strike,
                short_contract,
                short_quote,
                long_strike,
                long_contract,
                long_quote,
            )
        )
    if not complete:
        raise StockPutCreditSelectionError(
            "missing_option_quotes",
            "no exact-width put pair has complete live quotes and short delta",
        )
    in_delta_range = [
        item
        for item in complete
        if minimum_abs_delta <= abs(float(item[2].delta)) <= maximum_abs_delta
    ]
    if not in_delta_range:
        observed = sorted(abs(float(item[2].delta)) for item in complete)
        raise StockPutCreditSelectionError(
            "no_short_delta_in_range",
            f"no exact-width put pair has short delta in "
            f"[{minimum_abs_delta}, {maximum_abs_delta}]; observed={observed}",
        )
    chosen = min(
        in_delta_range,
        key=lambda item: (
            abs(float(item[2].delta) - target_delta),
            item[0],
        ),
    )
    conservative_credit = chosen[2].bid - chosen[5].ask
    if not 0 < conservative_credit < width:
        raise StockPutCreditSelectionError(
            "no_positive_conservative_credit",
            "delta-nearest exact-width put pair has no positive conservative "
            f"credit: short_delta={chosen[2].delta} credit={conservative_credit}",
        )
    return chosen


def select_stock_put_credit(
    ib: IB,
    *,
    symbol: str,
    decision_day: pd.Timestamp,
    target_dte: int,
    min_dte: int,
    max_dte: int,
    target_delta: float,
    minimum_abs_delta: float,
    maximum_abs_delta: float,
    width: float,
    expiry_selection: StockPutExpirySelection | None = None,
) -> StockPutCreditSelection:
    selected_expiry = expiry_selection or select_stock_put_expiry(
        ib,
        symbol=symbol,
        decision_day=decision_day,
        target_dte=target_dte,
        min_dte=min_dte,
        max_dte=max_dte,
    )
    if selected_expiry.symbol.upper() != symbol.upper():
        raise ValueError("expiry_selection symbol does not match requested symbol")
    expiry = selected_expiry.expiry
    spot = selected_expiry.spot
    strikes = list(selected_expiry.strikes)
    strike_set = set(strikes)
    candidate_shorts = [
        strike
        for strike in strikes
        if strike - width in strike_set and max(0.0, spot * 0.45) <= strike <= spot
    ][-80:]
    if not candidate_shorts:
        raise StockPutCreditSelectionError(
            "no_exact_width_pair",
            f"{symbol} chain contains no possible {width:g}-point put verticals",
        )
    requested_strikes = sorted(
        set(candidate_shorts) | {strike - width for strike in candidate_shorts}
    )
    qualified = list(
        ib.qualifyContracts(
            *[
                _option(
                    symbol,
                    expiry,
                    strike,
                    selected_expiry.trading_class,
                    selected_expiry.multiplier,
                )
                for strike in requested_strikes
            ]
        )
    )
    qualified_by_strike = {float(contract.strike): contract for contract in qualified}
    actual_pair_strikes = {
        strike
        for strike in candidate_shorts
        if strike in qualified_by_strike and strike - width in qualified_by_strike
    }
    if not actual_pair_strikes:
        raise StockPutCreditSelectionError(
            "no_exact_width_pair",
            f"{symbol} expiry {expiry} has no qualified {width:g}-point put pair",
        )
    quote_contracts = [
        contract
        for strike, contract in qualified_by_strike.items()
        if strike in actual_pair_strikes or strike + width in actual_pair_strikes
    ]
    quoted_by_strike = {
        float(contract.strike): (contract, quote)
        for contract, quote in _quotes_many(ib, quote_contracts)
    }
    (
        short_strike,
        short_contract,
        short_quote,
        long_strike,
        long_contract,
        long_quote,
    ) = _select_quoted_vertical(
        quoted_by_strike,
        target_delta=target_delta,
        minimum_abs_delta=minimum_abs_delta,
        maximum_abs_delta=maximum_abs_delta,
        width=width,
    )
    details = ib.reqContractDetails(short_contract) + ib.reqContractDetails(long_contract)
    min_tick = max(
        [float(getattr(item, "minTick", 0.01) or 0.01) for item in details] or [0.01]
    )
    selection = StockPutCreditSelection(
        symbol,
        expiry,
        short_strike,
        long_strike,
        short_contract,
        long_contract,
        short_quote,
        long_quote,
        build_vertical_combo(symbol, short_contract, long_contract),
        build_vertical_combo(symbol, short_contract, long_contract, inverse=True),
        min_tick,
        selected_expiry.multiplier,
        spot,
    )
    return selection


def selection_for_identity(
    ib: IB,
    *,
    symbol: str,
    expiry: str,
    short_strike: float,
    long_strike: float,
    short_con_id: int,
    long_con_id: int,
) -> StockPutCreditSelection:
    underlying = ib.qualifyContracts(Stock(symbol, "SMART", "USD"))[0]
    ticker = ib.reqMktData(underlying, "", False, False)
    ib.sleep(0.5)
    try:
        spot = next(
            (
                value
                for value in (
                    _finite(ticker.marketPrice()),
                    _finite(getattr(ticker, "last", None)),
                    _finite(getattr(ticker, "close", None)),
                )
                if value is not None and value > 0
            ),
            0.0,
        )
    finally:
        ib.cancelMktData(underlying)
    contracts = ib.qualifyContracts(
        Contract(conId=int(short_con_id), exchange="SMART"),
        Contract(conId=int(long_con_id), exchange="SMART"),
    )
    if len(contracts) != 2:
        raise RuntimeError(f"saved {symbol} option identity could not be qualified")
    short_contract, long_contract = contracts
    observed = (
        str(getattr(short_contract, "symbol", "") or "").upper(),
        str(getattr(short_contract, "lastTradeDateOrContractMonth", "") or "")[:8],
        float(getattr(short_contract, "strike", 0.0) or 0.0),
        str(getattr(short_contract, "right", "") or "").upper(),
        str(getattr(long_contract, "symbol", "") or "").upper(),
        str(getattr(long_contract, "lastTradeDateOrContractMonth", "") or "")[:8],
        float(getattr(long_contract, "strike", 0.0) or 0.0),
        str(getattr(long_contract, "right", "") or "").upper(),
    )
    expected = (
        symbol.upper(), expiry.replace("-", "")[:8], float(short_strike), "P",
        symbol.upper(), expiry.replace("-", "")[:8], float(long_strike), "P",
    )
    if observed != expected:
        raise RuntimeError(
            f"saved {symbol} option contract identity mismatch: "
            f"expected={expected} observed={observed}"
        )
    quotes = _quotes_many(ib, list(contracts))
    if len(quotes) != 2:
        raise RuntimeError(f"saved {symbol} option identity has incomplete quotes")
    by_id = {int(contract.conId): quote for contract, quote in quotes}
    short_quote, long_quote = by_id[int(short_contract.conId)], by_id[int(long_contract.conId)]
    multiplier = int(float(getattr(short_contract, "multiplier", 100) or 100))
    details = ib.reqContractDetails(short_contract) + ib.reqContractDetails(long_contract)
    min_tick = max([float(getattr(item, "minTick", 0.01) or 0.01) for item in details] or [0.01])
    return StockPutCreditSelection(
        symbol, expiry, short_strike, long_strike, short_contract, long_contract,
        short_quote, long_quote,
        build_vertical_combo(symbol, short_contract, long_contract),
        build_vertical_combo(symbol, short_contract, long_contract, inverse=True),
        min_tick, multiplier, float(spot),
    )


def _round_down(value: float, tick: float) -> float:
    return round(math.floor((value + 1e-12) / tick) * tick, 8)


def _round_up(value: float, tick: float) -> float:
    return round(math.ceil((value - 1e-12) / tick) * tick, 8)


def build_entry_order(
    selection: StockPutCreditSelection, *, account: str, order_ref: str
) -> LimitOrder:
    require_paper_account(account)
    order = LimitOrder("BUY", 1, -_round_down(selection.conservative_credit, selection.min_tick))
    order.account, order.orderRef, order.tif = account, order_ref[:45], "DAY"
    order.outsideRth, order.transmit = False, True
    return order


def build_exit_order(
    selection: StockPutCreditSelection, *, account: str, order_ref: str
) -> LimitOrder:
    require_paper_account(account)
    order = LimitOrder("BUY", 1, _round_up(selection.conservative_exit_debit, selection.min_tick))
    order.account, order.orderRef, order.tif = account, order_ref[:45], "DAY"
    order.outsideRth, order.transmit = False, True
    return order
