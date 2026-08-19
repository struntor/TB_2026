from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable


RiskProvider = Callable[[], float]


@dataclass(slots=True)
class OptionsRiskBudget:
    """Shared defined-risk budget for independently running option monitors."""

    maximum_open_risk_usd: float
    _providers: dict[str, RiskProvider] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.maximum_open_risk_usd <= 0:
            raise ValueError("maximum_open_risk_usd must be positive")

    def register(self, strategy_id: str, provider: RiskProvider) -> None:
        if not strategy_id:
            raise ValueError("strategy_id must not be empty")
        existing = self._providers.get(strategy_id)
        if existing is not None and existing is not provider:
            raise ValueError(f"risk provider already registered for {strategy_id}")
        self._providers[strategy_id] = provider

    @property
    def open_risk_usd(self) -> float:
        values = [float(provider()) for provider in self._providers.values()]
        if any(value < 0 for value in values):
            raise ValueError("open option risk must not be negative")
        return sum(values)

    @property
    def available_risk_usd(self) -> float:
        return max(0.0, self.maximum_open_risk_usd - self.open_risk_usd)

    def can_allocate(self, requested_risk_usd: float) -> bool:
        if requested_risk_usd <= 0:
            raise ValueError("requested_risk_usd must be positive")
        return self.open_risk_usd + requested_risk_usd <= self.maximum_open_risk_usd + 1e-9

