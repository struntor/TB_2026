from __future__ import annotations

import hashlib
from pathlib import Path
import sys

import pandas as pd


BOT_ROOT = Path(__file__).resolve().parents[1]
ENGINE_ROOT = (
    BOT_ROOT.parent
    / "PythonProject"
    / "statistic"
    / "spx_adx_di_5min_2024_02_03"
)
for path in (BOT_ROOT, BOT_ROOT / "scripts", ENGINE_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import manual_regime_decision_time_manifest as timing_manifest
import manual_regime_rules_backtest_v1_1 as timing_engine
import run_manual_regime_decision_time_variants as runner


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    symbol = "CEG"
    variant = timing_manifest.VARIANTS["90m"]
    end_exclusive = pd.Timestamp("2025-02-01")
    root = Path(r"D:\Trading\journal\smoke\decision-time-symbol-cache-real")
    (root / "logs").mkdir(parents=True, exist_ok=True)
    raw_path = timing_engine.RAW_DIR / f"Rohdaten_{symbol}.csv"
    common_inputs = {
        "data_revision": "real-smoke-2025-02-01",
        "files": {"timing_engine": {"sha256": sha256(Path(timing_engine.__file__))}},
        "raw_data": {
            symbol: {
                "sha256": sha256(raw_path),
                "size": raw_path.stat().st_size,
            }
        },
    }
    returned_symbol, completed, openings = runner.run_symbol(
        symbol,
        str(root / "logs"),
        end_exclusive.date().isoformat(),
        variant.key,
    )
    if returned_symbol != symbol:
        raise RuntimeError(f"Unexpected symbol: {returned_symbol}")
    expectation = runner.symbol_cache_expectation(
        symbol, variant, common_inputs, end_exclusive
    )
    cache_dir = root / "cache" / symbol
    runner.write_symbol_cache(
        cache_dir,
        expectation=expectation,
        completed=completed,
        openings=openings,
    )
    loaded = runner.load_valid_symbol_cache(cache_dir, expectation=expectation)
    if loaded is None:
        raise RuntimeError("Fresh real-data symbol cache did not validate")
    loaded_completed, loaded_openings = loaded
    if completed.to_csv(index=False) != loaded_completed.to_csv(index=False):
        raise RuntimeError("Completed CEG cache payload changed during roundtrip")
    if [item.to_json() for item in openings] != [
        item.to_json() for item in loaded_openings
    ]:
        raise RuntimeError("CEG DecisionRecord cache payload changed during roundtrip")
    print(
        f"real cache ok: {symbol} {variant.key} completed={len(completed)} "
        f"openings={len(openings)} path={cache_dir}",
        flush=True,
    )


if __name__ == "__main__":
    main()
