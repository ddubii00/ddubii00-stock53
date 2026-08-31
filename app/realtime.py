"""Realtime price-source boundary for Oracle deployments.

Vercel never imports or runs this module. The default Oracle worker remains
polling; this read-only KIS adapter is a production integration skeleton for a
separate event-driven worker. It contains no account or order APIs.
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Protocol

import requests


@dataclass(frozen=True)
class PriceTick:
    symbol: str
    price: float
    accumulated_volume: float
    trading_time: str
    source: str = "kis-websocket"


class RealtimePriceSource(Protocol):
    async def stream(self, symbols: Sequence[str]) -> AsyncIterator[PriceTick]: ...


class KisRealtimeWebSocketAdapter:
    """Read KRX executions (H0STCNT0) from the official KIS WebSocket feed."""

    tr_id = "H0STCNT0"
    field_count = 46

    def __init__(self, timeout: float = 8.0):
        self.app_key = os.getenv("KIS_APP_KEY", "").strip()
        self.app_secret = os.getenv("KIS_APP_SECRET", "").strip()
        self.base_url = os.getenv("KIS_BASE_URL", "https://openapi.koreainvestment.com:9443").rstrip("/")
        self.websocket_url = os.getenv(
            "KIS_WEBSOCKET_URL", "ws://ops.koreainvestment.com:21000/tryitout"
        )
        self.timeout = timeout
        if not self.app_key or not self.app_secret:
            raise RuntimeError("KIS_APP_KEY/KIS_APP_SECRET are required")

    def _approval_key(self) -> str:
        response = requests.post(
            f"{self.base_url}/oauth2/Approval",
            headers={"content-type": "application/json"},
            json={
                "grant_type": "client_credentials",
                "appkey": self.app_key,
                "secretkey": self.app_secret,
            },
            timeout=self.timeout,
        )
        if not response.ok:
            raise RuntimeError(f"KIS WebSocket approval failed with HTTP {response.status_code}")
        key = str(response.json().get("approval_key") or "")
        if not key:
            raise RuntimeError("KIS WebSocket approval response had no approval_key")
        return key

    def _subscription(self, approval_key: str, symbol: str) -> str:
        return json.dumps(
            {
                "header": {
                    "approval_key": approval_key,
                    "custtype": "P",
                    "tr_type": "1",
                    "content-type": "utf-8",
                },
                "body": {"input": {"tr_id": self.tr_id, "tr_key": symbol}},
            },
            ensure_ascii=False,
        )

    def _parse(self, message: str) -> list[PriceTick]:
        parts = message.split("|", 3)
        if len(parts) != 4 or parts[1] != self.tr_id:
            return []
        try:
            record_count = int(parts[2])
        except ValueError:
            return []
        values = parts[3].split("^")
        ticks: list[PriceTick] = []
        for index in range(record_count):
            record = values[index * self.field_count : (index + 1) * self.field_count]
            if len(record) < 14:
                continue
            try:
                ticks.append(
                    PriceTick(
                        symbol=record[0],
                        trading_time=record[1],
                        price=float(record[2]),
                        accumulated_volume=float(record[13]),
                    )
                )
            except ValueError:
                continue
        return ticks

    async def stream(self, symbols: Sequence[str]) -> AsyncIterator[PriceTick]:
        """Subscribe and yield price ticks. Reconnect/backoff belongs in the runner."""

        try:
            import websockets
        except ImportError as exc:
            raise RuntimeError("Install requirements-oracle.txt for KIS WebSocket support") from exc

        normalized = list(dict.fromkeys(symbol.strip() for symbol in symbols if symbol.strip()))
        if not normalized:
            raise ValueError("at least one symbol is required")
        approval_key = self._approval_key()
        async with websockets.connect(self.websocket_url, ping_interval=None) as websocket:
            for symbol in normalized:
                await websocket.send(self._subscription(approval_key, symbol))
            async for raw in websocket:
                message = raw.decode("utf-8") if isinstance(raw, bytes) else raw
                if message.startswith(("0|", "1|")):
                    for tick in self._parse(message):
                        yield tick
                    continue
                try:
                    payload = json.loads(message)
                except json.JSONDecodeError:
                    continue
                if payload.get("header", {}).get("tr_id") == "PINGPONG":
                    await websocket.send(message)
