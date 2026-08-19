from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
import html
import json
import math
from pathlib import Path
import re
import sqlite3
from typing import Any, Iterable, Sequence
from urllib.parse import quote
from zoneinfo import ZoneInfo

import matplotlib.pyplot as plt
import pandas as pd

from .store import DEFAULT_JOURNAL_PATH


ET = ZoneInfo("America/New_York")
DEFAULT_REPORT_ROOT = Path(r"D:\Trading\journal\reports")
PERIOD_PATTERN = re.compile(r"^(\d{4})(?:-(\d{2}))?$")


@dataclass(frozen=True, slots=True)
class Period:
    label: str
    start: pd.Timestamp
    end_exclusive: pd.Timestamp


@dataclass(frozen=True, slots=True)
class ReportSelection:
    variant_id: str | None
    strategy_id: str | None
    period: Period
    actual_run_types: tuple[str, ...]
    backtest_run_id: str | None
    starting_capital: float | None

    def __post_init__(self) -> None:
        if bool(self.variant_id) == bool(self.strategy_id):
            raise ValueError("select exactly one of variant_id or strategy_id")
        if not self.actual_run_types:
            raise ValueError("at least one actual run type is required")
        unsupported = set(self.actual_run_types) - {"paper", "live"}
        if unsupported:
            raise ValueError(f"unsupported actual run types: {sorted(unsupported)}")

    @property
    def selector_kind(self) -> str:
        return "variant_id" if self.variant_id else "strategy_id"

    @property
    def selector_value(self) -> str:
        return str(self.variant_id or self.strategy_id)


def parse_period(value: str) -> Period:
    match = PERIOD_PATTERN.fullmatch(value.strip())
    if not match:
        raise ValueError("period must be YYYY or YYYY-MM")
    year = int(match.group(1))
    month_text = match.group(2)
    if month_text is None:
        start = pd.Timestamp(year=year, month=1, day=1, tz=ET)
        end = pd.Timestamp(year=year + 1, month=1, day=1, tz=ET)
        return Period(str(year), start, end)
    month = int(month_text)
    if not 1 <= month <= 12:
        raise ValueError("period month must be between 01 and 12")
    start = pd.Timestamp(year=year, month=month, day=1, tz=ET)
    end = start + pd.offsets.MonthBegin(1)
    return Period(f"{year:04d}-{month:02d}", start, end)


@contextmanager
def _read_only_connection(path: Path) -> Iterable[sqlite3.Connection]:
    if not path.exists():
        raise FileNotFoundError(path)
    uri = f"file:{quote(str(path.resolve()).replace('\\', '/'), safe='/:')}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=5.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    try:
        yield connection
    finally:
        connection.close()


def _json(value: object) -> dict[str, Any]:
    if value in {None, ""}:
        return {}
    payload = json.loads(str(value))
    return payload if isinstance(payload, dict) else {}


def _finite(value: object) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


class JournalReporter:
    def __init__(self, database: str | Path) -> None:
        self.database = Path(database)

    def list_strategies(self) -> list[dict[str, object]]:
        with _read_only_connection(self.database) as connection:
            rows = connection.execute(
                """
                SELECT strategy_id, strategy_version, variant_id, run_type,
                       COUNT(*) AS run_count,
                       SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) AS completed_runs,
                       MAX(COALESCE(ended_at_utc, started_at_utc)) AS latest_run_at_utc
                FROM strategy_runs
                WHERE strategy_id <> 'ibkr-broker-sync'
                GROUP BY strategy_id, strategy_version, variant_id, run_type
                ORDER BY strategy_id, variant_id, run_type
                """
            ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _selector_clause(selection: ReportSelection, alias: str = "sr") -> tuple[str, str]:
        if selection.variant_id:
            return f"{alias}.variant_id=?", selection.variant_id
        return f"{alias}.strategy_id=?", str(selection.strategy_id)

    def select_backtest_run(self, selection: ReportSelection) -> dict[str, object] | None:
        with _read_only_connection(self.database) as connection:
            if selection.backtest_run_id:
                clause, value = self._selector_clause(selection, alias="strategy_runs")
                row = connection.execute(
                    f"""
                    SELECT * FROM strategy_runs
                    WHERE run_id=? AND run_type='backtest' AND status='completed'
                      AND {clause}
                    """,
                    (selection.backtest_run_id, value),
                ).fetchone()
                if row is None:
                    raise ValueError(
                        "completed backtest run not found for the selected strategy: "
                        f"{selection.backtest_run_id}"
                    )
                return dict(row)
            clause, value = self._selector_clause(selection)
            row = connection.execute(
                f"""
                SELECT * FROM strategy_runs sr
                WHERE {clause} AND sr.run_type='backtest' AND sr.status='completed'
                ORDER BY COALESCE(sr.ended_at_utc, sr.started_at_utc) DESC, sr.run_id DESC
                LIMIT 1
                """,
                (value,),
            ).fetchone()
        return dict(row) if row else None

    def _load_trades(
        self,
        selection: ReportSelection,
        *,
        reference_run_id: str | None,
        actual: bool,
    ) -> pd.DataFrame:
        parameters: list[object] = []
        if actual:
            clause, value = self._selector_clause(selection)
            placeholders = ",".join("?" for _ in selection.actual_run_types)
            where = f"{clause} AND sr.run_type IN ({placeholders})"
            parameters.extend((value, *selection.actual_run_types))
        else:
            if reference_run_id is None:
                return pd.DataFrame()
            where = "sr.run_id=?"
            parameters.append(reference_run_id)
        with _read_only_connection(self.database) as connection:
            rows = connection.execute(
                f"""
                SELECT t.trade_id, t.run_id, sr.run_type, sr.variant_id,
                       sr.strategy_id, t.signal_key, t.symbol, t.side, t.status,
                       t.quantity, t.entry_price, t.exit_price, t.realized_pnl,
                       t.opened_at_utc, t.closed_at_utc,
                       COALESCE(sd.rule_id, sd.primary_rule) AS rule_id,
                       sd.primary_rule
                FROM trades t
                JOIN strategy_runs sr ON sr.run_id=t.run_id
                LEFT JOIN signal_decisions sd
                  ON sd.run_id=t.run_id AND sd.signal_key=t.signal_key
                WHERE {where}
                  AND t.status='closed'
                  AND t.closed_at_utc IS NOT NULL
                  AND t.realized_pnl IS NOT NULL
                ORDER BY t.closed_at_utc, t.trade_id
                """,
                tuple(parameters),
            ).fetchall()
        if not rows:
            return pd.DataFrame()
        frame = pd.DataFrame([dict(row) for row in rows])
        frame["closed_at"] = pd.to_datetime(frame["closed_at_utc"], utc=True).dt.tz_convert(ET)
        frame["opened_at"] = pd.to_datetime(frame["opened_at_utc"], utc=True).dt.tz_convert(ET)
        mask = (
            (frame["closed_at"] >= selection.period.start)
            & (frame["closed_at"] < selection.period.end_exclusive)
        )
        frame = frame.loc[mask].copy()
        for column in ("quantity", "entry_price", "exit_price", "realized_pnl"):
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
        return frame.sort_values(["closed_at", "trade_id"]).reset_index(drop=True)

    @staticmethod
    def _deduplicate_actual(frame: pd.DataFrame) -> pd.DataFrame:
        if frame.empty or "signal_key" not in frame:
            return frame
        keyed = frame[frame["signal_key"].notna()].copy()
        duplicates = keyed[keyed["signal_key"].duplicated(keep=False)]
        if not duplicates.empty:
            details = duplicates[["signal_key", "trade_id", "run_id"]].to_dict("records")
            raise RuntimeError(f"duplicate actual signal keys: {details}")
        return frame

    @staticmethod
    def _starting_capital(
        requested: float | None,
        backtest_run: dict[str, object] | None,
    ) -> float:
        if requested is not None:
            if not math.isfinite(requested) or requested < 0:
                raise ValueError("starting capital must be finite and non-negative")
            return requested
        if backtest_run:
            parameters = _json(backtest_run.get("parameters_json"))
            inferred = _finite(parameters.get("starting_capital"))
            if inferred is not None and inferred >= 0:
                return inferred
        return 0.0

    @staticmethod
    def equity_curve(frame: pd.DataFrame, starting_capital: float) -> pd.DataFrame:
        columns = [
            "point", "closed_at", "trade_id", "signal_key", "symbol", "rule_id",
            "realized_pnl", "cumulative_pnl", "equity", "drawdown",
        ]
        if frame.empty:
            return pd.DataFrame(columns=columns)
        curve = frame.copy()
        curve["cumulative_pnl"] = curve["realized_pnl"].cumsum()
        curve["equity"] = starting_capital + curve["cumulative_pnl"]
        curve["drawdown"] = curve["equity"] - curve["equity"].cummax()
        curve.insert(0, "point", range(1, len(curve) + 1))
        return curve[columns]

    @staticmethod
    def metrics(frame: pd.DataFrame, curve: pd.DataFrame, starting_capital: float) -> dict[str, object]:
        if frame.empty:
            return {
                "trades": 0, "wins": 0, "losses": 0, "win_rate": None,
                "net_pnl": 0.0, "profit_factor": None,
                "max_drawdown": 0.0, "starting_capital": starting_capital,
                "ending_equity": starting_capital,
            }
        pnl = frame["realized_pnl"].astype(float)
        gains = float(pnl[pnl > 0].sum())
        losses = float(pnl[pnl < 0].sum())
        wins = int((pnl > 0).sum())
        return {
            "trades": int(len(frame)),
            "wins": wins,
            "losses": int((pnl < 0).sum()),
            "win_rate": wins / len(frame),
            "net_pnl": float(pnl.sum()),
            "profit_factor": gains / abs(losses) if losses < 0 else None,
            "max_drawdown": float(curve["drawdown"].min()) if not curve.empty else 0.0,
            "starting_capital": starting_capital,
            "ending_equity": starting_capital + float(pnl.sum()),
        }

    @staticmethod
    def compare(backtest: pd.DataFrame, actual: pd.DataFrame) -> pd.DataFrame:
        fields = [
            "signal_key", "match_status", "symbol", "rule_id",
            "backtest_entry", "actual_entry", "entry_difference",
            "backtest_exit", "actual_exit", "exit_difference",
            "backtest_quantity", "actual_quantity", "quantity_difference",
            "backtest_pnl", "actual_pnl", "pnl_difference",
            "backtest_closed_at", "actual_closed_at",
        ]
        backtest_by_key = {
            str(row.signal_key): row
            for row in backtest.itertuples(index=False)
            if pd.notna(row.signal_key)
        }
        actual_by_key = {
            str(row.signal_key): row
            for row in actual.itertuples(index=False)
            if pd.notna(row.signal_key)
        }

        def difference(left: object, right: object) -> float | None:
            actual_value, backtest_value = _finite(left), _finite(right)
            if actual_value is None or backtest_value is None:
                return None
            return actual_value - backtest_value

        rows: list[dict[str, object]] = []
        for key in sorted(set(backtest_by_key) | set(actual_by_key)):
            bt = backtest_by_key.get(key)
            act = actual_by_key.get(key)
            status = "matched" if bt is not None and act is not None else (
                "backtest_only" if bt is not None else "actual_only"
            )
            rows.append(
                {
                    "signal_key": key,
                    "match_status": status,
                    "symbol": getattr(act or bt, "symbol", None),
                    "rule_id": getattr(act or bt, "rule_id", None),
                    "backtest_entry": getattr(bt, "entry_price", None),
                    "actual_entry": getattr(act, "entry_price", None),
                    "entry_difference": difference(
                        getattr(act, "entry_price", None), getattr(bt, "entry_price", None)
                    ),
                    "backtest_exit": getattr(bt, "exit_price", None),
                    "actual_exit": getattr(act, "exit_price", None),
                    "exit_difference": difference(
                        getattr(act, "exit_price", None), getattr(bt, "exit_price", None)
                    ),
                    "backtest_quantity": getattr(bt, "quantity", None),
                    "actual_quantity": getattr(act, "quantity", None),
                    "quantity_difference": difference(
                        getattr(act, "quantity", None), getattr(bt, "quantity", None)
                    ),
                    "backtest_pnl": getattr(bt, "realized_pnl", None),
                    "actual_pnl": getattr(act, "realized_pnl", None),
                    "pnl_difference": difference(
                        getattr(act, "realized_pnl", None), getattr(bt, "realized_pnl", None)
                    ),
                    "backtest_closed_at": getattr(bt, "closed_at", None),
                    "actual_closed_at": getattr(act, "closed_at", None),
                }
            )
        return pd.DataFrame(rows, columns=fields)

    @staticmethod
    def _comparison_metrics(comparison: pd.DataFrame) -> dict[str, object]:
        if comparison.empty:
            return {
                "matched": 0, "backtest_only": 0, "actual_only": 0,
                "matched_backtest_pnl": 0.0, "matched_actual_pnl": 0.0,
                "matched_pnl_difference": 0.0,
            }
        statuses = comparison["match_status"].value_counts()
        matched = comparison[comparison["match_status"] == "matched"]
        return {
            "matched": int(statuses.get("matched", 0)),
            "backtest_only": int(statuses.get("backtest_only", 0)),
            "actual_only": int(statuses.get("actual_only", 0)),
            "matched_backtest_pnl": float(matched["backtest_pnl"].sum()),
            "matched_actual_pnl": float(matched["actual_pnl"].sum()),
            "matched_pnl_difference": float(matched["pnl_difference"].sum()),
        }

    @staticmethod
    def difference_curve(comparison: pd.DataFrame) -> pd.DataFrame:
        columns = [
            "point", "closed_at", "signal_key", "symbol",
            "pnl_difference", "cumulative_pnl_difference",
        ]
        if comparison.empty:
            return pd.DataFrame(columns=columns)
        matched = comparison[comparison["match_status"] == "matched"].copy()
        matched = matched[matched["pnl_difference"].notna()].copy()
        if matched.empty:
            return pd.DataFrame(columns=columns)
        matched["closed_at"] = matched["actual_closed_at"].combine_first(
            matched["backtest_closed_at"]
        )
        matched = matched.sort_values(["closed_at", "signal_key"]).reset_index(drop=True)
        matched["cumulative_pnl_difference"] = matched["pnl_difference"].cumsum()
        matched.insert(0, "point", range(1, len(matched) + 1))
        return matched[columns]

    @staticmethod
    def _plot(
        backtest_curve: pd.DataFrame,
        actual_curve: pd.DataFrame,
        difference_curve: pd.DataFrame,
        path: Path,
        *,
        title: str,
    ) -> None:
        fig, (equity_ax, pnl_ax, difference_ax) = plt.subplots(
            3, 1, figsize=(13, 10), sharex=True,
            gridspec_kw={"height_ratios": [3, 1, 1]},
        )
        series: list[tuple[str, pd.DataFrame, str]] = []
        if not backtest_curve.empty:
            series.append(("Backtest", backtest_curve, "#1f77b4"))
        if not actual_curve.empty:
            series.append(("Paper/Live", actual_curve, "#ff7f0e"))
        for label, curve, color in series:
            equity_ax.step(curve["closed_at"], curve["equity"], where="post", label=label, color=color, linewidth=2)
            pnl_ax.step(
                curve["closed_at"], curve["cumulative_pnl"], where="post",
                label=label, color=color, linewidth=1.6,
            )
        if not difference_curve.empty:
            difference_ax.step(
                difference_curve["closed_at"],
                difference_curve["cumulative_pnl_difference"],
                where="post", color="#9467bd", linewidth=1.8,
                label="Matched actual − backtest",
            )
        equity_ax.set_title(title)
        equity_ax.set_ylabel("Equity (USD)")
        pnl_ax.set_ylabel("Cum. P&L")
        difference_ax.set_ylabel("Cum. difference")
        difference_ax.set_xlabel("Close time (America/New_York)")
        difference_ax.axhline(0.0, color="#555555", linewidth=1, alpha=0.7)
        for axis in (equity_ax, pnl_ax, difference_ax):
            axis.grid(True, alpha=0.25)
            if axis.get_legend_handles_labels()[0]:
                axis.legend(loc="best")
        fig.tight_layout()
        fig.savefig(path, dpi=170)
        plt.close(fig)

    @staticmethod
    def _write_html(summary: dict[str, object], comparison: pd.DataFrame, path: Path) -> None:
        def metric_table(title: str, payload: dict[str, object]) -> str:
            rows = "".join(
                f"<tr><th>{html.escape(str(key))}</th><td>{html.escape(str(value))}</td></tr>"
                for key, value in payload.items()
            )
            return f"<h2>{html.escape(title)}</h2><table>{rows}</table>"

        comparison_preview = comparison.head(100).copy()
        for column in ("backtest_closed_at", "actual_closed_at"):
            if column in comparison_preview:
                comparison_preview[column] = comparison_preview[column].astype(str)
        table = (
            comparison_preview.to_html(index=False, border=0, classes="comparison")
            if not comparison_preview.empty
            else "<p>No comparable closed trades in this period.</p>"
        )
        document = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Trading Journal Report</title>
<style>
body{{font-family:system-ui,sans-serif;max-width:1400px;margin:2rem auto;padding:0 1rem;color:#202124}}
img{{max-width:100%;height:auto}} table{{border-collapse:collapse;width:100%;font-size:.9rem}}
th,td{{padding:.45rem .6rem;border-bottom:1px solid #ddd;text-align:left}} th{{background:#f3f5f7}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:1.5rem}}
.scroll{{overflow-x:auto}} code{{overflow-wrap:anywhere}}
</style></head><body>
<h1>Trading Journal Report</h1>
<p><strong>{html.escape(str(summary['selector']['value']))}</strong> · {html.escape(str(summary['period']['label']))}</p>
<img src="equity.png" alt="Backtest and Paper/Live equity curves">
<div class="grid">{metric_table('Backtest', summary['backtest_metrics'])}{metric_table('Paper/Live', summary['actual_metrics'])}{metric_table('Comparison', summary['comparison'])}</div>
<h2>Signal comparison</h2><div class="scroll">{table}</div>
<p>Differences use actual minus backtest. Equity is a realized-P&amp;L strategy curve, not broker account NAV.</p>
</body></html>"""
        path.write_text(document, encoding="utf-8")

    def create_report(
        self,
        selection: ReportSelection,
        output_dir: str | Path,
    ) -> dict[str, object]:
        output = Path(output_dir)
        output.mkdir(parents=True, exist_ok=True)
        backtest_run = self.select_backtest_run(selection)
        backtest_run_id = str(backtest_run["run_id"]) if backtest_run else None
        starting_capital = self._starting_capital(selection.starting_capital, backtest_run)
        backtest = self._load_trades(
            selection, reference_run_id=backtest_run_id, actual=False
        )
        actual = self._deduplicate_actual(
            self._load_trades(selection, reference_run_id=None, actual=True)
        )
        backtest_curve = self.equity_curve(backtest, starting_capital)
        actual_curve = self.equity_curve(actual, starting_capital)
        comparison = self.compare(backtest, actual)
        difference_curve = self.difference_curve(comparison)
        summary: dict[str, object] = {
            "database": str(self.database),
            "selector": {
                "kind": selection.selector_kind,
                "value": selection.selector_value,
            },
            "period": {
                "label": selection.period.label,
                "timezone": "America/New_York",
                "start": selection.period.start.isoformat(),
                "end_exclusive": selection.period.end_exclusive.isoformat(),
                "trade_inclusion": "closed_at within period",
            },
            "backtest_run_id": backtest_run_id,
            "actual_run_types": list(selection.actual_run_types),
            "backtest_metrics": self.metrics(backtest, backtest_curve, starting_capital),
            "actual_metrics": self.metrics(actual, actual_curve, starting_capital),
            "comparison": self._comparison_metrics(comparison),
            "semantics": {
                "difference": "actual_minus_backtest",
                "equity": "starting_capital_plus_cumulative_realized_pnl",
                "not_account_nav": True,
            },
        }
        backtest.to_csv(output / "backtest_trades.csv", index=False)
        actual.to_csv(output / "actual_trades.csv", index=False)
        backtest_curve.to_csv(output / "backtest_equity.csv", index=False)
        actual_curve.to_csv(output / "actual_equity.csv", index=False)
        comparison.to_csv(output / "backtest_vs_actual.csv", index=False)
        difference_curve.to_csv(output / "matched_pnl_difference.csv", index=False)
        (output / "summary.json").write_text(
            json.dumps(summary, indent=2, ensure_ascii=False, allow_nan=False),
            encoding="utf-8",
        )
        self._plot(
            backtest_curve,
            actual_curve,
            difference_curve,
            output / "equity.png",
            title=f"{selection.selector_value} · {selection.period.label}",
        )
        self._write_html(summary, comparison, output / "report.html")
        return summary


def _safe_component(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._@-]+", "_", value).strip("_") or "report"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create read-only equity and backtest-vs-actual reports from the journal"
    )
    parser.add_argument("--database", type=Path, default=DEFAULT_JOURNAL_PATH)
    selector = parser.add_mutually_exclusive_group()
    selector.add_argument("--variant-id")
    selector.add_argument("--strategy-id")
    parser.add_argument("--period", help="calendar year (YYYY) or month (YYYY-MM), in New York time")
    parser.add_argument("--backtest-run-id")
    parser.add_argument(
        "--actual-run-types",
        default="paper,live",
        help="comma-separated subset of paper,live",
    )
    parser.add_argument("--starting-capital", type=float)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--list-strategies", action="store_true")
    args = parser.parse_args(argv)
    if not args.list_strategies:
        if not (args.variant_id or args.strategy_id):
            parser.error("one of --variant-id or --strategy-id is required")
        if not args.period:
            parser.error("--period is required")
    return args


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    reporter = JournalReporter(args.database)
    if args.list_strategies:
        print(json.dumps(reporter.list_strategies(), indent=2, ensure_ascii=False))
        return
    period = parse_period(args.period)
    selection = ReportSelection(
        variant_id=args.variant_id,
        strategy_id=args.strategy_id,
        period=period,
        actual_run_types=tuple(
            item.strip() for item in args.actual_run_types.split(",") if item.strip()
        ),
        backtest_run_id=args.backtest_run_id,
        starting_capital=args.starting_capital,
    )
    output = args.output_dir or (
        DEFAULT_REPORT_ROOT
        / _safe_component(selection.selector_value)
        / period.label
    )
    summary = reporter.create_report(selection, output)
    print(json.dumps({"output_dir": str(output), **summary}, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
