from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping, Sequence

import pandas as pd

from src.models.money_management import MoneyManagementConfig


PLAN_COLUMNS = [
    "symbol",
    "track",
    "source_tracks",
    "track_count",
    "rule",
    "side",
    "entry_date",
    "exit_date",
    "entry",
    "stop",
    "target",
    "signal_size",
    "risk_per_share",
    "risk_budget_usd",
    "suggested_quantity",
    "quantity",
    "allocated_risk_usd",
    "notional_usd",
    "allowed",
    "blocked_reason",
    "regime_at_entry",
    "labels",
    "entry_labels",
]


@dataclass(frozen=True)
class SizedTradeIntent:
    symbol: str
    track: str
    source_tracks: str
    track_count: int
    rule: str
    side: str
    entry_date: pd.Timestamp
    exit_date: pd.Timestamp
    entry: float
    stop: float
    target: float
    signal_size: float
    risk_per_share: float
    risk_budget_usd: float
    suggested_quantity: int
    quantity: int
    allocated_risk_usd: float
    notional_usd: float
    allowed: bool
    blocked_reason: str
    regime_at_entry: str = ""
    labels: str = ""
    entry_labels: str = ""


class MoneyManagementBlock:
    """
    Bot-side trade sizing and portfolio gating.

    The rule engine produces trade intents. This block turns those intents into
    executable quantities while keeping the engine untouched.
    """

    def __init__(
        self,
        config: MoneyManagementConfig,
        *,
        track_priority: Sequence[str] = ("baseline", "experimental"),
        dedupe_identical_intents: bool = True,
    ) -> None:
        self.config = config
        self.track_priority = tuple(track_priority)
        self.dedupe_identical_intents = dedupe_identical_intents
        self._track_rank = {track.lower(): idx for idx, track in enumerate(self.track_priority)}

    @property
    def available_risk_budget_usd(self) -> float:
        return self.config.risk_budget_usd

    def plan_from_result_map(
        self,
        results: Mapping[str, object],
        *,
        symbol_order: Sequence[str] | None = None,
        current_open_trades: int = 0,
        current_open_long_trades: int = 0,
        current_open_short_trades: int = 0,
    ) -> pd.DataFrame:
        frames: list[pd.DataFrame] = []
        for symbol, batch in results.items():
            trades = getattr(batch, "trades", None)
            if trades is None or not isinstance(trades, pd.DataFrame) or trades.empty:
                continue
            frame = trades.copy()
            if "symbol" not in frame.columns:
                frame["symbol"] = symbol
            frames.append(frame)

        if not frames:
            return pd.DataFrame(columns=PLAN_COLUMNS)
        combined = pd.concat(frames, ignore_index=True, sort=False)
        return self.plan_from_trades(
            combined,
            symbol_order=symbol_order,
            current_open_trades=current_open_trades,
            current_open_long_trades=current_open_long_trades,
            current_open_short_trades=current_open_short_trades,
        )

    def plan_from_trades(
        self,
        trades: pd.DataFrame,
        *,
        symbol_order: Sequence[str] | None = None,
        current_open_trades: int = 0,
        current_open_long_trades: int = 0,
        current_open_short_trades: int = 0,
    ) -> pd.DataFrame:
        if trades.empty:
            return pd.DataFrame(columns=PLAN_COLUMNS)

        frame = self._normalize_trade_frame(trades)
        if self.dedupe_identical_intents:
            frame = self._dedupe_identical_intents(frame)

        frame = self._sort_candidates(frame, symbol_order=symbol_order)
        planned_rows: list[SizedTradeIntent] = []
        current_open_trades = max(
            int(current_open_trades),
            int(current_open_long_trades) + int(current_open_short_trades),
        )
        remaining_total_slots = max(self.config.max_open_trades - current_open_trades, 0)
        remaining_long_slots = max(self.config.max_open_trades_long - int(current_open_long_trades), 0)
        remaining_short_slots = max(self.config.max_open_trades_short - int(current_open_short_trades), 0)
        accepted_total = 0
        accepted_long = 0
        accepted_short = 0

        for _, row in frame.iterrows():
            side = str(row["side"]).lower()
            capacity_allowed = True
            capacity_reason = ""
            if side in {"long", "short"}:
                capacity_allowed, capacity_reason = self._capacity_allowed(
                    side,
                    accepted_total=accepted_total,
                    accepted_long=accepted_long,
                    accepted_short=accepted_short,
                    remaining_total_slots=remaining_total_slots,
                    remaining_long_slots=remaining_long_slots,
                    remaining_short_slots=remaining_short_slots,
                )
            planned_rows.append(
                self._size_row(
                    row,
                    capacity_allowed=capacity_allowed,
                    capacity_block_reason=capacity_reason,
                )
            )
            if planned_rows[-1].allowed:
                accepted_total += 1
                if side == "long":
                    accepted_long += 1
                elif side == "short":
                    accepted_short += 1

        return pd.DataFrame([row.__dict__ for row in planned_rows], columns=PLAN_COLUMNS)

    def _normalize_trade_frame(self, trades: pd.DataFrame) -> pd.DataFrame:
        frame = trades.copy()

        if "symbol" not in frame.columns:
            raise ValueError("trade frame must contain a symbol column")
        if "track" not in frame.columns:
            frame["track"] = ""
        if "rule" not in frame.columns:
            frame["rule"] = ""
        if "side" not in frame.columns:
            frame["side"] = ""
        if "entry" not in frame.columns and "entry_price" in frame.columns:
            frame["entry"] = frame["entry_price"]
        if "entry_price" not in frame.columns and "entry" in frame.columns:
            frame["entry_price"] = frame["entry"]
        if "stop" not in frame.columns:
            frame["stop"] = pd.NA
        if "target" not in frame.columns:
            frame["target"] = pd.NA
        if "size" not in frame.columns:
            frame["size"] = 1.0
        if "regime_at_entry" not in frame.columns:
            frame["regime_at_entry"] = ""
        if "labels" not in frame.columns:
            frame["labels"] = ""
        if "entry_labels" not in frame.columns:
            frame["entry_labels"] = ""

        frame["symbol"] = frame["symbol"].astype(str).str.upper()
        frame["track"] = frame["track"].fillna("").astype(str)
        frame["rule"] = frame["rule"].fillna("").astype(str)
        frame["side"] = frame["side"].fillna("").astype(str).str.lower()
        frame["entry"] = pd.to_numeric(frame["entry"], errors="coerce")
        frame["entry_price"] = pd.to_numeric(frame["entry_price"], errors="coerce")
        frame["stop"] = pd.to_numeric(frame["stop"], errors="coerce")
        frame["target"] = pd.to_numeric(frame["target"], errors="coerce")
        frame["size"] = pd.to_numeric(frame["size"], errors="coerce").fillna(1.0)
        frame["regime_at_entry"] = frame["regime_at_entry"].fillna("").astype(str)
        frame["labels"] = frame["labels"].fillna("").astype(str)
        frame["entry_labels"] = frame["entry_labels"].fillna("").astype(str)
        frame["entry_date"] = pd.to_datetime(frame["entry_date"], errors="coerce")
        if "exit_date" not in frame.columns:
            frame["exit_date"] = pd.NaT
        frame["exit_date"] = pd.to_datetime(frame["exit_date"], errors="coerce")

        if "signal_size" not in frame.columns:
            frame["signal_size"] = frame["size"]
        else:
            frame["signal_size"] = pd.to_numeric(frame["signal_size"], errors="coerce").fillna(frame["size"])

        return frame

    def _dedupe_identical_intents(self, trades: pd.DataFrame) -> pd.DataFrame:
        key_columns = [
            "symbol",
            "entry_date",
            "exit_date",
            "side",
            "entry",
            "stop",
            "target",
            "rule",
            "regime_at_entry",
        ]

        rows: list[pd.Series] = []
        for _, group in trades.groupby(key_columns, dropna=False, sort=False):
            group = group.copy()
            group["_track_rank"] = group["track"].str.lower().map(lambda value: self._track_rank.get(value, len(self._track_rank)))
            group = group.sort_values(["_track_rank", "track"], kind="mergesort")
            preferred = group.iloc[0].copy()
            tracks = [value for value in group["track"].astype(str).tolist() if value]
            preferred["source_tracks"] = ",".join(dict.fromkeys(tracks))
            preferred["track_count"] = len(dict.fromkeys(tracks))
            rows.append(preferred.drop(labels=["_track_rank"], errors="ignore"))

        if not rows:
            return pd.DataFrame(columns=trades.columns.tolist() + ["source_tracks", "track_count"])

        return pd.DataFrame(rows).reset_index(drop=True)

    def _sort_candidates(
        self,
        trades: pd.DataFrame,
        *,
        symbol_order: Sequence[str] | None = None,
    ) -> pd.DataFrame:
        frame = trades.copy()
        frame["_track_rank"] = frame["track"].str.lower().map(lambda value: self._track_rank.get(value, len(self._track_rank)))
        if symbol_order is not None:
            symbol_rank = {symbol.upper(): idx for idx, symbol in enumerate(symbol_order)}
            frame["_symbol_rank"] = frame["symbol"].map(lambda value: symbol_rank.get(str(value).upper(), len(symbol_rank)))
            sort_columns = ["entry_date", "_symbol_rank", "_track_rank", "rule"]
        else:
            sort_columns = ["entry_date", "symbol", "_track_rank", "rule"]

        frame = frame.sort_values(sort_columns, kind="mergesort").reset_index(drop=True)
        return frame

    def _capacity_allowed(
        self,
        side: str,
        *,
        accepted_total: int,
        accepted_long: int,
        accepted_short: int,
        remaining_total_slots: int,
        remaining_long_slots: int,
        remaining_short_slots: int,
    ) -> tuple[bool, str]:
        if accepted_total >= remaining_total_slots:
            return False, "max_open_trades_total_reached"
        if side == "long" and accepted_long >= remaining_long_slots:
            return False, "max_open_trades_long_reached"
        if side == "short" and accepted_short >= remaining_short_slots:
            return False, "max_open_trades_short_reached"
        return True, ""

    def _size_row(
        self,
        row: pd.Series,
        *,
        capacity_allowed: bool,
        capacity_block_reason: str = "",
    ) -> SizedTradeIntent:
        symbol = str(row["symbol"]).upper()
        track = str(row["track"])
        source_tracks = str(row.get("source_tracks", track))
        track_count = int(row.get("track_count", 1) or 1)
        rule = str(row["rule"])
        side = str(row["side"]).lower()
        entry_date = pd.Timestamp(row["entry_date"])
        exit_date = pd.Timestamp(row["exit_date"]) if not pd.isna(row.get("exit_date", pd.NaT)) else pd.NaT
        entry = float(row["entry"]) if not pd.isna(row["entry"]) else float("nan")
        stop = float(row["stop"]) if not pd.isna(row["stop"]) else float("nan")
        target = float(row["target"]) if not pd.isna(row["target"]) else float("nan")
        signal_size = float(row.get("signal_size", row.get("size", 1.0)) or 1.0)
        regime_at_entry = str(row.get("regime_at_entry", ""))
        labels = str(row.get("labels", ""))
        entry_labels = str(row.get("entry_labels", ""))

        risk_budget_usd = self.config.risk_budget_usd
        risk_per_share = float("nan")
        suggested_quantity = 0
        quantity = 0
        allocated_risk_usd = 0.0
        notional_usd = 0.0
        blocked_reason = ""

        if not capacity_allowed:
            blocked_reason = capacity_block_reason or "max_open_trades_reached"
        elif side not in {"long", "short"}:
            blocked_reason = "unsupported_side"
        elif pd.isna(entry) or pd.isna(stop) or pd.isna(target):
            blocked_reason = "missing_price"
        else:
            risk_per_share = abs(entry - stop)
            if risk_per_share <= 0:
                blocked_reason = "invalid_risk_distance"
            else:
                suggested_quantity = int(math.floor(risk_budget_usd / risk_per_share))
                if suggested_quantity < 1:
                    blocked_reason = "risk_budget_too_small"
                else:
                    quantity = suggested_quantity
                    allocated_risk_usd = quantity * risk_per_share
                    notional_usd = quantity * entry

        return SizedTradeIntent(
            symbol=symbol,
            track=track,
            source_tracks=source_tracks,
            track_count=track_count,
            rule=rule,
            side=side,
            entry_date=entry_date,
            exit_date=exit_date,
            entry=entry,
            stop=stop,
            target=target,
            signal_size=signal_size,
            risk_per_share=risk_per_share,
            risk_budget_usd=risk_budget_usd,
            suggested_quantity=suggested_quantity,
            quantity=quantity,
            allocated_risk_usd=allocated_risk_usd,
            notional_usd=notional_usd,
            allowed=bool(quantity > 0 and blocked_reason == ""),
            blocked_reason=blocked_reason,
            regime_at_entry=regime_at_entry,
            labels=labels,
            entry_labels=entry_labels,
        )


MoneyManager = MoneyManagementBlock
