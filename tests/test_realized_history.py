from __future__ import annotations

from datetime import date
from pathlib import Path
import unittest
import uuid

from src.journal.models import (
    RuleDefinitionRecord,
    RuleVersionRecord,
    SignalDecisionRecord,
    StrategyRunRecord,
    StrategyVariantRecord,
    StrategyVariantRuleRecord,
    TradeRecord,
    deterministic_id,
)
from src.journal.realized_history import JournalHistoryDataError, load_model_history
from src.journal.store import JournalStore


VARIANT = "manual-regime-baseline@1.0.0"
RULE = "bearish_lower_high_failure_short"
NOW = "2026-07-15T09:35:00Z"


class ModelHistoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.path = Path(__file__).parent / f".history-{uuid.uuid4().hex}.sqlite"
        self.store = JournalStore(self.path)
        self._register_variant(VARIANT, "1.0.0")
        self._register_variant("other@1.0.0", "2.0.0")

    def tearDown(self) -> None:
        self.store.close()
        for suffix in ("", "-wal", "-shm"):
            Path(str(self.path) + suffix).unlink(missing_ok=True)

    def _register_variant(self, variant_id: str, version: str) -> None:
        rule_id = f"registry-{version}"
        rule_version_id = f"{rule_id}@1.0.0"
        self.store.register_rule(RuleDefinitionRecord(rule_id, rule_id, "test", {}))
        self.store.register_rule_version(
            RuleVersionRecord(
                rule_version_id, rule_id, "1.0.0", "logic", "code", "engine",
                NOW, {}, {},
            )
        )
        self.store.register_strategy_variant(
            StrategyVariantRecord(
                variant_id, "manual-regime", version, variant_id, "engine", "mm",
                f"config-{version}", "data", "fee", "slippage", NOW, {},
            ),
            [
                StrategyVariantRuleRecord(
                    variant_id, rule_id, rule_version_id, 0, "entry", True, {}
                )
            ],
        )

    def _start_run(
        self,
        run_id: str,
        *,
        run_type: str = "backtest",
        variant_id: str = VARIANT,
        status: str = "completed",
        role: str | None = None,
        period_start: str = "2026-07-01",
        period_end_exclusive: str = "2026-07-15",
    ) -> None:
        metadata = {} if role is None else {"model_history_role": role}
        self.store.start_run(
            StrategyRunRecord(
                run_id, "manual-regime", "1.0.0", f"config-{run_id}", run_type,
                run_type, "2026-07-01T00:00:00Z", NOW, status,
                {
                    "period_start": period_start,
                    "period_end_exclusive": period_end_exclusive,
                },
                metadata,
                variant_id,
            )
        )

    def _add_trade(
        self,
        *,
        run_id: str,
        signal_key: str,
        closed_at: str | None,
        pnl: float | None,
        primary_rule: str = RULE,
        trade_status: str = "closed",
        trade_suffix: str = "base",
        symbol: str = "TEST",
        side: str = "short",
        metadata: dict | None = None,
    ) -> str:
        decision_id = deterministic_id("decision", run_id, signal_key)
        self.store.upsert_decision(
            SignalDecisionRecord(
                decision_id, run_id, signal_key, f"decision-{signal_key}", "v1",
                "backtest", "manual-regime", "1.0.0", "engine", f"config-{run_id}",
                NOW, symbol, primary_rule, side, "baseline", 0, "accepted",
                "trade", "ready", primary_rule, {}, {},
            )
        )
        trade_id = deterministic_id("trade", run_id, signal_key, trade_suffix)
        self.store.create_trade(
            TradeRecord(
                trade_id, run_id, signal_key, decision_id, "STK", symbol, side,
                trade_status, "2026-07-13T14:00:00Z", closed_at, 1.0, "USD", 100.0,
                101.0 if closed_at else None, pnl, metadata or {},
            )
        )
        return trade_id

    def _load(self, *, model_run_id: str | None = None):
        return load_model_history(
            self.path,
            strategy_variant_id=VARIANT,
            rule_ids={RULE},
            trading_day=date(2026, 7, 15),
            lookback_calendar_days=2,
            model_run_id=model_run_id,
        )

    def test_calendar_window_is_start_inclusive_end_exclusive_and_filters_scope(self) -> None:
        self._start_run("canonical")
        self._add_trade(run_id="canonical", signal_key="start", closed_at="2026-07-13T04:00:00Z", pnl=-10.0)
        self._add_trade(run_id="canonical", signal_key="end", closed_at="2026-07-15T04:00:00Z", pnl=-20.0)
        self._add_trade(run_id="canonical", signal_key="rule", closed_at="2026-07-14T12:00:00Z", pnl=-30.0, primary_rule="unrelated")
        self._add_trade(run_id="canonical", signal_key="open", closed_at=None, pnl=None, trade_status="planned")
        self._start_run("other-variant", variant_id="other@1.0.0")
        self._add_trade(run_id="other-variant", signal_key="variant", closed_at="2026-07-14T12:00:00Z", pnl=-40.0)

        snapshot = self._load()

        self.assertEqual(["start"], [trade.signal_key for trade in snapshot.trades])
        self.assertEqual("2026-07-13T04:00:00Z", snapshot.window_start_utc)
        self.assertEqual("2026-07-15T04:00:00Z", snapshot.window_end_utc)
        self.assertEqual("canonical", snapshot.selected_run_id)

    def test_paper_and_live_never_replace_or_suppress_model_trade(self) -> None:
        self._start_run("model")
        self._add_trade(run_id="model", signal_key="same", closed_at="2026-07-14T15:00:00Z", pnl=-100.0)
        self._start_run("paper", run_type="paper")
        self._add_trade(run_id="paper", signal_key="same", closed_at="2026-07-14T16:00:00Z", pnl=999.0)
        self._start_run("live", run_type="live")
        self._add_trade(run_id="live", signal_key="unexpected", closed_at=None, pnl=None, trade_status="planned")

        snapshot = self._load()

        self.assertEqual(["same"], [trade.signal_key for trade in snapshot.trades])
        self.assertEqual("backtest", snapshot.trades[0].run_type)
        self.assertEqual(-100.0, snapshot.trades[0].realized_pnl)
        self.assertEqual(1, snapshot.source_candidate_count)

    def test_missing_real_execution_does_not_change_model_state(self) -> None:
        self._start_run("model")
        self._add_trade(run_id="model", signal_key="missed-at-broker", closed_at="2026-07-14T12:00:00Z", pnl=-75.0)

        snapshot = self._load()

        self.assertEqual(["missed-at-broker"], [trade.signal_key for trade in snapshot.trades])
        self.assertEqual(-75.0, snapshot.loss_net)

    def test_closed_model_trade_requires_finite_pnl(self) -> None:
        self._start_run("model")
        self._add_trade(run_id="model", signal_key="bad", closed_at="2026-07-14T12:00:00Z", pnl=None)

        with self.assertRaisesRegex(JournalHistoryDataError, "finite realized_pnl"):
            self._load()

    def test_open_and_legacy_end_of_test_positions_are_carried_forward(self) -> None:
        self._start_run("model")
        self._add_trade(
            run_id="model",
            signal_key="native-open",
            closed_at=None,
            pnl=None,
            trade_status="open",
            symbol="PSA",
            side="long",
            primary_rule="bullish_add_on_higher_low",
            metadata={"source_row": {"stop": 90.0, "target": 120.0}},
        )
        self._add_trade(
            run_id="model",
            signal_key="legacy-open",
            closed_at="2026-07-14T20:00:00Z",
            pnl=25.0,
            trade_status="closed",
            symbol="AMZN",
            primary_rule="bullish_add_on_higher_low",
            metadata={
                "exit_reason": "end_of_test",
                "source_row": {"stop": 95.0, "target": 130.0},
            },
        )

        snapshot = self._load()

        self.assertEqual((), snapshot.trades)
        self.assertEqual({"PSA", "AMZN"}, snapshot.open_symbols)
        by_symbol = {position.symbol: position for position in snapshot.open_positions}
        self.assertEqual("open", by_symbol["PSA"].source_status)
        self.assertEqual("legacy_end_of_test", by_symbol["AMZN"].source_status)
        self.assertEqual(90.0, by_symbol["PSA"].stop_price)
        self.assertEqual(130.0, by_symbol["AMZN"].target_price)
        self.assertEqual(2, snapshot.open_position_source_count)
        self.assertEqual("complete", snapshot.data_quality_status)

    def test_multiple_runs_fail_closed_but_explicit_run_is_auditable(self) -> None:
        self._start_run("older")
        self._add_trade(run_id="older", signal_key="older-signal", closed_at="2026-07-14T12:00:00Z", pnl=-10.0)
        self._start_run("newer")
        self._add_trade(run_id="newer", signal_key="newer-signal", closed_at="2026-07-14T12:00:00Z", pnl=-20.0)

        with self.assertRaisesRegex(JournalHistoryDataError, "multiple completed"):
            self._load()
        snapshot = self._load(model_run_id="newer")

        self.assertEqual("newer", snapshot.selected_run_id)
        self.assertEqual("explicit_run_id", snapshot.run_selection)
        self.assertEqual(["newer-signal"], [trade.signal_key for trade in snapshot.trades])

    def test_one_metadata_role_selects_canonical_run(self) -> None:
        self._start_run("old")
        self._start_run("canonical", role="canonical_model_history")
        self._add_trade(run_id="canonical", signal_key="chosen", closed_at="2026-07-14T12:00:00Z", pnl=-20.0)

        snapshot = self._load()

        self.assertEqual("canonical", snapshot.selected_run_id)
        self.assertEqual("metadata_role", snapshot.run_selection)
        self.assertEqual(2, snapshot.run_candidate_count)

    def test_empty_canonical_run_is_complete_empty_model_window(self) -> None:
        self._start_run("canonical", role="canonical_model_history")

        snapshot = self._load()

        self.assertEqual((), snapshot.trades)
        self.assertEqual("complete_empty_window", snapshot.data_quality_status)

    def test_stale_model_run_fails_closed(self) -> None:
        self._start_run("stale", period_end_exclusive="2026-07-14")

        with self.assertRaisesRegex(JournalHistoryDataError, "stale model history"):
            self._load()

if __name__ == "__main__":
    unittest.main()
