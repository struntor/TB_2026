"""Create a verified NAS migration snapshot without deleting local data."""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import time
import uuid


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def verified_copy(source: Path, target: Path, backup_suffix: str | None = None) -> dict:
    target.parent.mkdir(parents=True, exist_ok=True)
    before = source.stat()
    source_hash = digest(source)
    same = target.exists() and digest(target) == source_hash
    if target.exists() and not same and backup_suffix is None:
        raise FileExistsError(f"Different file already exists; retained: {target}")
    if not same:
        temporary = target.with_name(target.name + ".incoming-" + uuid.uuid4().hex)
        try:
            shutil.copyfile(source, temporary)
            if digest(temporary) != source_hash:
                raise RuntimeError(f"Copy checksum mismatch: {target}")
            after = source.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise RuntimeError(f"Source changed during transfer: {source}")
            # Preserve a previous NAS version before installing the verified copy.
            if target.exists():
                backup = target.with_name(target.name + ".before-" + backup_suffix)
                if backup.exists():
                    raise FileExistsError(backup)
                target.rename(backup)
                try:
                    temporary.rename(target)
                except BaseException:
                    backup.rename(target)
                    raise
            else:
                temporary.rename(target)
        finally:
            temporary.unlink(missing_ok=True)
    after = source.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise RuntimeError(f"Source changed during verification: {source}")
    return {"path": str(target), "bytes": before.st_size, "sha256": source_hash}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(r"D:\Trading"))
    parser.add_argument("--nas-home", type=Path, required=True)
    parser.add_argument("--stage", type=Path, required=True)
    parser.add_argument("--resume-snapshot", action="store_true")
    parser.add_argument("--preserve-existing", action="store_true")
    args = parser.parse_args()
    args.stage.mkdir(parents=True, exist_ok=True)
    nas = args.nas_home
    if not nas.is_dir():
        raise FileNotFoundError(nas)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report = {"created_utc": run_id, "source": str(args.source), "files": []}
    snapshot = args.stage / "trading_journal.sqlite"
    if snapshot.exists() and not args.resume_snapshot:
        raise FileExistsError(f"Use a fresh staging directory: {snapshot}")
    started = time.monotonic()
    last_progress = 0.0

    def progress(status, remaining, total):
        nonlocal last_progress
        now = time.monotonic()
        if now - last_progress >= 15 or remaining == 0:
            print(f"SQLite snapshot: {total - remaining}/{total} pages", flush=True)
            last_progress = now

    source_db = args.source / "journal" / "trading_journal.sqlite"
    # Online backup reads a consistent database and includes committed WAL data.
    if not snapshot.exists():
        with closing(sqlite3.connect(source_db.resolve().as_uri() + "?mode=ro", uri=True)) as source:
            with closing(sqlite3.connect(snapshot)) as destination:
                source.backup(destination, pages=8192, progress=progress, sleep=0.1)
    with closing(sqlite3.connect(snapshot.resolve().as_uri() + "?mode=ro", uri=True)) as destination:
        check = destination.execute("PRAGMA quick_check").fetchall()
        if check != [("ok",)]:
            raise RuntimeError(f"SQLite integrity check failed: {check}")
    report["journal_snapshot_mtime_utc"] = datetime.fromtimestamp(
        snapshot.stat().st_mtime, timezone.utc).isoformat()
    print(f"SQLite snapshot checked ({time.monotonic() - started:.1f}s)", flush=True)
    raw = sorted((args.source / "Rohdaten").glob("*.csv"))
    if not raw:
        raise RuntimeError("No raw-data CSV files found")
    for number, path in enumerate(raw, 1):
        report["files"].append(verified_copy(path, nas / "Rohdaten" / path.name,
                                             run_id if args.preserve_existing else None))
        if number % 20 == 0 or number == len(raw):
            print(f"Raw data verified: {number}/{len(raw)}", flush=True)
    report["files"].append(verified_copy(snapshot, nas / "Journal" / snapshot.name,
                                         run_id if args.preserve_existing else None))
    print("Journal snapshot copied and SHA-256 verified", flush=True)
    for path in sorted((args.source / "state").glob("*.json")):
        report["files"].append(verified_copy(path, nas / "Journal" / "strategy_state" / path.name,
                                             run_id if args.preserve_existing else None))
    report["files"].append(verified_copy(args.source / "assets.csv", nas / "Rohdaten" / "assets.csv",
                                         run_id if args.preserve_existing else None))
    # Day-supervisor state and lock files are deliberately not restored: the new
    # computer must run its own preparation and acquire its own process lock.
    report["sqlite_quick_check"] = "ok"
    report["completed_utc"] = datetime.now(timezone.utc).isoformat()
    report["total_bytes"] = sum(item["bytes"] for item in report["files"])
    encoded = json.dumps(report, indent=2) + "\n"
    local_report = args.stage / "transfer_manifest.json"
    local_report.write_text(encoded, encoding="utf-8")
    verified_copy(local_report, nas / "Journal" / f"transfer_manifest_{run_id}.json")
    print(json.dumps({"status": "ok", "files": len(report["files"]),
                      "bytes": report["total_bytes"], "manifest": str(local_report)}), flush=True)


if __name__ == "__main__":
    main()
