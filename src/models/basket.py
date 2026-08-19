from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class BasketRequest:
    symbol: str
    start: str
    end_exclusive: str
    structure_lookback: int = 10
    bull_continuation_mode: str = "combo"
    learn_long_mode: str = "off"
    learn_long_window: int = 5
    learn_long_min_trades: int = 3
    learn_long_min_win_rate: float = 0.45
    learn_long_cooldown_days: int = 15


@dataclass(frozen=True)
class BasketDefinition:
    name: str
    symbols: tuple[str, ...]
    description: str = ""


@dataclass(frozen=True)
class BasketSelection:
    day: str
    basket: BasketDefinition
    request: BasketRequest
    notes: tuple[str, ...] = field(default_factory=tuple)
