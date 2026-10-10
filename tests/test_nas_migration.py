from pathlib import Path
from contextlib import closing
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
from export_nas_data import verified_copy
from restore_nas_data import destination_for
from export_nas_data import main as export_main
from export_nas_runtime_inputs import main as export_inputs_main
from restore_nas_data import main as restore_main


class NasMigrationTests(unittest.TestCase):
    def test_changed_destination_is_preserved(self):
        temporary_root = SCRIPTS.parent / "runs"
        temporary_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temporary_root) as folder:
            root = Path(folder)
            source, target = root / "source", root / "target"
            source.write_bytes(b"new data")
            target.write_bytes(b"old data")
            with self.assertRaises(FileExistsError):
                verified_copy(source, target)
            self.assertEqual(target.read_bytes(), b"old data")
            verified_copy(source, target, "test")
            self.assertEqual(target.read_bytes(), b"new data")
            self.assertEqual((root / "target.before-test").read_bytes(), b"old data")

    def test_restore_rejects_unexpected_and_parent_paths(self):
        for path in ["../private", "Journal/arbitrary.sqlite", "Rohdaten/other.txt"]:
            with self.assertRaises(ValueError):
                destination_for(Path(path), Path("runtime"))
        self.assertEqual(destination_for(Path("Journal/strategy_state/trade.json"), Path("runtime")),
                         Path("runtime/state/trade.json"))
        self.assertEqual(destination_for(Path("Rohdaten/assets.csv"), Path("runtime")),
                         Path("runtime/assets.csv"))

    def test_snapshot_export_restore_roundtrip(self):
        temporary_root = SCRIPTS.parent / "runs"
        temporary_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temporary_root) as folder:
            root = Path(folder)
            source, nas, restored = root / "source", root / "nas", root / "restored"
            for path in [source / "journal", source / "Rohdaten", source / "state", nas]:
                path.mkdir(parents=True)
            with closing(sqlite3.connect(source / "journal/trading_journal.sqlite")) as db:
                db.execute("CREATE TABLE trades (id INTEGER, status TEXT)")
                db.execute("INSERT INTO trades VALUES (1, 'open')")
                db.commit()
            (source / "Rohdaten/Rohdaten_SPY.csv").write_text("date,close\n2026-10-09,100\n")
            (source / "assets.csv").write_text("symbol,market\nSPY,US\n")
            (source / "state/strategy.json").write_text('{"position": 1}')
            research = source / "research/single_stock_drawdown_recovery_put_credit"
            (research / "equity_screen_v1").mkdir(parents=True)
            (research / "sec_earnings_cache_all").mkdir()
            (research / "equity_screen_v1/asset_diagnostics.csv").write_text("symbol\nSPY\n")
            (research / "sec_earnings_cache_all/tickers.json").write_text("{}")
            args = ["tool", "--source", str(source), "--nas-home", str(nas),
                    "--stage", str(root / "stage")]
            with patch.object(sys, "argv", args):
                export_main()
                export_inputs_main()
            with patch.object(sys, "argv", ["tool", "--nas-home", str(nas), "--target", str(restored)]):
                restore_main()
            with closing(sqlite3.connect(restored / "journal/trading_journal.sqlite")) as db:
                self.assertEqual(db.execute("SELECT status FROM trades").fetchone(), ("open",))
            self.assertEqual((restored / "state/strategy.json").read_text(), '{"position": 1}')
            self.assertTrue((restored / "research/single_stock_drawdown_recovery_put_credit/equity_screen_v1/asset_diagnostics.csv").is_file())


if __name__ == "__main__":
    unittest.main()
