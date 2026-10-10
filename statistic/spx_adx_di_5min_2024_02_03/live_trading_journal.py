from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Mapping, Sequence

import pandas as pd

from src.journal.decision_ingest import (
    DecisionIngestContext,
    DecisionJournalIngestor,
    TradeIntent,
)
from src.journal.execution_ingest import ExecutionContext, ExecutionJournalIngestor
from src.journal.models import StrategyRunRecord, deterministic_id


@dataclass(frozen=True, slots=True)
class JournalTradeLink:
    signal_key: str
    decision_id: str
    trade_id: str


class ManualRegimeJournalSession:
    """Own one concrete manual-regime execution session."""

    def __init__(
        self,
        store: Any,
        *,
        day: pd.Timestamp,
        run_mode: str,
        strategy_id: str,
        strategy_version: str,
        strategy_variant_id: str,
        engine_version: str,
        money_management_version: str,
        config_hash: str,
        account: str,
        parameters: Mapping[str, Any],
        metadata: Mapping[str, Any],
        now_utc: Callable[[], datetime] | None = None,
    ) -> None:
        if run_mode not in {"paper", "live"}:
            raise ValueError("live journal run_mode must be paper or live")
        self.store = store
        self.day = pd.Timestamp(day).normalize()
        self.run_mode = run_mode
        self.strategy_id = strategy_id
        self.strategy_version = strategy_version
        self.strategy_variant_id = strategy_variant_id
        self.engine_version = engine_version
        self.money_management_version = money_management_version
        self.config_hash = config_hash
        if not account:
            raise ValueError("account is required")
        self.account = account
        self.parameters = dict(parameters)
        self.metadata = dict(metadata)
        self._now_utc = now_utc or (lambda: datetime.now(timezone.utc))
        self._session_started_at_utc = self._now_utc()
        self.run_id = deterministic_id(
            "run",
            strategy_id,
            strategy_variant_id,
            self.day.date().isoformat(),
            run_mode,
            config_hash,
            account,
            bool(self.parameters.get("place_orders", False)),
            self._session_started_at_utc.isoformat(),
        )
        self._started_at_utc: datetime | None = None
        self._decision_ingestor = DecisionJournalIngestor(store)
        self._execution_ingestor = ExecutionJournalIngestor(store)
        self._links: dict[str, JournalTradeLink] = {}
        self._submission_contexts: dict[int, ExecutionContext] = {}

    @property
    def decision_context(self) -> DecisionIngestContext:
        return DecisionIngestContext(self.run_id, self.run_mode)

    def start(self) -> str:
        if self._started_at_utc is not None:
            return self.run_id
        self._started_at_utc = self._session_started_at_utc
        try:
            self.store.start_run(self._run_record(status="running", ended_at_utc=None))
        except BaseException:
            self._started_at_utc = None
            raise
        return self.run_id

    def finish(self, status: str, *, error: str | None) -> None:
        if status not in {"completed", "failed"}:
            raise ValueError("final run status must be completed or failed")
        if self._started_at_utc is None:
            return
        metadata = dict(self.metadata)
        if error:
            metadata["error"] = error
        self.store.start_run(
            self._run_record(
                status=status,
                ended_at_utc=self._now_utc(),
                metadata=metadata,
            )
        )

    def ingest_decisions(self, records: Iterable[Any]) -> dict[str, str]:
        results = self._decision_ingestor.ingest_many(records, self.decision_context)
        return {result.signal_key: result.decision_id for result in results}

    def create_stock_trade_intents(
        self,
        trades: pd.DataFrame,
        final_records: Sequence[Any],
        quantities_by_signal: Mapping[str, int],
    ) -> dict[str, JournalTradeLink]:
        accepted = {
            str(record.signal_key): record
            for record in final_records
            if str(record.status) == "accepted"
        }
        row_keys = set(trades.get("signal_key", pd.Series(dtype="object")).astype(str))
        if row_keys != set(accepted):
            raise RuntimeError(
                "accepted journal DecisionRecords do not match live trade rows: "
                f"rows={sorted(row_keys)} decisions={sorted(accepted)}"
            )
        links: dict[str, JournalTradeLink] = {}
        for row in trades.itertuples(index=False):
            payload = row._asdict()
            signal_key = str(payload["signal_key"])
            record = accepted[signal_key]
            calculated_quantity = int(quantities_by_signal[signal_key])
            intent = TradeIntent(
                asset_class="STK",
                symbol=str(payload["symbol"]).upper(),
                side=str(payload["side"]).lower(),
                status="planned",
                opened_at_utc=str(record.decision_time_utc),
                quantity=(
                    float(calculated_quantity) if calculated_quantity > 0 else None
                ),
                currency="USD",
                entry_price=float(payload["entry"]),
                stop_price=float(payload["stop"]),
                target_price=float(payload["target"]),
                exchange="SMART",
                legs=(),
                metadata={
                    "track": str(payload["track"]),
                    "rule": str(payload["rule"]),
                    "decision_key": str(payload["decision_key"]),
                    "size_multiplier": float(payload.get("size", 1.0) or 1.0),
                    "calculated_quantity": calculated_quantity,
                    **(
                        {
                            "position_sizing_policy_version": str(
                                payload["position_sizing_policy_version"]
                            ),
                            "regime_volume_risk_multiplier": float(
                                payload["regime_volume_risk_multiplier"]
                            ),
                            "position_sizing_reasons": str(
                                payload["position_sizing_reasons"]
                            ),
                            "prior_structure_score": int(
                                payload["prior_structure_score"]
                            ),
                            "prior_structure_phase": str(
                                payload["prior_structure_phase"]
                            ),
                            "vp_overhead_magnet_short_risk": bool(
                                payload["vp_overhead_magnet_short_risk"]
                            ),
                            "legacy_shadow_variant_id": str(
                                payload["legacy_shadow_variant_id"]
                            ),
                            "legacy_shadow_size": float(
                                payload["legacy_shadow_size"]
                            ),
                            "legacy_shadow_quantity": int(
                                payload["legacy_shadow_quantity"]
                            ),
                        }
                        if "position_sizing_policy_version" in payload
                        else {}
                    ),
                    **(
                        {
                            "fresh_stop_policy_version": str(
                                payload["fresh_stop_policy_version"]
                            ),
                            "fresh_stop_parent_variant_id": str(
                                payload["fresh_stop_parent_variant_id"]
                            ),
                            "legacy_v2_stop": float(payload["legacy_v2_stop"]),
                            "fresh_stop_prior_afternoon_high": float(
                                payload["fresh_stop_prior_afternoon_high"]
                            ),
                            "fresh_stop_opening_high": float(
                                payload["fresh_stop_opening_high"]
                            ),
                            "fresh_stop_tolerance": float(
                                payload["fresh_stop_tolerance"]
                            ),
                            "fresh_stop_crv": float(payload["fresh_stop_crv"]),
                            "fresh_stop_reason": str(payload["fresh_stop_reason"]),
                            "fresh_stop_parent_v2_quantity": int(
                                payload["fresh_stop_parent_v2_quantity"]
                            ),
                        }
                        if str(payload.get("fresh_stop_policy_version", ""))
                        else {}
                    ),
                    **(
                        {
                            "gap_breakdown_quarter_policy_version": str(
                                payload["gap_breakdown_quarter_policy_version"]
                            ),
                            "gap_breakdown_quarter_risk_multiplier": float(
                                payload["gap_breakdown_quarter_risk_multiplier"]
                            ),
                            "gap_breakdown_quarter_reasons": str(
                                payload["gap_breakdown_quarter_reasons"]
                            ),
                            "gap_breakdown_quarter_context": dict(
                                payload["gap_breakdown_quarter_context"]
                            ),
                            "v3_parent_shadow_variant_id": str(
                                payload["v3_parent_shadow_variant_id"]
                            ),
                            "v3_parent_shadow_size": float(
                                payload["v3_parent_shadow_size"]
                            ),
                            "v3_parent_shadow_quantity": int(
                                payload["v3_parent_shadow_quantity"]
                            ),
                        }
                        if str(
                            payload.get(
                                "gap_breakdown_quarter_policy_version",
                                "",
                            )
                        )
                        else {}
                    ),
                },
            )
            trade_id = self._decision_ingestor.create_trade_intent(
                record,
                self.decision_context,
                intent,
            )
            decision_id = deterministic_id("decision", self.run_id, signal_key)
            links[signal_key] = JournalTradeLink(signal_key, decision_id, trade_id)
        self._links.update(links)
        return links

    def execution_context(
        self,
        row: pd.Series,
        *,
        account: str,
        client_id: int,
    ) -> ExecutionContext:
        signal_key = str(row["signal_key"])
        try:
            link = self._links[signal_key]
        except KeyError as exc:
            raise RuntimeError(f"no journal trade intent for signal_key={signal_key}") from exc
        return ExecutionContext(
            run_id=self.run_id,
            signal_key=signal_key,
            trade_id=link.trade_id,
            decision_id=link.decision_id,
            account=account,
            client_id=client_id,
            observed_at_utc=self._now_utc(),
        )

    def record_blocker(
        self,
        row: pd.Series,
        *,
        account: str,
        client_id: int,
        status: str,
        reason_code: str,
        reason: str,
        requested_quantity: float | None,
        details: Mapping[str, Any],
    ) -> None:
        context = self.execution_context(row, account=account, client_id=client_id)
        self._execution_ingestor.record_blocker(
            context,
            status=status,
            reason_code=reason_code,
            reason=reason,
            requested_quantity=requested_quantity,
            occurred_at=self._now_utc(),
            details=details,
        )

    def ingest_submission(
        self,
        submission: Any,
        row: pd.Series,
        *,
        account: str,
        client_id: int,
    ) -> None:
        context = self.execution_context(row, account=account, client_id=client_id)
        with self.store.transaction():
            self._execution_ingestor.ingest_bracket_submission(submission, context)
        self._submission_contexts[id(submission)] = context

    def reingest_submissions(self, submissions: Iterable[Any]) -> None:
        for submission in submissions:
            context = self._submission_contexts.get(id(submission))
            if context is None:
                raise RuntimeError("submission was not registered with this journal run")
            refreshed = ExecutionContext(
                run_id=context.run_id,
                signal_key=context.signal_key,
                trade_id=context.trade_id,
                decision_id=context.decision_id,
                account=context.account,
                client_id=context.client_id,
                observed_at_utc=self._now_utc(),
            )
            with self.store.transaction():
                self._execution_ingestor.ingest_bracket_submission(submission, refreshed)

    def _run_record(
        self,
        *,
        status: str,
        ended_at_utc: datetime | None,
        metadata: Mapping[str, Any] | None = None,
    ) -> StrategyRunRecord:
        if self._started_at_utc is None:
            raise RuntimeError("journal run was not started")
        return StrategyRunRecord(
            run_id=self.run_id,
            strategy_id=self.strategy_id,
            strategy_version=self.strategy_version,
            config_hash=self.config_hash,
            run_type=self.run_mode,
            environment=self.run_mode,
            started_at_utc=self._started_at_utc,
            ended_at_utc=ended_at_utc,
            status=status,
            parameters=self.parameters,
            metadata={
                **dict(metadata or self.metadata),
                "engine_version": self.engine_version,
                "money_management_version": self.money_management_version,
                "strategy_variant_id": self.strategy_variant_id,
            },
            variant_id=self.strategy_variant_id,
        )
