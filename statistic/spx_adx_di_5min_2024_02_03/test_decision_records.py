from __future__ import annotations

from types import SimpleNamespace
import json
import unittest
import uuid

import pandas as pd

import decision_records as records
import manual_regime_bot_api as bot_api
import manual_regime_rules_backtest as rules


class DecisionRecordTests(unittest.TestCase):
    def setUp(self) -> None:
        self.explanation = {
            "trade_action": "trade",
            "setup_state": "trade_opened",
            "primary_reason": "test_rule",
            "setup_detail": "opened=test_rule",
            "market_phase": "tradeable_breakout",
            "volume_price_phase": "normal_volume_trend",
            "volume_price_detail": "known at decision time",
            "risk_note": "-",
        }
        self.context = {
            "day": "2024-02-13",
            "regime": "bullish_tier1",
            "open": 100.0,
            "decision_price": 101.0,
            "atr14": 2.0,
        }

    @staticmethod
    def trade(track: str) -> SimpleNamespace:
        return SimpleNamespace(
            track=track,
            rule="test_rule",
            side="long",
            entry_date=pd.Timestamp("2024-02-13 08:35:00"),
            entry_price=101.0,
            stop=99.0,
            target=105.0,
            size=1.0,
            regime_at_entry="bullish_tier1",
            labels="",
            entry_labels="",
        )

    def test_signal_key_is_stable_across_timezone_representation(self) -> None:
        kwargs = {
            "strategy_id": "manual-regime",
            "source_timezone": records.DECISION_TIMEZONE,
            "symbol": "spx",
            "primary_rule": "test_rule",
            "side": "long",
            "track": "baseline",
            "occurrence": 0,
        }
        naive, naive_time = records.build_signal_key(
            decision_time="2024-02-13 08:35:00",
            **kwargs,
        )
        aware, aware_time = records.build_signal_key(
            decision_time="2024-02-13T13:35:00Z",
            **kwargs,
        )
        self.assertEqual(naive, aware)
        self.assertEqual(naive_time, aware_time)

    def test_one_accepted_record_is_created_per_opened_track(self) -> None:
        result = records.build_decision_records(
            symbol="SPX",
            decision_time="2024-02-13 08:35:00",
            strategy_version="test-v1",
            config_hash=records.stable_config_hash({"mode": "test"}),
            checks={"test_rule": "trade"},
            explanation=self.explanation,
            context=self.context,
            opened_trades=[self.trade("baseline"), self.trade("experimental")],
        )
        self.assertEqual(2, len(result))
        self.assertEqual({"baseline", "experimental"}, {item.track for item in result})
        self.assertEqual(2, len({item.signal_key for item in result}))
        self.assertEqual(1, len({item.decision_key for item in result}))
        self.assertTrue(all(item.status == "accepted" for item in result))
        self.assertTrue(
            all(
                any(factor.category == "risk" for factor in item.factors)
                for item in result
            )
        )

    def test_no_trade_creates_one_structured_daily_record(self) -> None:
        explanation = {
            **self.explanation,
            "trade_action": "no_trade",
            "setup_state": "warmup",
            "primary_reason": "insufficient_history",
        }
        result = records.build_decision_records(
            symbol="SPX",
            decision_time="2024-01-02 08:35:00",
            strategy_version="test-v1",
            config_hash=records.stable_config_hash({"mode": "test"}),
            checks={"warmup": "skip"},
            explanation=explanation,
            context=self.context,
            opened_trades=[],
        )
        self.assertEqual(1, len(result))
        self.assertEqual("missing_data", result[0].status)
        self.assertEqual(result[0].decision_key, result[0].signal_key)

    def test_trade_action_without_opened_trade_is_blocked(self) -> None:
        result = records.build_decision_records(
            symbol="ALV",
            decision_time="2024-02-13 08:35:00",
            strategy_version="test-v1",
            config_hash=records.stable_config_hash({"mode": "test"}),
            checks={"crash_repair": "trade"},
            explanation=self.explanation,
            context={**self.context, "open_trade_count": 2},
            opened_trades=[],
        )

        self.assertEqual(1, len(result))
        self.assertEqual("blocked", result[0].status)
        self.assertEqual("no_trade", result[0].trade_action)
        self.assertEqual("blocked_no_trade_object", result[0].setup_state)
        self.assertEqual(
            "trade_signal_without_opened_trade", result[0].primary_reason
        )
        invariant = next(
            factor
            for factor in result[0].factors
            if factor.factor_code == "decision_trade_invariant"
        )
        self.assertFalse(invariant.passed)
        self.assertTrue(invariant.is_veto)

    def test_json_is_strict_and_contains_calculation_version(self) -> None:
        result = records.build_decision_records(
            symbol="SPX",
            decision_time="2024-02-13 08:35:00",
            strategy_version="test-v1",
            config_hash=records.stable_config_hash({"nan": float("nan")}),
            checks={"test_rule": "trade"},
            explanation=self.explanation,
            context={**self.context, "missing": pd.NA, "nan": float("nan")},
            opened_trades=[self.trade("baseline")],
        )
        payload = json.loads(result[0].to_json())
        self.assertIsNone(payload["context"]["missing"])
        self.assertIsNone(payload["context"]["nan"])
        self.assertEqual(
            records.FACTOR_CALCULATION_VERSION,
            payload["factors"][0]["calculation_version"],
        )

    def test_versioned_json_roundtrip_preserves_provenance(self) -> None:
        record = records.build_decision_records(
            symbol="SPX",
            decision_time="2024-02-13 08:35:00",
            strategy_version=records.STRATEGY_VERSION,
            config_hash=records.stable_config_hash({"mode": "test"}),
            checks={"test_rule": "trade"},
            explanation=self.explanation,
            context=self.context,
            opened_trades=[self.trade("baseline")],
        )[0]

        restored = records.DecisionRecord.from_json(record.to_json())

        self.assertEqual(record, restored)
        self.assertEqual("test_rule", restored.rule_id)
        self.assertEqual("1.0.0", restored.rule_version)
        self.assertEqual(records.STRATEGY_VARIANT_ID, restored.strategy_variant_id)
        self.assertEqual(
            records.MONEY_MANAGEMENT_VERSION,
            restored.money_management_version,
        )
        self.assertEqual(
            restored.rule_id,
            restored.context["version_provenance"]["rule_id"],
        )

    def test_pre_versioning_payload_remains_readable(self) -> None:
        record = records.build_decision_records(
            symbol="SPX",
            decision_time="2024-02-13 08:35:00",
            strategy_version="legacy-rules-v1",
            config_hash=records.stable_config_hash({"mode": "legacy"}),
            checks={"test_rule": "trade"},
            explanation=self.explanation,
            context=self.context,
            opened_trades=[self.trade("baseline")],
        )[0]
        legacy = record.to_dict()
        for field in (
            "rule_id",
            "rule_version",
            "strategy_variant_id",
            "money_management_version",
        ):
            legacy.pop(field)
        legacy["schema_version"] = "1.1"
        legacy["context"].pop("version_provenance", None)

        restored = records.DecisionRecord.from_dict(legacy)

        self.assertEqual("1.1", restored.schema_version)
        self.assertIsNone(restored.rule_id)
        self.assertIsNone(restored.rule_version)
        self.assertIsNone(restored.strategy_variant_id)
        self.assertIsNone(restored.money_management_version)

    def test_rule_version_and_variant_do_not_change_signal_key(self) -> None:
        baseline = records.build_decision_records(
            symbol="SPX",
            decision_time="2024-02-13 08:35:00",
            strategy_version=records.STRATEGY_VERSION,
            config_hash=records.stable_config_hash({"variant": "baseline"}),
            checks={"test_rule": "trade"},
            explanation=self.explanation,
            context=self.context,
            opened_trades=[self.trade("baseline")],
        )[0]
        candidate_context = records.DecisionVersionContext(
            strategy_variant_id="manual-regime-candidate@1.0.0",
            engine_version="manual-regime-engine@1.1.0",
            money_management_version="fixed-risk-per-trade@1.0.0",
            rule_versions={"test_rule": "2.0.0"},
            manifest_hash="candidate-manifest-hash",
        )
        candidate = records.build_decision_records(
            symbol="SPX",
            decision_time="2024-02-13 08:35:00",
            strategy_version="candidate-rules-v1",
            config_hash=records.stable_config_hash({"variant": "candidate"}),
            checks={"test_rule": "trade"},
            explanation=self.explanation,
            context=self.context,
            opened_trades=[self.trade("baseline")],
            version_context=candidate_context,
        )[0]

        self.assertEqual(baseline.signal_key, candidate.signal_key)
        self.assertEqual(baseline.rule_id, candidate.rule_id)
        self.assertNotEqual(baseline.rule_version, candidate.rule_version)
        self.assertNotEqual(baseline.strategy_variant_id, candidate.strategy_variant_id)

    def test_human_log_is_rendered_from_the_record(self) -> None:
        result = records.build_decision_records(
            symbol="SPX",
            decision_time="2024-02-13 08:35:00",
            strategy_version="test-v1",
            config_hash=records.stable_config_hash({"mode": "test"}),
            checks={"test_rule": "trade"},
            explanation=self.explanation,
            context=self.context,
            opened_trades=[self.trade("baseline")],
        )
        line = records.format_log_line(result, closed_tracks=set(), runtime=True)
        self.assertIn("asset=SPX phase=decision", line)
        self.assertIn("opened=test_rule", line)
        self.assertIn("reason=test_rule", line)


class DecisionRecordIntegrationTests(unittest.TestCase):
    def test_engine_and_daily_api_expose_the_same_signal_keys(self) -> None:
        raw_file = rules.RAW_DIR / "Rohdaten_SPX.csv"
        if not raw_file.exists():
            self.skipTest(f"integration data is missing: {raw_file}")

        token = uuid.uuid4().hex
        engine_log = rules.RAW_DIR.parent / f".decision_record_engine_{token}.log"
        api_log = rules.RAW_DIR.parent / f".decision_record_api_{token}.log"
        jsonl_path = rules.RAW_DIR.parent / f".decision_records_{token}.jsonl"
        for path in (engine_log, api_log, jsonl_path):
            self.addCleanup(path.unlink, missing_ok=True)

        collected: list[records.DecisionRecord] = []
        result = rules.run_backtest(
            "SPX",
            "2024-01-01",
            "2024-04-01",
            runtime_log_path=engine_log,
            decision_records_out=collected,
            decision_records_jsonl_path=jsonl_path,
        )
        self.assertEqual(3, len(result))
        signals, trades, daily = result
        self.assertFalse(signals.empty)
        self.assertFalse(daily.empty)
        self.assertGreaterEqual(len(collected), len(signals))
        accepted = [item for item in collected if item.status == "accepted"]
        self.assertEqual(len(trades), len(accepted))
        self.assertEqual(len(accepted), len({item.signal_key for item in accepted}))
        jsonl_rows = jsonl_path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(collected), len(jsonl_rows))
        self.assertTrue(all(json.loads(row)["signal_key"] for row in jsonl_rows))

        accepted_day = (
            pd.Timestamp(accepted[0].decision_time_utc)
            .tz_convert(records.DECISION_TIMEZONE)
            .date()
            .isoformat()
        )
        full_day_keys = {
            item.signal_key
            for item in accepted
            if (
                pd.Timestamp(item.decision_time_utc)
                .tz_convert(records.DECISION_TIMEZONE)
                .date()
                .isoformat()
                == accepted_day
            )
        }
        batch = bot_api.get_trade_openings_for_day(
            "SPX",
            accepted_day,
            start="2024-01-01",
            history_lookback_days=None,
            runtime_log_path=api_log,
        )
        api_keys = {
            item.signal_key
            for item in batch.decision_records
            if item.status == "accepted"
        }
        self.assertEqual(full_day_keys, api_keys)
        self.assertTrue(
            {"signal_key", "decision_key"}.issubset(batch.trades.columns)
        )
        self.assertEqual(set(batch.trades["signal_key"]), api_keys)
        self.assertEqual(
            set(batch.trades["track"].astype(str)),
            {item.track for item in batch.decision_records},
        )


if __name__ == "__main__":
    unittest.main()
