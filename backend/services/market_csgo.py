from typing import Optional

from core.config import CSGOMARKET_API_KEY, MARKET_CSGO_ITEM_URL
from core.http import get_client


async def fetch_price(name: str) -> Optional[float]:
    if not CSGOMARKET_API_KEY:
        return None
    try:
        resp = await get_client().get(
            MARKET_CSGO_ITEM_URL,
            params={"key": CSGOMARKET_API_KEY, "hash_name": name},
            timeout=10,
        )
        data = resp.json()
    except Exception as e:
        print(f"[market.csgo] {name}: {e}")
        return None

    if not data.get("success") or not data.get("data"):
        return None

    prices = []
    for lot in data["data"]:
        raw = lot.get("price")
        if raw is None:
            continue
        try:
            prices.append(float(raw) / 1000)
        except (TypeError, ValueError):
            continue
    return min(prices) if prices else None
