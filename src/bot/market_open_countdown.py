from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.append(str(Path(__file__).resolve().parents[2]))
    sys.path.append(str(Path(__file__).resolve().parents[3] / "IBKR_TradingBot" / "statistic" / "spx_adx_di_5min_2024_02_03"))

from src.bot.basket_selector import StaticBasketSelector
from src.engine.engine_core import EngineCore
from src.models.money_management import MoneyManagementConfig
from src.risk.money_manager import MoneyManagementBlock


def _fmt(seconds: int) -> str:
    hours, rem = divmod(max(seconds, 0), 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _resolve_path(path_str: str) -> Path:
    path = Path(path_str)
    if path.is_absolute():
        return path
    return Path(__file__).resolve().parents[2] / path


def _parse_symbols(raw: str | None, basket_symbols: list[str]) -> list[str]:
    if raw:
        wanted = [sym.strip().upper() for sym in raw.split(",") if sym.strip()]
        result = [sym for sym in wanted if sym in basket_symbols]
        if result:
            return result
    return list(basket_symbols)


def main() -> None:
    parser = argparse.ArgumentParser(description="Countdown to market open and run a historical engine test.")
    parser.add_argument("--config", default=str(Path("configs") / "baskets_v1.json"))
    parser.add_argument("--day", default="2026-06-21")
    parser.add_argument("--target-time", default="")
    parser.add_argument("--skip-wait", action="store_true")
    parser.add_argument("--sleep-seconds", type=int, default=5)
    parser.add_argument("--symbols", default="")
    parser.add_argument("--money-config", default=str(Path("configs") / "money_management_v1.json"))
    parser.add_argument("--decision-log", default="")
    args = parser.parse_args()

    selector = StaticBasketSelector.from_config(_resolve_path(args.config))
    basket = selector.baskets[selector.default_basket]
    symbols = _parse_symbols(args.symbols, list(basket.symbols))
    money_config = MoneyManagementConfig.from_config(_resolve_path(args.money_config))
    engine = EngineCore()

    if not args.skip_wait:
        if args.target_time:
            target = datetime.fromisoformat(args.target_time)
        else:
            target = datetime.now() + timedelta(seconds=30)
        print(f"market_open_target={target.isoformat(sep=' ', timespec='seconds')}")
        while True:
            remaining = int((target - datetime.now()).total_seconds())
            if remaining <= 0:
                print("market_open=reached")
                break
            print(f"countdown={_fmt(remaining)}")
            time.sleep(min(args.sleep_seconds, remaining))
    else:
        print("market_open=skipped")

    print(f"basket={selector.default_basket}")
    print(f"assets={len(basket.symbols)}")
    print(f"test_symbols={','.join(symbols)}")
    print(f"test_day={args.day}")
    print(f"money_account_size_usd={money_config.account_size_usd}")
    print(f"money_risk_per_trade_pct={money_config.risk_per_trade_pct}")
    print(f"money_risk_budget_usd={money_config.risk_budget_usd:.2f}")
    print(f"money_max_open_trades={money_config.max_open_trades}")
    print(f"money_max_open_trades_long={money_config.max_open_trades_long}")
    print(f"money_max_open_trades_short={money_config.max_open_trades_short}")

    decision_log = args.decision_log or str(
        Path("runs") / "decision_logs" / args.day / f"{selector.default_basket}.log"
    )
    decision_log_path = _resolve_path(decision_log)
    decision_log_path.parent.mkdir(parents=True, exist_ok=True)

    results = engine.evaluate_basket(symbols, args.day, decision_log_path=decision_log_path)
    total_trades = sum(len(batch.trades) for batch in results.values())
    assets_with_trades = [symbol for symbol, batch in results.items() if not batch.trades.empty]
    print(f"total_trades={total_trades}")
    print(f"assets_with_trades={len(assets_with_trades)}")
    if assets_with_trades:
        print(f"trade_assets={','.join(assets_with_trades)}")

    money_manager = MoneyManagementBlock(money_config)
    plan = money_manager.plan_from_result_map(results, symbol_order=symbols)
    accepted = plan[plan["allowed"]].copy()
    blocked = plan[~plan["allowed"]].copy()
    print(f"money_plan_candidates={len(plan)}")
    print(f"money_plan_accepted={len(accepted)}")
    print(f"money_plan_blocked={len(blocked)}")
    if not accepted.empty:
        print("money_plan_accepted_rows")
        print(
            accepted[
                [
                    "entry_date",
                    "symbol",
                    "track",
                    "source_tracks",
                    "rule",
                    "side",
                    "quantity",
                    "entry",
                    "stop",
                    "target",
                    "risk_per_share",
                    "risk_budget_usd",
                    "allocated_risk_usd",
                    "notional_usd",
                ]
            ].to_string(index=False)
        )
    if not blocked.empty:
        print("money_plan_blocked_rows")
        print(
            blocked[
                [
                    "entry_date",
                    "symbol",
                    "track",
                    "source_tracks",
                    "rule",
                    "side",
                    "suggested_quantity",
                    "blocked_reason",
                ]
            ].to_string(index=False)
        )
    print(f"decision_log={decision_log_path}")


if __name__ == "__main__":
    main()
