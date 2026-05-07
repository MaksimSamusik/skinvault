"""Эндпоинты текущего Telegram-юзера: /api/me, /api/me/link-steam.

Все требуют верифицированный X-Telegram-Init-Data заголовок (см. core/auth.py).
"""
import time

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth import get_current_tg_user
from db.models import User
from db.session import get_session
from schemas import LinkSteamRequest, UpdateMeRequest
from services.steam import resolve_steam_id

router = APIRouter(prefix="/api/me", tags=["me"])


async def _get_or_create(session: AsyncSession, tg_user: dict) -> User:
    """Находит User по tg_user_id или создаёт новую запись с дефолтами из tg_user."""
    tg_id = int(tg_user["id"])
    user = (await session.execute(
        select(User).where(User.tg_user_id == tg_id)
    )).scalar_one_or_none()

    now = int(time.time())
    if user is None:
        locale = (tg_user.get("language_code") or "ru")[:8]
        user = User(
            tg_user_id=tg_id,
            locale=locale,
            currency="USD",
            created_at=now,
            last_seen_at=now,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
    else:
        user.last_seen_at = now
        await session.commit()
    return user


def _serialize(user: User) -> dict:
    return {
        "tg_user_id":   user.tg_user_id,
        "steam_id":     user.steam_id,
        "locale":       user.locale,
        "currency":     user.currency,
        "created_at":   user.created_at,
        "last_seen_at": user.last_seen_at,
    }


@router.get("")
async def get_me(
    tg_user: dict = Depends(get_current_tg_user),
    session: AsyncSession = Depends(get_session),
):
    """Возвращает текущего юзера, создаёт запись при первом обращении."""
    user = await _get_or_create(session, tg_user)
    return _serialize(user)


@router.post("/link-steam")
async def link_steam(
    req: LinkSteamRequest,
    tg_user: dict = Depends(get_current_tg_user),
    session: AsyncSession = Depends(get_session),
):
    """Привязывает Steam-аккаунт к текущему Telegram-юзеру.
    Принимает Steam64 ID или vanity URL — резолвится через services.steam."""
    resolved = await resolve_steam_id(req.steam_id.strip())
    if not resolved.isdigit():
        raise HTTPException(status_code=400, detail=f"Не удалось определить SteamID64 для '{req.steam_id}'")

    user = await _get_or_create(session, tg_user)
    user.steam_id = resolved
    user.last_seen_at = int(time.time())
    await session.commit()
    return _serialize(user)


@router.patch("")
async def update_me(
    req: UpdateMeRequest,
    tg_user: dict = Depends(get_current_tg_user),
    session: AsyncSession = Depends(get_session),
):
    """Обновляет настройки юзера (locale, currency)."""
    user = await _get_or_create(session, tg_user)
    if req.locale is not None:
        user.locale = req.locale
    if req.currency is not None:
        user.currency = req.currency.upper()
    user.last_seen_at = int(time.time())
    await session.commit()
    return _serialize(user)
