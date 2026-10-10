from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
from thetadata import ThetaClient
from thetadata.errors import NoDataFoundError


@dataclass(frozen=True)
class ThetaOptionsDownloadConfig:
    symbol: str
    as_of_date: date
    horizon_days: int
    output_dir: Path
    credentials_file: Path
    max_dte: int

    @property
    def end_date(self) -> date:
        return self.as_of_date + timedelta(days=self.horizon_days)


def make_client(credentials_file: Path) -> ThetaClient:
    return ThetaClient(creds_file=credentials_file, dataframe_type="pandas")


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out.columns = [str(col).strip().lower().replace(" ", "_") for col in out.columns]
    return out


def write_frame(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    normalize_columns(df).to_csv(path, index=False)


def is_error_cache(df: pd.DataFrame) -> bool:
    return "error_type" in df.columns or "error_message" in df.columns


def list_expirations_in_horizon(client: ThetaClient, config: ThetaOptionsDownloadConfig) -> list[date]:
    expirations = normalize_columns(client.option_list_expirations(config.symbol))
    if expirations.empty:
        return []

    date_col = "expiration" if "expiration" in expirations.columns else expirations.columns[0]
    expiration_dates = pd.to_datetime(expirations[date_col], errors="coerce").dt.date.dropna().unique().tolist()
    return sorted(exp for exp in expiration_dates if config.as_of_date <= exp <= config.end_date)


def download_contracts_for_as_of(client: ThetaClient, config: ThetaOptionsDownloadConfig) -> pd.DataFrame:
    errors = []
    for request_type in ["EOD", "OHLC", "QUOTE", "TRADE"]:
        try:
            contracts = client.option_list_contracts(
                request_type=request_type,
                date=config.as_of_date,
                symbol=config.symbol,
                max_dte=config.max_dte,
            )
            break
        except NoDataFoundError:
            return pd.DataFrame()
        except Exception as exc:
            errors.append(f"{request_type}: {type(exc).__name__}: {exc}")
    else:
        out = pd.DataFrame({"request_type_error": errors})
        out["download_symbol"] = config.symbol
        out["download_as_of_date"] = config.as_of_date.isoformat()
        return out

    if contracts.empty:
        return contracts
    contracts = normalize_columns(contracts)
    contracts["download_symbol"] = config.symbol
    contracts["download_as_of_date"] = config.as_of_date.isoformat()
    return contracts


def download_open_interest_for_expiration(
    client: ThetaClient,
    config: ThetaOptionsDownloadConfig,
    expiration: date,
) -> pd.DataFrame:
    try:
        oi = client.option_history_open_interest(
            symbol=config.symbol,
            expiration=expiration,
            start_date=config.as_of_date,
            end_date=config.end_date,
            right="both",
        )
    except NoDataFoundError:
        return pd.DataFrame()

    if oi.empty:
        return oi
    oi = normalize_columns(oi)
    oi["download_symbol"] = config.symbol
    oi["download_expiration"] = expiration.isoformat()
    oi["download_start_date"] = config.as_of_date.isoformat()
    oi["download_end_date"] = config.end_date.isoformat()
    return oi


def download_eod_for_expiration(
    client: ThetaClient,
    config: ThetaOptionsDownloadConfig,
    expiration: date,
) -> pd.DataFrame:
    try:
        eod = client.option_history_eod(
            symbol=config.symbol,
            expiration=expiration,
            start_date=config.as_of_date,
            end_date=config.end_date,
            right="both",
        )
    except NoDataFoundError:
        return pd.DataFrame()

    if eod.empty:
        return eod
    eod = normalize_columns(eod)
    eod["download_symbol"] = config.symbol
    eod["download_expiration"] = expiration.isoformat()
    eod["download_start_date"] = config.as_of_date.isoformat()
    eod["download_end_date"] = config.end_date.isoformat()
    return eod


def download_greeks_eod_for_expiration(
    client: ThetaClient,
    config: ThetaOptionsDownloadConfig,
    expiration: date,
) -> pd.DataFrame:
    try:
        greeks = client.option_history_greeks_eod(
            symbol=config.symbol,
            expiration=expiration,
            start_date=config.as_of_date,
            end_date=config.end_date,
            right="both",
        )
    except NoDataFoundError:
        return pd.DataFrame()
    except Exception as exc:
        return pd.DataFrame(
            [
                {
                    "download_symbol": config.symbol,
                    "download_expiration": expiration.isoformat(),
                    "download_start_date": config.as_of_date.isoformat(),
                    "download_end_date": config.end_date.isoformat(),
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                }
            ]
        )

    if greeks.empty:
        return greeks
    greeks = normalize_columns(greeks)
    greeks["download_symbol"] = config.symbol
    greeks["download_expiration"] = expiration.isoformat()
    greeks["download_start_date"] = config.as_of_date.isoformat()
    greeks["download_end_date"] = config.end_date.isoformat()
    return greeks


def download_msft_options_window(config: ThetaOptionsDownloadConfig) -> dict[str, Path]:
    client = make_client(config.credentials_file)
    run_dir = config.output_dir / config.symbol / f"{config.as_of_date:%Y%m%d}_plus_{config.horizon_days}d"
    run_dir.mkdir(parents=True, exist_ok=True)

    paths: dict[str, Path] = {}
    expirations = list_expirations_in_horizon(client, config)
    expirations_df = pd.DataFrame({"expiration": [exp.isoformat() for exp in expirations]})
    paths["expirations"] = run_dir / "expirations_in_horizon.csv"
    write_frame(expirations_df, paths["expirations"])

    contracts = download_contracts_for_as_of(client, config)
    paths["contracts"] = run_dir / "contracts_as_of.csv"
    write_frame(contracts, paths["contracts"])

    oi_frames = []
    eod_frames = []
    greeks_frames = []
    for idx, expiration in enumerate(expirations, start=1):
        oi_path = run_dir / "open_interest_by_expiration" / f"open_interest_{expiration:%Y%m%d}.csv"
        eod_path = run_dir / "eod_by_expiration" / f"eod_{expiration:%Y%m%d}.csv"
        greeks_path = run_dir / "greeks_eod_by_expiration" / f"greeks_eod_{expiration:%Y%m%d}.csv"
        print(f"[{idx}/{len(expirations)}] expiration={expiration:%Y-%m-%d}", flush=True)

        if oi_path.exists():
            oi = pd.read_csv(oi_path)
            print(f"  open_interest cached rows={len(oi)}", flush=True)
        else:
            oi = download_open_interest_for_expiration(client, config, expiration)
            if not oi.empty:
                write_frame(oi, oi_path)
            print(f"  open_interest downloaded rows={len(oi)}", flush=True)

        if eod_path.exists():
            eod = pd.read_csv(eod_path)
            print(f"  eod cached rows={len(eod)}", flush=True)
        else:
            eod = download_eod_for_expiration(client, config, expiration)
            if not eod.empty:
                write_frame(eod, eod_path)
            print(f"  eod downloaded rows={len(eod)}", flush=True)

        if greeks_path.exists():
            greeks = pd.read_csv(greeks_path)
            if is_error_cache(greeks):
                print(f"  greeks_eod cached error rows={len(greeks)}; redownloading", flush=True)
                greeks = download_greeks_eod_for_expiration(client, config, expiration)
                if not greeks.empty:
                    write_frame(greeks, greeks_path)
                print(f"  greeks_eod downloaded rows={len(greeks)}", flush=True)
            else:
                print(f"  greeks_eod cached rows={len(greeks)}", flush=True)
        else:
            greeks = download_greeks_eod_for_expiration(client, config, expiration)
            if not greeks.empty:
                write_frame(greeks, greeks_path)
            print(f"  greeks_eod downloaded rows={len(greeks)}", flush=True)

        if not oi.empty:
            oi_frames.append(oi)
        if not eod.empty:
            eod_frames.append(eod)
        if not greeks.empty:
            greeks_frames.append(greeks)

    all_oi = pd.concat(oi_frames, ignore_index=True) if oi_frames else pd.DataFrame()
    all_eod = pd.concat(eod_frames, ignore_index=True) if eod_frames else pd.DataFrame()
    all_greeks = pd.concat(greeks_frames, ignore_index=True) if greeks_frames else pd.DataFrame()
    paths["open_interest_all"] = run_dir / "open_interest_all_expirations.csv"
    paths["eod_all"] = run_dir / "eod_all_expirations.csv"
    paths["greeks_eod_all"] = run_dir / "greeks_eod_all_expirations.csv"
    write_frame(all_oi, paths["open_interest_all"])
    write_frame(all_eod, paths["eod_all"])
    write_frame(all_greeks, paths["greeks_eod_all"])

    summary = pd.DataFrame(
        [
            {
                "symbol": config.symbol,
                "as_of_date": config.as_of_date.isoformat(),
                "end_date": config.end_date.isoformat(),
                "horizon_days": config.horizon_days,
                "max_dte": config.max_dte,
                "expiration_count": len(expirations),
                "contracts_rows": len(contracts),
                "open_interest_rows": len(all_oi),
                "eod_rows": len(all_eod),
                "greeks_eod_rows": len(all_greeks),
            }
        ]
    )
    paths["summary"] = run_dir / "download_summary.csv"
    write_frame(summary, paths["summary"])
    return paths
