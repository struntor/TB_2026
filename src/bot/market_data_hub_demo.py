from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.append(str(Path(__file__).resolve().parents[2]))

from ib_insync import util

from src.bot.basket_selector import StaticBasketSelector
from src.data.ib_connection import IBConnectionConfig, IBConnectionManager
from src.data.ib_market_data import build_contract
from src.data.market_data_hub import MarketDataHub


def _fmt(seconds: int) -> str:
    hours, rem = divmod(max(seconds, 0), 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _parse_symbols(raw: str | None, basket_symbols: list[str]) -> list[str]:
    if raw:
        wanted = [sym.strip().upper() for sym in raw.split(",") if sym.strip()]
        result = [sym for sym in wanted if sym in basket_symbols]
        if result:
            return result
    return list(basket_symbols)


def main() -> None:
    parser = argparse.ArgumentParser(description="MarketDataHub demo with countdown and two test subscriptions.")
    parser.add_argument("--config", default=str(Path("configs") / "baskets_v1.json"))
    parser.add_argument("--target-time", default="")
    parser.add_argument("--skip-wait", action="store_true")
    parser.add_argument("--poll-seconds", type=int, default=5)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7496)
    parser.add_argument("--client-id", type=int, default=21)
    parser.add_argument("--runtime-seconds", type=int, default=20)
    parser.add_argument("--symbols", default="")
    parser.add_argument("--no-bootstrap-first-bar", action="store_false", dest="bootstrap_first_bar")
    parser.set_defaults(bootstrap_first_bar=True)
    args = parser.parse_args()

    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = Path(__file__).resolve().parents[2] / config_path
    selector = StaticBasketSelector.from_config(config_path)
    basket = selector.baskets[selector.default_basket]
    demo_symbols = _parse_symbols(args.symbols, list(basket.symbols))

    print(f"basket={selector.default_basket}")
    print(f"assets={len(basket.symbols)}")
    print(f"demo_symbols={','.join(demo_symbols)}")
    print(f"demo_symbol_count={len(demo_symbols)}")

    if not args.skip_wait:
        if args.target_time:
            target = datetime.fromisoformat(args.target_time)
        else:
            target = datetime.now() + timedelta(seconds=30)
        print(f"market_open_target={target.isoformat(sep=' ', timespec='seconds')}")
        while True:
            remaining = int((target - datetime.now()).total_seconds())
            if remaining <= 0:
                print("market_open=reached")
                break
            print(f"countdown={_fmt(remaining)}")
            time.sleep(min(args.poll_seconds, remaining))
    else:
        print("market_open=skipped")

    cfg = IBConnectionConfig(host=args.host, port=args.port, client_id=args.client_id)
    manager = IBConnectionManager(cfg)
    try:
        ib = manager.connect()
        print("ib=connected")
        hub = MarketDataHub(ib)

        for symbol in demo_symbols:
            contract, what_to_show = build_contract(symbol, "US")
            qualified = ib.qualifyContracts(contract)
            if not qualified:
                print(f"subscribe_failed={symbol} reason=unqualified_contract")
                continue
            contract = qualified[0]
            if args.bootstrap_first_bar:
                boot = hub.bootstrap_first_5m_bar(symbol, contract, what_to_show)
                if boot is not None:
                    print(
                        f"bootstrap5m {symbol} start={boot['start']} end={boot['end']} "
                        f"o={boot['open']} h={boot['high']} l={boot['low']} c={boot['close']} v={boot['volume']}"
                    )
                else:
                    print(f"bootstrap5m {symbol} none")
            bars = hub.subscribe_realtime_bars(symbol, contract, what_to_show)
            print(
                f"subscribed={symbol} secType={getattr(contract, 'secType', None)} "
                f"exchange={getattr(contract, 'exchange', None)} localSymbol={getattr(contract, 'localSymbol', None)}"
            )
            print(f"hint={symbol} whatToShow={what_to_show} barsType={type(bars).__name__}")

        deadline = time.time() + args.runtime_seconds
        last_payload = None
        while time.time() < deadline:
            ib.sleep(1)
            payload = tuple((row["symbol"], row["last"], row["close"]) for row in hub.snapshot())
            if payload != last_payload:
                for row in hub.snapshot():
                    print(f"state {row['symbol']} last={row['last']} close={row['close']}")
                last_payload = payload

        collected = hub.collected_5m_bars()
        print(f"collected_5m_bars={len(collected)}")
        for row in collected[-5:]:
            print(
                f"bar5m_summary {row['symbol']} start={row['start']} end={row['end']} "
                f"o={row['open']} h={row['high']} l={row['low']} c={row['close']} v={row['volume']}"
            )

        print("mode=stream_ready")
        print("next_step=wire_5m_ingest")
        hub.unsubscribe_all()
    finally:
        manager.disconnect()
        print("ib=disconnected")


if __name__ == "__main__":
    util.patchAsyncio()
    main()
