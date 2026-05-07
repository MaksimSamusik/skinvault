"""Композиция цен из всех источников + кэш + история."""
import asyncio
import time
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import CACHE_TTL
from db.models import PriceCache, PriceHistory
from db.session import AsyncSessionLocal
from services import lisskins, market_csgo, steam

SOURCE_KEY_MAP = {
    "steam":       "price_steam",
    "lisskins":    "price_lisskins",
    "market_csgo": "price_market_csgo",
}


def best_price(p_steam, p_lisskins, p_market_csgo) -> Optional[float]:
    candidates = [p for p in (p_steam, p_lisskins, p_market_csgo) if p is not None and p > 0]
    return min(candidates) if candidates else None


def best_source(p_steam, p_lisskins, p_market_csgo) -> str:
    pairs = {
        "steam":       p_steam,
        "lisskins":    p_lisskins,
        "market_csgo": p_market_csgo,
    }
    valid = {k: v for k, v in pairs.items() if v is not None and v > 0}
    if not valid:
        return "steam"
    return min(valid, key=valid.get)


def build_price_response(cached: PriceCache) -> dict:
    """Lisskins всегда подтягиваем из in-memory словаря, чтобы возвращать самую свежую
    цену сразу после refresh, не дожидаясь TTL DB-кэша."""
    live_lisskins = lisskins.get_lisskins_price(cached.market_hash_name)
    p_lisskins = live_lisskins if live_lisskins is not None else cached.price_lisskins

    best = best_price(cached.price_steam, p_lisskins, cached.price_market_csgo)
    return {
        "price_usd":          best,
        "price_steam":        cached.price_steam,
        "price_lisskins":     p_lisskins,
        "price_market_csgo":  cached.price_market_csgo,
        "best_price":         best,
        "best_source":        best_source(cached.price_steam, p_lisskins, cached.price_market_csgo),
        "image_url":          cached.image_url,
    }


async def fetch_all_prices(name: str, session: AsyncSession) -> dict:
    """Тянет цены из всех источников параллельно, обновляет кэш и историю."""
    now = int(time.time())

    cached = (await session.execute(
        select(PriceCache).where(PriceCache.market_hash_name == name)
    )).scalar_one_or_none()

    if cached and (now - cached.fetched_at) < CACHE_TTL:
        return build_price_response(cached)

    image_url = cached.image_url if cached else None

    p_steam_task    = asyncio.create_task(steam.fetch_market_price(name))
    p_market_task   = asyncio.create_task(market_csgo.fetch_price(name))
    image_task      = asyncio.create_task(steam.fetch_item_image(name)) if not image_url else None

    p_steam       = await p_steam_task
    p_market_csgo = await p_market_task
    p_lisskins    = lisskins.get_lisskins_price(name)

    if image_task is not None:
        image_url = await image_task

    if cached:
        cached.price_steam       = p_steam
        cached.price_lisskins    = p_lisskins
        cached.price_market_csgo = p_market_csgo
        cached.image_url         = image_url
        cached.fetched_at        = now
    else:
        cached = PriceCache(
            market_hash_name=name,
            price_steam=p_steam,
            price_lisskins=p_lisskins,
            price_market_csgo=p_market_csgo,
            image_url=image_url,
            fetched_at=now,
        )
        session.add(cached)

    best = best_price(p_steam, p_lisskins, p_market_csgo)
    if best:
        session.add(PriceHistory(
            market_hash_name=name,
            price_usd=best,
            source="best",
            recorded_at=now,
        ))

    await session.commit()
    return build_price_response(cached)


async def get_cached_price(name: str, session: AsyncSession) -> dict:
    """Мгновенное чтение из кэша. Если кэша нет / устарел — рефреш в фоне."""
    now = int(time.time())

    cached = (await session.execute(
        select(PriceCache).where(PriceCache.market_hash_name == name)
    )).scalar_one_or_none()

    if cached:
        if (now - cached.fetched_at) > CACHE_TTL:
            asyncio.create_task(refresh_in_background(name))
        return build_price_response(cached)

    cached = PriceCache(
        market_hash_name=name,
        price_steam=None,
        price_lisskins=lisskins.get_lisskins_price(name),
        price_market_csgo=None,
        image_url=None,
        fetched_at=0,
    )
    session.add(cached)
    await session.commit()

    asyncio.create_task(refresh_in_background(name))
    return build_price_response(cached)


async def refresh_in_background(name: str) -> None:
    try:
        async with AsyncSessionLocal() as session:
            await fetch_all_prices(name, session)
    except Exception as e:
        print(f"[refresh-bg] {name}: {e}")
