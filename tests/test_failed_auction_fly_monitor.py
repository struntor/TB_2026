from __future__ import annotations

import unittest
from pathlib import Path

import pandas as pd
from ib_insync import Contract

from src.bot.failed_auction_poc_fly_monitor import (
    FailedAuctionFlyMonitor,
    FlyMonitorConfig,
    FlyMonitorState,
    atomic_write_state,
    exit_fallback_due,
    first_confirmed_poc_touch,
    load_state,
    require_paper_account,
)
from src.execution.spx_zebra import ZebraOptionQuote
from src.execution.spy_poc_fly import (
    FlyStructureQuote,
    SpyPocFlySelection,
    build_put_fly_combo,
)
from src.journal.decision_ingest import DecisionIngestContext, DecisionJournalIngestor
from src.journal.models import StrategyRunRecord
from src.journal.store import JournalStore
from src.strategies.failed_auction_journal import (
    config_hash,
    decision_payload,
    register_manifest,
)
from src.strategies.failed_auction_poc_fly import (
    STRATEGY_ID,
    STRATEGY_VARIANT_ID,
    STRATEGY_VERSION,
    FailedAuctionConfig,
    evaluate_day,
)

TEST_STATE_PATH = Path(r"D:\Trading\state\test_failed_auction_fly_monitor.json")
TEST_JOURNAL_PATH = Path(r"D:\Trading\test_failed_auction_fly_monitor.sqlite3")


def remove_sqlite_artifacts(path: Path) -> None:
    for candidate in (path, Path(str(path) + "-wal"), Path(str(path) + "-shm")):
        if candidate.exists():
            candidate.unlink()


class FailedAuctionFlyMonitorTests(unittest.TestCase):
    def test_paper_guard_rejects_live_account(self) -> None:
        require_paper_account("DU123456")
        with self.assertRaisesRegex(RuntimeError, "DU paper account"):
            require_paper_account("U123456")

    def test_touch_requires_completed_bar(self) -> None:
        bars = pd.DataFrame(
            [
                {
                    "date": "2026-07-17 10:00:00",
                    "open": 601,
                    "high": 602,
                    "low": 599,
                    "close": 600,
                    "volume": 100,
                }
            ]
        )
        before_close = first_confirmed_poc_touch(
            bars,
            poc=600,
            after=pd.Timestamp("2026-07-17 09:55"),
            now_et=pd.Timestamp("2026-07-17 10:04:59", tz="America/New_York"),
        )
        after_close = first_confirmed_poc_touch(
            bars,
            poc=600,
            after=pd.Timestamp("2026-07-17 09:55"),
            now_et=pd.Timestamp("2026-07-17 10:05:00", tz="America/New_York"),
        )
        self.assertIsNone(before_close)
        self.assertEqual(after_close, pd.Timestamp("2026-07-17 10:00:00"))

    def test_exit_fallback_is_due_at_exactly_thirty_minutes(self) -> None:
        submitted = "2026-07-17T14:00:00Z"
        self.assertFalse(
            exit_fallback_due(
                submitted, pd.Timestamp("2026-07-17T14:29:59Z"), minutes=30
            )
        )
        self.assertTrue(
            exit_fallback_due(
                submitted, pd.Timestamp("2026-07-17T14:30:00Z"), minutes=30
            )
        )

    def test_state_round_trip_preserves_order_identity(self) -> None:
        state = FlyMonitorState(
            schema_version=1,
            status="entry_submitted",
            signal_day="2026-01-15",
            run_id="run-1",
            signal_key="signal-1",
            decision_id="decision-1",
            trade_id="trade-1",
            poc=691.25,
            body_strike=691,
            upper_strike=701,
            lower_strike=681,
            expiry="20260116",
            upper_con_id=11,
            body_con_id=12,
            lower_con_id=13,
            entry_order_ref="FAFLY-E-20260115",
            entry_order_id=71,
        )
        TEST_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        TEST_STATE_PATH.unlink(missing_ok=True)
        self.addCleanup(TEST_STATE_PATH.unlink, missing_ok=True)
        atomic_write_state(TEST_STATE_PATH, state)
        self.assertEqual(load_state(TEST_STATE_PATH), state)
        self.assertFalse(TEST_STATE_PATH.with_suffix(".json.tmp").exists())

    def test_frozen_decision_payload_ingests_into_registered_journal(self) -> None:
        config = FailedAuctionConfig()
        bars = pd.read_csv(r"D:\Trading\Rohdaten\Rohdaten_SPY.csv")
        evaluation = evaluate_day(bars, "2025-10-09", config)
        self.assertEqual(evaluation.status, "accepted")
        remove_sqlite_artifacts(TEST_JOURNAL_PATH)
        self.addCleanup(remove_sqlite_artifacts, TEST_JOURNAL_PATH)
        store = JournalStore(TEST_JOURNAL_PATH)
        try:
            register_manifest(store, config)
            run_id = "failed-auction-test-run"
            store.start_run(
                StrategyRunRecord(
                    run_id=run_id,
                    strategy_id=STRATEGY_ID,
                    strategy_version=STRATEGY_VERSION,
                    config_hash=config_hash(config),
                    run_type="paper",
                    environment="test",
                    started_at_utc="2025-10-09T13:30:00Z",
                    ended_at_utc=None,
                    status="running",
                    parameters={},
                    metadata={},
                    variant_id=STRATEGY_VARIANT_ID,
                )
            )
            result = DecisionJournalIngestor(store).ingest(
                decision_payload(evaluation, config),
                DecisionIngestContext(run_id, "paper"),
            )
            self.assertEqual(result.status, "accepted")
            self.assertGreaterEqual(result.factor_count, 4)
        finally:
            store.close()

    def test_accepted_signal_creates_one_bag_intent_with_three_legs(self) -> None:
        config = FailedAuctionConfig()
        bars = pd.read_csv(r"D:\Trading\Rohdaten\Rohdaten_SPY.csv")
        evaluation = evaluate_day(bars, "2025-10-09", config)
        contracts = []
        for con_id, strike in ((101, 679), (102, 669), (103, 659)):
            contract = Contract(
                conId=con_id,
                symbol="SPY",
                secType="OPT",
                exchange="SMART",
                currency="USD",
                lastTradeDateOrContractMonth="20251010",
                strike=strike,
                right="P",
                multiplier="100",
                localSymbol=f"SPY {strike}P",
            )
            contracts.append(contract)
        upper, body, lower = contracts
        quote = FlyStructureQuote(
            upper=ZebraOptionQuote(6.0, 6.1, None, None, None),
            body=ZebraOptionQuote(3.0, 3.1, None, None, None),
            lower=ZebraOptionQuote(1.0, 1.1, None, None, None),
        )
        selection = SpyPocFlySelection(
            symbol="SPY",
            expiry="20251010",
            upper_strike=679,
            body_strike=669,
            lower_strike=659,
            quote=quote,
            min_tick=0.01,
            multiplier=100,
            upper_contract=upper,
            body_contract=body,
            lower_contract=lower,
            combo_contract=build_put_fly_combo(upper, body, lower),
        )
        remove_sqlite_artifacts(TEST_JOURNAL_PATH)
        TEST_STATE_PATH.unlink(missing_ok=True)
        self.addCleanup(remove_sqlite_artifacts, TEST_JOURNAL_PATH)
        self.addCleanup(TEST_STATE_PATH.unlink, missing_ok=True)
        store = JournalStore(TEST_JOURNAL_PATH)
        try:
            register_manifest(store, config)
            monitor = FailedAuctionFlyMonitor.__new__(FailedAuctionFlyMonitor)
            monitor.config = FlyMonitorConfig(
                day=pd.Timestamp("2025-10-09"),
                account="DU123",
                client_id=62,
                place_orders=False,
                journal_path=TEST_JOURNAL_PATH,
                state_path=TEST_STATE_PATH,
            )
            monitor.strategy_config = config
            monitor.store = store
            monitor.decisions = DecisionJournalIngestor(store)
            state = monitor._journal_new_trade(evaluation, selection)
            trade = store.get_trade(state.trade_id)
            legs = store.list_trade_legs(state.trade_id)
            self.assertEqual(trade["asset_class"], "BAG")
            self.assertEqual(len(legs), 3)
            self.assertEqual([row["ratio"] for row in legs], [1, 2, 1])
            self.assertEqual(
                [row["side"] for row in legs], ["BUY", "SELL", "BUY"]
            )
        finally:
            store.close()


if __name__ == "__main__":
    unittest.main()
