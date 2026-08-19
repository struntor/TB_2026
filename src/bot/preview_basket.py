from __future__ import annotations

import argparse
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.append(str(Path(__file__).resolve().parents[2]))

from src.bot.basket_selector import StaticBasketSelector
from src.models.basket import BasketRequest


def main() -> None:
    parser = argparse.ArgumentParser(description="Preview the basket selection for a given day.")
    parser.add_argument("--day", required=True)
    parser.add_argument("--symbol", default="CURRENT_UNIVERSE")
    parser.add_argument("--config", default=str(Path("configs") / "baskets_v1.json"))
    args = parser.parse_args()

    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = Path(__file__).resolve().parents[2] / config_path
    selector = StaticBasketSelector.from_config(config_path)
    request = BasketRequest(symbol=args.symbol, start=selector.start, end_exclusive=args.day)
    selection = selector.select_basket(args.day, request)

    print(f"day={selection.day}")
    print(f"basket={selection.basket.name}")
    print(f"symbols={','.join(selection.basket.symbols)}")
    print(f"request_symbol={selection.request.symbol}")
    print(f"request_start={selection.request.start}")
    print(f"request_end_exclusive={selection.request.end_exclusive}")
    if selection.basket.description:
        print(f"description={selection.basket.description}")


if __name__ == "__main__":
    main()
