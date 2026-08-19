from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from typing import Any, Sequence
import unittest

from src.journal.decision_ingest import (
    DecisionIngestContext,
    DecisionJournalIngestor,
    TradeIntent,
    TradeIntentLeg,
    correlate_by_signal_key,
    map_decision_record,
)
from src.journal.models import (
    DecisionFactorRecord,
    ReconciliationRecord,
    SignalDecisionRecord,
    StrategyRunRecord,
    TradeLegRecord,
    TradeRecord,
    deterministic_id,
)
from src.journal.store import JournalStore


def _factor(code: str = "entry_rule") -> dict[str, Any]:
    return {
        "factor_code": code,
        "category": "entry_rule",
        "observed_value": {"price": 101.25, "labels": ["a", "b"]},
        "operator": ">",
        "threshold": 100.0,
        "passed": True,
        "is_veto": False,
        "reason": "causal test factor",
        "as_of_time_utc": "2026-06-15T13:35:00Z",
        "calculation_version": "test-factors-v1",
    }


def _decision(
    *,
    signal_key: str = "manual-regime-v1:signal-a",
    status: str = "accepted",
    stage: str = "engine",
    factors: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    trade_action = "trade" if status == "accepted" else "no_trade"
    return {
        "schema_version": "1.1",
        "decision_key": "manual-regime-v1:day-a",
        "signal_key": signal_key,
        "signal_key_version": "manual-regime-v1",
        "decision_stage": stage,
        "strategy_id": "multi-asset-manual-regime-top50",
        "strategy_version": "rules-v1",
        "config_hash": "abc123",
        "engine_version": "engine-v1",
        "decision_time_utc": "2026-06-15T13:35:00Z",
        "symbol": "SPX",
        "primary_rule": "test_rule",
        "side": "long",
        "track": "baseline",
        "occurrence": 0,
        "status": status,
        "trade_action": trade_action,
        "setup_state": "trade_opened" if status == "accepted" else status,
        "primary_reason": "test_rule" if status == "accepted" else f"test_{status}",
        "factors": factors if factors is not None else [_factor()],
        "context": {
            "nested": {"regime": "bull", "values": [1, 2, None]},
            "trade": {"entry_price": 101.25, "stop": 99.0, "target": 105.0},
        },
    }


class _MemoryStore:
    def __init__(self) -> None:
        self.decisions: dict[str, tuple[SignalDecisionRecord, tuple[DecisionFactorRecord, ...]]] = {}
        self.trades: dict[str, tuple[TradeRecord, tuple[TradeLegRecord, ...]]] = {}

    @contextmanager
    def transaction(self):
        yield

    def upsert_decision(
        self,
        record: SignalDecisionRecord,
        factors: Sequence[DecisionFactorRecord] = (),
    ) -> None:
        self.decisions[record.decision_id] = (record, tuple(factors))

    def create_trade(
        self,
        record: TradeRecord,
        legs: Sequence[TradeLegRecord] = (),
    ) -> None:
        self.trades[record.trade_id] = (record, tuple(legs))


def _asset_intent() -> TradeIntent:
    return TradeIntent(
        asset_class="STK",
        symbol="SPX",
        side="BUY",
        status="planned",
        opened_at_utc="2026-06-15T13:35:00Z",
        quantity=3.0,
        currency="USD",
        entry_price=101.25,
        stop_price=99.0,
        target_price=105.0,
        exchange="SMART",
        legs=(),
        metadata={"sizing_model": "test"},
    )


def _option_intent() -> TradeIntent:
    legs = tuple(
        TradeIntentLeg(
            asset_class="OPT",
            symbol="SPX",
            con_id=con_id,
            local_symbol=f"SPXW TEST {index}",
            exchange="SMART",
            currency="USD",
            side=side,
            ratio=1,
            quantity=1.0,
            expiry="20260619",
            strike=strike,
            option_right=right,
            multiplier=100.0,
            open_price=price,
            metadata={"role": role},
        )
        for index, (con_id, side, strike, right, price, role) in enumerate(
            (
                (101, "BUY", 6000.0, "P", 20.0, "long_put"),
                (102, "SELL", 6020.0, "P", 28.0, "short_put"),
                (103, "BUY", 6100.0, "C", 5.0, "long_call"),
            )
        )
    )
    return TradeIntent(
        asset_class="BAG",
        symbol="SPX",
        side="BUY",
        status="planned",
        opened_at_utc="2026-06-15T13:35:00Z",
        quantity=1.0,
        currency="USD",
        entry_price=-3.0,
        stop_price=None,
        target_price=None,
        exchange="SMART",
        legs=legs,
        metadata={"structure": "flyagonal"},
    )


def _single_option_leg(**overrides: Any) -> TradeIntentLeg:
    values: dict[str, Any] = {
        "asset_class": "OPT",
        "symbol": "SPX",
        "con_id": 201,
        "local_symbol": "SPXW  260619C06000000",
        "exchange": "SMART",
        "currency": "USD",
        "side": "BUY",
        "ratio": 1,
        "quantity": 2.0,
        "expiry": "20260619",
        "strike": 6000.0,
        "option_right": "C",
        "multiplier": 100.0,
        "open_price": 12.5,
        "metadata": {"role": "single_option"},
    }
    values.update(overrides)
    return TradeIntentLeg(**values)


def _single_option_intent() -> TradeIntent:
    return TradeIntent(
        asset_class="OPT",
        symbol="SPX",
        side="BUY",
        status="planned",
        opened_at_utc="2026-06-15T13:35:00Z",
        quantity=2.0,
        currency="USD",
        entry_price=12.5,
        stop_price=8.0,
        target_price=20.0,
        exchange="SMART",
        legs=(_single_option_leg(),),
        metadata={"structure": "single_call"},
    )


class DecisionIngestTests(unittest.TestCase):
    def test_lossless_mapping_keeps_context_raw_record_and_factor_values(self) -> None:
        source = _decision()
        record, factors = map_decision_record(
            source,
            DecisionIngestContext(run_id="run-backtest", run_mode="backtest"),
        )

        self.assertEqual(source, record.raw_record)
        self.assertEqual(source["context"], record.context)
        self.assertEqual(source["factors"][0]["observed_value"], factors[0].observed_value)
        self.assertEqual(100.0, factors[0].threshold)

    def test_versioned_mapping_uses_stable_rule_version_identity(self) -> None:
        source = {
            **_decision(),
            "rule_id": "test_rule",
            "rule_version": "2.1.0",
            "strategy_variant_id": "test-candidate@2.1.0",
            "money_management_version": "fixed-risk@1.0.0",
        }

        record, _ = map_decision_record(
            source,
            DecisionIngestContext(run_id="run-versioned", run_mode="backtest"),
        )

        self.assertEqual("test_rule", record.rule_id)
        self.assertEqual(
            deterministic_id("rule-version", "test_rule", "2.1.0"),
            record.rule_version_id,
        )
        self.assertEqual(source, record.raw_record)

    def test_legacy_mapping_keeps_optional_rule_foreign_keys_empty(self) -> None:
        record, _ = map_decision_record(
            _decision(),
            DecisionIngestContext(run_id="run-legacy", run_mode="backtest"),
        )

        self.assertIsNone(record.rule_id)
        self.assertIsNone(record.rule_version_id)

    def test_supported_decision_outcomes_are_ingested(self) -> None:
        statuses = ("accepted", "vetoed", "blocked", "no_setup", "missing_data", "blocked_risk")
        for status in statuses:
            with self.subTest(status=status):
                store = _MemoryStore()
                result = DecisionJournalIngestor(store).ingest(
                    _decision(status=status),
                    DecisionIngestContext(run_id=f"run-{status}", run_mode="paper"),
                )
                self.assertEqual(status, result.status)
                self.assertEqual(1, len(store.decisions))

    def test_reingest_is_idempotent_and_live_final_replaces_engine_state(self) -> None:
        store = _MemoryStore()
        ingestor = DecisionJournalIngestor(store)
        context = DecisionIngestContext(run_id="run-live", run_mode="live")
        engine = _decision(status="accepted", stage="engine")
        first = ingestor.ingest(engine, context)
        second = ingestor.ingest(engine, context)
        self.assertEqual(first.decision_id, second.decision_id)
        self.assertEqual(1, len(store.decisions))

        live_final = _decision(
            status="vetoed",
            stage="live_final",
            factors=[
                _factor(),
                {**_factor("weak_retest_veto"), "category": "veto", "is_veto": True},
            ],
        )
        finalized = ingestor.ingest(live_final, context)
        stored, factors = store.decisions[finalized.decision_id]
        self.assertEqual(first.decision_id, finalized.decision_id)
        self.assertEqual("vetoed", stored.status)
        self.assertEqual("live_final", stored.decision_stage)
        self.assertEqual(2, len(factors))

    def test_backtest_and_live_share_signal_key_but_never_trade_id(self) -> None:
        source = _decision()
        backtest_store = _MemoryStore()
        live_store = _MemoryStore()
        backtest = DecisionJournalIngestor(backtest_store)
        live = DecisionJournalIngestor(live_store)
        backtest_context = DecisionIngestContext("run-backtest", "backtest")
        live_context = DecisionIngestContext("run-live", "live")

        backtest_id = backtest.create_trade_intent(source, backtest_context, _asset_intent())
        live_id = live.create_trade_intent(source, live_context, _asset_intent())
        self.assertNotEqual(backtest_id, live_id)
        backtest_trade = backtest_store.trades[backtest_id][0]
        live_trade = live_store.trades[live_id][0]
        self.assertEqual(source["signal_key"], backtest_trade.signal_key)
        self.assertEqual(source["signal_key"], live_trade.signal_key)
        self.assertEqual(1, len(backtest_store.trades[backtest_id][1]))
        self.assertEqual("single_asset", backtest_store.trades[backtest_id][1][0].metadata["role"])
        links = correlate_by_signal_key([source], [{**source, "decision_stage": "live_final"}])
        self.assertEqual([source["signal_key"]], [link.signal_key for link in links])

    def test_multileg_option_intent_is_created_without_flattening_legs(self) -> None:
        store = _MemoryStore()
        ingestor = DecisionJournalIngestor(store)
        trade_id = ingestor.create_trade_intent(
            _decision(),
            DecisionIngestContext("run-options", "paper"),
            _option_intent(),
        )
        trade, legs = store.trades[trade_id]
        self.assertEqual("BAG", trade.asset_class)
        self.assertIsNone(trade.metadata["planned_stop_price"])
        self.assertEqual(3, len(legs))
        self.assertEqual(
            ["long_put", "short_put", "long_call"],
            [leg.metadata["role"] for leg in legs],
        )
        self.assertEqual(3, len({leg.leg_id for leg in legs}))

    def test_single_leg_option_keeps_reconciliation_identity(self) -> None:
        store = _MemoryStore()
        trade_id = DecisionJournalIngestor(store).create_trade_intent(
            _decision(),
            DecisionIngestContext("run-single-option", "paper"),
            _single_option_intent(),
        )
        trade, legs = store.trades[trade_id]
        self.assertEqual("OPT", trade.asset_class)
        self.assertEqual(1, len(legs))
        leg = legs[0]
        self.assertEqual(201, leg.con_id)
        self.assertEqual("20260619", leg.expiry)
        self.assertEqual(6000.0, leg.strike)
        self.assertEqual("C", leg.option_right)
        self.assertEqual(100.0, leg.multiplier)

    def test_option_intent_requires_explicit_complete_leg(self) -> None:
        with self.assertRaisesRegex(ValueError, "explicit option leg"):
            replace(_asset_intent(), asset_class="OPT")

        invalid_fields = {
            "expiry": None,
            "strike": None,
            "option_right": None,
            "multiplier": None,
        }
        for field, value in invalid_fields.items():
            with self.subTest(field=field):
                with self.assertRaisesRegex(ValueError, "option leg requires"):
                    _single_option_leg(**{field: value})

        with self.assertRaisesRegex(ValueError, "con_id or local_symbol"):
            _single_option_leg(con_id=None, local_symbol=None)

    def test_non_finite_trade_and_leg_values_are_rejected(self) -> None:
        for field in ("quantity", "entry_price", "stop_price", "target_price"):
            for value in (float("inf"), float("-inf"), float("nan")):
                with self.subTest(scope="trade", field=field, value=value):
                    with self.assertRaisesRegex(ValueError, "finite"):
                        replace(_asset_intent(), **{field: value})

        for field in ("quantity", "strike", "multiplier", "open_price"):
            for value in (float("inf"), float("-inf"), float("nan")):
                with self.subTest(scope="leg", field=field, value=value):
                    with self.assertRaisesRegex(ValueError, "finite"):
                        _single_option_leg(**{field: value})

    def test_trade_intent_is_rejected_for_veto_or_no_setup(self) -> None:
        ingestor = DecisionJournalIngestor(_MemoryStore())
        with self.assertRaisesRegex(ValueError, "cannot create"):
            ingestor.create_trade_intent(
                _decision(status="no_setup"),
                DecisionIngestContext("run-live", "live"),
                _asset_intent(),
            )

    def test_rejects_duplicate_signal_keys_within_a_comparison_side(self) -> None:
        source = _decision()
        with self.assertRaisesRegex(ValueError, "duplicate signal_key"):
            correlate_by_signal_key([source, source], [source])

    def test_sqlite_store_finalizes_status_and_reconciles_backtest_to_live(self) -> None:
        store = JournalStore(":memory:")
        self.addCleanup(store.close)
        for run_id, run_type, environment in (
            ("run-backtest", "backtest", "simulation"),
            ("run-live", "live", "paper"),
        ):
            store.start_run(
                StrategyRunRecord(
                    run_id=run_id,
                    strategy_id="multi-asset-manual-regime-top50",
                    strategy_version="rules-v1",
                    config_hash="abc123",
                    run_type=run_type,
                    environment=environment,
                    started_at_utc="2026-06-15T13:30:00Z",
                    ended_at_utc=None,
                    status="running",
                    parameters={},
                    metadata={},
                )
            )

        ingestor = DecisionJournalIngestor(store)
        backtest_context = DecisionIngestContext("run-backtest", "backtest")
        live_context = DecisionIngestContext("run-live", "live")
        source = _decision()
        backtest_trade_id = ingestor.create_trade_intent(source, backtest_context, _asset_intent())
        live_trade_id = ingestor.create_trade_intent(source, live_context, _asset_intent())
        self.assertNotEqual(backtest_trade_id, live_trade_id)
        self.assertEqual(2, store.count("trades"))
        self.assertEqual(2, store.count("trade_legs"))

        live_final = _decision(
            status="vetoed",
            stage="live_final",
            factors=[{**_factor("late_live_veto"), "category": "veto", "is_veto": True}],
        )
        finalized = ingestor.ingest(live_final, live_context)
        stored = store.get_decision("run-live", source["signal_key"])
        self.assertIsNotNone(stored)
        self.assertEqual("vetoed", stored["status"])
        self.assertEqual("live_final", stored["decision_stage"])
        self.assertEqual(2, store.count("decision_factors"))

        # Replaying an older engine ledger must not undo a final live veto.
        ingestor.ingest(source, live_context)
        stored_after_replay = store.get_decision("run-live", source["signal_key"])
        self.assertEqual("vetoed", stored_after_replay["status"])
        self.assertEqual("live_final", stored_after_replay["decision_stage"])
        self.assertEqual(2, store.count("decision_factors"))

        reconciliation_id = deterministic_id(
            "reconciliation",
            live_trade_id,
            backtest_trade_id,
            source["signal_key"],
        )
        store.upsert_reconciliation(
            ReconciliationRecord(
                reconciliation_id=reconciliation_id,
                live_trade_id=live_trade_id,
                reference_trade_id=backtest_trade_id,
                signal_key=source["signal_key"],
                status="matched",
                matched_at_utc="2026-06-15T14:00:00Z",
                price_slippage=0.0,
                quantity_difference=0.0,
                pnl_difference=None,
                notes="same causal signal across distinct runs",
                details={"live_decision_id": finalized.decision_id},
            )
        )
        self.assertEqual(1, store.count("reconciliation"))


if __name__ == "__main__":
    unittest.main()
