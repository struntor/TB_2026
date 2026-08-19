from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ib_insync import Index, Stock


US_INDEX_EXCHANGES = {
    "SPX": "CBOE",
    "NDX": "NASDAQ",
    "VIX": "CBOE",
}


@dataclass(frozen=True)
class MarketDataSubscription:
    symbol: str
    contract: Any
    ticker: Any
    mode: str


def build_contract(symbol: str, market: str = "US") -> tuple[Any, str]:
    s = symbol.upper()
    m = market.upper()

    if m == "US" and s in US_INDEX_EXCHANGES:
        return Index(s, US_INDEX_EXCHANGES[s], "USD"), "TRADES"

    if m == "US":
        return Stock(s, "SMART", "USD"), "TRADES"

    raise ValueError(f"Unsupported market '{market}' for symbol '{symbol}'.")

