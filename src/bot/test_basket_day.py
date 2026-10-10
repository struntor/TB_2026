from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.append(str(Path(__file__).resolve().parents[2]))
    sys.path.append(str(Path(__file__).resolve().parents[3] / "IBKR_TradingBot" / "statistic" / "spx_adx_di_5min_2024_02_03"))

from src.bot.basket_selector import StaticBasketSelector
from manual_regime_bot_api import get_trade_openings_for_day


def main() -> None:
    parser = argparse.ArgumentParser(description="Test all basket assets for a single decision day.")
    parser.add_argument("--day", required=True)
    parser.add_argument("--config", default=str(Path("configs") / "baskets_v1.json"))
    parser.add_argument("--output", default=str(Path("runs") / "basket_day_test.csv"))
    args = parser.parse_args()

    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = Path(__file__).resolve().parents[2] / config_path
    selector = StaticBasketSelector.from_config(config_path)
    basket = selector.baskets[selector.default_basket]
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    existing: dict[str, dict[str, str]] = {}
    if output_path.exists():
        with output_path.open("r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                existing[row["symbol"]] = row

    rows: list[dict[str, str]] = []

    for symbol in basket.symbols:
        if symbol in existing:
            rows.append(existing[symbol])
            print(f"skip={symbol} status={existing[symbol]['status']} trade_count={existing[symbol]['trade_count']}")
            continue
        row = {
            "day": args.day,
            "symbol": symbol,
            "status": "ok",
            "trade_count": "0",
            "error": "",
        }
        try:
            batch = get_trade_openings_for_day(symbol, args.day)
            row["trade_count"] = str(len(batch.trades))
        except Exception as exc:
            row["status"] = "error"
            row["error"] = f"{type(exc).__name__}: {exc}"
        rows.append(row)
        mode = "a" if output_path.exists() else "w"
        with output_path.open(mode, newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["day", "symbol", "status", "trade_count", "error"])
            if mode == "w":
                writer.writeheader()
            writer.writerow(row)
        print(f"done={symbol} status={row['status']} trade_count={row['trade_count']}")

    ok = sum(1 for row in rows if row["status"] == "ok")
    err = sum(1 for row in rows if row["status"] == "error")
    with_trades = sum(1 for row in rows if row["status"] == "ok" and row["trade_count"] != "0")
    print(f"day={args.day}")
    print(f"basket={selector.default_basket}")
    print(f"symbols={len(basket.symbols)}")
    print(f"ok={ok}")
    print(f"errors={err}")
    print(f"symbols_with_trades={with_trades}")
    print(f"output={output_path}")

    if err:
        for row in rows:
            if row["status"] == "error":
                print(f"error_symbol={row['symbol']} error={row['error']}")


if __name__ == "__main__":
    main()
