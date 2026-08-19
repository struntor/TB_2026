from __future__ import annotations

from pathlib import Path
import sys

import pandas as pd


ENGINE_ROOT = (
    Path(__file__).resolve().parents[2]
    / "PythonProject"
    / "statistic"
    / "spx_adx_di_5min_2024_02_03"
)
if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))

import manual_regime_decision_time_manifest as timing_manifest
import manual_regime_rules_backtest_v1_1 as timing_engine


def main() -> None:
    expected = {"10m": "09:35", "60m": "10:25", "90m": "10:55"}
    for symbol in ("CEG", "SPX"):
        for key, variant in timing_manifest.VARIANTS.items():
            records = []
            timing_engine.run_backtest(
                symbol,
                "2025-01-01",
                "2025-02-01",
                bull_continuation_mode="combo",
                max_history_days=548,
                decision_records_out=records,
                decision_bar_number=variant.decision_bar_number,
                strategy_version=variant.strategy_version,
                decision_version_context=variant.version_context(),
            )
            observed_et = {
                pd.Timestamp(record.decision_time_utc)
                .tz_convert("America/New_York")
                .strftime("%H:%M")
                for record in records
            }
            if observed_et != {expected[key]}:
                raise RuntimeError(
                    f"{symbol} {key}: expected {expected[key]} ET, got {sorted(observed_et)}"
                )
            if len({record.signal_key for record in records}) != len(records):
                raise RuntimeError(f"{symbol} {key}: duplicate variant signal keys")
            print(
                f"{symbol} {key}: records={len(records)} observed_et={sorted(observed_et)}",
                flush=True,
            )


if __name__ == "__main__":
    main()
