from ib_insync import ExecutionFilter, IB


def main() -> None:
    ib = IB()
    try:
        ib.connect(
            "127.0.0.1",
            7496,
            clientId=187,
            timeout=10,
            readonly=True,
            account="DU9372630",
        )
        ib.reqAllOpenOrders()
        ib.sleep(2)
        print("POSITIONS")
        for position in ib.positions("DU9372630"):
            contract = position.contract
            if str(getattr(contract, "tradingClass", "")).upper() != "SPXW":
                continue
            print(
                position.account,
                contract.localSymbol,
                contract.conId,
                position.position,
                position.avgCost,
            )
        print("OPEN_ORDERS")
        for trade in ib.openTrades():
            contract, order, status = trade.contract, trade.order, trade.orderStatus
            if not str(getattr(order, "orderRef", "")).startswith(("SPX15", "SPX75")):
                continue
            print(
                order.orderId,
                order.permId,
                order.orderRef,
                order.action,
                order.totalQuantity,
                order.orderType,
                order.lmtPrice,
                order.tif,
                status.status,
                [(leg.conId, leg.action, leg.ratio) for leg in (contract.comboLegs or [])],
            )
        print("COMPLETED_SPX")
        for trade in ib.reqCompletedOrders(False):
            contract, order, status = trade.contract, trade.order, trade.orderStatus
            if not str(getattr(order, "orderRef", "")).startswith(("SPX15", "SPX75")):
                continue
            print(
                order.orderId,
                order.permId,
                order.orderRef,
                order.action,
                order.totalQuantity,
                order.orderType,
                order.lmtPrice,
                order.tif,
                status.status,
                status.avgFillPrice,
                [(leg.conId, leg.action, leg.ratio) for leg in (contract.comboLegs or [])],
            )
        print("EXECUTIONS_SPX")
        for fill in ib.reqExecutions(ExecutionFilter(acctCode="DU9372630")):
            contract, execution = fill.contract, fill.execution
            if str(getattr(contract, "tradingClass", "")).upper() != "SPXW":
                continue
            print(
                execution.time,
                execution.orderId,
                execution.permId,
                execution.orderRef,
                execution.side,
                execution.shares,
                execution.price,
                contract.localSymbol,
                contract.conId,
            )
    finally:
        if ib.isConnected():
            ib.disconnect()


if __name__ == "__main__":
    main()
