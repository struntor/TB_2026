from __future__ import annotations

import unittest

import current_live_baseline_config as config


class CurrentLiveBaselineConfigTests(unittest.TestCase):
    def test_live_symbols_and_enabled_pair_symbols_are_the_same_basket(self) -> None:
        symbols = set(config.load_optimized_live_top50_symbols())
        pair_symbols = {symbol for symbol, _rule in config.load_live_keep_pairs()}
        self.assertEqual(symbols, pair_symbols)


if __name__ == "__main__":
    unittest.main()
