"""Ежедневные снимки стоимости портфеля для всех уникальных steam_id.

Используется только PriceCache + lisskins in-memory словарь, никаких внешних
запросов. Запускается раз в сутки из core/lifespan.py + есть admin trigger
для ручного запуска.

Если за последние 6 часов снапшот уже был — пропускаем (защита от дублей если
сервис перезапустился несколько раз).
"""
import time

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Portfolio, PortfolioSnapshot, PriceCache
from db.session import AsyncSessionLocal
from services import lisskins
from services.pricing import best_price

DEDUP_WINDOW = 6 * 3600


def _value_for_lot(lot: Portfolio, cached: PriceCache | None, ls_price: float | None) -> float:
    """Возвращает текущую стоимость одного лота: primary_source с fallback на best_price."""
    p_steam       = cached.price_steam       if cached else None
    p_market_csgo = cached.price_market_csgo if cached else None

    src = (lot.buy_source or "steam").lower()
    if src == "steam":
        cur = p_steam
    elif src == "lisskins":
        cur = ls_price
    elif src == "market_csgo":
        cur = p_market_csgo
    else:
        cur = None

    if not cur or cur <= 0:
        cur = best_price(p_steam, ls_price, p_market_csgo)

    if not cur or cur <= 0:
        return 0.0
    return cur * lot.quantity


async def _snapshot_one(session: AsyncSession, steam_id: str, now: int) -> PortfolioSnapshot | None:
    last = (await session.execute(
        select(PortfolioSnapshot)
        .where(PortfolioSnapshot.steam_id == steam_id)
        .order_by(PortfolioSnapshot.recorded_at.desc())
        .limit(1)
    )).scalar_one_or_none()
    if last and (now - last.recorded_at) < DEDUP_WINDOW:
        return None

    lots = (await session.execute(
        select(Portfolio).where(Portfolio.steam_id == steam_id)
    )).scalars().all()
    if not lots:
        return None

    names = list({l.market_hash_name for l in lots})
    cache_rows = (await session.execute(
        select(PriceCache).where(PriceCache.market_hash_name.in_(names))
    )).scalars().all()
    cache_map = {r.market_hash_name: r for r in cache_rows}

    total_value = 0.0
    total_invested = 0.0
    item_count = 0
    for lot in lots:
        ls_price = lisskins.get_lisskins_price(lot.market_hash_name)
        total_value    += _value_for_lot(lot, cache_map.get(lot.market_hash_name), ls_price)
        total_invested += lot.buy_price * lot.quantity
        item_count     += lot.quantity

    snap = PortfolioSnapshot(
        steam_id=steam_id,
        recorded_at=now,
        total_value=round(total_value, 2),
        total_invested=round(total_invested, 2),
        item_count=item_count,
    )
    session.add(snap)
    return snap


async def take_snapshots() -> dict:
    """Один проход для всех уникальных steam_id из таблицы portfolios."""
    async with AsyncSessionLocal() as session:
        steam_ids = (await session.execute(
            select(Portfolio.steam_id).distinct()
        )).scalars().all()

        if not steam_ids:
            return {"users": 0, "written": 0}

        now = int(time.time())
        written = 0
        for sid in steam_ids:
            snap = await _snapshot_one(session, sid, now)
            if snap is not None:
                written += 1
        await session.commit()
        return {"users": len(steam_ids), "written": written}
