import asyncio
import json
import logging
from typing import Callable, Awaitable

import websockets
from config import ARKHAM_API_KEY, ARKHAM_WS_URL

logger = logging.getLogger(__name__)


class WalletStreamManager:
    def __init__(self, on_transfer: Callable[[dict], Awaitable[None]]):
        self._on_transfer = on_transfer
        self._ws = None
        self._task: asyncio.Task | None = None
        self._addresses: set[str] = set()
        self._backoff = 1

    async def start(self) -> None:
        self._task = asyncio.create_task(self._ws_loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def add_address(self, address: str) -> None:
        self._addresses.add(address.lower())
        await self._resubscribe()

    async def remove_address(self, address: str) -> None:
        self._addresses.discard(address.lower())
        await self._resubscribe()

    async def set_addresses(self, addresses: set[str]) -> None:
        self._addresses = {a.lower() for a in addresses}
        await self._resubscribe()

    async def _resubscribe(self) -> None:
        if not self._ws:
            return
        try:
            await self._send_subscribe()
        except Exception as e:
            logger.warning(f"resubscribe failed: {e}")

    async def _send_subscribe(self) -> None:
        if not self._ws or not self._addresses:
            return
        addrs = list(self._addresses)
        msg = json.dumps({
            "type": "subscribe",
            "filters": {
                "from": addrs,
                "to": addrs,
            },
        })
        await self._ws.send(msg)
        logger.info(f"WS subscribed to {len(addrs)} addresses")

    async def _ws_loop(self) -> None:
        while True:
            try:
                headers = {"API-Key": ARKHAM_API_KEY}
                async with websockets.connect(
                    ARKHAM_WS_URL,
                    additional_headers=headers,
                    ping_interval=30,
                    ping_timeout=10,
                ) as ws:
                    self._ws = ws
                    self._backoff = 1
                    logger.info("Arkham WebSocket connected")
                    await self._send_subscribe()
                    async for raw in ws:
                        try:
                            data = json.loads(raw)
                            msg_type = data.get("type", "")
                            if msg_type == "transfer":
                                asyncio.create_task(
                                    self._safe_handle(data.get("data", {}))
                                )
                            elif msg_type == "error":
                                logger.error(f"WS error: {data}")
                        except json.JSONDecodeError:
                            pass
            except asyncio.CancelledError:
                logger.info("WS loop cancelled")
                break
            except Exception as e:
                logger.warning(f"WS disconnected: {e}, reconnecting in {self._backoff}s")
                self._ws = None
                await asyncio.sleep(self._backoff)
                self._backoff = min(self._backoff * 2, 30)

    async def _safe_handle(self, data: dict) -> None:
        try:
            await self._on_transfer(data)
        except Exception as e:
            logger.error(f"on_transfer error: {e}", exc_info=True)
