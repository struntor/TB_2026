# === Multi-Asset Downloader mit korrekten IB-Contracts
# - HG: Front-Month Future @ COMEX (automatisch ermittelt)
# - DAX: Index @ EUREX (IND, MIDPOINT)
# - FX: Forex @ IDEALPRO (MIDPOINT)
# - US Stocks/ETFs: SMART/USD (TRADES)
# - CL: Continuous Future @ NYMEX (TRADES)
#
# Speicherung: d:\Trading\Rohdaten\Rohdaten_{SYMBOL}.csv

from ib_insync import IB, Stock, Forex, Contract, ContFuture, Future, Index
import argparse
import pandas as pd
from datetime import datetime, timedelta
import pytz, time, os, sys
import os

from zoneinfo import ZoneInfo

# =========================
# Konfiguration
# =========================

DATA_DIR = r'd:\Trading\Rohdaten'
BAR_SIZE = "5 mins"
CHUNK_DAYS = 50                      # Abruf in 50-Tage-Chunks
DEFAULT_START = datetime(2021, 2, 20) # wenn keine Datei existiert
HOST, PORT = '127.0.0.1', 7496
CLIENT_ID = 3

SYMBOL_DEFAULT_STARTS = {
    # COIN started trading on 2021-04-14; older bars cannot exist.
    "COIN": datetime(2021, 4, 14),
    # GE HealthCare started when-trading in December 2022; older bars cannot exist.
    "GEHC": datetime(2022, 12, 15),
}

US_INDEX_EXCHANGES = {
    "SPX": "CBOE",
    "NDX": "NASDAQ",
    "VIX": "CBOE",
    # Constant-maturity SPX volatility term-structure indices.
    "VIX9D": "CBOE",
    "VIX3M": "CBOE",
    "VIX6M": "CBOE",
    "VIX1Y": "CBOE",
    # Cboe implied-volatility indices for Nasdaq-100 and Russell 2000.
    "VXN": "CBOE",
    "RVX": "CBOE",
    # Cboe implied-volatility indices for gold and crude-oil ETFs.
    "GVZ": "CBOE",
    "OVX": "CBOE",
}

US_STOCK_ALIASES = {
    # Bank of New York Mellon changed its listed ticker from BK to BNY.
    # Keep the configured asset/file name as BK, but request BNY from IB.
    "BK": {"ib_symbol": "BNY", "primaryExchange": "NYSE"},
}

BUILTIN_ASSETS = [
    {"symbol": "SPX_FUT", "market": "US"},
    {"symbol": "GVZ", "market": "US"},
    {"symbol": "OVX", "market": "US"},
    {"symbol": "VIX9D", "market": "US"},
    {"symbol": "VIX3M", "market": "US"},
    {"symbol": "VIX6M", "market": "US"},
    {"symbol": "VIX1Y", "market": "US"},
]

FRONT_FUTURES = {
    # SPX itself is an index; the liquid front future is the E-mini S&P 500.
    "SPX_FUT": {
        "root": "ES",
        "exchange": "CME",
        "currency": "USD",
        "stitch_front": True,
        "front_window_days": 100,
    },
}

# =========================
# Hilfsfunktionen
# =========================
import random, time

def _is_pacing_err(e: Exception) -> bool:
    s = str(e).lower()
    return ("pacing" in s) or ("historical data request pacing" in s)

def _is_no_data_err(e: Exception) -> bool:
    s = str(e).lower()
    return ("no data" in s) or ("keine daten" in s) or ("hmds-anfrage ergab keine daten" in s)

def _backoff_sleep(attempt: int, base: float = 0.4, cap: float = 8.0):
    t = min(cap, base * (2 ** attempt)) * (0.7 + 0.6 * random.random())
    time.sleep(t)


def ensure_dir(path: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)

def get_head_timestamp(ib: IB, contract, what="TRADES", useRTH=True):
    """
    Erster verfügbarer Zeitstempel laut HMDS; None, wenn IB nichts liefert.
    """
    try:
        ts = ib.reqHeadTimeStamp(contract, whatToShow=what, useRTH=useRTH, formatDate=2)
        # formatDate=2 -> ISO string
        return pd.to_datetime(ts) if ts else None
    except Exception as e:
        print(f"     [headTs {getattr(contract,'localSymbol',None)}] Warnung: {e}")
        return None


def read_existing_csv(file_path: str) -> pd.DataFrame:
    if os.path.exists(file_path):
        try:
            df = pd.read_csv(file_path, parse_dates=['date'])
            df['date'] = pd.to_datetime(df['date'])
            return df
        except Exception as e:
            print(f"[{file_path}] Fehler beim Einlesen: {e}")
    return pd.DataFrame()

def determine_fetch_window(existing_df: pd.DataFrame):
    """
    Liefert (fetch_from, fetch_to).
    fetch_from: letzter gespeicherter Timestamp + 1s oder DEFAULT_START
    fetch_to:   jetzt (naiv)
    """
    if not existing_df.empty and 'date' in existing_df.columns:
        last = existing_df['date'].max()
        # Falls 'date' als String gespeichert war, sauber parsen:
        last = pd.to_datetime(last, errors='coerce')
        fetch_from = (last + pd.Timedelta(seconds=1)) if pd.notna(last) else DEFAULT_START
        print(f"  -> Abruf neuer Daten ab: {fetch_from}")
    else:
        fetch_from = DEFAULT_START
        print(f"  -> Keine bestehenden Daten. Abruf ab: {fetch_from}")

    fetch_to = datetime.now()  # naiv, keine TZ-Annahme
    return fetch_to, fetch_from  # beachte Reihenfolge für die rückwärtslaufende Chunk-Schleife


def get_front_month_future(ib: IB, symbol: str, exchange: str = "COMEX", currency: str = "USD"):
    cds = ib.reqContractDetails(Future(symbol.upper(), exchange=exchange, currency=currency))
    if not cds:
        raise RuntimeError(f"Keine Future-Monate gefunden für {symbol} @ {exchange}")
    return cds[0].contract  # nächstfälliger Monat

def get_sorted_front_month_future(ib: IB, symbol: str, exchange: str, currency: str = "USD"):
    cds = ib.reqContractDetails(Future(symbol.upper(), exchange=exchange, currency=currency))
    if not cds:
        raise RuntimeError(f"Keine Future-Monate gefunden fuer {symbol} @ {exchange}")
    contracts = sorted(
        (cd.contract for cd in cds if cd.contract.lastTradeDateOrContractMonth),
        key=lambda c: c.lastTradeDateOrContractMonth,
    )
    if not contracts:
        raise RuntimeError(f"Keine Future-Monate mit Ablaufdatum gefunden fuer {symbol} @ {exchange}")
    return contracts[0]


def build_contract(ib: IB, symbol: str, market: str):
    """
    Liefert (contract, whatToShow, file_path, beschreibung).
    Enthält Sonderlogik für HG (Front-Month @ COMEX) und DAX.
    """
    s = symbol.upper()
    m = market.upper()
    file_path = os.path.join(DATA_DIR, f"Rohdaten_{s}.csv")

    if s in FRONT_FUTURES:
        cfg = FRONT_FUTURES[s]
        if cfg.get("stitch_front"):
            return (
                None,
                "TRADES",
                file_path,
                f"{s} stitched Front-Month {cfg['root']} Futures @ {cfg['exchange']}",
            )
        front = get_sorted_front_month_future(
            ib,
            cfg["root"],
            cfg["exchange"],
            cfg.get("currency", "USD"),
        )
        return (
            front,
            "TRADES",
            file_path,
            f"{s} Front-Month {front.localSymbol} ({cfg['root']}) @ {cfg['exchange']}",
        )

    # FX (IDEALPRO)
    if s in {"EURUSD", "USDJPY", "GBPUSD", "USDCHF", "AUDUSD", "USDCAD"}:
        return Forex(s), "MIDPOINT", file_path, f"FX {s} @ IDEALPRO"

    # HG: Front-Month auf COMEX (Future)
    if s == "HG":
        front = get_front_month_future(ib, "HG", "COMEX")
        return front, "TRADES", file_path, f"HG Front-Month {front.localSymbol} @ COMEX"

    # CL: Continuous Future auf NYMEX
    if s == "CL":
        # Kein Contract zurückgeben -> Spezialpfad, wir stitchen später
        return None, "TRADES", file_path, "CL stitched Futures @ NYMEX"
        # DAX Index (IND) – EUREX (MIDPOINT)
    if m == "US" and s in US_INDEX_EXCHANGES:
        exchange = US_INDEX_EXCHANGES[s]
        return Index(s, exchange, "USD"), "TRADES", file_path, f"{s} Index @ {exchange}"

    if m == "DE" and s == "DAX":
        dax = Contract(symbol='DAX', secType='IND', exchange='EUREX', currency='EUR')
        return dax, "MIDPOINT", file_path, "DAX Index (IND) @ EUREX"
    # DAX Index (IND) – nur falls in Liste aktiv
        # 🇩🇪 DE-Aktien (Xetra): EUR + primaryExchange=IBIS
    if m == "DE":
            # Empfehlung: SMART-Routing mit Primärbörse IBIS (XETRA)
        stk = Stock(s, exchange='SMART', currency='EUR', primaryExchange='IBIS')
        return stk, "TRADES", file_path, f"{s} @ XETRA (SMART/EUR; primary IBIS)"
    # Standard: US Stocks/ETFs über SMART
    alias = US_STOCK_ALIASES.get(s)
    if alias:
        ib_symbol = alias["ib_symbol"]
        stk = Stock(
            ib_symbol,
            exchange="SMART",
            currency="USD",
            primaryExchange=alias.get("primaryExchange", ""),
        )
        return stk, "TRADES", file_path, f"Stock/ETF {s} via {ib_symbol} @ SMART"

    stk = Stock(s, 'SMART', 'USD')
    return stk, "TRADES", file_path, f"Stock/ETF {s} @ SMART"


def ensure_history_from_default(
    file_path: str,
    existing: pd.DataFrame,
    default_start: pd.Timestamp,
    symbol: str | None = None,
) -> pd.DataFrame:
    """
    Wenn existing vorhanden ist, aber der früheste Timestamp > default_start (+Toleranz),
    dann Datei löschen und leeren DF zurückgeben, damit ein Full-Backfill startet.
    """
    if existing is None or existing.empty:
        return existing

    if "date" not in existing.columns:
        return existing

    # robust parsen
    dmin = pd.to_datetime(existing["date"].min(), errors="coerce")
    if pd.isna(dmin):
        return existing

    default_start = SYMBOL_DEFAULT_STARTS.get((symbol or "").upper(), default_start)
    default_start = pd.to_datetime(default_start, errors="coerce")
    if pd.isna(default_start):
        return existing

    # Toleranz, falls default_start nicht exakt Bar-Start ist (z.B. 00:00 vs Session)
    tol = pd.Timedelta(days=2)

    # Wenn die Datei erst viel später beginnt → löschen
    if dmin > (default_start + tol):
        print(f"  X Datei startet zu spaet ({dmin}). Erwartet ab ~{default_start}. -> loesche {file_path} und lade neu.")
        try:
            os.remove(file_path)
        except Exception as e:
            print(f"  X Konnte Datei nicht loeschen: {e}")
            return existing  # im Zweifel lieber nicht "leer" machen

        # leeres DF erzwingt DEFAULT_START in determine_fetch_window()
        return pd.DataFrame()

    return existing


def ensure_history_from_default_no_delete(
    file_path: str,
    existing: pd.DataFrame,
    default_start: pd.Timestamp,
    symbol: str | None = None,
) -> pd.DataFrame:
    """
    Keep existing data intact even when the file starts after the default start.

    This avoids a full reset when 2021 data are missing for some assets. The
    incremental download logic will append only newer bars.
    """
    if existing is None or existing.empty:
        return existing

    if "date" not in existing.columns:
        return existing

    dmin = pd.to_datetime(existing["date"].min(), errors="coerce")
    if pd.isna(dmin):
        return existing

    default_start = SYMBOL_DEFAULT_STARTS.get((symbol or "").upper(), default_start)
    default_start = pd.to_datetime(default_start, errors="coerce")
    if pd.isna(default_start):
        return existing

    tol = pd.Timedelta(days=2)
    if dmin > (default_start + tol):
        print(
            f"  ! Datei startet spaet ({dmin}). Erwartet ab ~{default_start}. "
            f"Bestehende Rohdaten bleiben erhalten; es wird nur angehaengt."
        )

    return existing


ensure_history_from_default = ensure_history_from_default_no_delete


# in Download_Asset_Data.py
def fetch_asset(ib: IB, symbol: str, market: str, cap_end: datetime | str | pd.Timestamp | None = None):
    contract, whatToShow, file_path, desc = build_contract(ib, symbol, market)
    ensure_dir(file_path)
    print(f"\n=== {symbol} | {desc} | whatToShow={whatToShow}")

    existing = read_existing_csv(file_path)

    # >>> NEU: prüfen ob Historie ab DEFAULT_START vorhanden ist, sonst Datei löschen
    existing = ensure_history_from_default_no_delete(file_path, existing, DEFAULT_START, symbol)

    if not existing.empty:
        print(f"  -> Letzter Eintrag: {existing['date'].max()}")

    # Achtung: determine_fetch_window gibt (fetch_to, fetch_from) zurück!
    start_date, end_date = determine_fetch_window(existing)  # start_date = fetch_to, end_date = fetch_from


    # >>> NEU: Cap-Ende (naiv, in Session-Zeit der CSVs) berücksichtigen
    if cap_end is not None:
        cap = pd.to_datetime(cap_end, errors="coerce")
        if cap is not None and getattr(cap, "tzinfo", None):
            cap = cap.tz_localize(None)  # CSVs sind naiv (Session-Zeit)
        # 'start_date' ist das 'to'-Ende (nach hinten). Auf Cap deckeln:
        if cap is not None:
            start_date = min(start_date, cap)

    print(f"[dbg] fetch_from={end_date} fetch_to={start_date}")

    if contract is None and symbol.upper() in FRONT_FUTURES:
        expected_start = get_front_future_oldest_active_start(ib, symbol.upper())
        if expected_start is not None and not existing.empty and "date" in existing.columns:
            existing_start = pd.to_datetime(existing["date"].min(), errors="coerce")
            if pd.notna(existing_start) and existing_start > pd.Timestamp(expected_start) + pd.Timedelta(days=2):
                print(
                    f"  -> Bestehende {symbol.upper()}-Datei startet erst bei {existing_start}. "
                    f"Erwartet ab ~{expected_start}. Datei wird als Front-Future-Kette neu aufgebaut."
                )
                existing = pd.DataFrame()
                end_date = DEFAULT_START

        print(f"  -> Lade {symbol.upper()} als gestitchte Front-Future-Kette ...")
        all_bars = fetch_front_future_stitched(ib, symbol.upper(), end_date, start_date)
    # --- CL Spezialpfad (falls du ihn wieder aktivierst) bleibt wie zuvor ---
    elif contract is None and symbol.upper() == "CL":
        print("  -> Lade CL ueber Futures-Kette (monatlich) ...")
        all_bars = fetch_cl_stitched(ib, end_date, start_date)
    else:
        # Qualify + chunking wie gehabt — nutzt start_date als oberes Ende (gekappter 'to')
        try:
            q = ib.qualifyContracts(contract)
            if not q:
                print("  -> qualifyContracts leer - Contract pruefen.")
                return
            contract = q[0]
            print(f"  OK Qualified: conId={getattr(contract,'conId',None)}, "
                  f"secType={getattr(contract,'secType',None)}, "
                  f"exchange={getattr(contract,'exchange',None)}, "
                  f"localSymbol={getattr(contract,'localSymbol',None)}")
        except Exception as e:
            print(f"  X Fehler bei qualifyContracts: {e}")
            return

        if str(getattr(contract, "secType", "")).upper() == "FUT":
            future_cfg = FRONT_FUTURES.get(symbol.upper(), {})
            max_backfill_days = future_cfg.get("max_backfill_days")
            expiry = pd.to_datetime(getattr(contract, "lastTradeDateOrContractMonth", None), errors="coerce")
            if max_backfill_days and pd.notna(expiry):
                active_start = (expiry - pd.Timedelta(days=int(max_backfill_days))).to_pydatetime()
                if end_date < active_start:
                    print(f"  -> Front-Future Backfill wird auf {active_start} begrenzt.")
                    end_date = active_start

            head_ts = get_head_timestamp(ib, contract, what=whatToShow, useRTH=True)
            if head_ts is not None:
                head_dt = pd.Timestamp(to_naive(head_ts)).to_pydatetime()
                if end_date < head_dt:
                    print(f"  -> Future-Historie erst ab {head_dt} verfuegbar. Fetch-Start wird angepasst.")
                    end_date = head_dt
                if start_date <= end_date:
                    print("  -> Keine neueren Daten im verfuegbaren Future-Fenster.")
                    return

        all_bars = []
        cur_end = start_date
        attempt = 0
        while cur_end > end_date:
            chunk_end = max(cur_end - timedelta(days=CHUNK_DAYS), end_date)
            duration_days = (cur_end - chunk_end).days or 1
            duration_str = f"{duration_days // 365} Y" if duration_days > 365 else f"{duration_days} D"
            end_str = cur_end.strftime('%Y%m%d %H:%M:%S')
            print(f"  -> req {duration_str} bis {end_str}")
            try:
                bars = ib.reqHistoricalData(
                    contract,
                    endDateTime=end_str,
                    durationStr=duration_str,
                    barSizeSetting=BAR_SIZE,
                    whatToShow=whatToShow,
                    useRTH=True
                )

                if not bars:
                    print("  -> keine Bars (Stop).")
                    break

                bars = [
                    bar for bar in bars
                    if end_date <= pd.Timestamp(to_naive(bar.date)) <= cur_end
                ]
                if not bars:
                    print("  -> keine Bars im Zielfenster (Stop).")
                    break

                all_bars.extend(bars)

                # >>> WICHTIG: weiter nach hinten schieben
                # ib_insync liefert bars[i].date als datetime
                earliest = pd.Timestamp(to_naive(bars[0].date))

                cur_end = (earliest - pd.Timedelta(seconds=1)).to_pydatetime()

                # Sicherheitsgurt: falls cur_end nicht kleiner wird
                if cur_end <= end_date:
                    break

            except Exception as e:
                if _is_no_data_err(e):
                    print(f"  -> keine Bars fuer diesen Zeitraum ({e}). Stop.")
                    break
                if _is_pacing_err(e):
                    _backoff_sleep(attempt)
                    attempt += 1
                    continue
                raise



    # --- Rest: DataFrame, Mergen, Speichern wie gehabt ---
    if not all_bars:
        print("  -> Keine neuen Daten geladen.")
        return

    df_new = pd.DataFrame(all_bars)

    df_new.columns = df_new.columns.str.lower()
    df_new['date'] = pd.to_datetime(df_new['date'], errors='coerce').map(to_naive)

    df_new.drop_duplicates(subset=['date'], inplace=True)
    df_new.sort_values('date', inplace=True)
    df_new.reset_index(drop=True, inplace=True)

    combined = pd.concat([existing, df_new], ignore_index=True) if not existing.empty else df_new
    combined.drop_duplicates(subset=['date'], keep='last', inplace=True)
    combined.sort_values('date', inplace=True)

    n_new = len(df_new); n_existing = len(existing); n_total = len(combined)
    try:
        combined.to_csv(file_path, index=False)
        print(f"  -> gespeichert: {file_path}")
        print(f"     + {n_new} neue Bars hinzugefügt (vorher: {n_existing}, jetzt gesamt: {n_total})")
    except Exception as e:
        print(f"  -> Fehler beim Speichern: {e}")

def to_naive(dt):
    """Lässt naive Datetimes unverändert. Entfernt nur vorhandene TZ ohne Shift."""
    if dt is None:
        return None
    ts = pd.to_datetime(dt, errors='coerce')
    if ts is None or pd.isna(ts):
        return None
    # Nur falls tz-aware, TZ entfernen (keine Konvertierung):
    return ts.tz_localize(None) if ts.tzinfo is not None else ts

def get_future_chain(ib, symbol="CL", exchange="NYMEX", currency="USD", include_expired=False):
    cds = ib.reqContractDetails(
        Future(
            symbol.upper(),
            exchange=exchange,
            currency=currency,
            includeExpired=include_expired,
        )
    )
    # nach Fälligkeitsmonat sortieren
    chain = sorted((cd.contract for cd in cds if cd.contract.lastTradeDateOrContractMonth),
                   key=lambda c: c.lastTradeDateOrContractMonth)
    if not chain:
        raise RuntimeError(f"Keine Future-Monate für {symbol} @ {exchange} gefunden.")
    return chain

def fetch_hist_chunked(ib, contract, start_dt, end_dt, bar_size="5 mins", what="TRADES", chunk_days=50):
    all_bars = []

    # Eingaben vereinheitlichen (naiv lassen)
    start_dt = pd.to_datetime(start_dt, errors='coerce')
    end_dt   = pd.to_datetime(end_dt,   errors='coerce')
    if start_dt is not None and getattr(start_dt, "tzinfo", None): start_dt = start_dt.tz_localize(None)
    if end_dt   is not None and getattr(end_dt,   "tzinfo", None): end_dt   = end_dt.tz_localize(None)

    # Qualifizieren
    try:
        qc = ib.qualifyContracts(contract)
        if qc:
            contract = qc[0]
    except Exception as e:
        print(f"     [qualify {getattr(contract,'localSymbol',None)}] Warnung: {e}")

    head_ts = get_head_timestamp(ib, contract, what=what, useRTH=True)  # <<< RTH
    if head_ts is not None:
        head_ts = pd.Timestamp(to_naive(head_ts))
    try:
        exp_token = getattr(contract, "lastTradeDateOrContractMonth", None)
        exp = pd.to_datetime(exp_token, errors='coerce')
        if exp is not None and getattr(exp, "tzinfo", None): exp = exp.tz_localize(None)
        if pd.notna(exp) and exp_token and len(str(exp_token)) == 8:
            exp = exp + pd.Timedelta(days=1)
    except Exception:
        exp = None

    eff_start = max(start_dt, head_ts) if head_ts is not None else start_dt
    eff_end   = min(end_dt, exp)       if exp     is not None else end_dt
    if eff_end is None or eff_start is None or eff_end <= eff_start:
        return all_bars

    cur_end = eff_end
    while cur_end > eff_start:
        chunk_start   = max(cur_end - timedelta(days=chunk_days), eff_start)
        duration_days = max((cur_end - chunk_start).days, 1)
        duration_str  = f"{duration_days // 365} Y" if duration_days > 365 else f"{duration_days} D"
        end_str       = cur_end.strftime('%Y%m%d %H:%M:%S')
        try:
            bars = ib.reqHistoricalData(
                contract,
                endDateTime=end_str,
                durationStr=duration_str,
                barSizeSetting=bar_size,
                whatToShow=what,
                useRTH=True   # <<< RTH konsistent
            )
            if bars:
                bars = [
                    bar for bar in bars
                    if chunk_start <= pd.Timestamp(to_naive(bar.date)) <= cur_end
                ]
                all_bars.extend(bars)
        except Exception as e:
            if "162" not in str(e):
                print(f"     [{getattr(contract,'localSymbol',None)}] Fehler: {e}")
        cur_end = chunk_start - timedelta(seconds=1)
        time.sleep(1)
    return all_bars


def fetch_front_future_stitched(ib, symbol: str, start_dt, end_dt):
    cfg = FRONT_FUTURES[symbol.upper()]
    root = cfg["root"]
    exchange = cfg["exchange"]
    currency = cfg.get("currency", "USD")
    chain = get_future_chain(
        ib,
        root,
        exchange,
        currency=currency,
        include_expired=True,
    )

    start_dt = pd.to_datetime(start_dt, errors="coerce")
    end_dt = pd.to_datetime(end_dt, errors="coerce")
    if start_dt is not None and getattr(start_dt, "tzinfo", None):
        start_dt = start_dt.tz_localize(None)
    if end_dt is not None and getattr(end_dt, "tzinfo", None):
        end_dt = end_dt.tz_localize(None)
    if start_dt is None or end_dt is None or pd.isna(start_dt) or pd.isna(end_dt) or end_dt <= start_dt:
        return []

    all_bars = []
    prev_exp_end = None
    used = 0
    for fut in chain:
        exp_token = getattr(fut, "lastTradeDateOrContractMonth", None)
        exp = pd.to_datetime(exp_token, errors="coerce")
        if pd.isna(exp):
            continue
        exp_end = exp + pd.Timedelta(days=1) if len(str(exp_token)) == 8 else exp
        active_start = (
            prev_exp_end
            if prev_exp_end is not None
            else max(start_dt, exp_end - pd.Timedelta(days=int(cfg.get("front_window_days", 100))))
        )
        active_end = exp_end
        prev_exp_end = exp_end

        seg_start = max(start_dt, active_start)
        seg_end = min(end_dt, active_end)
        if seg_end <= seg_start:
            continue

        bars = fetch_hist_chunked(
            ib,
            fut,
            seg_start,
            seg_end,
            bar_size=BAR_SIZE,
            what="TRADES",
            chunk_days=CHUNK_DAYS,
        )
        if bars:
            used += 1
            print(
                f"  -> {getattr(fut, 'localSymbol', root)} "
                f"{seg_start.strftime('%Y-%m-%d')}..{seg_end.strftime('%Y-%m-%d')}: +{len(bars)} Bars"
            )
            all_bars.extend(bars)
        else:
            print(
                f"  -> {getattr(fut, 'localSymbol', root)} "
                f"{seg_start.strftime('%Y-%m-%d')}..{seg_end.strftime('%Y-%m-%d')}: keine Bars"
            )

    if not all_bars:
        oldest = getattr(chain[0], "localSymbol", root) if chain else root
        print(f"  -> Keine Bars aus Front-Future-Kette geladen. Aeltester IB-Kontrakt: {oldest}")
    else:
        print(f"  -> Front-Future-Kette: {used} Kontrakte genutzt, {len(all_bars)} Bars gesamt")
    return all_bars


def get_front_future_oldest_active_start(ib, symbol: str):
    cfg = FRONT_FUTURES[symbol.upper()]
    chain = get_future_chain(
        ib,
        cfg["root"],
        cfg["exchange"],
        currency=cfg.get("currency", "USD"),
        include_expired=True,
    )
    if not chain:
        return None

    exp_token = getattr(chain[0], "lastTradeDateOrContractMonth", None)
    exp = pd.to_datetime(exp_token, errors="coerce")
    if pd.isna(exp):
        return None
    exp_end = exp + pd.Timedelta(days=1) if len(str(exp_token)) == 8 else exp
    return (exp_end - pd.Timedelta(days=int(cfg.get("front_window_days", 100)))).to_pydatetime()


def fetch_cl_stitched(ib, start_dt, end_dt):
    chain = get_future_chain(ib, "CL", "NYMEX")
    all = []
    for fut in chain:
        # simple Filter: nur Kontrakte mit Expiry in Fensternähe (±6 Monate)
        try:
            exp = pd.to_datetime(fut.lastTradeDateOrContractMonth)
        except Exception:
            exp = None
        if exp is not None and not (start_dt - pd.Timedelta(days=180) <= exp <= end_dt + pd.Timedelta(days=180)):
            continue

        bars = fetch_hist_chunked(ib, fut, start_dt, end_dt, bar_size=BAR_SIZE, what="TRADES", chunk_days=CHUNK_DAYS)
        if bars:
            print(f"  -> {fut.localSymbol}: +{len(bars)} Bars")
            all.extend(bars)
    return all


def fetch_cl_stitched(ib, start_dt, end_dt):
    chain = get_future_chain(ib, "CL", "NYMEX", include_expired=True)
    start_dt = pd.to_datetime(start_dt, errors="coerce")
    end_dt = pd.to_datetime(end_dt, errors="coerce")
    if start_dt is not None and getattr(start_dt, "tzinfo", None):
        start_dt = start_dt.tz_localize(None)
    if end_dt is not None and getattr(end_dt, "tzinfo", None):
        end_dt = end_dt.tz_localize(None)
    if start_dt is None or end_dt is None or pd.isna(start_dt) or pd.isna(end_dt) or end_dt <= start_dt:
        return []

    all_bars = []
    prev_exp_end = None
    used = 0
    for fut in chain:
        exp_token = getattr(fut, "lastTradeDateOrContractMonth", None)
        exp = pd.to_datetime(exp_token, errors="coerce")
        if pd.isna(exp):
            continue
        exp_end = exp + pd.Timedelta(days=1) if len(str(exp_token)) == 8 else exp
        active_start = prev_exp_end if prev_exp_end is not None else max(start_dt, exp_end - pd.Timedelta(days=45))
        active_end = exp_end
        prev_exp_end = exp_end

        seg_start = max(start_dt, active_start)
        seg_end = min(end_dt, active_end)
        if seg_end <= seg_start:
            continue

        bars = fetch_hist_chunked(ib, fut, seg_start, seg_end, bar_size=BAR_SIZE, what="TRADES", chunk_days=CHUNK_DAYS)
        if bars:
            used += 1
            print(
                f"  -> {fut.localSymbol} "
                f"{seg_start.strftime('%Y-%m-%d')}..{seg_end.strftime('%Y-%m-%d')}: +{len(bars)} Bars"
            )
            all_bars.extend(bars)
        else:
            print(
                f"  -> {fut.localSymbol} "
                f"{seg_start.strftime('%Y-%m-%d')}..{seg_end.strftime('%Y-%m-%d')}: keine Bars"
            )

    if all_bars:
        print(f"  -> CL-Future-Kette: {used} Kontrakte genutzt, {len(all_bars)} Bars gesamt")
    else:
        oldest = getattr(chain[0], "localSymbol", "CL") if chain else "CL"
        print(f"  -> Keine Bars aus CL-Future-Kette geladen. Aeltester IB-Kontrakt: {oldest}")
    return all_bars

import pandas as pd




# =========================
# Main
# =========================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Download or update configured market-data assets from IBKR.")
    parser.add_argument(
        "--only-symbol",
        action="append",
        help="Download only this symbol; repeat the flag for multiple symbols.",
    )
    cli_args = parser.parse_args()
    ib = IB()

    from datetime import datetime, date, time as dt_time, timedelta

    try:
        print(f"Verbinde {HOST}:{PORT} (clientId={CLIENT_ID}) ...")
        ib.connect(HOST, PORT, clientId=CLIENT_ID)

        # Optional: Delayed Market Data erlauben (falls Live-Abo fehlt)
        # ib.reqMarketDataType(3)  # 1=Live, 2=Frozen, 3=Delayed, 4=Delayed-Frozen

        ASSETS_CSV = r"d:\Trading\assets.csv"
        RAW_DIR = r"d:\Trading\Rohdaten"

        US_TZ = ZoneInfo("America/New_York")
        RTH_CLOSE = dt_time(16, 0)
        BAR_MINUTES = 5


        # Falls deine Rohdaten-Spalte anders heißt, hier ergänzen:
        TS_COL_CANDIDATES = ("datetime", "date", "timestamp", "time")


        def _prev_trading_day_us(today_us: date) -> date:
            # Ohne Feiertagskalender, aber Wochenenden korrekt überspringen.
            candidate = today_us - timedelta(days=1)
            while candidate.weekday() >= 5:
                candidate -= timedelta(days=1)
            return candidate


        def _expected_last_bar_start_us(day: date) -> datetime:
            close_dt = datetime.combine(day, RTH_CLOSE, tzinfo=US_TZ)
            return close_dt - timedelta(minutes=BAR_MINUTES)  # 15:55 bei 5m


        def _read_last_ts_from_csv(csv_path: str) -> datetime | None:
            if not os.path.exists(csv_path):
                return None

            df = pd.read_csv(csv_path)
            if df.empty:
                return None

            ts = None
            for col in TS_COL_CANDIDATES:
                if col in df.columns:
                    s = pd.to_datetime(df[col], errors="coerce")
                    s = s.dropna()
                    if not s.empty:
                        ts = s.max()
                        break

            if ts is None:
                raise ValueError(f"Keine Timestamp-Spalte gefunden in {csv_path}. Spalten: {list(df.columns)}")

            # in Python-datetime wandeln
            dt = ts.to_pydatetime()

            # Wenn naiv: als US/Eastern interpretieren (so sind IB-Bar-Timestamps meist gemeint)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=US_TZ)

            return dt


        def _needs_fetch(symbol: str, market: str) -> bool:
            # Aktuell: US-Logik; andere Märkte -> lieber fetchen (oder eigene Regeln bauen)
            if (market or "").upper().strip() != "US":
                return True

            csv_path = os.path.join(RAW_DIR, f"Rohdaten_{symbol}.csv")
            last_ts = _read_last_ts_from_csv(csv_path)

            if last_ts is None:
                return True

            today_us = datetime.now(US_TZ).date()
            last_trading_day = _prev_trading_day_us(today_us)
            expected_last = _expected_last_bar_start_us(last_trading_day)

            # vollständig, wenn letzter gespeicherter Bar >= erwarteter letzter Start (15:55)
            return last_ts < expected_last


        def load_assets_csv(
                only_symbol: str | list[str] | None,
                path: str = ASSETS_CSV,
                only_need_fetch: bool = True,
        ) -> list[dict]:
            df = pd.read_csv(path)

            # Pflichtspalten prüfen
            required = {"symbol", "market"}
            missing = required - set(df.columns)
            if missing:
                raise ValueError(f"{path} missing columns: {sorted(missing)}")

            # Normalisieren
            df["symbol"] = df["symbol"].astype(str).str.strip().str.upper()
            df["market"] = df["market"].astype(str).str.strip().str.upper()

            # Deduplizieren + NaNs raus
            df = df.dropna(subset=["symbol", "market"])
            df = df.drop_duplicates(subset=["symbol", "market"])

            # Optional auf ein oder mehrere Symbole einschränken
            if only_symbol is not None:
                if isinstance(only_symbol, str):
                    symbols_filter = {only_symbol.strip().upper()}
                else:
                    symbols_filter = {
                        str(sym).strip().upper()
                        for sym in only_symbol
                        if str(sym).strip()
                    }

                df = df[df["symbol"].isin(symbols_filter)]

            df = df.sort_values(["market", "symbol"])

            assets = df[["symbol", "market"]].to_dict("records")

            if not only_need_fetch:
                return assets

            out = []
            for a in assets:
                sym = a["symbol"]
                mkt = a["market"]
                try:
                    if _needs_fetch(sym, mkt):
                        out.append(a)
                except Exception as e:
                    # Safety: lieber nicht skippen, wenn Prüfung kaputt ist
                    print(f"[assets-filter] {sym} check failed -> include. reason: {e}")
                    out.append(a)

            return out


        def add_builtin_assets(assets: list[dict]) -> list[dict]:
            seen = {
                (str(a.get("symbol", "")).strip().upper(), str(a.get("market", "")).strip().upper())
                for a in assets
            }
            out = list(assets)
            for asset in BUILTIN_ASSETS:
                sym = str(asset["symbol"]).strip().upper()
                mkt = str(asset["market"]).strip().upper()
                if (sym, mkt) in seen:
                    continue
                try:
                    if _needs_fetch(sym, mkt):
                        out.append({"symbol": sym, "market": mkt})
                except Exception as e:
                    print(f"[builtin-assets-filter] {sym} check failed -> include. reason: {e}")
                    out.append({"symbol": sym, "market": mkt})
                seen.add((sym, mkt))
            return out



        ASSETS = add_builtin_assets(load_assets_csv(None))
        if cli_args.only_symbol:
            requested = {str(symbol).strip().upper() for symbol in cli_args.only_symbol}
            ASSETS = [asset for asset in ASSETS if str(asset["symbol"]).strip().upper() in requested]
            missing = requested - {str(asset["symbol"]).strip().upper() for asset in ASSETS}
            if missing:
                raise ValueError(f"Unknown --only-symbol values: {sorted(missing)}")
        for a in ASSETS:
            try:
                fetch_asset(ib, a["symbol"], a["market"])
            except Exception as e:
                print(f"[{a['symbol']}] Fehler: {e}")

        # for a in ASSETS:
        #     if not a.get("enabled", True):
        #         print(f"=== {a['symbol']}: disabled → übersprungen ===")
        #         continue
        #     try:
        #         fetch_asset(ib, a["symbol"], a["market"])
        #     except Exception as e:
        #         print(f"[{a['symbol']}] Fehler: {e}")

    finally:
        ib.disconnect()
        print("\nGetrennt.")
