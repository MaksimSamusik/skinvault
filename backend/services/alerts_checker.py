"""Периодическая проверка прайс-алертов против PriceCache.

Источники цен берутся **из существующего кэша**, без обращений в Steam Market API
(который ограничен 20 req/min). PriceCache обновляется отдельной periodic-задачей
warmup_price_cache + lisskins refresh.

Cooldown между срабатываниями одного алерта = 6h, чтобы не спамить когда цена
ходит туда-сюда около threshold.
"""
import time
from collections import defaultdict
from typing import Optional

from sqlalchemy import select

from db.models import PriceAlert, PriceCache
from db.session import AsyncSessionLocal
from services import lisskins, notifications
from services.pricing import best_price

ALERT_COOLDOWN = 6 * 3600


def _current_price_for(alert: PriceAlert, cached: Optional[PriceCache], ls_price: Optional[float]) -> Optional[float]:
    """Возвращает цену по которой считаем алерт, в зависимости от alert.source."""
    p_steam       = cached.price_steam       if cached else None
    p_market_csgo = cached.price_market_csgo if cached else None
    if alert.source == "steam":
        return p_steam
    if alert.source == "lisskins":
        return ls_price
    if alert.source == "market_csgo":
        return p_market_csgo
    return best_price(p_steam, ls_price, p_market_csgo)


def _format_message(alert: PriceAlert, cur_price: float) -> str:
    arrow  = "📉" if alert.condition == "below" else "📈"
    cond_t = "упала ниже" if alert.condition == "below" else "поднялась выше"
    src    = "лучшей цены" if alert.source == "best" else alert.source
    return (
        f"{arrow} *Прайс-алерт*\n"
        f"`{alert.market_hash_name}`\n\n"
        f"Цена {cond_t} *${alert.threshold:.2f}*\n"
        f"Сейчас: *${cur_price:.2f}* ({src})"
    )


async def check_alerts() -> dict:
    """Один проход проверки. Возвращает статистику для логов."""
    async with AsyncSessionLocal() as session:
        active = (await session.execute(
            select(PriceAlert).where(PriceAlert.is_active == 1)
        )).scalars().all()

        if not active:
            return {"checked": 0, "fired": 0}

        by_name: dict[str, list[PriceAlert]] = defaultdict(list)
        for a in active:
            by_name[a.market_hash_name].append(a)

        names = list(by_name.keys())
        cache_rows = (await session.execute(
            select(PriceCache).where(PriceCache.market_hash_name.in_(names))
        )).scalars().all()
        cache_map = {r.market_hash_name: r for r in cache_rows}

        now = int(time.time())
        fired = 0

        for name, alerts in by_name.items():
            cached = cache_map.get(name)
            ls_price = lisskins.get_lisskins_price(name)

            for alert in alerts:
                if alert.last_fired_at and (now - alert.last_fired_at) < ALERT_COOLDOWN:
                    continue
                cur = _current_price_for(alert, cached, ls_price)
                if cur is None or cur <= 0:
                    continue
                triggered = (
                    (alert.condition == "below" and cur <= alert.threshold)
                    or (alert.condition == "above" and cur >= alert.threshold)
                )
                if not triggered:
                    continue

                ok = await notifications.send_message(alert.tg_user_id, _format_message(alert, cur))
                if ok:
                    alert.last_fired_at = now
                    alert.fired_count = (alert.fired_count or 0) + 1
                    fired += 1

        await session.commit()
        return {"checked": len(active), "fired": fired}
