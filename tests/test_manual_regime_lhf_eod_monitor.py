from __future__ import annotations

from pathlib import Path
import json
from types import SimpleNamespace
import unittest

import pandas as pd

from src.bot.manual_regime_lhf_eod_monitor import (
    ManualRegimeLhfEodMonitor,
    MonitorConfig,
    MonitorState,
    ShadowPosition,
    decision_bar_for_day,
    shadow_exit_from_bars,
    short_profit_r,
)


def bars(*rows: tuple[str, float, float, float, float]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "date": pd.Timestamp(stamp),
                "open": open_price,
                "high": high,
                "low": low,
                "close": close,
                "volume": 100,
            }
            for stamp, open_price, high, low, close in rows
        ]
    )


def shadow_position(delta_r: float | None) -> ShadowPosition:
    return ShadowPosition(
        trade_id=f"trade-{delta_r}",
        run_id="run",
        decision_id="decision",
        signal_key=f"signal-{delta_r}",
        symbol="IVV",
        quantity=1,
        entry_price=100,
        stop_price=102,
        target_price=96,
        risk_per_share=2,
        decision_bar_at_et="2026-07-31T15:50:00",
        decision_bar_close=98,
        decision_r=1,
        delta_r=delta_r,
    )


class ManualRegimeLhfEodMonitorTests(unittest.TestCase):
    def test_1550_bar_becomes_eligible_only_at_1555_et(self) -> None:
        frame = bars(("2026-07-31 15:50", 99, 99.2, 97.8, 98))

        before = decision_bar_for_day(
            frame, pd.Timestamp("2026-07-31 15:54:59", tz="America/New_York")
        )
        at_exit = decision_bar_for_day(
            frame, pd.Timestamp("2026-07-31 15:55:00", tz="America/New_York")
        )

        self.assertIsNone(before)
        self.assertEqual(98, float(at_exit["close"]))

    def test_short_profit_r_uses_actual_entry_and_original_stop(self) -> None:
        self.assertEqual(1.0, short_profit_r(100, 102, 98))
        self.assertAlmostEqual(0.75, short_profit_r(100, 102, 98.5))

    def test_shadow_starts_after_decision_bar_and_uses_conservative_same_bar_order(self) -> None:
        frame = bars(
            ("2026-07-31 15:50", 99, 103, 95, 98),
            ("2026-07-31 15:55", 98, 102.5, 95.5, 97),
        )

        result = shadow_exit_from_bars(
            frame,
            after_bar_et=pd.Timestamp("2026-07-31 15:50"),
            now_et=pd.Timestamp("2026-07-31 16:00", tz="America/New_York"),
            stop_price=102,
            target_price=96,
        )

        self.assertEqual(pd.Timestamp("2026-07-31 15:55"), result[0])
        self.assertEqual(102, result[1])
        self.assertEqual("stop_same_bar_conservative", result[2])

    def test_shadow_target_is_detected_on_later_completed_bar(self) -> None:
        frame = bars(
            ("2026-07-31 15:55", 98, 99, 97, 98),
            ("2026-08-03 09:30", 97, 98, 95.5, 96),
        )

        result = shadow_exit_from_bars(
            frame,
            after_bar_et=pd.Timestamp("2026-07-31 15:50"),
            now_et=pd.Timestamp("2026-08-03 09:35", tz="America/New_York"),
            stop_price=102,
            target_price=96,
        )

        self.assertEqual("target", result[2])
        self.assertEqual(96, result[1])

    def test_kill_switch_needs_five_completed_comparisons_and_minus_two_r(self) -> None:
        four = MonitorState(positions=[shadow_position(-0.5) for _ in range(4)])
        five = MonitorState(positions=[shadow_position(-0.4) for _ in range(5)])

        self.assertFalse(ManualRegimeLhfEodMonitor._kill_switch_active(four))
        self.assertTrue(ManualRegimeLhfEodMonitor._kill_switch_active(five))

    def test_non_paper_account_is_rejected_before_any_broker_or_db_access(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "non-paper"):
            ManualRegimeLhfEodMonitor(
                object(),
                MonitorConfig(
                    account="U123",
                    client_id=62,
                    place_orders=True,
                    journal_path=Path("unused.sqlite"),
                    state_path=Path("unused.json"),
                    poll_seconds=15,
                    profit_threshold_r=1,
                    cancel_timeout_seconds=8,
                ),
            )

    def test_open_trade_lookup_uses_configured_strategy_variant(self) -> None:
        captured = {}

        def list_open_trades_for_rule(**kwargs):
            captured.update(kwargs)
            return []

        monitor = object.__new__(ManualRegimeLhfEodMonitor)
        monitor.config = MonitorConfig(
            account="DU123",
            client_id=62,
            place_orders=False,
            journal_path=Path("unused.sqlite"),
            state_path=Path("unused.json"),
            poll_seconds=15,
            profit_threshold_r=1,
            cancel_timeout_seconds=8,
            strategy_variant_id="manual-regime-sizing-v2@test",
        )
        monitor.store = SimpleNamespace(
            list_open_trades_for_rule=list_open_trades_for_rule
        )

        monitor._evaluate_open_trades(
            MonitorState(),
            pd.Timestamp("2026-08-03 15:55", tz="America/New_York"),
        )

        self.assertEqual(
            "manual-regime-sizing-v2@test", captured["variant_id"]
        )

    def test_cancel_recognizes_synced_children_even_when_role_was_overwritten(self) -> None:
        order_ref = "MRB:20260730:HUM:bearish_lower_high_failure_s"
        rows = [
            {
                "metadata_json": json.dumps({"role": "parent"}),
                "action": action,
                "order_type": order_type,
                "parent_order_id": "journal-parent",
                "broker_perm_id": perm_id,
                "broker_client_id": 62,
                "broker_client_order_id": order_id,
                "order_ref": order_ref,
            }
            for action, order_type, perm_id, order_id in (
                ("BUY", "LMT", 101, 11),
                ("BUY", "STP", 102, 12),
            )
        ]
        trades = [
            SimpleNamespace(
                order=SimpleNamespace(
                    account="DU123",
                    permId=perm_id,
                    clientId=62,
                    orderId=order_id,
                    orderRef=order_ref,
                ),
                orderStatus=SimpleNamespace(status="Submitted"),
            )
            for perm_id, order_id in ((101, 11), (102, 12))
        ]

        class FakeIb:
            def reqAllOpenOrders(self):
                return []

            def sleep(self, _seconds):
                return None

            def openTrades(self):
                return trades

            def cancelOrder(self, order):
                next(trade for trade in trades if trade.order is order).orderStatus.status = "Cancelled"

            def waitOnUpdate(self, timeout):
                return False

        monitor = object.__new__(ManualRegimeLhfEodMonitor)
        monitor.ib = FakeIb()
        monitor.config = MonitorConfig(
            account="DU123",
            client_id=62,
            place_orders=True,
            journal_path=Path("unused.sqlite"),
            state_path=Path("unused.json"),
            poll_seconds=15,
            profit_threshold_r=1,
            cancel_timeout_seconds=1,
        )
        monitor.store = SimpleNamespace(list_orders_for_trade=lambda _trade_id: rows)
        position = shadow_position(None)

        monitor._cancel_original_children(position)

        self.assertEqual(["Cancelled", "Cancelled"], [trade.orderStatus.status for trade in trades])


if __name__ == "__main__":
    unittest.main()
