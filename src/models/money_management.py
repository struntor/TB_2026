from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class MoneyManagementConfig:
    account_size_usd: float
    risk_per_trade_pct: float
    max_open_trades: int
    max_open_trades_long: int = 5
    max_open_trades_short: int = 5
    currency: str = "USD"
    version: str = "v1"
    as_of: str = ""
    description: str = ""

    @property
    def risk_per_trade_fraction(self) -> float:
        return self.risk_per_trade_pct / 100.0

    @property
    def risk_budget_usd(self) -> float:
        return self.account_size_usd * self.risk_per_trade_fraction

    @classmethod
    def from_config(cls, config_path: str | Path) -> "MoneyManagementConfig":
        path = Path(config_path)
        data = json.loads(path.read_text(encoding="utf-8"))
        account_size_usd = float(data["account_size_usd"])
        risk_per_trade_pct = float(data["risk_per_trade_pct"])
        max_open_trades = int(data["max_open_trades"])
        max_open_trades_long = int(data.get("max_open_trades_long", max_open_trades))
        max_open_trades_short = int(data.get("max_open_trades_short", max_open_trades))
        currency = str(data.get("currency", "USD"))
        version = str(data.get("version", "v1"))
        as_of = str(data.get("as_of", ""))
        description = str(data.get("description", ""))

        if account_size_usd <= 0:
            raise ValueError("account_size_usd must be positive")
        if not (0 < risk_per_trade_pct <= 100):
            raise ValueError("risk_per_trade_pct must be in the range (0, 100]")
        if max_open_trades < 1:
            raise ValueError("max_open_trades must be >= 1")
        if max_open_trades_long < 1:
            raise ValueError("max_open_trades_long must be >= 1")
        if max_open_trades_short < 1:
            raise ValueError("max_open_trades_short must be >= 1")

        return cls(
            account_size_usd=account_size_usd,
            risk_per_trade_pct=risk_per_trade_pct,
            max_open_trades=max_open_trades,
            max_open_trades_long=max_open_trades_long,
            max_open_trades_short=max_open_trades_short,
            currency=currency,
            version=version,
            as_of=as_of,
            description=description,
        )
