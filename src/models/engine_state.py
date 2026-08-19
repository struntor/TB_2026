from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class EngineState:
    symbol: str
    regime: str = "sideways"
    last_confirmed_swing_high: float | None = None
    last_confirmed_swing_low: float | None = None
    broken_support: float | None = None
    broken_resistance: float | None = None
    open_trades: list[dict[str, Any]] = field(default_factory=list)
    exit_state: dict[str, Any] = field(default_factory=dict)
    gating_state: dict[str, Any] = field(default_factory=dict)
    last_processed_bar_ts: str | None = None
    last_decision_ts: str | None = None

