import asyncio
import json
import logging
from typing import Callable, Awaitable

import websockets
from config import ARKHAM_API_KEY, ARKHAM_WS_URL

logger = logging.getLogger(__name__)

_sub_counter = 0


def _next_id() -> str:
    global _sub_counter
    _sub_counter += 1
    return str(_sub_counter)


# Try multiple connection strategies
_CONNECT_STRATEGIES = [
    {"label": "header", "url": ARKHAM_WS_URL, "extra_headers": {"API-Key": ARKHAM_API_KEY}},
    {"label": "query_api_key", "url": f"{ARKHAM_WS_URL}?api_key={ARKHAM_API_KEY}"},
    {"label": "query_apiKey", "url": f"{ARKHAM_WS_URL}?apiKey={ARKHAM_API_KEY}"},
    {"label": "query_key", "url": f"{ARKHAM_WS_URL}?key={ARKHAM_API_KEY}"},
]


class WalletStreamManager:
    def __init__(self, on_transfer: Callable[[dict], Awaitable[None]]):
        self._on_transfer = on_transfer
        self._ws = None
        self._task: asyncio.Task | None = None
        self._addresses: set[str] = set()
        self._backoff = 1
        self._working_strategy: int | None = None

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
            "id": _next_id(),
            "type": "subscribe",
            "payload": {
                "filters": {
                    "from": addrs,
                    "to": addrs,
                }
            },
        })
        await self._ws.send(msg)
        logger.info(f"WS subscribed to {len(addrs)} addresses")

    async def _try_connect(self, strategy: dict):
        url = strategy["url"]
        kwargs = {"ping_interval": 30, "ping_timeout": 10}
        if "extra_headers" in strategy:
            kwargs["extra_headers"] = strategy["extra_headers"]
        return await websockets.connect(url, **kwargs)

    async def _ws_loop(self) -> None:
        while True:
            try:
                # If we know which strategy works, use it directly
                if self._working_strategy is not None:
                    strategies = [_CONNECT_STRATEGIES[self._working_strategy]]
                else:
                    strategies = _CONNECT_STRATEGIES

                ws = None
                for i, strat in enumerate(strategies):
                    try:
                        logger.info(f"WS trying strategy: {strat['label']} → {strat['url'][:60]}...")
                        ws = await self._try_connect(strat)
                        idx = i if self._working_strategy is None else self._working_strategy
                        self._working_strategy = idx
                        logger.info(f"WS connected via strategy: {strat['label']}")
                        break
                    except Exception as e:
                        logger.warning(f"WS strategy {strat['label']} failed: {e}")
                        continue

                if ws is None:
                    raise ConnectionError("All WS connection strategies failed")

                async with ws:
                    self._ws = ws
                    self._backoff = 1
                    await self._send_subscribe()
                    async for raw in ws:
                        try:
                            data = json.loads(raw)
                            logger.debug(f"WS raw msg type={data.get('type')}")
                            msg_type = data.get("type", "")
                            if msg_type == "transfer":
                                payload = data.get("payload", {})
                                transfer = payload.get("transfer", payload)
                                if transfer:
                                    asyncio.create_task(
                                        self._safe_handle(transfer)
                                    )
                            elif msg_type == "error":
                                logger.error(f"WS error: {data}")
                            elif msg_type == "subscribed":
                                logger.info(f"WS subscription confirmed: {data}")
                            else:
                                logger.info(f"WS msg: {str(data)[:200]}")
                        except json.JSONDecodeError:
                            logger.warning(f"WS non-JSON: {str(raw)[:200]}")
            except asyncio.CancelledError:
                logger.info("WS loop cancelled")
                break
            except Exception as e:
                self._ws = None
                logger.warning(f"WS error: {type(e).__name__}: {e}, reconnecting in {self._backoff}s")
                await asyncio.sleep(self._backoff)
                self._backoff = min(self._backoff * 2, 30)

    async def _safe_handle(self, data: dict) -> None:
        try:
            await self._on_transfer(data)
        except Exception as e:
            logger.error(f"on_transfer error: {e}", exc_info=True)
