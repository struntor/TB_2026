from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import time

import pandas as pd
from ib_insync import ComboLeg, Contract, IB, LimitOrder, Option

from src.data.ib_market_data import build_contract


US_TZ = "America/New_York"
DEFAULT_OPTION_EXCHANGE = "SMART"
DEFAULT_COMBO_EXCHANGE = "SMART"
OPTION_MULTIPLIER = 100


@dataclass(frozen=True)
class ZebraOptionQuote:
    bid: float
    ask: float
    last: float | None
    close: float | None
    delta: float | None

    @property
    def mid(self) -> float:
        return float((self.bid + self.ask) / 2.0)


@dataclass(frozen=True)
class ZebraSelection:
    symbol: str
    side: str
    right: str
    spot: float
    expiry: str
    atm_strike: float
    short_strike: float
    long_strike: float
    short_quote: ZebraOptionQuote
    long_quote: ZebraOptionQuote
    entry_mid: float
    entry_limit: float
    intrinsic_value: float
    extrinsic_gap: float
    net_delta: float | None
    delta_error: float | None
    min_tick: float
    short_contract: Contract
    long_contract: Contract
    combo_contract: Contract

    @property
    def entry_debit_usd(self) -> float:
        return self.entry_mid * OPTION_MULTIPLIER


def _to_naive_timestamp(value: object) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is not None:
        stamp = stamp.tz_convert(US_TZ).tz_localize(None)
    return stamp


def _round_to_tick(price: float, tick: float) -> float:
    if tick is None or pd.isna(tick):
        return round(float(price), 2)
    tick_value = float(tick)
    if tick_value <= 0:
        return round(float(price), 2)
    steps = round(float(price) / tick_value)
    return round(steps * tick_value, 2)


def _safe_option_multiplier(value: object, default: int = OPTION_MULTIPLIER) -> int:
    if value in (None, ""):
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if pd.isna(parsed) or parsed <= 0:
        return default
    return int(parsed)


def _load_underlying_contract(ib: IB, symbol: str) -> Contract:
    underlying, _ = build_contract(symbol, "US")
    qualified = ib.qualifyContracts(underlying)
    if not qualified:
        raise RuntimeError(f"Unable to qualify underlying contract for {symbol}")
    return qualified[0]


def _market_price_from_ticker(ticker: Any) -> float:
    price = getattr(ticker, "marketPrice", None)
    if callable(price):
        value = float(price())
        if not pd.isna(value) and value > 0:
            return value

    last = float(getattr(ticker, "last", 0.0) or 0.0)
    bid = float(getattr(ticker, "bid", 0.0) or 0.0)
    ask = float(getattr(ticker, "ask", 0.0) or 0.0)
    close = float(getattr(ticker, "close", 0.0) or 0.0)
    if pd.isna(last):
        last = 0.0
    if pd.isna(bid):
        bid = 0.0
    if pd.isna(ask):
        ask = 0.0
    if pd.isna(close):
        close = 0.0

    if bid > 0 and ask > 0:
        return (bid + ask) / 2.0
    if last > 0:
        return last
    if close > 0:
        return close
    if bid > 0:
        return bid
    if ask > 0:
        return ask
    raise RuntimeError("Unable to resolve a live SPX spot price")


def _snapshot_spot_price(ib: IB, underlying: Contract, timeout_seconds: float = 3.0) -> float:
    ticker = ib.reqMktData(underlying, "", False, False)
    deadline = time.monotonic() + timeout_seconds
    try:
        while time.monotonic() < deadline:
            ib.sleep(0.2)
            try:
                return _market_price_from_ticker(ticker)
            except RuntimeError:
                continue
        return _market_price_from_ticker(ticker)
    finally:
        try:
            ib.cancelMktData(underlying)
        except Exception:
            pass


def _resolve_option_chains(ib: IB, underlying: Contract) -> list[Any]:
    chains = ib.reqSecDefOptParams(
        underlyingSymbol=underlying.symbol,
        futFopExchange="",
        underlyingSecType=underlying.secType,
        underlyingConId=underlying.conId,
    )
    symbol = str(getattr(underlying, "symbol", "")).upper()
    preferred_trading_classes = {symbol}
    if symbol == "SPX":
        preferred_trading_classes.add("SPXW")
    preferred = [chain for chain in chains if str(getattr(chain, "tradingClass", "")).upper() in preferred_trading_classes]
    if not preferred:
        preferred = list(chains)
    if not preferred:
        raise RuntimeError(f"No option chain returned by IB for {symbol}")

    exchange_rank = {"SMART": 0, "CBOE": 1, "CBOE2": 2, "GEMINI": 3}
    preferred.sort(
        key=lambda chain: (
            str(getattr(chain, "tradingClass", "")).upper() not in preferred_trading_classes,
            exchange_rank.get(str(getattr(chain, "exchange", "")).upper(), 99),
            -len(getattr(chain, "expirations", [])),
            -len(getattr(chain, "strikes", [])),
        )
    )
    return preferred


def _expiry_candidates(expirations: list[str], target_day: pd.Timestamp, limit: int = 3) -> list[str]:
    parsed = sorted({pd.Timestamp(exp).normalize() for exp in expirations})
    if not parsed:
        return []

    candidates = sorted(
        parsed,
        key=lambda exp: (abs((exp - target_day).days), exp < target_day, exp),
    )
    return [exp.strftime("%Y%m%d") for exp in candidates[:limit]]


def _option_contract(
    symbol: str,
    expiry: str,
    strike: float,
    right: str,
    *,
    trading_class: str,
    exchange: str = DEFAULT_OPTION_EXCHANGE,
    multiplier: int = OPTION_MULTIPLIER,
) -> Option:
    return Option(
        symbol=symbol,
        lastTradeDateOrContractMonth=expiry,
        strike=float(strike),
        right=right,
        exchange=exchange,
        currency="USD",
        multiplier=str(multiplier),
        tradingClass=trading_class,
    )


def _quote_option(
    ib: IB,
    contract: Contract,
    timeout_seconds: float = 2.0,
    *,
    allow_close_fallback: bool = False,
) -> ZebraOptionQuote:
    ticker = ib.reqMktData(contract, "", False, False)
    deadline = time.monotonic() + timeout_seconds
    try:
        while time.monotonic() < deadline:
            ib.sleep(0.2)
            bid = float(getattr(ticker, "bid", 0.0) or 0.0)
            ask = float(getattr(ticker, "ask", 0.0) or 0.0)
            if pd.isna(bid):
                bid = 0.0
            if pd.isna(ask):
                ask = 0.0
            if bid > 0 and ask > 0:
                break

        bid = float(getattr(ticker, "bid", 0.0) or 0.0)
        ask = float(getattr(ticker, "ask", 0.0) or 0.0)
        if pd.isna(bid):
            bid = 0.0
        if pd.isna(ask):
            ask = 0.0
        greeks = getattr(ticker, "modelGreeks", None)
        delta = None
        if greeks is not None and getattr(greeks, "delta", None) is not None:
            delta = float(greeks.delta)

        last = getattr(ticker, "last", None)
        close = getattr(ticker, "close", None)
        if last is not None and pd.isna(last):
            last = None
        if close is not None and pd.isna(close):
            close = None
        if bid <= 0 or ask <= 0:
            fallback = None
            if allow_close_fallback:
                if last not in (None, "") and float(last) > 0:
                    fallback = float(last)
                elif close not in (None, "") and float(close) > 0:
                    fallback = float(close)
            if fallback is None:
                raise RuntimeError(f"No valid bid/ask for {getattr(contract, 'localSymbol', '') or contract}")
            bid = fallback
            ask = fallback
        return ZebraOptionQuote(
            bid=bid,
            ask=ask,
            last=float(last) if last not in (None, "") else None,
            close=float(close) if close not in (None, "") else None,
            delta=delta,
        )
    finally:
        try:
            ib.cancelMktData(contract)
        except Exception:
            pass


def select_spx_zebra(
    ib: IB,
    *,
    symbol: str = "SPX",
    side: str = "long",
    target_dte: int = 90,
    strike_window_points: int = 250,
    expiry_candidates: int = 3,
    max_debit_usd: float | None = None,
) -> ZebraSelection:
    return select_zebra(
        ib,
        symbol=symbol,
        side=side,
        target_dte=target_dte,
        strike_window_points=strike_window_points,
        expiry_candidates=expiry_candidates,
        max_debit_usd=max_debit_usd,
    )


def select_zebra(
    ib: IB,
    *,
    symbol: str,
    side: str = "long",
    target_dte: int = 90,
    strike_window_points: int = 250,
    expiry_candidates: int = 3,
    max_debit_usd: float | None = None,
    allow_close_fallback: bool = False,
) -> ZebraSelection:
    safe_side = side.lower().strip()
    if safe_side not in {"long", "short"}:
        raise ValueError("side must be 'long' or 'short'")

    right = "CALL" if safe_side == "long" else "PUT"
    safe_symbol = symbol.upper().strip()
    underlying = _load_underlying_contract(ib, safe_symbol)
    spot = _snapshot_spot_price(ib, underlying)
    target_day = _to_naive_timestamp(pd.Timestamp.now(tz=US_TZ).normalize() + pd.Timedelta(days=target_dte)).normalize()
    chains = _resolve_option_chains(ib, underlying)

    for chain in chains:
        strikes = sorted(
            float(value)
            for value in getattr(chain, "strikes", [])
            if value is not None and pd.notna(value)
        )
        if not strikes:
            continue

        atm_strike = min(strikes, key=lambda strike: abs(float(strike) - float(spot)))
        expirations = _expiry_candidates(list(getattr(chain, "expirations", [])), target_day, limit=expiry_candidates)
        if not expirations:
            continue

        option_exchange = str(getattr(chain, "exchange", "") or DEFAULT_OPTION_EXCHANGE).upper()
        trading_class = str(getattr(chain, "tradingClass", safe_symbol) or safe_symbol).upper()
        option_multiplier = _safe_option_multiplier(getattr(chain, "multiplier", OPTION_MULTIPLIER))

        short_candidates = [
            strike
            for strike in strikes
            if abs(float(strike) - float(spot)) <= float(strike_window_points)
        ]
        if safe_side == "long":
            short_candidates.sort(key=lambda strike: (0 if strike >= spot else 1, abs(float(strike) - float(spot)), float(strike)))
        else:
            short_candidates.sort(key=lambda strike: (0 if strike <= spot else 1, abs(float(strike) - float(spot)), -float(strike)))
        short_candidates = short_candidates[:30]

        for expiry in expirations:
            for short_strike in short_candidates:
                short_contract = _option_contract(
                    safe_symbol,
                    expiry,
                    short_strike,
                    right[0],
                    trading_class=trading_class,
                    exchange=option_exchange,
                    multiplier=option_multiplier,
                )
                qualified_short = ib.qualifyContracts(short_contract)
                if not qualified_short:
                    continue
                short_contract = qualified_short[0]

                try:
                    short_quote = _quote_option(ib, short_contract, allow_close_fallback=allow_close_fallback)
                except Exception:
                    continue

                if safe_side == "long":
                    long_candidates = [
                        strike
                        for strike in strikes
                        if strike < short_strike and (short_strike - strike) <= strike_window_points
                    ]
                    long_candidates.sort(key=lambda strike: (abs(float(strike) - float(short_strike)), float(strike)))
                else:
                    long_candidates = [
                        strike
                        for strike in strikes
                        if strike > short_strike and (strike - short_strike) <= strike_window_points
                    ]
                    long_candidates.sort(key=lambda strike: (abs(float(strike) - float(short_strike)), float(strike)))
                long_candidates = long_candidates[:30]

                for long_strike in long_candidates:
                    long_contract = _option_contract(
                        safe_symbol,
                        expiry,
                        long_strike,
                        right[0],
                        trading_class=trading_class,
                        exchange=option_exchange,
                        multiplier=option_multiplier,
                    )
                    qualified_long = ib.qualifyContracts(long_contract)
                    if not qualified_long:
                        continue
                    long_contract = qualified_long[0]

                    try:
                        long_quote = _quote_option(ib, long_contract, allow_close_fallback=allow_close_fallback)
                    except Exception:
                        continue

                    short_details = ib.reqContractDetails(short_contract)
                    long_details = ib.reqContractDetails(long_contract)
                    if not short_details or not long_details:
                        continue
                    short_min_tick = float(getattr(short_details[0], "minTick", 0.01) or 0.01)
                    long_min_tick = float(getattr(long_details[0], "minTick", 0.01) or 0.01)
                    if pd.isna(short_min_tick) or short_min_tick <= 0:
                        short_min_tick = 0.01
                    if pd.isna(long_min_tick) or long_min_tick <= 0:
                        long_min_tick = 0.01
                    min_tick = max(short_min_tick, long_min_tick)
                    if pd.isna(min_tick) or min_tick <= 0:
                        min_tick = 0.01

                    if safe_side == "long":
                        intrinsic_value = 2.0 * max(float(spot) - float(long_strike), 0.0) - max(float(spot) - float(short_strike), 0.0)
                    else:
                        intrinsic_value = 2.0 * max(float(long_strike) - float(spot), 0.0) - max(float(short_strike) - float(spot), 0.0)

                    entry_mid = 2.0 * float(long_quote.mid) - float(short_quote.mid)
                    if entry_mid <= 0:
                        continue
                    entry_debit_usd = entry_mid * option_multiplier
                    if max_debit_usd is not None and entry_debit_usd > float(max_debit_usd):
                        continue

                    extrinsic_gap = abs(entry_mid - intrinsic_value)
                    net_delta = None
                    delta_error = None
                    if long_quote.delta is not None and short_quote.delta is not None:
                        net_delta = 2.0 * float(long_quote.delta) - float(short_quote.delta)
                        delta_error = abs(net_delta - 1.0)

                    combo = build_zebra_combo(
                        short_contract,
                        long_contract,
                        symbol=safe_symbol,
                        side=safe_side,
                        exchange=DEFAULT_COMBO_EXCHANGE,
                    )
                    safe_min_tick = float(min_tick) if not pd.isna(min_tick) and float(min_tick) > 0 else 0.01
                    limit_price = _round_to_tick(entry_mid, safe_min_tick)
                    return ZebraSelection(
                        symbol=safe_symbol,
                        side=safe_side,
                        right=right,
                        spot=float(spot),
                        expiry=str(expiry),
                        atm_strike=float(atm_strike),
                        short_strike=float(short_strike),
                        long_strike=float(long_strike),
                        short_quote=short_quote,
                        long_quote=long_quote,
                        entry_mid=float(entry_mid),
                        entry_limit=float(limit_price),
                        intrinsic_value=float(intrinsic_value),
                        extrinsic_gap=float(extrinsic_gap),
                        net_delta=net_delta,
                        delta_error=delta_error,
                        min_tick=safe_min_tick,
                        short_contract=short_contract,
                        long_contract=long_contract,
                        combo_contract=combo,
                    )

    raise RuntimeError(
        f"Unable to find a valid {safe_symbol} ZEBRA candidate with the current live chain and quote window"
    )


def select_zebra_for_strikes(
    ib: IB,
    *,
    symbol: str,
    side: str,
    expiry: str,
    short_strike: float,
    long_strike: float,
    max_debit_usd: float | None = None,
    allow_close_fallback: bool = False,
) -> ZebraSelection:
    safe_side = side.lower().strip()
    if safe_side not in {"long", "short"}:
        raise ValueError("side must be 'long' or 'short'")

    safe_symbol = symbol.upper().strip()
    right = "CALL" if safe_side == "long" else "PUT"
    underlying = _load_underlying_contract(ib, safe_symbol)
    spot = _snapshot_spot_price(ib, underlying)
    chain = _resolve_option_chains(ib, underlying)[0]
    strikes = sorted(float(value) for value in getattr(chain, "strikes", []) if value is not None and pd.notna(value))
    atm_strike = min(strikes, key=lambda strike: abs(float(strike) - float(spot))) if strikes else float(short_strike)
    option_multiplier = _safe_option_multiplier(getattr(chain, "multiplier", OPTION_MULTIPLIER))
    trading_class = str(getattr(chain, "tradingClass", safe_symbol) or safe_symbol).upper()

    short_contract = _option_contract(
        safe_symbol,
        str(expiry),
        float(short_strike),
        right[0],
        trading_class=trading_class,
        exchange=DEFAULT_OPTION_EXCHANGE,
        multiplier=option_multiplier,
    )
    long_contract = _option_contract(
        safe_symbol,
        str(expiry),
        float(long_strike),
        right[0],
        trading_class=trading_class,
        exchange=DEFAULT_OPTION_EXCHANGE,
        multiplier=option_multiplier,
    )
    qualified = ib.qualifyContracts(short_contract, long_contract)
    if len(qualified) != 2:
        raise RuntimeError(f"Unable to qualify ORCL ZEBRA legs for {expiry} {long_strike}/{short_strike}")
    short_contract, long_contract = qualified
    short_quote = _quote_option(ib, short_contract, allow_close_fallback=allow_close_fallback)
    long_quote = _quote_option(ib, long_contract, allow_close_fallback=allow_close_fallback)

    short_details = ib.reqContractDetails(short_contract)
    long_details = ib.reqContractDetails(long_contract)
    short_min_tick = float(getattr(short_details[0], "minTick", 0.01) or 0.01) if short_details else 0.01
    long_min_tick = float(getattr(long_details[0], "minTick", 0.01) or 0.01) if long_details else 0.01
    min_tick = max(short_min_tick, long_min_tick, 0.01)

    if safe_side == "long":
        intrinsic_value = 2.0 * max(float(spot) - float(long_strike), 0.0) - max(float(spot) - float(short_strike), 0.0)
    else:
        intrinsic_value = 2.0 * max(float(long_strike) - float(spot), 0.0) - max(float(short_strike) - float(spot), 0.0)
    entry_mid = 2.0 * float(long_quote.mid) - float(short_quote.mid)
    if entry_mid <= 0:
        raise RuntimeError(f"Selected ZEBRA legs produce non-positive debit: {entry_mid:.2f}")
    entry_debit_usd = entry_mid * option_multiplier
    if max_debit_usd is not None and entry_debit_usd > float(max_debit_usd):
        raise RuntimeError(f"Selected ZEBRA debit {entry_debit_usd:.2f} exceeds max_debit_usd {max_debit_usd:.2f}")

    net_delta = None
    delta_error = None
    if long_quote.delta is not None and short_quote.delta is not None:
        net_delta = 2.0 * float(long_quote.delta) - float(short_quote.delta)
        delta_error = abs(net_delta - 1.0)

    combo = build_zebra_combo(
        short_contract,
        long_contract,
        symbol=safe_symbol,
        side=safe_side,
        exchange=DEFAULT_COMBO_EXCHANGE,
    )
    return ZebraSelection(
        symbol=safe_symbol,
        side=safe_side,
        right=right,
        spot=float(spot),
        expiry=str(expiry),
        atm_strike=float(atm_strike),
        short_strike=float(short_strike),
        long_strike=float(long_strike),
        short_quote=short_quote,
        long_quote=long_quote,
        entry_mid=float(entry_mid),
        entry_limit=float(_round_to_tick(entry_mid, min_tick)),
        intrinsic_value=float(intrinsic_value),
        extrinsic_gap=float(abs(entry_mid - intrinsic_value)),
        net_delta=net_delta,
        delta_error=delta_error,
        min_tick=float(min_tick),
        short_contract=short_contract,
        long_contract=long_contract,
        combo_contract=combo,
    )


def build_zebra_combo(
    short_contract: Contract,
    long_contract: Contract,
    *,
    symbol: str,
    side: str = "long",
    exchange: str = DEFAULT_COMBO_EXCHANGE,
) -> Contract:
    safe_side = side.lower().strip()
    if safe_side not in {"long", "short"}:
        raise ValueError("side must be 'long' or 'short'")

    if safe_side == "long":
        long_action = "BUY"
        short_action = "SELL"
    else:
        long_action = "BUY"
        short_action = "SELL"

    long_leg = ComboLeg(
        conId=int(long_contract.conId),
        ratio=2,
        action=long_action,
        exchange=str(getattr(long_contract, "exchange", "") or exchange),
    )
    short_leg = ComboLeg(
        conId=int(short_contract.conId),
        ratio=1,
        action=short_action,
        exchange=str(getattr(short_contract, "exchange", "") or exchange),
    )

    combo = Contract()
    combo.symbol = symbol
    combo.secType = "BAG"
    combo.currency = "USD"
    combo.exchange = exchange
    combo.comboLegs = [long_leg, short_leg]
    return combo


def build_zebra_entry_order(
    selection: ZebraSelection,
    *,
    quantity: int = 1,
    account: str | None = None,
    order_ref: str = "",
) -> LimitOrder:
    if quantity < 1:
        raise ValueError("quantity must be >= 1")

    order = LimitOrder("BUY", quantity, float(selection.entry_limit))
    order.tif = "DAY"
    order.transmit = False
    order.outsideRth = False
    if account:
        order.account = account
    if order_ref:
        order.orderRef = order_ref
    return order
