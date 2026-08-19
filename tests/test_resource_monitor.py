from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import unittest

from src.bot.resource_monitor import PhaseResourceMonitor, SCHEMA_VERSION


class ResourceMonitorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.log_path = Path(
            r"D:\Trading\journal\orchestrator_test_runtime\resource_monitor.jsonl"
        )
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_path.unlink(missing_ok=True)

    def test_process_resources_are_written_as_jsonl(self) -> None:
        command = [
            sys.executable,
            "-c",
            "import time; payload=bytearray(8*1024*1024); time.sleep(1.0)",
        ]
        process = subprocess.Popen(command)
        monitor = PhaseResourceMonitor(
            phase="test",
            command=command,
            log_path=self.log_path,
            sample_seconds=0.1,
        )
        monitor.attach(process.pid)
        return_code = process.wait(timeout=10)
        summary = monitor.finish(return_code)

        self.assertEqual(0, return_code)
        self.assertEqual(SCHEMA_VERSION, summary["schema_version"])
        self.assertGreater(summary["sample_count"], 1)
        self.assertGreater(summary["process_tree"]["peak_private_bytes"], 0)
        self.assertGreaterEqual(summary["process_tree"]["peak_process_count"], 1)
        self.assertGreater(summary["wall_seconds"], 0.5)
        written = json.loads(self.log_path.read_text(encoding="utf-8").strip())
        self.assertEqual("test", written["phase"])


if __name__ == "__main__":
    unittest.main()
