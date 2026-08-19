from __future__ import annotations

import unittest

from src.execution.safe_bracket_order import BracketOrderSpec, SafeBracketOrderManager


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


def _spec(**overrides: object) -> BracketOrderSpec:
    values: dict[str, object] = {
        "action": "BUY",
        "quantity": 1,
        "reference_entry_price": 100.0,
        "take_profit_price": 105.0,
        "stop_loss_price": 95.0,
        "entry_order_type": "LMT",
        "limit_price": 100.0,
        "parent_tif": "DAY",
        "exit_tif": "GTC",
        "outside_rth": False,
        "order_ref": "CODEX_BRACKET_TEST",
        "tick_size": 0.01,
    }
    values.update(overrides)
    return BracketOrderSpec(**values)


class SafeBracketOrderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manager = SafeBracketOrderManager(
            _FakeIB(),
            account="DU1234",
            require_paper_account=True,
            ack_timeout_seconds=5.0,
        )

    def test_build_orders_uses_atomic_transmit_chain(self) -> None:
        parent, take_profit, stop_loss = self.manager.build_orders(_spec())

        self.assertEqual((parent.transmit, take_profit.transmit, stop_loss.transmit), (False, False, True))
        self.assertEqual(take_profit.parentId, parent.orderId)
        self.assertEqual(stop_loss.parentId, parent.orderId)
        self.assertEqual(len({parent.orderId, take_profit.orderId, stop_loss.orderId}), 3)
        self.assertEqual({parent.account, take_profit.account, stop_loss.account}, {"DU1234"})
        self.assertEqual({parent.orderRef, take_profit.orderRef, stop_loss.orderRef}, {"CODEX_BRACKET_TEST"})

    def test_build_market_parent_keeps_children_attached(self) -> None:
        parent, take_profit, stop_loss = self.manager.build_orders(
            _spec(entry_order_type="MKT", limit_price=None)
        )

        self.assertEqual(parent.orderType, "MKT")
        self.assertEqual(take_profit.parentId, parent.orderId)
        self.assertEqual(stop_loss.parentId, parent.orderId)
        self.assertEqual((parent.transmit, take_profit.transmit, stop_loss.transmit), (False, False, True))

    def test_invalid_specs_are_rejected(self) -> None:
        cases = [
            ({"stop_loss_price": 101.0}, "BUY bracket"),
            ({"take_profit_price": 99.0}, "BUY bracket"),
            ({"quantity": 0}, "quantity"),
            ({"order_ref": "X" * 46}, "order_ref"),
            (
                {
                    "reference_entry_price": 100.004,
                    "take_profit_price": 100.0049,
                    "stop_loss_price": 99.0,
                },
                "rounded BUY levels",
            ),
        ]
        for overrides, message in cases:
            with self.subTest(overrides=overrides):
                with self.assertRaisesRegex(ValueError, message):
                    self.manager.build_orders(_spec(**overrides))


if __name__ == "__main__":
    unittest.main()
