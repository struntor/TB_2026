from __future__ import annotations

import json
import unittest

import pandas as pd

from src.journal.backtest_ingest import (
    BacktestJournalIngestor,
    BacktestRunSpec,
    BacktestTradeDefaults,
    reconcile_backtest_to_execution,
)
from src.journal.models import StrategyRunRecord, TradeLegRecord, TradeRecord, deterministic_id
from src.journal.store import JournalStore


def _decision(signal_key: str, *, status: str = "accepted") -> dict:
    return {
        "schema_version": "1.1",
        "decision_key": signal_key,
        "signal_key": signal_key,
        "signal_key_version": "manual-regime-v1",
        "decision_stage": "engine",
        "strategy_id": "manual-regime",
        "strategy_version": "rules-v1",
        "config_hash": "config-abc",
        "engine_version": "engine-v1",
        "decision_time_utc": "2026-06-15T13:35:00Z",
        "symbol": "SPX",
        "primary_rule": "breakout",
        "side": "long",
        "track": "baseline",
        "occurrence": 0,
        "status": status,
        "trade_action": "trade" if status == "accepted" else "no_trade",
        "setup_state": "trade_opened" if status == "accepted" else status,
        "primary_reason": "test",
        "factors": [],
        "context": {"trade": {"entry_price": 6000.0}},
    }


def _row(signal_key: str, *, entry: float, points: float) -> dict:
    return {
        "signal_key": signal_key,
        "decision_key": signal_key,
        "track": "baseline",
        "rule": "breakout",
        "side": "long",
        "entry_date": pd.Timestamp("2026-06-15 09:35:00"),
        "exit_date": pd.Timestamp("2026-06-16 10:00:00"),
        "entry": entry,
        "stop": entry - 10.0,
        "target": entry + 20.0,
        "exit": entry + points,
        "exit_reason": "target" if points > 0 else "stop",
        "points": points,
        "size": 0.5,
        "weighted_points": points * 0.5,
        "labels": "test|causal",
    }


def _spec(run_id: str = "backtest-run") -> BacktestRunSpec:
    return BacktestRunSpec(
        run_id=run_id,
        strategy_id="manual-regime",
        strategy_version="rules-v1",
        strategy_variant_id=None,
        engine_version="engine-v1",
        money_management_version="fixed-risk-v1",
        config_hash="config-abc",
        environment="simulation",
        started_at_utc="2026-06-17T08:00:00Z",
        parameters={"start": "2026-06-01", "end": "2026-06-17"},
        metadata={"data_revision": "test-v1"},
        trade_defaults=BacktestTradeDefaults(
            symbol="SPX",
            asset_class="IND",
            currency="USD",
            exchange="CBOE",
            naive_timestamp_timezone="America/New_York",
        ),
    )


class BacktestJournalIngestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = JournalStore(":memory:")
        self.addCleanup(self.store.close)

    def test_enriched_dataframe_matches_by_signal_key_not_row_order(self) -> None:
        ingestor = BacktestJournalIngestor(self.store)
        decisions = [_decision("signal-b"), _decision("ignored", status="vetoed"), _decision("signal-a")]
        frame = pd.DataFrame(
            [
                _row("signal-a", entry=6000.0, points=20.0),
                _row("signal-b", entry=6050.0, points=-10.0),
            ]
        ).iloc[::-1]

        result = ingestor.ingest_run(
            _spec(),
            decisions,
            frame,
            completed_at_utc="2026-06-17T08:01:00Z",
        )

        self.assertEqual(3, result.decision_count)
        self.assertEqual(2, result.trade_count)
        self.assertEqual(2, self.store.count("trades"))
        self.assertEqual(2, self.store.count("trade_legs"))
        trade = self.store.get_trade_by_signal("backtest-run", "signal-a")
        self.assertIsNotNone(trade)
        self.assertEqual("closed", trade["status"])
        self.assertEqual("2026-06-15T13:35:00Z", trade["opened_at_utc"])
        self.assertEqual("2026-06-16T14:00:00Z", trade["closed_at_utc"])
        self.assertEqual(0.5, trade["quantity"])
        self.assertIsNone(trade["realized_pnl"])
        metadata = json.loads(trade["metadata_json"])
        self.assertEqual("size", metadata["quantity_source"])
        self.assertEqual(20.0, metadata["points"])
        self.assertEqual(10.0, metadata["weighted_points"])
        self.assertEqual("target", metadata["exit_reason"])
        self.assertEqual("test|causal", metadata["source_row"]["labels"])
        run_metadata = json.loads(self.store.get_run("backtest-run")["metadata_json"])
        self.assertEqual("engine-v1", run_metadata["engine_version"])
        self.assertEqual("fixed-risk-v1", run_metadata["money_management_version"])
        self.assertIsNone(run_metadata["strategy_variant_id"])
        leg = self.store.list_trade_legs(str(trade["trade_id"]))[0]
        self.assertEqual(6000.0, leg["open_price"])
        self.assertEqual(6020.0, leg["close_price"])

    def test_replay_is_idempotent_and_run_is_finalized(self) -> None:
        ingestor = BacktestJournalIngestor(self.store)
        arguments = (
            _spec(),
            [_decision("signal-a")],
            pd.DataFrame([_row("signal-a", entry=6000.0, points=20.0)]),
        )
        first = ingestor.ingest_run(
            *arguments,
            completed_at_utc="2026-06-17T08:01:00Z",
        )
        second = ingestor.ingest_run(
            *arguments,
            completed_at_utc="2026-06-17T08:01:00Z",
        )

        self.assertEqual(first.trade_ids, second.trade_ids)
        self.assertEqual(1, self.store.count("trades"))
        self.assertEqual(1, self.store.count("trade_legs"))
        run = self.store.get_run("backtest-run")
        self.assertEqual("completed", run["status"])
        self.assertEqual("2026-06-17T08:01:00Z", run["ended_at_utc"])
        self.assertEqual(1, json.loads(run["metadata_json"])["journal_trade_count"])

    def test_explicit_quantity_and_currency_pnl_are_not_confused_with_points(self) -> None:
        row = _row("signal-a", entry=6000.0, points=20.0)
        row.update({"quantity": 4, "realized_pnl": 800.0})
        BacktestJournalIngestor(self.store).ingest_run(
            _spec(),
            [_decision("signal-a")],
            pd.DataFrame([row]),
            completed_at_utc="2026-06-17T08:01:00Z",
        )

        trade = self.store.get_trade_by_signal("backtest-run", "signal-a")
        metadata = json.loads(trade["metadata_json"])
        self.assertEqual(4.0, trade["quantity"])
        self.assertEqual(800.0, trade["realized_pnl"])
        self.assertEqual("quantity", metadata["quantity_source"])
        self.assertEqual(20.0, metadata["points"])
        self.assertEqual(0.5, metadata["size"])

    def test_end_of_test_is_persisted_as_open_carry_forward_position(self) -> None:
        row = _row("signal-a", entry=6000.0, points=25.0)
        row.update(
            {
                "exit_reason": "end_of_test",
                "quantity": 2,
                "realized_pnl": 50.0,
            }
        )
        BacktestJournalIngestor(self.store).ingest_run(
            _spec(),
            [_decision("signal-a")],
            pd.DataFrame([row]),
            completed_at_utc="2026-06-17T08:01:00Z",
        )

        trade = self.store.get_trade_by_signal("backtest-run", "signal-a")
        self.assertEqual("open", trade["status"])
        self.assertIsNone(trade["closed_at_utc"])
        self.assertIsNone(trade["exit_price"])
        self.assertIsNone(trade["realized_pnl"])
        metadata = json.loads(trade["metadata_json"])
        self.assertTrue(metadata["carry_forward"])
        self.assertEqual("end_of_data", metadata["snapshot_reason"])
        self.assertEqual(6025.0, metadata["mark_price"])
        self.assertEqual(50.0, metadata["unrealized_pnl"])
        leg = self.store.list_trade_legs(str(trade["trade_id"]))[0]
        self.assertIsNone(leg["close_price"])
        run_metadata = json.loads(self.store.get_run("backtest-run")["metadata_json"])
        self.assertEqual(1, run_metadata["journal_open_trade_count"])
        self.assertEqual(0, run_metadata["journal_closed_trade_count"])

    def test_unmatched_trade_rolls_back_payload_and_marks_run_failed(self) -> None:
        ingestor = BacktestJournalIngestor(self.store)
        with self.assertRaisesRegex(ValueError, "no accepted DecisionRecord"):
            ingestor.ingest_run(
                _spec(),
                [_decision("signal-a")],
                pd.DataFrame([_row("different-signal", entry=6000.0, points=5.0)]),
                completed_at_utc="2026-06-17T08:01:00Z",
            )

        self.assertEqual(0, self.store.count("signal_decisions"))
        self.assertEqual(0, self.store.count("trades"))
        self.assertEqual("failed", self.store.get_run("backtest-run")["status"])

    def test_duplicate_trade_signal_is_rejected_instead_of_overwritten(self) -> None:
        row = _row("signal-a", entry=6000.0, points=5.0)
        with self.assertRaisesRegex(ValueError, "duplicate completed trade signal_key"):
            BacktestJournalIngestor(self.store).ingest_run(
                _spec(),
                [_decision("signal-a")],
                pd.DataFrame([row, row]),
                completed_at_utc="2026-06-17T08:01:00Z",
            )
        self.assertEqual(0, self.store.count("trades"))

    def test_naive_timestamp_requires_explicit_timezone(self) -> None:
        spec = _spec()
        unsafe_spec = BacktestRunSpec(
            run_id=spec.run_id,
            strategy_id=spec.strategy_id,
            strategy_version=spec.strategy_version,
            strategy_variant_id=spec.strategy_variant_id,
            engine_version=spec.engine_version,
            money_management_version=spec.money_management_version,
            config_hash=spec.config_hash,
            environment=spec.environment,
            started_at_utc=spec.started_at_utc,
            parameters=spec.parameters,
            metadata=spec.metadata,
            trade_defaults=BacktestTradeDefaults(
                symbol="SPX",
                asset_class="IND",
                currency="USD",
                exchange="CBOE",
                naive_timestamp_timezone=None,
            ),
        )
        with self.assertRaisesRegex(ValueError, "timezone-naive"):
            BacktestJournalIngestor(self.store).ingest_run(
                unsafe_spec,
                [_decision("signal-a")],
                pd.DataFrame([_row("signal-a", entry=6000.0, points=5.0)]),
                completed_at_utc="2026-06-17T08:01:00Z",
            )

    def test_reconciliation_uses_signal_key_and_is_idempotent(self) -> None:
        BacktestJournalIngestor(self.store).ingest_run(
            _spec(),
            [_decision("signal-a")],
            pd.DataFrame([_row("signal-a", entry=6000.0, points=20.0)]),
            completed_at_utc="2026-06-17T08:01:00Z",
        )
        self.store.start_run(
            StrategyRunRecord(
                run_id="paper-run",
                strategy_id="manual-regime",
                strategy_version="rules-v1",
                config_hash="config-abc",
                run_type="live",
                environment="paper",
                started_at_utc="2026-06-15T13:30:00Z",
                ended_at_utc=None,
                status="running",
                parameters={},
                metadata={},
            )
        )
        actual_id = deterministic_id("trade", "paper-run", "signal-a")
        self.store.create_trade(
            TradeRecord(
                trade_id=actual_id,
                run_id="paper-run",
                signal_key="signal-a",
                decision_id=None,
                asset_class="IND",
                symbol="SPX",
                side="long",
                status="closed",
                opened_at_utc="2026-06-15T13:35:00Z",
                closed_at_utc="2026-06-16T14:00:00Z",
                quantity=1.0,
                currency="USD",
                entry_price=6000.75,
                exit_price=6020.0,
                realized_pnl=19.25,
                metadata={},
            ),
            (
                TradeLegRecord(
                    leg_id=deterministic_id("trade-leg", actual_id, 0),
                    trade_id=actual_id,
                    leg_index=0,
                    asset_class="IND",
                    symbol="SPX",
                    con_id=None,
                    local_symbol=None,
                    exchange="CBOE",
                    currency="USD",
                    side="long",
                    ratio=1,
                    quantity=1.0,
                    expiry=None,
                    strike=None,
                    option_right=None,
                    multiplier=None,
                    open_price=6000.75,
                    close_price=6020.0,
                    metadata={"role": "single_asset"},
                ),
            ),
        )

        first = reconcile_backtest_to_execution(
            self.store,
            backtest_run_id="backtest-run",
            execution_run_id="paper-run",
            signal_key="signal-a",
            matched_at_utc="2026-06-17T09:00:00Z",
        )
        second = reconcile_backtest_to_execution(
            self.store,
            backtest_run_id="backtest-run",
            execution_run_id="paper-run",
            signal_key="signal-a",
            matched_at_utc="2026-06-17T09:00:00Z",
        )

        self.assertEqual(first, second)
        self.assertEqual(1, self.store.count("reconciliation"))


if __name__ == "__main__":
    unittest.main()
