from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

from manual_regime_bot_api import (
    DailyTradeBatch,
    get_trade_openings_for_day_only,
    get_trade_openings_for_symbols,
)
from src.models.engine_state import EngineState


@dataclass
class EngineCore:
    states: dict[str, EngineState] = field(default_factory=dict)

    def get_state(self, symbol: str) -> EngineState:
        symbol = symbol.upper()
        if symbol not in self.states:
            self.states[symbol] = EngineState(symbol=symbol)
        return self.states[symbol]

    def bootstrap(self, symbol: str) -> EngineState:
        return self.get_state(symbol)

    def evaluate_day(
        self,
        symbol: str,
        day: str | pd.Timestamp,
        decision_log_path: str | Path | None = None,
    ) -> DailyTradeBatch:
        batch = get_trade_openings_for_day_only(symbol, day, decision_log_path=decision_log_path)
        self.get_state(symbol).last_decision_ts = pd.Timestamp(day).strftime("%Y-%m-%d")
        return batch

    def evaluate_basket(
        self,
        symbols: list[str],
        day: str | pd.Timestamp,
        decision_log_path: str | Path | None = None,
    ) -> dict[str, DailyTradeBatch]:
        ordered_symbols = [symbol.upper() for symbol in symbols]
        if decision_log_path is None or len(ordered_symbols) <= 1:
            return {
                symbol: self.evaluate_day(symbol, day, decision_log_path=decision_log_path)
                for symbol in ordered_symbols
            }

        base_path = Path(decision_log_path)
        base_path.parent.mkdir(parents=True, exist_ok=True)
        combined_log_parts: list[str] = []

        with TemporaryDirectory(dir=str(base_path.parent)) as tmpdir:
            tmpdir_path = Path(tmpdir)
            symbol_batches = get_trade_openings_for_symbols(
                ordered_symbols,
                day,
                log_dir=tmpdir_path,
            )
            results: dict[str, DailyTradeBatch] = {}
            for item in symbol_batches:
                results[item.symbol] = item.batch
                self.get_state(item.symbol).last_decision_ts = pd.Timestamp(day).strftime("%Y-%m-%d")
            for symbol in ordered_symbols:
                symbol_log_path = tmpdir_path / f"Trading_Engine_{symbol}_{pd.Timestamp(day).normalize().date()}_decision.log"
                if symbol_log_path.exists():
                    text = symbol_log_path.read_text(encoding="utf-8").strip()
                    if text:
                        combined_log_parts.append(text)

        base_path.write_text("\n".join(combined_log_parts) + ("\n" if combined_log_parts else ""), encoding="utf-8")
        return results
