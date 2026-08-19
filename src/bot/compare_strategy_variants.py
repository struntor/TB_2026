from __future__ import annotations

import argparse
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.append(str(Path(__file__).resolve().parents[2]))
    sys.path.append(str(Path(__file__).resolve().parents[3] / "PythonProject" / "statistic" / "spx_adx_di_5min_2024_02_03"))

from src.compare.strategy_compare import load_compare_config, run_many_symbol_comparisons, run_symbol_comparison


def _resolve_path(path_str: str) -> Path:
    path = Path(path_str)
    if path.is_absolute():
        return path
    return Path(__file__).resolve().parents[2] / path


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare the engine's underlying proxy against a ZEBRA options variant.")
    parser.add_argument("--symbol", default="SPX")
    parser.add_argument("--symbols", default="")
    parser.add_argument("--start", default="2024-01-01")
    parser.add_argument("--end-exclusive", default="2026-06-24")
    parser.add_argument("--target-dte", type=int, default=None)
    parser.add_argument("--config", default=str(Path("configs") / "strategy_compare_v1.json"))
    parser.add_argument("--output-dir", default=str(Path("runs") / "strategy_compare"))
    parser.add_argument("--cache-dir", default=str(Path("runs") / "strategy_compare_cache"))
    parser.add_argument("--credentials-file", default=str(Path(__file__).resolve().parents[3] / "PythonProject" / "config" / "thetadata_creds.txt"))
    args = parser.parse_args()

    config_path = _resolve_path(args.config)
    config = load_compare_config(config_path)
    target_dte = int(args.target_dte) if args.target_dte is not None else int(config["target_dte"])

    symbols = [item.strip().upper() for item in args.symbols.split(",") if item.strip()]
    if symbols:
        result = run_many_symbol_comparisons(
            symbols,
            args.start,
            args.end_exclusive,
            target_dte=target_dte,
            option_strike_range=int(config["option_strike_range"]),
            option_multiplier=int(config["option_multiplier"]),
            use_mid_prices=bool(config["use_mid_prices"]),
            proxy_symbol_map=dict(config["proxy_symbol_map"]),
            option_root_map=dict(config["option_root_map"]),
            cache_dir=_resolve_path(args.cache_dir),
            credentials_file=_resolve_path(args.credentials_file),
            output_dir=_resolve_path(args.output_dir),
        )
        print(f"symbols={','.join(symbols)}")
    else:
        result = run_symbol_comparison(
            args.symbol,
            args.start,
            args.end_exclusive,
            target_dte=target_dte,
            option_strike_range=int(config["option_strike_range"]),
            option_multiplier=int(config["option_multiplier"]),
            use_mid_prices=bool(config["use_mid_prices"]),
            proxy_symbol_map=dict(config["proxy_symbol_map"]),
            option_root_map=dict(config["option_root_map"]),
            cache_dir=_resolve_path(args.cache_dir),
            credentials_file=_resolve_path(args.credentials_file),
            output_dir=_resolve_path(args.output_dir),
        )
        print(f"symbol={args.symbol.upper()}")
    print(f"output_dir={result.output_dir}")
    print(f"config={config_path}")
    if not result.summary.empty:
        print(result.summary.to_string(index=False))


if __name__ == "__main__":
    main()
