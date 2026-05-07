"""Подписки и tier-логика.

`is_premium_async()` проверяет таблицу subscriptions: активна если есть запись с
`expires_at > now()`. Используется alerts API и любым require_tier-гейтом.

Чтобы не плодить DB-запросы внутри одного HTTP-handler'а, каждая функция требует
session. В лимит-чекерах (max_alerts) это OK — там всё равно есть сессия рядом.

Sprint 3 расширил тарифы: free / premium / pro.
"""
import time

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Subscription

# Tier лимиты
FREE_MAX_ALERTS            = 1
FREE_MAX_PORTFOLIO_ITEMS   = 50
FREE_HISTORY_DAYS          = 7

PREMIUM_MAX_ALERTS         = 100
PREMIUM_MAX_PORTFOLIO_ITEMS = 500
PREMIUM_HISTORY_DAYS       = 365

PRO_MAX_ALERTS             = 1000
PRO_MAX_PORTFOLIO_ITEMS    = 100000
PRO_HISTORY_DAYS           = 365 * 5

# Прайсинг (фиксированные значения для v1; A/B позже)
# Stars: 1 Star ≈ $0.013 на момент запуска (Telegram конвертит)
# Crypto: USDT через CryptoBot (TRC-20 / TON, без комиссии для платящего)
PRICING = {
    "premium": {"stars": 75,  "usdt": "1.5", "duration_days": 30, "label": "Premium 1 мес"},
    "pro":     {"stars": 250, "usdt": "5.0", "duration_days": 30, "label": "Pro 1 мес"},
}


async def get_active_subscription(tg_user_id: int, session: AsyncSession) -> Subscription | None:
    """Возвращает самую свежую активную подписку (с наибольшим expires_at) или None."""
    now = int(time.time())
    return (await session.execute(
        select(Subscription)
        .where(Subscription.tg_user_id == tg_user_id, Subscription.expires_at > now)
        .order_by(Subscription.expires_at.desc())
        .limit(1)
    )).scalar_one_or_none()


async def current_tier(tg_user_id: int, session: AsyncSession) -> str:
    sub = await get_active_subscription(tg_user_id, session)
    return sub.tier if sub else "free"


async def is_premium_async(tg_user_id: int, session: AsyncSession) -> bool:
    return (await current_tier(tg_user_id, session)) in ("premium", "pro")


async def max_alerts(tg_user_id: int, session: AsyncSession) -> int:
    tier = await current_tier(tg_user_id, session)
    return {"pro": PRO_MAX_ALERTS, "premium": PREMIUM_MAX_ALERTS}.get(tier, FREE_MAX_ALERTS)


async def extend_subscription(
    tg_user_id: int,
    tier: str,
    payment_method: str,
    payment_id: str | None,
    amount: float | None,
    currency: str | None,
    session: AsyncSession,
) -> Subscription:
    """Создаёт новую запись подписки. Если уже есть активная — продлеваем от её expires_at,
    иначе от now(). Идемпотентность: если payment_id уже был обработан — возвращаем существующую."""
    if payment_id:
        existing = (await session.execute(
            select(Subscription).where(Subscription.payment_id == payment_id).limit(1)
        )).scalar_one_or_none()
        if existing:
            return existing

    duration = PRICING[tier]["duration_days"] * 86400
    now = int(time.time())

    active = await get_active_subscription(tg_user_id, session)
    start_from = active.expires_at if (active and active.tier == tier) else now

    sub = Subscription(
        tg_user_id=tg_user_id,
        tier=tier,
        started_at=now,
        expires_at=start_from + duration,
        payment_method=payment_method,
        payment_id=payment_id,
        amount=amount,
        currency=currency,
        created_at=now,
    )
    session.add(sub)
    await session.commit()
    await session.refresh(sub)
    return sub
