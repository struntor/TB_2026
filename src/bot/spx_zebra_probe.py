from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import pandas as pd
from ib_insync import util

if __package__ in {None, ""}:
    sys.path.append(str(Path(__file__).resolve().parents[2]))

from src.data.ib_connection import IBConnectionConfig, IBConnectionManager
from src.execution.spx_zebra import (
    ZebraSelection,
    build_zebra_entry_order,
    select_spx_zebra,
    select_zebra_for_strikes,
)


US_TZ = "America/New_York"


def _resolve_path(path_str: str) -> Path:
    path = Path(path_str)
    if path.is_absolute():
        return path
    return Path(__file__).resolve().parents[2] / path


def _selection_payload(selection: ZebraSelection) -> dict[str, object]:
    return {
        "symbol": selection.symbol,
        "side": selection.side,
        "right": selection.right,
        "spot": round(float(selection.spot), 4),
        "expiry": selection.expiry,
        "atm_strike": round(float(selection.atm_strike), 4),
        "short_strike": round(float(selection.short_strike), 4),
        "long_strike": round(float(selection.long_strike), 4),
        "short_bid": round(float(selection.short_quote.bid), 4),
        "short_ask": round(float(selection.short_quote.ask), 4),
        "long_bid": round(float(selection.long_quote.bid), 4),
        "long_ask": round(float(selection.long_quote.ask), 4),
        "short_delta": None if selection.short_quote.delta is None else round(float(selection.short_quote.delta), 4),
        "long_delta": None if selection.long_quote.delta is None else round(float(selection.long_quote.delta), 4),
        "entry_mid": round(float(selection.entry_mid), 4),
        "entry_limit": round(float(selection.entry_limit), 4),
        "entry_debit_usd": round(float(selection.entry_debit_usd), 2),
        "intrinsic_value": round(float(selection.intrinsic_value), 4),
        "extrinsic_gap": round(float(selection.extrinsic_gap), 4),
        "net_delta": None if selection.net_delta is None else round(float(selection.net_delta), 4),
        "delta_error": None if selection.delta_error is None else round(float(selection.delta_error), 4),
        "min_tick": round(float(selection.min_tick), 4),
        "combo_exchange": str(getattr(selection.combo_contract, "exchange", "")),
        "combo_leg_count": len(getattr(selection.combo_contract, "comboLegs", []) or []),
    }


def main() -> None:
    default_output = Path("runs") / "zebra_probe" / "zebra_probe.json"
    parser = argparse.ArgumentParser(description="Select a live ZEBRA structure and stage it in TWS.")
    parser.add_argument("--symbol", default="ORCL")
    parser.add_argument("--side", choices=["long", "short"], default="long")
    parser.add_argument("--target-dte", type=int, default=90)
    parser.add_argument("--strike-window-points", type=int, default=250)
    parser.add_argument("--expiry-candidates", type=int, default=3)
    parser.add_argument("--max-debit-usd", type=float, default=10000.0)
    parser.add_argument("--quantity", type=int, default=1)
    parser.add_argument("--expiry", default="")
    parser.add_argument("--short-strike", type=float, default=0.0)
    parser.add_argument("--long-strike", type=float, default=0.0)
    parser.add_argument("--allow-close-quotes", action="store_true")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7496)
    parser.add_argument("--client-id", type=int, default=51)
    parser.add_argument("--account", default="")
    parser.add_argument("--order-ref", default="")
    parser.add_argument("--transmit", action="store_true")
    parser.add_argument("--skip-what-if", action="store_true")
    parser.add_argument("--skip-stage", action="store_true")
    parser.add_argument("--post-submit-hold-seconds", type=float, default=2.0)
    parser.add_argument("--output", default=str(default_output))
    args = parser.parse_args()

    output_path = _resolve_path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    cfg = IBConnectionConfig(host=args.host, port=args.port, client_id=args.client_id, readonly=False)
    manager = IBConnectionManager(cfg)
    result_payload: dict[str, object] = {
        "status": "pending",
        "timestamp": pd.Timestamp.now(tz=US_TZ).isoformat(),
        "symbol": args.symbol.upper(),
        "side": args.side,
        "target_dte": args.target_dte,
        "strike_window_points": args.strike_window_points,
        "quantity": args.quantity,
        "max_debit_usd": args.max_debit_usd,
    }

    try:
        ib = manager.connect()
        print("ib=connected")
        accounts = list(ib.managedAccounts() or [])
        account = args.account.strip() or (accounts[0] if len(accounts) == 1 else "")
        if account:
            print(f"account={account}")
        elif accounts:
            print(f"managed_accounts={','.join(accounts)}")

        if args.expiry and args.short_strike > 0 and args.long_strike > 0:
            selection = select_zebra_for_strikes(
                ib,
                symbol=args.symbol,
                side=args.side,
                expiry=args.expiry,
                short_strike=args.short_strike,
                long_strike=args.long_strike,
                max_debit_usd=args.max_debit_usd,
                allow_close_fallback=args.allow_close_quotes,
            )
        else:
            selection = select_spx_zebra(
                ib,
                symbol=args.symbol,
                side=args.side,
                target_dte=args.target_dte,
                strike_window_points=args.strike_window_points,
                expiry_candidates=args.expiry_candidates,
                max_debit_usd=args.max_debit_usd,
            )
        payload = _selection_payload(selection)
        result_payload.update(payload)

        print(f"spot={payload['spot']}")
        print(f"expiry={payload['expiry']}")
        print(f"atm_strike={payload['atm_strike']}")
        print(f"short_strike={payload['short_strike']}")
        print(f"long_strike={payload['long_strike']}")
        print(f"entry_mid={payload['entry_mid']}")
        print(f"entry_limit={payload['entry_limit']}")
        print(f"entry_debit_usd={payload['entry_debit_usd']}")
        print(f"intrinsic_value={payload['intrinsic_value']}")
        print(f"extrinsic_gap={payload['extrinsic_gap']}")
        print(f"net_delta={payload['net_delta']}")
        print(f"delta_error={payload['delta_error']}")

        order_ref = args.order_ref.strip() or f"{selection.symbol}_ZEBRA_{args.side.upper()}_{selection.expiry}"
        print(f"order_ref={order_ref}")
        print(f"max_debit_usd={args.max_debit_usd}")
        if float(selection.entry_debit_usd) > float(args.max_debit_usd):
            print(
                f"warning=selected_debit_above_budget actual={selection.entry_debit_usd:.2f} "
                f"budget={args.max_debit_usd:.2f}"
            )

        if not args.skip_what_if:
            try:
                what_if_order = build_zebra_entry_order(
                    selection,
                    quantity=args.quantity,
                    account=account or None,
                    order_ref=order_ref,
                )
                what_if_order.transmit = True
                what_if_state = ib.whatIfOrder(selection.combo_contract, what_if_order)
                what_if_payload = {
                    "initMarginBefore": getattr(what_if_state, "initMarginBefore", ""),
                    "initMarginChange": getattr(what_if_state, "initMarginChange", ""),
                    "initMarginAfter": getattr(what_if_state, "initMarginAfter", ""),
                    "maintMarginBefore": getattr(what_if_state, "maintMarginBefore", ""),
                    "maintMarginChange": getattr(what_if_state, "maintMarginChange", ""),
                    "maintMarginAfter": getattr(what_if_state, "maintMarginAfter", ""),
                    "equityWithLoanBefore": getattr(what_if_state, "equityWithLoanBefore", ""),
                    "equityWithLoanChange": getattr(what_if_state, "equityWithLoanChange", ""),
                    "equityWithLoanAfter": getattr(what_if_state, "equityWithLoanAfter", ""),
                    "commission": getattr(what_if_state, "commission", ""),
                    "commissionCurrency": getattr(what_if_state, "commissionCurrency", ""),
                }
                result_payload["what_if"] = what_if_payload
                print("what_if_margin_preview=")
                for key, value in what_if_payload.items():
                    print(f"  {key}={value}")
            except Exception as exc:
                what_if_error = f"{type(exc).__name__}: {exc}"
                result_payload["what_if_error"] = what_if_error
                print(f"what_if_error={what_if_error}")

        if not args.skip_stage:
            staged_order = build_zebra_entry_order(
                selection,
                quantity=args.quantity,
                account=account or None,
                order_ref=order_ref,
            )
            staged_order.transmit = bool(args.transmit)
            trade = ib.placeOrder(selection.combo_contract, staged_order)
            ib.sleep(0.5)
            result_payload["staged_order"] = {
                "orderId": getattr(trade.order, "orderId", None),
                "status": getattr(getattr(trade, "orderStatus", None), "status", ""),
                "transmit": bool(args.transmit),
            }
            print(f"staged_order_id={result_payload['staged_order']['orderId']}")
            print(f"staged_order_status={result_payload['staged_order']['status']}")
            print(f"transmit={'yes' if args.transmit else 'no'}")

            if args.post_submit_hold_seconds > 0:
                print(f"hold_seconds={args.post_submit_hold_seconds}")
                ib.sleep(args.post_submit_hold_seconds)

        result_payload["status"] = "ok"
    except Exception as exc:
        result_payload["status"] = "error"
        result_payload["error"] = f"{type(exc).__name__}: {exc}"
        print(f"error={result_payload['error']}")
        raise
    finally:
        output_path.write_text(json.dumps(result_payload, indent=2, sort_keys=True), encoding="utf-8")
        print(f"output={output_path}")
        manager.disconnect()
        print("ib=disconnected")


if __name__ == "__main__":
    util.patchAsyncio()
    main()
