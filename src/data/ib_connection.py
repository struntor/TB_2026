from __future__ import annotations

from dataclasses import dataclass

from ib_insync import IB


@dataclass(frozen=True)
class IBConnectionConfig:
    host: str = "127.0.0.1"
    port: int = 7496
    client_id: int = 21
    readonly: bool = True
    timeout: float = 10.0


class IBConnectionManager:
    def __init__(self, config: IBConnectionConfig | None = None) -> None:
        self.config = config or IBConnectionConfig()
        self.ib = IB()

    def connect(self) -> IB:
        if self.ib.isConnected():
            return self.ib
        self.ib.connect(
            self.config.host,
            self.config.port,
            clientId=self.config.client_id,
            readonly=self.config.readonly,
            timeout=self.config.timeout,
        )
        return self.ib

    def disconnect(self) -> None:
        if self.ib.isConnected():
            self.ib.disconnect()

    def __enter__(self) -> IB:
        return self.connect()

    def __exit__(self, exc_type, exc, tb) -> None:
        self.disconnect()

