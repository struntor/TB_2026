"""Offline deployment check. Never connects to TWS or changes journal state."""
from __future__ import annotations

import asyncio
import importlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "statistic" / "spx_adx_di_5min_2024_02_03"


def main() -> None:
    # Windows creates an internal socketpair for the event loop. Initialize that
    # before blocking network connections; importing runtime code stays offline.
    asyncio.set_event_loop(asyncio.new_event_loop())
    # A remaining fallback to the old checkout must fail, even if it still exists.
    old = str(ROOT.parent / "PythonProject").casefold()

    def guard(event, args):
        if event == "socket.connect":
            raise RuntimeError("Network access is forbidden during the layout check")
        if event == "open" and args and isinstance(args[0], (str, bytes)):
            path = str(args[0]).replace("/", "\\").casefold()
            if path.startswith(old.replace("/", "\\") + "\\"):
                raise RuntimeError("Runtime still accesses the old PythonProject checkout")

    sys.addaudithook(guard)
    sys.path[:] = [p for p in sys.path if "pythonproject" not in p.casefold()]
    sys.path[:0] = [str(ROOT), str(ENGINE)]
    names = [
        "live_manual_regime_tws_bot", "manual_regime_bot_api",
        "manual_regime_rules_backtest", "current_live_baseline_config",
        "research.stock_ivr70_live_candidates", "Daten_Download.Download_Asset_Data",
        "Daten_Download.thetadata_options_client", "src.compare.strategy_compare",
    ]
    loaded = {}
    for name in names:
        module = importlib.import_module(name)
        path = Path(module.__file__).resolve()
        if not path.is_relative_to(ROOT):
            raise RuntimeError(f"External runtime module: {name}: {path}")
        loaded[name] = str(path.relative_to(ROOT))
    from current_live_baseline_config import load_optimized_live_top50_symbols, load_live_keep_pairs
    import manual_regime_gap_quarter_v4_manifest as manifest
    manifest.verify_code_hashes()
    rule_stack = ENGINE / "current_baseline_rule_stack_2026_07_04.json"
    json.loads(rule_stack.read_text(encoding="utf-8"))
    symbols = load_optimized_live_top50_symbols()
    pairs = load_live_keep_pairs()
    assert len(symbols) == 50 and set(symbols) == {symbol for symbol, _ in pairs}
    forbidden = [name for name, mod in sys.modules.copy().items()
                 if "pythonproject" in str(getattr(mod, "__file__", "")).casefold()]
    assert not forbidden, forbidden
    print(json.dumps({"status": "ok", "root": str(ROOT), "modules": loaded,
                      "symbols": len(symbols), "enabled_pairs": len(pairs),
                      "strategy_hashes": "unchanged", "network": "blocked"}, indent=2))


if __name__ == "__main__":
    main()
