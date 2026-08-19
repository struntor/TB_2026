from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.append(str(Path(__file__).resolve().parents[2]))
    sys.path.append(str(Path(__file__).resolve().parents[3] / "PythonProject" / "statistic" / "spx_adx_di_5min_2024_02_03"))

from ib_insync import util

from src.bot.basket_selector import StaticBasketSelector
from src.data.ib_connection import IBConnectionConfig, IBConnectionManager
from src.data.ib_market_data import build_contract
from src.models.money_management import MoneyManagementConfig


def _fmt(seconds: int) -> str:
    hours, rem = divmod(max(seconds, 0), 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _resolve_path(path_str: str) -> Path:
    path = Path(path_str)
    if path.is_absolute():
        return path
    return Path(__file__).resolve().parents[2] / path


def main() -> None:
    parser = argparse.ArgumentParser(description="Live bot skeleton with countdown and IB market data preview.")
    parser.add_argument("--config", default=str(Path("configs") / "baskets_v1.json"))
    parser.add_argument("--symbol", default="IVV")
    parser.add_argument("--target-time", default="")
    parser.add_argument("--skip-wait", action="store_true")
    parser.add_argument("--poll-seconds", type=int, default=5)
    parser.add_argument("--money-config", default=str(Path("configs") / "money_management_v1.json"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7496)
    parser.add_argument("--client-id", type=int, default=21)
    args = parser.parse_args()

    config_path = _resolve_path(args.config)
    money_config = MoneyManagementConfig.from_config(_resolve_path(args.money_config))
    selector = StaticBasketSelector.from_config(config_path)
    basket = selector.baskets[selector.default_basket]
    print(f"basket={selector.default_basket}")
    print(f"assets={len(basket.symbols)}")
    print(f"focus_symbol={args.symbol.upper()}")
    print(f"money_account_size_usd={money_config.account_size_usd}")
    print(f"money_risk_per_trade_pct={money_config.risk_per_trade_pct}")
    print(f"money_risk_budget_usd={money_config.risk_budget_usd:.2f}")
    print(f"money_max_open_trades={money_config.max_open_trades}")
    print(f"money_max_open_trades_long={money_config.max_open_trades_long}")
    print(f"money_max_open_trades_short={money_config.max_open_trades_short}")

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

        contract, what_to_show = build_contract(args.symbol, "US")
        qualified = ib.qualifyContracts(contract)
        if not qualified:
            raise RuntimeError(f"Could not qualify contract for {args.symbol}.")
        contract = qualified[0]
        print(
            f"contract={getattr(contract, 'secType', None)} "
            f"{getattr(contract, 'localSymbol', None)} "
            f"{getattr(contract, 'exchange', None)} "
            f"{getattr(contract, 'currency', None)}"
        )

        ticker = ib.reqMktData(contract, genericTickList="", snapshot=False, regulatorySnapshot=False)
        print("subscription=stream mode=top-of-book-or-last")
        print(f"market_data_hint=whatToShow={what_to_show}")

        deadline = time.time() + 20
        last_printed = None
        while time.time() < deadline:
            ib.sleep(1)
            bid = ticker.bid if ticker.bid is not None else float("nan")
            ask = ticker.ask if ticker.ask is not None else float("nan")
            last = ticker.last if ticker.last is not None else float("nan")
            close = ticker.close if ticker.close is not None else float("nan")
            snapshot = (bid, ask, last, close)
            if snapshot != last_printed:
                print(f"quote bid={bid} ask={ask} last={last} close={close}")
                last_printed = snapshot

        print("mode=stream_or_poll_ready")
        print("next_step=wire_incremental_bar_ingest")
    finally:
        manager.disconnect()
        print("ib=disconnected")


if __name__ == "__main__":
    util.patchAsyncio()
    main()
