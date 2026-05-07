"""Работа со Steam: resolve vanity URL, цены Steam Market, инвентарь."""
import re
from typing import Optional

from fastapi import HTTPException

from core.config import (
    INVENTORY_MAX_PAGES,
    INVENTORY_PAGE_SIZE,
    STEAM_API_KEY,
    STEAM_CDN,
    STEAM_INVENTORY_URL,
    STEAM_PRICE_URL,
    STEAM_RESOLVE_URL,
    STEAM_SEARCH_URL,
)
from core.http import get_client, steam_market_limiter

_PRICE_HEADERS = {
    "Referer": "https://steamcommunity.com/market/",
    "Accept": "application/json, text/plain, */*",
}
_PRICE_COOKIES = {"Steam_Language": "english"}
_VANITY_RE = re.compile(r"<steamID64>(\d+)</steamID64>")


async def resolve_steam_id(steam_id_or_name: str) -> str:
    """Преобразует vanity URL в Steam64 ID. Если уже число — возвращает как есть."""
    if steam_id_or_name.isdigit():
        return steam_id_or_name

    client = get_client()

    if STEAM_API_KEY:
        try:
            resp = await client.get(
                STEAM_RESOLVE_URL,
                params={"key": STEAM_API_KEY, "vanityurl": steam_id_or_name},
                timeout=10,
            )
            response = resp.json().get("response", {})
            if response.get("success") == 1:
                return response["steamid"]
        except Exception:
            pass

    try:
        resp = await client.get(
            f"https://steamcommunity.com/id/{steam_id_or_name}",
            params={"xml": 1},
            timeout=10,
        )
        match = _VANITY_RE.search(resp.text)
        if match:
            return match.group(1)
    except Exception:
        pass

    return steam_id_or_name


async def fetch_market_price(name: str) -> Optional[float]:
    """Цена с Steam Market priceoverview API. Соблюдаем rate limit."""
    await steam_market_limiter.acquire()
    try:
        resp = await get_client().get(
            STEAM_PRICE_URL,
            params={"appid": 730, "currency": 1, "market_hash_name": name},
            headers=_PRICE_HEADERS,
            cookies=_PRICE_COOKIES,
            timeout=10,
        )
        if resp.status_code == 429:
            print(f"[steam] {name}: rate limit (429)")
            return None
        data = resp.json()
    except Exception as e:
        print(f"[steam] {name}: {e}")
        return None

    if not data.get("success"):
        return None
    raw = data.get("lowest_price") or data.get("median_price")
    if not raw:
        return None
    try:
        return float(raw.replace("$", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return None


async def fetch_item_image(name: str) -> Optional[str]:
    try:
        resp = await get_client().get(
            STEAM_SEARCH_URL,
            params={
                "appid": 730, "query": name, "count": 1,
                "search_descriptions": 0, "norender": 1,
            },
            timeout=10,
        )
        results = resp.json().get("results", [])
    except Exception:
        return None

    if not results:
        return None
    icon = results[0].get("asset_description", {}).get("icon_url", "")
    return STEAM_CDN + icon if icon else None


async def search_market(query: str, count: int = 10) -> list[dict]:
    try:
        resp = await get_client().get(
            STEAM_SEARCH_URL,
            params={
                "appid": 730, "query": query, "count": count,
                "search_descriptions": 0, "norender": 1,
            },
            timeout=10,
        )
        data = resp.json()
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))

    out = []
    for item in data.get("results", []):
        desc = item.get("asset_description", {})
        icon = desc.get("icon_url", "")
        out.append({
            "market_hash_name": item.get("hash_name", ""),
            "name": item.get("name", ""),
            "image_url": STEAM_CDN + icon if icon else None,
            "sell_listings": item.get("sell_listings", 0),
        })
    return out


def _extract_desc(d: dict) -> dict:
    """Один проход по тегам description'а — без вложенных циклов в hot-path."""
    rarity = wear = item_type = ""
    for tag in d.get("tags", ()):
        cat = tag.get("category")
        if cat == "Rarity" and not rarity:
            rarity = tag.get("internal_name", "").lower().replace("rarity_", "")
        elif cat == "Exterior" and not wear:
            wear = tag.get("localized_tag_name") or tag.get("name", "")
        elif cat == "Type" and not item_type:
            item_type = tag.get("internal_name", "")
    icon = d.get("icon_url", "")
    return {
        "name": d.get("market_hash_name") or d.get("name", "Unknown"),
        "image_url": (STEAM_CDN + icon) if icon else None,
        "rarity": rarity,
        "wear": wear,
        "type": item_type,
        "marketable": d.get("marketable", 0) == 1,
        "tradable": d.get("tradable", 0) == 1,
    }


async def fetch_inventory(steam_id: str) -> list[dict]:
    """Грузит публичный инвентарь Steam постранично, группирует marketable-ассеты
    по market_hash_name. Каждая группа содержит quantity и список asset_ids."""
    client = get_client()
    seen_assetids: set[str] = set()
    desc_cache: dict = {}
    grouped: dict[str, dict] = {}

    start_assetid: Optional[str] = None
    start_classid: Optional[str] = None

    for page in range(INVENTORY_MAX_PAGES):
        params = {"l": "english", "count": INVENTORY_PAGE_SIZE}
        if start_assetid:
            params["startAssetid"] = start_assetid
        if start_classid:
            params["startClassid"] = start_classid

        try:
            resp = await client.get(
                STEAM_INVENTORY_URL.format(steam_id=steam_id),
                params=params,
                timeout=15,
            )
            data = resp.json()
        except Exception as e:
            if page == 0:
                raise HTTPException(status_code=502, detail=f"Ошибка Steam API: {e}")
            break

        if data is None:
            if page == 0:
                raise HTTPException(status_code=502, detail="Steam вернул пустой ответ")
            break

        if page == 0 and not data.get("success", False) and data.get("Error"):
            raise HTTPException(
                status_code=404,
                detail="Инвентарь закрыт или SteamID не найден",
            )

        for d in data.get("descriptions", []):
            key = (d["classid"], d["instanceid"])
            if key not in desc_cache:
                desc_cache[key] = _extract_desc(d)

        has_new = False
        for asset in data.get("assets", []):
            assetid = asset.get("assetid")
            if not assetid or assetid in seen_assetids:
                continue
            seen_assetids.add(assetid)

            desc_key = (asset["classid"], asset["instanceid"])
            desc = desc_cache.get(desc_key)
            if not desc or not desc["marketable"]:
                continue

            name = desc["name"]
            existing = grouped.get(name)
            if existing is None:
                grouped[name] = {
                    "market_hash_name": name,
                    "image_url":        desc["image_url"],
                    "rarity":           desc["rarity"],
                    "wear":             desc["wear"],
                    "type":             desc["type"],
                    "tradable":         desc["tradable"],
                    "quantity":         1,
                    "asset_ids":        [assetid],
                }
            else:
                existing["quantity"] += 1
                existing["asset_ids"].append(assetid)
            has_new = True

        if not data.get("more_items") or not has_new or not data.get("assets"):
            break

        last_asset = data["assets"][-1]
        start_assetid = last_asset["assetid"]
        start_classid = last_asset.get("classid")

    total_qty = sum(g["quantity"] for g in grouped.values())
    print(f"[inventory] {steam_id}: {len(grouped)} групп / {total_qty} ассетов из {len(seen_assetids)} в инвентаре")
    return list(grouped.values())
