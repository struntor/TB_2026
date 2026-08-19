from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, time
import json
from pathlib import Path
import unittest

from src.bot.daily_orchestrator import (
    DailyTradingOrchestrator,
    OrchestratorConfig,
    StateStore,
    is_us_equity_session,
    next_us_equity_session_on_or_after,
    previous_us_equity_session,
    ET,
)


class DailyOrchestratorTests(unittest.TestCase):
    def setUp(self) -> None:
        root = Path(r"D:\Trading\journal\orchestrator_test_runtime")
        root.mkdir(parents=True, exist_ok=True)
        for name in ("state.json", "state.json.tmp", "bot.log", "bot.lock"):
            (root / name).unlink(missing_ok=True)
        (root / "performance.jsonl").unlink(missing_ok=True)
        self.config = OrchestratorConfig(
            python_executable=Path(r"D:\PycharmProjects\.venv\Scripts\python.exe"),
            projects_root=Path(r"D:\PycharmProjects"),
            trading_root=root,
            journal_path=root / "journal.sqlite",
            state_path=root / "state.json",
            lock_path=root / "bot.lock",
            log_path=root / "bot.log",
            performance_log_path=root / "performance.jsonl",
            host="127.0.0.1",
            port=7496,
            sync_client_id=63,
            live_client_id=62,
            workers=24,
            prepare_et=time(5, 0),
            latest_launch_et=time(9, 35),
            retry_seconds=300,
            risk_per_trade=100,
            place_orders=True,
            allow_live_account=False,
            account=None,
            enable_failed_auction_fly=False,
            enable_spxw_15dte_put_credit=True,
            enable_spxw_0dte_bull_put_iu30=True,
            enable_spxw_75dte_put_bwb=True,
            enable_stock_ivr70_put_credit=True,
            spxw_75dte_bwb_initial_entry_work_minutes=5,
            spxw_75dte_bwb_entry_work_minutes=5,
            spxw_75dte_bwb_final_entry_work_minutes=5,
            spxw_75dte_bwb_max_risk_usd=10_500.0,
            options_account_value_usd=25_000,
            options_max_risk_fraction=0.10,
            enable_xsp_vix_pullback=True,
            xsp_vix_pullback_entry_work_minutes=10,
        )
        self.orchestrator = DailyTradingOrchestrator(self.config)

    def test_us_market_calendar_handles_weekend_and_core_holidays(self) -> None:
        self.assertFalse(is_us_equity_session(date(2026, 7, 3)))
        self.assertFalse(is_us_equity_session(date(2026, 4, 3)))
        self.assertFalse(is_us_equity_session(date(2026, 7, 25)))
        self.assertTrue(is_us_equity_session(date(2026, 7, 24)))
        self.assertEqual(date(2026, 7, 24), previous_us_equity_session(date(2026, 7, 27)))
        self.assertEqual(
            date(2026, 7, 27),
            next_us_equity_session_on_or_after(date(2026, 7, 25)),
        )

    def test_commands_share_port_and_explicit_model_history(self) -> None:
        session_day = date(2026, 7, 27)
        sync = self.orchestrator.journal_sync_command(session_day)
        backtest = self.orchestrator.backtest_command(session_day)
        live = self.orchestrator.live_command(session_day, "model-run")
        live_with_irrelevant_gate = self.orchestrator.live_command(
            session_day,
            "model-run",
            enable_production=True,
        )

        self.assertEqual("7496", sync[sync.index("--port") + 1])
        self.assertEqual("2026-07-24", sync[sync.index("--execution-start-date") + 1])
        self.assertEqual("2026-07-27", backtest[backtest.index("--end-exclusive") + 1])
        self.assertEqual("model-run", live[live.index("--model-history-run-id") + 1])
        self.assertEqual("00:00", live[live.index("--start-local") + 1])
        self.assertIn("--place-orders", live)
        self.assertIn("--enable-spxw-15dte-put-credit", live)
        self.assertIn("--enable-spxw-0dte-bull-put-iu30", live)
        self.assertIn("--enable-spxw-75dte-put-bwb", live)
        self.assertEqual(
            "5", live[live.index("--spxw-75dte-bwb-initial-entry-work-minutes") + 1]
        )
        self.assertEqual(
            "5", live[live.index("--spxw-75dte-bwb-entry-work-minutes") + 1]
        )
        self.assertEqual(
            "5", live[live.index("--spxw-75dte-bwb-final-entry-work-minutes") + 1]
        )
        self.assertIn("--spxw-75dte-bwb-max-risk-usd", live)
        self.assertIn("10500.0", live)
        self.assertIn("--enable-stock-ivr70-put-credit", live)
        self.assertIn("--enable-xsp-vix-pullback", live)
        self.assertEqual(
            "10", live[live.index("--xsp-vix-pullback-entry-work-minutes") + 1]
        )
        self.assertIn("--enable-lhf-eod-profit-protection", live)
        self.assertNotIn("--dual-broker", live_with_irrelevant_gate)

    def test_lhf_eod_profit_protection_is_never_enabled_for_live_account(self) -> None:
        live_config = replace(self.config, allow_live_account=True)
        command = DailyTradingOrchestrator(live_config).live_command(
            date(2026, 7, 27), "model-run"
        )

        self.assertNotIn("--enable-lhf-eod-profit-protection", command)

    def test_dual_broker_builds_account_scoped_syncs_and_one_live_process(self) -> None:
        dual_config = replace(
            self.config,
            account="DU123456",
            dual_broker=True,
            production_host="127.0.0.1",
            production_port=7497,
            production_sync_client_id=73,
            production_live_client_id=72,
            production_account="U987654",
            production_place_orders=True,
        )
        orchestrator = DailyTradingOrchestrator(dual_config)
        day = date(2026, 7, 27)

        paper_sync = orchestrator.journal_sync_command(day)
        production_sync = orchestrator.journal_sync_command(day, production=True)
        live = orchestrator.live_command(day, "model-run")

        self.assertEqual("7496", paper_sync[paper_sync.index("--port") + 1])
        self.assertEqual("DU123456", paper_sync[paper_sync.index("--account") + 1])
        self.assertNotIn("--allow-live-account", paper_sync)
        self.assertEqual("7497", production_sync[production_sync.index("--port") + 1])
        self.assertEqual(
            "U987654", production_sync[production_sync.index("--account") + 1]
        )
        self.assertIn("--allow-live-account", production_sync)
        self.assertIn("--dual-broker", live)
        self.assertIn("--production-place-orders", live)
        self.assertEqual("7497", live[live.index("--production-port") + 1])
        self.assertEqual(
            "U987654", live[live.index("--production-account") + 1]
        )

        paper_only_live = orchestrator.live_command(
            day,
            "model-run",
            enable_production=False,
        )
        self.assertNotIn("--dual-broker", paper_only_live)
        self.assertNotIn("--production-place-orders", paper_only_live)

    def test_optional_production_phase_fails_closed_without_raising(self) -> None:
        day = date(2026, 7, 27)

        self.orchestrator._optional_phase(
            day,
            "journal_sync_production",
            lambda: (_ for _ in ()).throw(RuntimeError("TWS unavailable")),
        )

        self.assertEqual(
            "unavailable",
            self.orchestrator.state.phase_status(day, "journal_sync_production"),
        )

    def test_completed_phase_is_not_reexecuted(self) -> None:
        calls: list[int] = []
        day = date(2026, 7, 27)

        self.orchestrator._phase(day, "test", lambda: calls.append(1) or {"ok": True})
        self.orchestrator._phase(day, "test", lambda: calls.append(2) or {})

        self.assertEqual([1], calls)
        self.assertEqual("completed", StateStore(self.config.state_path).phase_status(day, "test"))

    def test_failed_phase_is_retriable(self) -> None:
        day = date(2026, 7, 27)

        with self.assertRaisesRegex(RuntimeError, "temporary"):
            self.orchestrator._phase(
                day, "test", lambda: (_ for _ in ()).throw(RuntimeError("temporary"))
            )
        self.assertEqual("failed", self.orchestrator.state.phase_status(day, "test"))
        self.orchestrator._phase(day, "test", lambda: {"recovered": True})
        self.assertEqual("completed", self.orchestrator.state.phase_status(day, "test"))

    def test_manifest_supplies_exact_backtest_run_to_live(self) -> None:
        day = date(2026, 7, 27)
        manifest = self.orchestrator._backtest_manifest(day)
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(json.dumps({"run_id": "exact-run"}), encoding="utf-8")

        self.assertEqual("exact-run", self.orchestrator.backtest_run_id(day))

    def test_live_launch_fails_closed_after_cutoff(self) -> None:
        day = date(2026, 7, 27)
        orchestrator = DailyTradingOrchestrator(
            self.config,
            now=lambda: datetime(2026, 7, 27, 9, 36, tzinfo=ET),
        )

        with self.assertRaisesRegex(RuntimeError, "late stock entries are prohibited"):
            orchestrator.ensure_live_launch_allowed(day)

    def test_manual_cycle_phase_order_is_sync_data_backtest_then_live(self) -> None:
        day = date(2026, 7, 27)
        phases: list[str] = []
        self.orchestrator._phase = (  # type: ignore[method-assign]
            lambda _day, name, _action: phases.append(name)
        )
        self.orchestrator.backtest_run_id = (  # type: ignore[method-assign]
            lambda _day: "model-run"
        )
        self.orchestrator.ensure_live_launch_allowed = (  # type: ignore[method-assign]
            lambda _day: None
        )

        self.orchestrator.run_cycle(day)

        self.assertEqual(
            ["journal_sync", "raw_data", "backtest", "live_trading"],
            phases,
        )

    def test_preparation_only_cycle_does_not_launch_live_trading(self) -> None:
        day = date(2026, 7, 27)
        phases: list[str] = []
        orchestrator = DailyTradingOrchestrator(replace(self.config, run_live_trading=False))
        orchestrator._phase = (  # type: ignore[method-assign]
            lambda _day, name, _action: phases.append(name)
        )
        orchestrator.backtest_run_id = (  # type: ignore[method-assign]
            lambda _day: "model-run"
        )

        orchestrator.run_cycle(day)

        self.assertEqual(["journal_sync", "raw_data", "backtest"], phases)


if __name__ == "__main__":
    unittest.main()
