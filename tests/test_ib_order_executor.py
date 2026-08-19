from __future__ import annotations

import pandas as pd
from ib_insync import LimitOrder

from src.execution.ib_order_executor import IBOrderExecutor


class _FakeClient:
    def __init__(self) -> None:
        self._next_id = 100

    def getReqId(self) -> int:
        order_id = self._next_id
        self._next_id += 1
        return order_id


class _FakeIB:
    def __init__(self) -> None:
        self.client = _FakeClient()


def test_format_good_till_date_ignores_expired_timestamp() -> None:
    floor = pd.Timestamp("2026-06-26 09:40:00")

    assert IBOrderExecutor._format_good_till_date("2026-06-26 09:35:00", min_timestamp=floor) is None


def test_format_good_till_date_keeps_future_timestamp() -> None:
    floor = pd.Timestamp("2026-06-26 09:40:00")

    assert (
        IBOrderExecutor._format_good_till_date("2026-06-26 15:55:00", min_timestamp=floor)
        == "20260626 15:55:00 US/Eastern"
    )


def test_apply_exit_leg_tif_leaves_day_order_without_gtd() -> None:
    order = LimitOrder("SELL", 1, 100.0)
    order.tif = "DAY"

    IBOrderExecutor._apply_exit_leg_tif(order, None)

    assert order.tif == "DAY"
    assert order.goodTillDate == ""


def test_manual_bracket_children_use_parent_id_without_oca() -> None:
    executor = IBOrderExecutor(_FakeIB(), entry_order_type="MKT", bracket_style="manual_oca")
    row = pd.Series(
        {
            "symbol": "SPY",
            "track": "test",
            "rule": "manual_test",
            "side": "short",
            "quantity": 5,
            "entry": 737.21,
            "stop": 742.21,
            "target": 732.21,
            "exit_date": "2026-06-30 16:00:00",
        }
    )

    parent, take_profit, stop_loss = executor._build_bracket_orders(row)

    assert take_profit.parentId == parent.orderId
    assert stop_loss.parentId == parent.orderId
    assert take_profit.ocaGroup == ""
    assert stop_loss.ocaGroup == ""
    assert take_profit.ocaType == 0
    assert stop_loss.ocaType == 0
