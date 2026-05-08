import logging
import time
from typing import Optional
import httpx
from config import ARKHAM_API_KEY, ARKHAM_BASE_URL

logger = logging.getLogger(__name__)

HEADERS = {"API-Key": ARKHAM_API_KEY}

_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(timeout=15.0, headers=HEADERS)
    return _client


async def get_portfolio(address: str) -> Optional[dict]:
    now_ms = int(time.time() * 1000)
    url = f"{ARKHAM_BASE_URL}/portfolio/address/{address}?time={now_ms}"
    try:
        resp = await _get_client().get(url)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error(f"get_portfolio({address}): {e}")
        return None


async def get_intelligence(address: str) -> Optional[dict]:
    url = f"{ARKHAM_BASE_URL}/intelligence/address/{address}/all"
    try:
        resp = await _get_client().get(url)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error(f"get_intelligence({address}): {e}")
        return None


async def get_transfers(
    address: str,
    time_gte: str | None = None,
    limit: int = 40,
) -> Optional[list]:
    params = {
        "base": address,
        "flow": "all",
        "limit": str(limit),
        "sortKey": "blockTimestamp",
        "sortDir": "desc",
    }
    if time_gte:
        params["timeGte"] = time_gte
    url = f"{ARKHAM_BASE_URL}/transfers"
    try:
        resp = await _get_client().get(url, params=params)
        resp.raise_for_status()
        data = resp.json()
        return data.get("transfers", [])
    except Exception as e:
        logger.error(f"get_transfers({address}): {e}")
        return None


def extract_entity(intel: dict | None, address: str) -> str | None:
    if not intel:
        return None
    for chain_data in intel.values():
        if not isinstance(chain_data, dict):
            continue
        label = chain_data.get("arkhamLabel")
        if label and label.get("name"):
            return label["name"]
        entity = chain_data.get("arkhamEntity")
        if entity and entity.get("name"):
            return entity["name"]
    return None


def extract_label(addr_obj: dict | None) -> str | None:
    if not addr_obj:
        return None
    label = addr_obj.get("arkhamLabel")
    if label and isinstance(label, dict) and label.get("name"):
        return label["name"]
    entity = addr_obj.get("arkhamEntity")
    if entity and isinstance(entity, dict) and entity.get("name"):
        return entity["name"]
    return None
