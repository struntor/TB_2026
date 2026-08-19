from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3


DEFAULT_DATABASE = Path(r"D:\Trading\journal\trading_journal.sqlite")


def load_summary(database: Path) -> list[dict[str, object]]:
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            WITH trade_totals AS (
                SELECT run_id,
                       COUNT(*) AS trades,
                       SUM(CASE WHEN status='open' THEN 1 ELSE 0 END) AS open_trades,
                       SUM(CASE WHEN status='closed' THEN 1 ELSE 0 END) AS closed_trades,
                       SUM(CASE WHEN realized_pnl IS NOT NULL THEN 1 ELSE 0 END) AS pnl_trades
                FROM trades
                GROUP BY run_id
            ),
            order_totals AS (
                SELECT run_id, COUNT(*) AS orders
                FROM orders
                GROUP BY run_id
            ),
            execution_totals AS (
                SELECT orders.run_id, COUNT(*) AS executions
                FROM executions
                JOIN orders ON orders.order_id=executions.order_id
                GROUP BY orders.run_id
            ),
            decision_totals AS (
                SELECT run_id, COUNT(*) AS decisions,
                       SUM(CASE WHEN trade_action='trade' THEN 1 ELSE 0 END) AS trade_decisions
                FROM signal_decisions
                GROUP BY run_id
            )
            SELECT strategy_runs.strategy_id,
                   strategy_runs.strategy_version,
                   strategy_runs.run_type,
                   strategy_runs.environment,
                   COUNT(*) AS runs,
                   MIN(strategy_runs.started_at_utc) AS first_run_utc,
                   MAX(strategy_runs.started_at_utc) AS latest_run_utc,
                   SUM(COALESCE(decision_totals.decisions, 0)) AS decisions,
                   SUM(COALESCE(decision_totals.trade_decisions, 0)) AS trade_decisions,
                   SUM(COALESCE(trade_totals.trades, 0)) AS trades,
                   SUM(COALESCE(trade_totals.open_trades, 0)) AS open_trades,
                   SUM(COALESCE(trade_totals.closed_trades, 0)) AS closed_trades,
                   SUM(COALESCE(trade_totals.pnl_trades, 0)) AS pnl_trades,
                   SUM(COALESCE(order_totals.orders, 0)) AS orders,
                   SUM(COALESCE(execution_totals.executions, 0)) AS executions
            FROM strategy_runs
            LEFT JOIN trade_totals ON trade_totals.run_id=strategy_runs.run_id
            LEFT JOIN order_totals ON order_totals.run_id=strategy_runs.run_id
            LEFT JOIN execution_totals ON execution_totals.run_id=strategy_runs.run_id
            LEFT JOIN decision_totals ON decision_totals.run_id=strategy_runs.run_id
            GROUP BY strategy_runs.strategy_id,
                     strategy_runs.strategy_version,
                     strategy_runs.run_type,
                     strategy_runs.environment
            ORDER BY strategy_runs.strategy_id,
                     strategy_runs.strategy_version,
                     strategy_runs.run_type,
                     strategy_runs.environment
            """
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Summarize per-strategy evidence in the trading journal"
    )
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    args = parser.parse_args()
    print(json.dumps(load_summary(args.database), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
