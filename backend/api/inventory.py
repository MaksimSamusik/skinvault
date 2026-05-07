import asyncio
import time

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import CACHE_TTL
from db.models import PriceCache
from db.session import get_session
from services import inventory_cache, lisskins
from services.pricing import refresh_in_background, best_price, best_source
from services.steam import fetch_inventory, resolve_steam_id

router = APIRouter(prefix="/api", tags=["inventory"])


@router.get("/resolve/{vanity_url}")
async def resolve_vanity(vanity_url: str):
    if vanity_url.isdigit():
        return {"vanity_url": vanity_url, "steam_id": vanity_url}
    resolved = await resolve_steam_id(vanity_url)
    if not resolved.isdigit():
        raise HTTPException(status_code=404, detail=f"Steam-аккаунт '{vanity_url}' не найден.")
    return {"vanity_url": vanity_url, "steam_id": resolved}


@router.get("/inventory/{steam_id}")
async def get_inventory(
    steam_id: str,
    session: AsyncSession = Depends(get_session),
):
    resolved = await resolve_steam_id(steam_id)
    if not resolved.isdigit():
        raise HTTPException(status_code=400, detail=f"Не удалось определить SteamID64 для '{steam_id}'.")

    items = await fetch_inventory(resolved)
    inventory_cache.store_quantities(resolved, items)
    await _enrich_with_prices(items, session)

    total = sum(it.get("quantity", 1) for it in items)
    return {
        "steam_id":    resolved,
        "items":       items,
        "count":       len(items),
        "total_count": total,
        "source":      "public",
    }


async def _enrich_with_prices(items: list[dict], session: AsyncSession) -> None:
    """Подгружает цены ко всем айтемам инвентаря одним SELECT'ом + lisskins из памяти."""
    if not items:
        return

    names = list({it["market_hash_name"] for it in items})

    rows = (await session.execute(
        select(PriceCache).where(PriceCache.market_hash_name.in_(names))
    )).scalars().all()
    cache_map = {r.market_hash_name: r for r in rows}

    now = int(time.time())
    stale: list[str] = []

    for it in items:
        name = it["market_hash_name"]
        cached = cache_map.get(name)

        p_steam       = cached.price_steam       if cached else None
        p_market_csgo = cached.price_market_csgo if cached else None
        p_lisskins    = lisskins.get_lisskins_price(name)

        bp = best_price(p_steam, p_lisskins, p_market_csgo)

        it["price_steam"]       = p_steam
        it["price_lisskins"]    = p_lisskins
        it["price_market_csgo"] = p_market_csgo
        it["best_price"]        = bp
        it["best_source"]       = best_source(p_steam, p_lisskins, p_market_csgo)
        it["current_price"]     = bp or 0.0

        if cached is None or (now - cached.fetched_at) > CACHE_TTL:
            stale.append(name)

    for name in stale:
        asyncio.create_task(refresh_in_background(name))
