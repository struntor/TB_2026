"""Restore verified NAS migration data into an empty local Windows runtime."""
import argparse
import json
from pathlib import Path

from export_nas_data import digest, verified_copy


def destination_for(relative: Path, root: Path) -> Path:
    parts = relative.parts
    if ".." in parts or relative.is_absolute():
        raise ValueError(f"Unsafe manifest path: {relative}")
    if parts[0] == "Rohdaten":
        if len(parts) == 2 and parts[1] == "assets.csv":
            return root / "assets.csv"
        if len(parts) > 2 and parts[1] == "runtime_inputs":
            return root.joinpath(*parts[2:])
        if len(parts) == 2 and parts[1].startswith("Rohdaten_") and relative.suffix == ".csv":
            return root / relative
    if parts[0] == "Journal":
        if len(parts) == 2 and parts[1] == "trading_journal.sqlite":
            return root / "journal" / parts[1]
        if len(parts) == 3 and parts[1] == "strategy_state" and relative.suffix == ".json":
            return root / "state" / parts[2]
    raise ValueError(f"Unexpected migration file: {relative}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nas-home", type=Path, required=True)
    parser.add_argument("--target", type=Path, default=Path(r"D:\Trading"))
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--inputs-manifest", type=Path)
    args = parser.parse_args()
    nas = args.nas_home.resolve()
    manifests = []
    for explicit, pattern in [(args.manifest, "transfer_manifest_*.json"),
                              (args.inputs_manifest, "runtime_inputs_manifest_*.json")]:
        candidates = sorted((nas / "Journal").glob(pattern))
        chosen = explicit or (candidates[-1] if candidates else None)
        if chosen is None:
            raise FileNotFoundError(f"Missing {pattern}")
        manifests.append(chosen)
    jobs = []
    for manifest in manifests:
        for row in json.loads(manifest.read_text(encoding="utf-8"))["files"]:
            source = Path(row["path"]).resolve()
            relative = source.relative_to(nas)
            target = destination_for(relative, args.target)
            # Check every input and destination before making any local changes.
            if source.stat().st_size != row["bytes"] or digest(source) != row["sha256"]:
                raise RuntimeError(f"NAS checksum mismatch: {source}")
            if target.exists() and digest(target) != row["sha256"]:
                raise FileExistsError(f"Existing local data retained: {target}")
            jobs.append((source, target))
    print(f"All {len(jobs)} files validated; restoring local runtime", flush=True)
    for number, (source, target) in enumerate(jobs, 1):
        verified_copy(source, target)
        if number % 20 == 0 or number == len(jobs):
            print(f"Restored and verified: {number}/{len(jobs)}", flush=True)
    print("Restore complete. Run offline preflight, then the normal day pipeline.", flush=True)


if __name__ == "__main__":
    main()
