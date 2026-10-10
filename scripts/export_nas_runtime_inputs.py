"""Transfer the public cached inputs required by the stock-IVR70 scanner."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from export_nas_data import verified_copy


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nas-home", type=Path, required=True)
    parser.add_argument("--source", type=Path, default=Path(r"D:\Trading"))
    parser.add_argument("--stage", type=Path, required=True)
    args = parser.parse_args()
    relative = Path("research/single_stock_drawdown_recovery_put_credit")
    source = args.source / relative
    files = [source / "equity_screen_v1/asset_diagnostics.csv"]
    files += sorted((source / "sec_earnings_cache_all").glob("*.json"))
    if len(files) < 2:
        raise RuntimeError("Missing IVR70 scanner inputs")
    run = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    rows = []
    for i, path in enumerate(files, 1):
        target = args.nas_home / "Rohdaten/runtime_inputs" / relative / path.relative_to(source)
        rows.append(verified_copy(path, target, run))
        if i % 50 == 0 or i == len(files):
            print(f"IVR70 inputs verified: {i}/{len(files)}", flush=True)
    args.stage.mkdir(parents=True, exist_ok=True)
    report = args.stage / "runtime_inputs_manifest.json"
    report.write_text(json.dumps({"files": rows}, indent=2) + "\n", encoding="utf-8")
    verified_copy(report, args.nas_home / "Journal" / f"runtime_inputs_manifest_{run}.json")
    print(f"Runtime inputs transferred: {len(rows)}", flush=True)


if __name__ == "__main__":
    main()
