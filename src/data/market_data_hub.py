from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd


US_TZ = ZoneInfo("America/New_York")


@dataclass
class FiveMinuteBarCollector:
    symbol: str
    bars: list[dict[str, Any]] = field(default_factory=list)
    _current_bucket_start: datetime | None = None
    _current_bucket: dict[str, Any] | None = None
    _seen_count: int = 0

    @staticmethod
    def _bucket_start(ts: Any) -> datetime:
        ts = pd.Timestamp(ts).to_pydatetime()
        minute = (ts.minute // 5) * 5
        return ts.replace(minute=minute, second=0, microsecond=0)

    def add_rt_bar(self, bar: Any) -> dict[str, Any] | None:
        ts = pd.Timestamp(bar.time).to_pydatetime()
        bucket_start = self._bucket_start(ts)
        emitted = None
        open_price = float(getattr(bar, "open_", getattr(bar, "open", 0.0)))

        if self._current_bucket_start is None:
            self._current_bucket_start = bucket_start
            self._current_bucket = self._new_bucket(bar, bucket_start, open_price)
            return None

        if bucket_start != self._current_bucket_start:
            emitted = self._current_bucket
            self.bars.append(emitted)
            self._current_bucket_start = bucket_start
            self._current_bucket = self._new_bucket(bar, bucket_start, open_price)
            return emitted

        bucket = self._current_bucket
        if bucket is None:
            self._current_bucket = self._new_bucket(bar, bucket_start, open_price)
            return None

        bucket["high"] = max(bucket["high"], float(bar.high))
        bucket["low"] = min(bucket["low"], float(bar.low))
        bucket["close"] = float(bar.close)
        bucket["volume"] += float(bar.volume)
        bucket["bar_count"] += 1
        bucket["end"] = ts
        return emitted

    def flush(self) -> dict[str, Any] | None:
        if self._current_bucket is None:
            return None
        emitted = self._current_bucket
        self.bars.append(emitted)
        self._current_bucket = None
        self._current_bucket_start = None
        return emitted

    def _new_bucket(self, bar: Any, bucket_start: datetime, open_price: float) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "start": bucket_start,
            "end": pd.Timestamp(bar.time).to_pydatetime(),
            "open": open_price,
            "high": float(bar.high),
            "low": float(bar.low),
            "close": float(bar.close),
            "volume": float(bar.volume),
            "bar_count": 1,
        }


@dataclass
class MarketDataHub:
    ib: Any
    subscriptions: dict[str, tuple[str, Any]] = field(default_factory=dict)
    collectors: dict[str, FiveMinuteBarCollector] = field(default_factory=dict)

    def subscribe(self, symbol: str, contract: Any, what_to_show: str = "TRADES") -> Any:
        symbol = symbol.upper()
        if symbol in self.subscriptions:
            return self.subscriptions[symbol][1]
        ticker = self.ib.reqMktData(contract, genericTickList="", snapshot=False, regulatorySnapshot=False)
        self.subscriptions[symbol] = ("mktdata", ticker)
        return ticker

    def subscribe_realtime_bars(self, symbol: str, contract: Any, what_to_show: str = "TRADES") -> Any:
        symbol = symbol.upper()
        if symbol in self.subscriptions:
            return self.subscriptions[symbol][1]
        bars = self.ib.reqRealTimeBars(contract, 5, what_to_show, True)
        collector = self.collectors.get(symbol)
        if collector is None:
            collector = FiveMinuteBarCollector(symbol=symbol)
        self.collectors[symbol] = collector

        def _on_update(*args: Any) -> None:
            if not args:
                return
            bar_list = args[0]
            for bar in list(bar_list)[collector._seen_count :]:
                emitted = collector.add_rt_bar(bar)
                collector._seen_count += 1
                if emitted is not None:
                    print(
                        f"bar5m {symbol} start={emitted['start']} end={emitted['end']} "
                        f"o={emitted['open']} h={emitted['high']} l={emitted['low']} c={emitted['close']} v={emitted['volume']}"
                    )

        bars.updateEvent += _on_update
        self.subscriptions[symbol] = ("realtime_bars", bars)
        return bars

    def seed_5m_bar(self, symbol: str, bar: dict[str, Any]) -> None:
        symbol = symbol.upper()
        collector = self.collectors.get(symbol)
        if collector is None:
            collector = FiveMinuteBarCollector(symbol=symbol)
            self.collectors[symbol] = collector
        collector.bars.append(bar)

    def seed_5m_bars(self, symbol: str, bars: list[dict[str, Any]]) -> None:
        for bar in bars:
            self.seed_5m_bar(symbol, bar)

    def bootstrap_session_5m_bars(
        self,
        symbol: str,
        contract: Any,
        what_to_show: str = "TRADES",
        session_date: date | None = None,
        count: int = 2,
        lookback_days: int = 2,
        seed: bool = True,
    ) -> list[dict[str, Any]]:
        """
        Load the first completed 5-minute RTH bars for the current session.

        The live bot uses this to prime the engine with the first two bars that
        define the session open and the current decision timestamp.
        """
        symbol = symbol.upper()
        session_date = session_date or datetime.now(US_TZ).date()
        bars = self.ib.reqHistoricalData(
            contract,
            endDateTime="",
            durationStr=f"{lookback_days} D",
            barSizeSetting="5 mins",
            whatToShow=what_to_show,
            useRTH=True,
            formatDate=1,
            keepUpToDate=False,
        )
        rows: list[dict[str, Any]] = []
        for bar in bars:
            ts = pd.Timestamp(bar.date)
            if ts.tzinfo is None:
                ts = ts.tz_localize(US_TZ)
            else:
                ts = ts.tz_convert(US_TZ)
            if ts.date() != session_date:
                continue
            row = {
                "symbol": symbol,
                "start": ts.to_pydatetime(),
                "end": ts.to_pydatetime(),
                "open": float(bar.open),
                "high": float(bar.high),
                "low": float(bar.low),
                "close": float(bar.close),
                "volume": float(bar.volume),
                "bar_count": 1,
            }
            rows.append(row)
            if len(rows) >= count:
                break
        if rows and seed:
            self.seed_5m_bars(symbol, rows)
        return rows

    def bootstrap_first_5m_bar(
        self,
        symbol: str,
        contract: Any,
        what_to_show: str = "TRADES",
        session_date: date | None = None,
        lookback_days: int = 2,
    ) -> dict[str, Any] | None:
        rows = self.bootstrap_session_5m_bars(
            symbol,
            contract,
            what_to_show=what_to_show,
            session_date=session_date,
            count=1,
            lookback_days=lookback_days,
        )
        return rows[0] if rows else None

    def unsubscribe_all(self) -> None:
        for kind, ticker in self.subscriptions.values():
            try:
                if kind == "mktdata":
                    self.ib.cancelMktData(ticker.contract)
                elif kind == "realtime_bars":
                    self.ib.cancelRealTimeBars(ticker)
            except Exception:
                pass
        self.subscriptions.clear()
        self.collectors.clear()

    def snapshot(self) -> list[dict[str, float | str | None]]:
        rows: list[dict[str, float | str | None]] = []
        for symbol, (kind, ticker) in self.subscriptions.items():
            if kind == "mktdata":
                rows.append(
                    {
                        "symbol": symbol,
                        "bid": getattr(ticker, "bid", None),
                        "ask": getattr(ticker, "ask", None),
                        "last": getattr(ticker, "last", None),
                        "close": getattr(ticker, "close", None),
                    }
                )
            else:
                collector = self.collectors.get(symbol)
                latest = collector.bars[-1] if collector and collector.bars else None
                rows.append(
                    {
                        "symbol": symbol,
                        "bid": None,
                        "ask": None,
                        "last": latest["close"] if latest else None,
                        "close": latest["close"] if latest else None,
                    }
                )
        return rows

    def collected_5m_bars(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for collector in self.collectors.values():
            rows.extend(collector.bars)
        return sorted(rows, key=lambda row: (row["symbol"], row["start"]))
