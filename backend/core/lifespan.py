import asyncio
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import select

from core.config import (
    CACHE_TTL,
    LISSKINS_REFRESH_INTERVAL,
    PRICE_REFRESH_INTERVAL,
    WARMUP_CONCURRENCY,
)
from core.http import close_client
from db.models import Portfolio, PriceCache
from db.session import AsyncSessionLocal, init_db
from services import lisskins
from services.pricing import fetch_all_prices


async def _warmup_one(name: str, sem: asyncio.Semaphore) -> bool:
    async with sem:
        try:
            async with AsyncSessionLocal() as session:
                cached = (await session.execute(
                    select(PriceCache).where(PriceCache.market_hash_name == name)
                )).scalar_one_or_none()
                now = int(time.time())
                if cached and (now - cached.fetched_at) < CACHE_TTL:
                    return False
                await fetch_all_prices(name, session)
                return True
        except Exception as e:
            print(f"[warmup] {name}: {e}")
            return False


async def warmup_price_cache() -> None:
    """Прогрев кэша всех скинов из портфелей с ограниченной конкурентностью."""
    async with AsyncSessionLocal() as session:
        names = [r[0] for r in (await session.execute(
            select(Portfolio.market_hash_name).distinct()
        )).all()]

    if not names:
        print("[warmup] портфели пусты, пропускаем")
        return

    print(f"[warmup] прогреваем {len(names)} скинов (concurrency={WARMUP_CONCURRENCY})...")
    sem = asyncio.Semaphore(WARMUP_CONCURRENCY)
    results = await asyncio.gather(*[_warmup_one(n, sem) for n in names])
    print(f"[warmup] обновлено {sum(results)}/{len(names)}")


async def _periodic(label: str, interval: int, fn):
    while True:
        try:
            await asyncio.sleep(interval)
            await fn()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[{label}] ошибка: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()

    print("[lisskins] загрузка из файла...")
    lisskins.load_from_file()
    age_h = lisskins.cache_age_seconds() / 3600
    print(f"[lisskins] загружено {lisskins.get_lisskins_cache_size()} цен (возраст файла: {age_h:.1f}h)")

    if age_h * 3600 > LISSKINS_REFRESH_INTERVAL:
        print(f"[lisskins] кэш старше {LISSKINS_REFRESH_INTERVAL // 60} мин, запускаем фоновый refresh")
        asyncio.create_task(lisskins.refresh_prices())

    asyncio.create_task(warmup_price_cache())

    bg_tasks = [
        asyncio.create_task(_periodic("price-refresh", PRICE_REFRESH_INTERVAL, warmup_price_cache)),
        asyncio.create_task(_periodic("lisskins-refresh", LISSKINS_REFRESH_INTERVAL, lisskins.refresh_prices)),
    ]

    try:
        yield
    finally:
        for t in bg_tasks:
            t.cancel()
        await asyncio.gather(*bg_tasks, return_exceptions=True)
        await close_client()
