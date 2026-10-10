from __future__ import annotations

import sys
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SHARED_ROOT = PROJECT_ROOT.parent
PYTHONPROJECT_ROOT = SHARED_ROOT / "IBKR_TradingBot"
ENGINE_ROOT = PYTHONPROJECT_ROOT / "statistic" / "spx_adx_di_5min_2024_02_03"

for candidate in (PYTHONPROJECT_ROOT, ENGINE_ROOT):
    if candidate.exists() and str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from Daten_Download.thetadata_options_client import make_client, normalize_columns, write_frame  # noqa: E402
import manual_regime_rules_backtest as rules  # noqa: E402


DEFAULT_PROXY_MAP = {"SPX": "SPY"}
DEFAULT_OPTION_ROOT_MAP = {"SPX": "SPXW"}


@dataclass(frozen=True)
class StrategyCompareConfig:
    start: str
    end_exclusive: str
    target_dte: int = 90
    option_strike_range: int = 200
    option_multiplier: int = 100
    use_mid_prices: bool = True
    proxy_symbol_map: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_PROXY_MAP))
    option_root_map: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_OPTION_ROOT_MAP))
    cache_dir: Path = PROJECT_ROOT / "runs" / "strategy_compare_cache"
    credentials_file: Path = PYTHONPROJECT_ROOT / "config" / "thetadata_creds.txt"

    def proxy_symbol_for(self, symbol: str) -> str:
        return self.proxy_symbol_map.get(symbol.upper(), symbol.upper())

    def option_root_for(self, symbol: str) -> str:
        return self.option_root_map.get(symbol.upper(), symbol.upper())


@dataclass(frozen=True)
class StrategyComparisonResult:
    trades: pd.DataFrame
    summary: pd.DataFrame
    output_dir: Path


class HistoricalOptionsAdapter:
    def __init__(
        self,
        *,
        credentials_file: str | Path | None = None,
        cache_dir: str | Path | None = None,
        strike_range: int = 200,
        option_multiplier: int = 100,
    ) -> None:
        self.credentials_file = Path(credentials_file) if credentials_file is not None else PYTHONPROJECT_ROOT / "config" / "thetadata_creds.txt"
        self.cache_dir = Path(cache_dir) if cache_dir is not None else PROJECT_ROOT / "runs" / "strategy_compare_cache"
        self.strike_range = int(strike_range)
        self.option_multiplier = int(option_multiplier)
        self._client = None
        self._expirations_cache: dict[str, list[pd.Timestamp]] = {}
        self._chain_cache: dict[tuple[str, str, str, str, int], pd.DataFrame] = {}

    def _client_or_create(self):
        if self._client is None:
            if not self.credentials_file.exists():
                raise FileNotFoundError(
                    f"ThetaData credentials file not found: {self.credentials_file}. "
                    "The comparison runner needs a valid ThetaData login."
                )
            self._client = make_client(self.credentials_file)
        return self._client

    def list_expirations(self, symbol: str) -> list[pd.Timestamp]:
        safe_symbol = symbol.upper()
        if safe_symbol in self._expirations_cache:
            return self._expirations_cache[safe_symbol]

        client = self._client_or_create()
        expirations = normalize_columns(client.option_list_expirations(safe_symbol))
        if expirations.empty:
            result: list[pd.Timestamp] = []
        else:
            date_col = "expiration" if "expiration" in expirations.columns else expirations.columns[0]
            parsed = pd.to_datetime(expirations[date_col], errors="coerce").dropna().sort_values()
            result = [pd.Timestamp(value).normalize() for value in parsed.tolist()]
        self._expirations_cache[safe_symbol] = result
        return result

    @staticmethod
    def _time_token(value: str | pd.Timestamp) -> str:
        stamp = pd.Timestamp(value)
        return stamp.strftime("%H%M%S")

    @staticmethod
    def _date_token(value: str | pd.Timestamp) -> str:
        return pd.Timestamp(value).strftime("%Y%m%d")

    def _chain_cache_path(
        self,
        symbol: str,
        trade_date: str | pd.Timestamp,
        time_of_day: str | pd.Timestamp,
        expiration: str | pd.Timestamp,
    ) -> Path:
        safe_symbol = symbol.upper()
        return (
            self.cache_dir
            / "chains"
            / safe_symbol
            / self._date_token(trade_date)
            / self._time_token(time_of_day)
            / f"exp_{self._date_token(expiration)}_sr{self.strike_range}.csv"
        )

    def load_chain(
        self,
        symbol: str,
        trade_date: str | pd.Timestamp,
        time_of_day: str | pd.Timestamp,
        expiration: str | pd.Timestamp,
    ) -> pd.DataFrame:
        safe_symbol = symbol.upper()
        cache_key = (
            safe_symbol,
            self._date_token(trade_date),
            self._time_token(time_of_day),
            self._date_token(expiration),
            self.strike_range,
        )
        if cache_key in self._chain_cache:
            return self._chain_cache[cache_key].copy()

        cache_path = self._chain_cache_path(safe_symbol, trade_date, time_of_day, expiration)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        if cache_path.exists():
            chain = normalize_columns(pd.read_csv(cache_path))
        else:
            client = self._client_or_create()
            if isinstance(time_of_day, pd.Timestamp):
                time_token = pd.Timestamp(time_of_day).strftime("%H:%M:%S")
            else:
                time_token = pd.Timestamp(str(time_of_day)).strftime("%H:%M:%S")
            chain = client.option_at_time_quote(
                symbol=safe_symbol,
                start_date=pd.Timestamp(trade_date).date(),
                end_date=pd.Timestamp(trade_date).date(),
                time_of_day=time_token,
                expiration=pd.Timestamp(expiration).date(),
                strike="*",
                right="both",
                strike_range=self.strike_range,
            )
            chain = normalize_columns(chain)
            if not chain.empty:
                chain = chain.drop_duplicates(subset=[c for c in ["strike", "right"] if c in chain.columns], keep="last").reset_index(drop=True)
            write_frame(chain, cache_path)

        self._chain_cache[cache_key] = chain.copy()
        return chain.copy()


class PriceHistoryProvider:
    def __init__(self, *, start: str, end_exclusive: str) -> None:
        self.start = start
        self.end_exclusive = end_exclusive
        self._bars: dict[str, pd.DataFrame] = {}

    def bars(self, symbol: str) -> pd.DataFrame:
        safe_symbol = symbol.upper()
        if safe_symbol not in self._bars:
            self._bars[safe_symbol] = rules.load_bars(safe_symbol, self.start, self.end_exclusive)
        return self._bars[safe_symbol]

    @staticmethod
    def _coerce_timestamp(value: str | pd.Timestamp) -> pd.Timestamp:
        stamp = pd.Timestamp(value)
        if stamp.tzinfo is not None:
            stamp = stamp.tz_convert(None)
        return stamp

    def price_at(self, symbol: str, ts: str | pd.Timestamp, column: str = "close") -> float:
        bars = self.bars(symbol)
        stamp = self._coerce_timestamp(ts)
        exact = bars[bars["date"].eq(stamp)]
        if not exact.empty:
            value = pd.to_numeric(exact.iloc[0][column], errors="coerce")
            if pd.notna(value):
                return float(value)
        same_day = bars[
            (pd.to_datetime(bars["date"]).dt.normalize() == stamp.normalize())
            & (pd.to_datetime(bars["date"]) <= stamp)
        ].copy()
        if same_day.empty:
            raise KeyError(f"No {column} price for {symbol} at {stamp}")
        same_day = same_day.sort_values("date", kind="mergesort")
        value = pd.to_numeric(same_day.iloc[-1][column], errors="coerce")
        if pd.isna(value):
            raise KeyError(f"No numeric {column} price for {symbol} at {stamp}")
        return float(value)


def _safe_timestamp(value: object) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is not None:
        stamp = stamp.tz_convert(None)
    return stamp


def _timestamp_token(ts: pd.Timestamp) -> str:
    return ts.strftime("%Y-%m-%d %H:%M:%S")


def _date_from_timestamp(ts: pd.Timestamp) -> pd.Timestamp:
    return pd.Timestamp(ts.date())


def _regular_session_quote_timestamp(ts: pd.Timestamp) -> pd.Timestamp:
    """Map pre-open decision timestamps onto the first regular-session quote."""
    if ts.time() < pd.Timestamp("09:30").time():
        return ts.normalize() + pd.Timedelta(hours=9, minutes=35)
    return ts


def _entry_expiration(
    expirations: Sequence[pd.Timestamp],
    entry_ts: pd.Timestamp,
    target_dte: int,
) -> pd.Timestamp | None:
    if not expirations:
        return None
    target_day = entry_ts.normalize() + pd.Timedelta(days=target_dte)
    for expiration in expirations:
        if expiration.normalize() >= target_day:
            return pd.Timestamp(expiration).normalize()
    return pd.Timestamp(expirations[-1]).normalize()


def _expiration_candidates(
    expirations: Sequence[pd.Timestamp],
    entry_ts: pd.Timestamp,
    target_dte: int,
    *,
    max_slack_days: int = 14,
    max_candidates: int = 5,
) -> list[pd.Timestamp]:
    if not expirations:
        return []
    target_day = entry_ts.normalize() + pd.Timedelta(days=target_dte)
    candidates: list[pd.Timestamp] = []
    for expiration in expirations:
        exp = pd.Timestamp(expiration).normalize()
        if exp < target_day:
            continue
        if candidates and (exp - target_day).days > max_slack_days:
            break
        candidates.append(exp)
        if len(candidates) >= max_candidates:
            break
    return candidates


def _prepare_chain(chain: pd.DataFrame) -> pd.DataFrame:
    if chain.empty:
        return chain.copy()
    out = normalize_columns(chain)
    for col in ["strike", "bid", "ask"]:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")
    if "right" in out.columns:
        out["right"] = out["right"].astype(str).str.upper()
    if {"strike", "right"}.issubset(out.columns):
        out = out.dropna(subset=["strike", "right"]).drop_duplicates(subset=["strike", "right"], keep="last")
    return out.reset_index(drop=True)


def _row_mid(row: pd.Series) -> float | None:
    bid = pd.to_numeric(row.get("bid"), errors="coerce")
    ask = pd.to_numeric(row.get("ask"), errors="coerce")
    if pd.isna(bid) or pd.isna(ask):
        return None
    return float((float(bid) + float(ask)) / 2.0)


def _numeric_series(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(dtype=float)
    return pd.to_numeric(frame[column], errors="coerce").dropna()


def _find_best_strike(chain: pd.DataFrame, spot: float, side: str) -> dict[str, object] | None:
    if chain.empty or "strike" not in chain.columns or "right" not in chain.columns:
        return None
    q = _prepare_chain(chain)
    if q.empty:
        return None

    right = "CALL" if side == "long" else "PUT"
    q = q[q["right"].eq(right)].copy()
    if q.empty:
        return None

    strikes = sorted(pd.to_numeric(q["strike"], errors="coerce").dropna().unique().tolist())
    if not strikes:
        return None

    atm_strike = min(strikes, key=lambda strike: abs(float(strike) - float(spot)))
    if side == "long":
        short_candidates = [strike for strike in strikes if strike >= atm_strike]
        short_strike = min(short_candidates, key=lambda strike: abs(float(strike) - float(spot))) if short_candidates else atm_strike
        long_candidates = [strike for strike in strikes if strike < short_strike]
    else:
        short_candidates = [strike for strike in strikes if strike <= atm_strike]
        short_strike = min(short_candidates, key=lambda strike: abs(float(strike) - float(spot))) if short_candidates else atm_strike
        long_candidates = [strike for strike in strikes if strike > short_strike]

    if not long_candidates:
        return None

    short_rows = q[q["strike"].eq(float(short_strike))]
    if short_rows.empty:
        return None
    short_row = short_rows.iloc[0]
    if pd.isna(short_row.get("bid")) or pd.isna(short_row.get("ask")):
        return None
    short_mid = _row_mid(short_row)
    if short_mid is None:
        return None

    best: dict[str, object] | None = None
    for long_strike in long_candidates:
        long_rows = q[q["strike"].eq(float(long_strike))]
        if long_rows.empty:
            continue
        long_row = long_rows.iloc[0]
        if pd.isna(long_row.get("bid")) or pd.isna(long_row.get("ask")):
            continue
        long_mid = _row_mid(long_row)
        if long_mid is None:
            continue

        if side == "long":
            intrinsic = 2.0 * max(float(spot) - float(long_strike), 0.0) - max(float(spot) - float(short_strike), 0.0)
        else:
            intrinsic = 2.0 * max(float(long_strike) - float(spot), 0.0) - max(float(short_strike) - float(spot), 0.0)

        entry_mid = 2.0 * long_mid - short_mid
        if entry_mid <= 0:
            continue
        extrinsic_gap = abs(entry_mid - intrinsic)
        candidate = {
            "side": side,
            "right": right,
            "atm_strike": float(atm_strike),
            "short_strike": float(short_strike),
            "long_strike": float(long_strike),
            "entry_value_mid": float(entry_mid),
            "intrinsic_mid_spot": float(intrinsic),
            "extrinsic_gap": float(extrinsic_gap),
            "short_bid": float(short_row["bid"]),
            "short_ask": float(short_row["ask"]),
            "long_bid": float(long_row["bid"]),
            "long_ask": float(long_row["ask"]),
            "short_mid": float(short_mid),
            "long_mid": float(long_mid),
        }
        if best is None or candidate["extrinsic_gap"] < best["extrinsic_gap"]:
            best = candidate

    return best


def _quote_value(
    chain: pd.DataFrame,
    *,
    short_strike: float,
    long_strike: float,
    right: str,
) -> dict[str, float] | None:
    q = _prepare_chain(chain)
    if q.empty:
        return None
    q = q[q["right"].eq(right)].copy()
    if q.empty:
        return None
    short_rows = q[q["strike"].eq(float(short_strike))]
    long_rows = q[q["strike"].eq(float(long_strike))]
    if short_rows.empty or long_rows.empty:
        return None
    short_row = short_rows.iloc[0]
    long_row = long_rows.iloc[0]
    if pd.isna(short_row.get("bid")) or pd.isna(short_row.get("ask")):
        return None
    if pd.isna(long_row.get("bid")) or pd.isna(long_row.get("ask")):
        return None
    short_mid = _row_mid(short_row)
    long_mid = _row_mid(long_row)
    if short_mid is None or long_mid is None:
        return None
    bid_value = 2.0 * float(long_row["bid"]) - float(short_row["ask"])
    mid_value = 2.0 * long_mid - float(short_mid)
    conservative_entry = 2.0 * float(long_row["ask"]) - float(short_row["bid"])
    conservative_exit = 2.0 * float(long_row["bid"]) - float(short_row["ask"])
    return {
        "mid_value": float(mid_value),
        "bid_value": float(bid_value),
        "entry_value_cons": float(conservative_entry),
        "exit_value_cons": float(conservative_exit),
        "short_mid": float(short_mid),
        "long_mid": float(long_mid),
    }


def compare_engine_trades(
    trades: pd.DataFrame,
    *,
    config: StrategyCompareConfig,
    symbol: str | None = None,
    options_adapter: HistoricalOptionsAdapter | None = None,
    price_provider: PriceHistoryProvider | None = None,
) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame()

    if "track" in trades.columns:
        dedupe_columns = [column for column in trades.columns if column not in {"track", "entry_labels"}]
        trades = trades.drop_duplicates(subset=dedupe_columns, keep="first").reset_index(drop=True)

    adapter = options_adapter or HistoricalOptionsAdapter(
        credentials_file=config.credentials_file,
        cache_dir=config.cache_dir,
        strike_range=config.option_strike_range,
        option_multiplier=config.option_multiplier,
    )
    prices = price_provider or PriceHistoryProvider(start=config.start, end_exclusive=config.end_exclusive)

    rows: list[dict[str, object]] = []
    for _, raw in trades.iterrows():
        row = raw.to_dict()
        trade_symbol = str(row.get("symbol", symbol or "")).upper()
        side = str(row.get("side", "")).lower()
        if side not in {"long", "short"}:
            continue

        proxy_symbol = config.proxy_symbol_for(trade_symbol)
        option_symbol = config.option_root_for(trade_symbol)
        zebra_status = "ok"
        zebra_reason = ""
        zebra_metrics: dict[str, object] = {}
        stock_error = ""
        zebra_error = ""

        try:
            entry_ts = _safe_timestamp(row.get("entry_date"))
            exit_ts = _safe_timestamp(row.get("exit_date"))
            option_entry_ts = _regular_session_quote_timestamp(entry_ts)
            option_exit_ts = _regular_session_quote_timestamp(exit_ts)
            size = float(row.get("size", 1.0) or 1.0)

            entry_price = pd.to_numeric(row.get("entry"), errors="coerce")
            exit_price = pd.to_numeric(row.get("exit"), errors="coerce")
            if pd.isna(entry_price) or pd.isna(exit_price):
                entry_price = prices.price_at(proxy_symbol, entry_ts, column="close")
                exit_price = prices.price_at(proxy_symbol, exit_ts, column="close")
            else:
                entry_price = float(entry_price)
                exit_price = float(exit_price)
            stock_direction = 1.0 if side == "long" else -1.0
            stock_pnl_unit = stock_direction * (exit_price - entry_price)
            stock_pnl = stock_pnl_unit * size
            stock_capital = abs(entry_price * size)
            stock_return = (stock_pnl / stock_capital) if stock_capital > 0 else None

            expirations = adapter.list_expirations(option_symbol)
            expiration_candidates = _expiration_candidates(expirations, option_entry_ts, config.target_dte)
            if not expiration_candidates:
                zebra_status = "skip"
                zebra_reason = "no_expiration"
            else:
                last_failure_reason = "no_valid_leg_pair"
                last_failure_error = ""
                for expiration in expiration_candidates:
                    try:
                        entry_chain = adapter.load_chain(
                            option_symbol,
                            option_entry_ts.date(),
                            option_entry_ts.strftime("%H:%M:%S"),
                            expiration.date(),
                        )
                    except Exception as exc:
                        last_failure_reason = "entry_chain_unavailable"
                        last_failure_error = f"{type(exc).__name__}: {exc}"
                        continue
                    leg = _find_best_strike(entry_chain, entry_price, side)
                    if leg is None:
                        last_failure_reason = "no_valid_leg_pair"
                        last_failure_error = ""
                        continue
                    try:
                        exit_chain = adapter.load_chain(
                            option_symbol,
                            option_exit_ts.date(),
                            option_exit_ts.strftime("%H:%M:%S"),
                            expiration.date(),
                        )
                    except Exception as exc:
                        last_failure_reason = "exit_chain_unavailable"
                        last_failure_error = f"{type(exc).__name__}: {exc}"
                        continue
                    right = str(leg["right"])
                    short_strike = float(leg["short_strike"])
                    long_strike = float(leg["long_strike"])
                    entry_value = _quote_value(
                        entry_chain,
                        short_strike=short_strike,
                        long_strike=long_strike,
                        right=right,
                    )
                    exit_value = _quote_value(
                        exit_chain,
                        short_strike=short_strike,
                        long_strike=long_strike,
                        right=right,
                    )
                    if entry_value is None or exit_value is None:
                        last_failure_reason = "missing_quote"
                        last_failure_error = ""
                        continue

                    entry_mid = float(leg["entry_value_mid"])
                    exit_mid = float(exit_value["mid_value"])
                    entry_cons = float(entry_value["entry_value_cons"])
                    exit_cons = float(exit_value["exit_value_cons"])
                    zebra_pnl_mid_unit = (exit_mid - entry_mid) * adapter.option_multiplier
                    zebra_pnl_cons_unit = (exit_cons - entry_cons) * adapter.option_multiplier
                    zebra_capital_mid = abs(entry_mid * adapter.option_multiplier * size)
                    zebra_capital_cons = abs(entry_cons * adapter.option_multiplier * size)
                    zebra_pnl_mid = zebra_pnl_mid_unit * size
                    zebra_pnl_cons = zebra_pnl_cons_unit * size
                    zebra_return_mid = (zebra_pnl_mid / zebra_capital_mid) if zebra_capital_mid > 0 else None
                    zebra_return_cons = (zebra_pnl_cons / zebra_capital_cons) if zebra_capital_cons > 0 else None
                    zebra_primary_mode = "mid" if config.use_mid_prices else "cons"
                    zebra_primary_pnl = zebra_pnl_mid if config.use_mid_prices else zebra_pnl_cons
                    zebra_primary_return = zebra_return_mid if config.use_mid_prices else zebra_return_cons
                    zebra_metrics = {
                        "option_symbol": option_symbol,
                        "zebra_type": f"{side}_{right.lower()}",
                        "expiration": expiration.date().isoformat(),
                        "spot_entry": entry_price,
                        "spot_exit": exit_price,
                        "short_strike": short_strike,
                        "long_strike": long_strike,
                        "atm_strike": float(leg["atm_strike"]),
                        "entry_value_mid": entry_mid,
                        "entry_value_cons": entry_cons,
                        "exit_value_mid": exit_mid,
                        "exit_value_cons": exit_cons,
                        "zebra_pnl_mid": zebra_pnl_mid,
                        "zebra_pnl_cons": zebra_pnl_cons,
                        "zebra_capital_mid": zebra_capital_mid,
                        "zebra_capital_cons": zebra_capital_cons,
                        "zebra_return_mid": zebra_return_mid,
                        "zebra_return_cons": zebra_return_cons,
                        "zebra_primary_mode": zebra_primary_mode,
                        "zebra_primary_pnl": zebra_primary_pnl,
                        "zebra_primary_return": zebra_primary_return,
                        "zebra_entry_extrinsic_gap": float(leg["extrinsic_gap"]),
                        "zebra_status": zebra_status,
                        "zebra_reason": zebra_reason,
                    }
                    last_failure_reason = ""
                    last_failure_error = ""
                    break
                else:
                    zebra_status = "skip"
                    zebra_reason = last_failure_reason
                    zebra_error = last_failure_error
                    if zebra_reason == "entry_chain_unavailable" and last_failure_error:
                        zebra_error = last_failure_error
        except Exception as exc:
            stock_error = f"{type(exc).__name__}: {exc}"
            zebra_error = stock_error
            zebra_status = "error"
            zebra_reason = "comparison_error"
            entry_price = None
            exit_price = None
            stock_pnl = None
            stock_capital = None
            stock_return = None

        rows.append(
            {
                **row,
                "symbol": trade_symbol,
                "proxy_symbol": proxy_symbol,
                "option_symbol": option_symbol,
                "entry_price_proxy": entry_price,
                "exit_price_proxy": exit_price,
                "stock_pnl": stock_pnl,
                "stock_capital": stock_capital,
                "stock_return": stock_return,
                "stock_error": stock_error,
                **zebra_metrics,
                "zebra_status": zebra_status,
                "zebra_reason": zebra_reason,
                "zebra_error": zebra_error,
            }
        )

    out = pd.DataFrame(rows)
    if not out.empty:
        out["entry_date"] = pd.to_datetime(out["entry_date"], errors="coerce")
        out["exit_date"] = pd.to_datetime(out["exit_date"], errors="coerce")
    return out


def summarize_comparison(comparison: pd.DataFrame) -> pd.DataFrame:
    if comparison.empty:
        return pd.DataFrame()

    def _max_drawdown(values: pd.Series) -> float:
        clean = pd.to_numeric(values, errors="coerce").dropna()
        if clean.empty:
            return 0.0
        equity = clean.cumsum()
        return float((equity - equity.cummax()).min())

    rows: list[dict[str, object]] = []
    grouped = comparison.groupby("symbol", sort=True)
    for symbol, frame in grouped:
        stock = _numeric_series(frame, "stock_pnl")
        zebra_mid = _numeric_series(frame, "zebra_pnl_mid")
        zebra_cons = _numeric_series(frame, "zebra_pnl_cons")
        zebra_mid_ret = _numeric_series(frame, "zebra_return_mid")
        zebra_cons_ret = _numeric_series(frame, "zebra_return_cons")
        zebra_primary = _numeric_series(frame, "zebra_primary_pnl")
        zebra_primary_ret = _numeric_series(frame, "zebra_primary_return")

        rows.append(
            {
                "symbol": symbol,
                "trades": int(len(frame)),
                "stock_trades": int(stock.shape[0]),
                "stock_total_pnl": float(stock.sum()) if not stock.empty else 0.0,
                "stock_avg_pnl": float(stock.mean()) if not stock.empty else 0.0,
                "stock_win_rate": float((stock > 0).mean()) if not stock.empty else 0.0,
                "stock_max_drawdown": _max_drawdown(stock),
                "zebra_trades": int(zebra_mid.shape[0]),
                "zebra_total_pnl_mid": float(zebra_mid.sum()) if not zebra_mid.empty else 0.0,
                "zebra_total_pnl_cons": float(zebra_cons.sum()) if not zebra_cons.empty else 0.0,
                "zebra_avg_return_mid": float(zebra_mid_ret.mean()) if not zebra_mid_ret.empty else 0.0,
                "zebra_avg_return_cons": float(zebra_cons_ret.mean()) if not zebra_cons_ret.empty else 0.0,
                "zebra_total_pnl_primary": float(zebra_primary.sum()) if not zebra_primary.empty else 0.0,
                "zebra_avg_return_primary": float(zebra_primary_ret.mean()) if not zebra_primary_ret.empty else 0.0,
                "zebra_win_rate_mid": float((zebra_mid > 0).mean()) if not zebra_mid.empty else 0.0,
                "zebra_win_rate_cons": float((zebra_cons > 0).mean()) if not zebra_cons.empty else 0.0,
                "zebra_win_rate_primary": float((zebra_primary > 0).mean()) if not zebra_primary.empty else 0.0,
                "zebra_max_drawdown_mid": _max_drawdown(zebra_mid),
                "zebra_max_drawdown_cons": _max_drawdown(zebra_cons),
                "zebra_max_drawdown_primary": _max_drawdown(zebra_primary),
                "zebra_valid_rate": float(frame["zebra_status"].eq("ok").mean()) if "zebra_status" in frame.columns else 0.0,
            }
        )

    overall = comparison.copy()
    stock = _numeric_series(overall, "stock_pnl")
    zebra_mid = _numeric_series(overall, "zebra_pnl_mid")
    zebra_cons = _numeric_series(overall, "zebra_pnl_cons")
    zebra_mid_ret = _numeric_series(overall, "zebra_return_mid")
    zebra_cons_ret = _numeric_series(overall, "zebra_return_cons")
    zebra_primary = _numeric_series(overall, "zebra_primary_pnl")
    zebra_primary_ret = _numeric_series(overall, "zebra_primary_return")
    rows.append(
        {
            "symbol": "__all__",
            "trades": int(len(overall)),
            "stock_trades": int(stock.shape[0]),
            "stock_total_pnl": float(stock.sum()) if not stock.empty else 0.0,
            "stock_avg_pnl": float(stock.mean()) if not stock.empty else 0.0,
            "stock_win_rate": float((stock > 0).mean()) if not stock.empty else 0.0,
            "stock_max_drawdown": _max_drawdown(stock),
            "zebra_trades": int(zebra_mid.shape[0]),
            "zebra_total_pnl_mid": float(zebra_mid.sum()) if not zebra_mid.empty else 0.0,
            "zebra_total_pnl_cons": float(zebra_cons.sum()) if not zebra_cons.empty else 0.0,
            "zebra_avg_return_mid": float(zebra_mid_ret.mean()) if not zebra_mid_ret.empty else 0.0,
            "zebra_avg_return_cons": float(zebra_cons_ret.mean()) if not zebra_cons_ret.empty else 0.0,
            "zebra_total_pnl_primary": float(zebra_primary.sum()) if not zebra_primary.empty else 0.0,
            "zebra_avg_return_primary": float(zebra_primary_ret.mean()) if not zebra_primary_ret.empty else 0.0,
            "zebra_win_rate_mid": float((zebra_mid > 0).mean()) if not zebra_mid.empty else 0.0,
            "zebra_win_rate_cons": float((zebra_cons > 0).mean()) if not zebra_cons.empty else 0.0,
            "zebra_win_rate_primary": float((zebra_primary > 0).mean()) if not zebra_primary.empty else 0.0,
            "zebra_max_drawdown_mid": _max_drawdown(zebra_mid),
            "zebra_max_drawdown_cons": _max_drawdown(zebra_cons),
            "zebra_max_drawdown_primary": _max_drawdown(zebra_primary),
            "zebra_valid_rate": float(overall["zebra_status"].eq("ok").mean()) if "zebra_status" in overall.columns else 0.0,
        }
    )
    return pd.DataFrame(rows)


def run_symbol_comparison(
    symbol: str,
    start: str,
    end_exclusive: str,
    *,
    target_dte: int = 90,
    option_strike_range: int = 200,
    option_multiplier: int = 100,
    use_mid_prices: bool = True,
    proxy_symbol_map: dict[str, str] | None = None,
    option_root_map: dict[str, str] | None = None,
    cache_dir: str | Path | None = None,
    credentials_file: str | Path | None = None,
    output_dir: str | Path | None = None,
) -> StrategyComparisonResult:
    config = StrategyCompareConfig(
        start=start,
        end_exclusive=end_exclusive,
        target_dte=target_dte,
        option_strike_range=option_strike_range,
        option_multiplier=option_multiplier,
        use_mid_prices=use_mid_prices,
        proxy_symbol_map=dict(DEFAULT_PROXY_MAP if proxy_symbol_map is None else proxy_symbol_map),
        option_root_map=dict(DEFAULT_OPTION_ROOT_MAP if option_root_map is None else option_root_map),
        cache_dir=Path(cache_dir) if cache_dir is not None else PROJECT_ROOT / "runs" / "strategy_compare_cache",
        credentials_file=Path(credentials_file) if credentials_file is not None else PYTHONPROJECT_ROOT / "config" / "thetadata_creds.txt",
    )
    signals, trades, daily = rules.run_backtest(symbol, start, end_exclusive)
    comparison = compare_engine_trades(trades, config=config, symbol=symbol)
    summary = summarize_comparison(comparison)

    target_output = Path(output_dir) if output_dir is not None else PROJECT_ROOT / "runs" / "strategy_compare" / symbol.upper()
    target_output.mkdir(parents=True, exist_ok=True)
    comparison.to_csv(target_output / f"{symbol.upper()}_strategy_compare_trades.csv", index=False)
    summary.to_csv(target_output / f"{symbol.upper()}_strategy_compare_summary.csv", index=False)
    write_frame(signals, target_output / f"{symbol.upper()}_engine_signals.csv")
    write_frame(trades, target_output / f"{symbol.upper()}_engine_trades.csv")
    write_frame(daily, target_output / f"{symbol.upper()}_engine_daily.csv")
    return StrategyComparisonResult(trades=comparison, summary=summary, output_dir=target_output)


def run_many_symbol_comparisons(
    symbols: Sequence[str],
    start: str,
    end_exclusive: str,
    *,
    target_dte: int = 90,
    option_strike_range: int = 200,
    option_multiplier: int = 100,
    use_mid_prices: bool = True,
    proxy_symbol_map: dict[str, str] | None = None,
    option_root_map: dict[str, str] | None = None,
    cache_dir: str | Path | None = None,
    credentials_file: str | Path | None = None,
    output_dir: str | Path | None = None,
) -> StrategyComparisonResult:
    if not symbols:
        return StrategyComparisonResult(trades=pd.DataFrame(), summary=pd.DataFrame(), output_dir=Path(output_dir) if output_dir is not None else PROJECT_ROOT / "runs" / "strategy_compare")

    root_output = Path(output_dir) if output_dir is not None else PROJECT_ROOT / "runs" / "strategy_compare"
    all_trades: list[pd.DataFrame] = []
    for symbol in [str(item).upper() for item in symbols if str(item).strip()]:
        result = run_symbol_comparison(
            symbol,
            start,
            end_exclusive,
            target_dte=target_dte,
            option_strike_range=option_strike_range,
            option_multiplier=option_multiplier,
            use_mid_prices=use_mid_prices,
            proxy_symbol_map=proxy_symbol_map,
            option_root_map=option_root_map,
            cache_dir=cache_dir,
            credentials_file=credentials_file,
            output_dir=root_output,
        )
        if not result.trades.empty:
            all_trades.append(result.trades.assign(comparison_group="basket"))

    combined_trades = pd.concat(all_trades, ignore_index=True) if all_trades else pd.DataFrame()
    combined_summary = summarize_comparison(combined_trades)
    if not combined_summary.empty:
        combined_summary["comparison_group"] = combined_summary["symbol"].apply(
            lambda value: "basket_total" if value == "__all__" else "basket"
        )
    root_output.mkdir(parents=True, exist_ok=True)
    if not combined_trades.empty:
        combined_trades.to_csv(root_output / "basket_strategy_compare_trades.csv", index=False)
    if not combined_summary.empty:
        combined_summary.to_csv(root_output / "basket_strategy_compare_summary.csv", index=False)
    return StrategyComparisonResult(trades=combined_trades, summary=combined_summary, output_dir=root_output)


def load_compare_config(config_path: str | Path) -> dict[str, object]:
    path = Path(config_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    return {
        "target_dte": int(data.get("target_dte", 90)),
        "option_strike_range": int(data.get("option_strike_range", 200)),
        "option_multiplier": int(data.get("option_multiplier", 100)),
        "use_mid_prices": bool(data.get("use_mid_prices", True)),
        "proxy_symbol_map": dict(data.get("proxy_symbol_map", DEFAULT_PROXY_MAP)),
        "option_root_map": dict(data.get("option_root_map", DEFAULT_OPTION_ROOT_MAP)),
    }
