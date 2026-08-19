from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
import json

from src.models.basket import BasketDefinition, BasketRequest, BasketSelection


class BasketSelector(Protocol):
    def select_basket(self, current_day: str, request: BasketRequest | None = None) -> BasketSelection:
        ...


@dataclass(frozen=True)
class StaticBasketSelector:
    baskets: dict[str, BasketDefinition]
    default_basket: str = "SPX"
    start: str = "2023-01-01"

    @classmethod
    def from_config(cls, config_path: str | Path) -> "StaticBasketSelector":
        path = Path(config_path)
        data = json.loads(path.read_text(encoding="utf-8"))
        baskets: dict[str, BasketDefinition] = {}
        for entry in data.get("baskets", []):
            baskets[entry["name"].upper()] = BasketDefinition(
                name=entry["name"].upper(),
                symbols=tuple(entry["symbols"]),
                description=entry.get("description", ""),
            )
        default_basket = data.get("default_basket", "SPX").upper()
        start = data.get("start", "2023-01-01")
        return cls(baskets=baskets, default_basket=default_basket, start=start)

    def select_basket(self, current_day: str, request: BasketRequest | None = None) -> BasketSelection:
        basket_name = (request.symbol if request is not None else self.default_basket).upper()
        basket = self.baskets.get(basket_name)
        if basket is None:
            raise KeyError(f"Unknown basket '{basket_name}'.")
        if request is None:
            end_exclusive = current_day
            request = BasketRequest(symbol=basket.name, start=self.start, end_exclusive=end_exclusive)
        notes = (
            f"basket={basket.name}",
            f"symbols={','.join(basket.symbols)}",
        )
        return BasketSelection(day=current_day, basket=basket, request=request, notes=notes)

