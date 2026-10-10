from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ConsolidatedRuntimeTests(unittest.TestCase):
    def test_runtime_imports_without_old_checkout_or_network(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "verify_runtime_layout.py")],
            cwd=ROOT, capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('"status": "ok"', result.stdout)

    def test_pipeline_uses_only_own_repo_for_executables(self):
        # Construct only the path properties; do not instantiate/run a supervisor.
        from src.bot.daily_orchestrator import OrchestratorConfig
        config = object.__new__(OrchestratorConfig)
        object.__setattr__(config, "projects_root", ROOT.parent)
        self.assertEqual(config.bot_root, ROOT)
        self.assertTrue(config.engine_root.is_relative_to(ROOT))
        self.assertTrue(config.downloader_path.is_relative_to(ROOT))
        self.assertTrue(config.downloader_path.is_file())
        self.assertTrue((config.engine_root / "live_manual_regime_tws_bot.py").is_file())


if __name__ == "__main__":
    unittest.main()
