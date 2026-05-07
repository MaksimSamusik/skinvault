"""CRUD для прайс-алертов текущего Telegram-юзера."""
import time

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth import get_current_tg_user
from db.models import PriceAlert
from db.session import get_session
from schemas import AlertCreateRequest, AlertUpdateRequest
from services.billing import max_alerts

router = APIRouter(prefix="/api/alerts", tags=["alerts"])


def _serialize(a: PriceAlert) -> dict:
    return {
        "id":               a.id,
        "market_hash_name": a.market_hash_name,
        "condition":        a.condition,
        "threshold":        a.threshold,
        "source":           a.source,
        "is_active":        bool(a.is_active),
        "created_at":       a.created_at,
        "last_fired_at":    a.last_fired_at,
        "fired_count":      a.fired_count,
    }


@router.get("")
async def list_alerts(
    tg_user: dict = Depends(get_current_tg_user),
    session: AsyncSession = Depends(get_session),
):
    rows = (await session.execute(
        select(PriceAlert)
        .where(PriceAlert.tg_user_id == int(tg_user["id"]))
        .order_by(PriceAlert.created_at.desc())
    )).scalars().all()
    return {"alerts": [_serialize(a) for a in rows]}


@router.post("")
async def create_alert(
    req: AlertCreateRequest,
    tg_user: dict = Depends(get_current_tg_user),
    session: AsyncSession = Depends(get_session),
):
    tg_id = int(tg_user["id"])

    active_count = (await session.execute(
        select(func.count(PriceAlert.id))
        .where(PriceAlert.tg_user_id == tg_id, PriceAlert.is_active == 1)
    )).scalar_one()

    limit = max_alerts(tg_id)
    if active_count >= limit:
        raise HTTPException(
            status_code=402,
            detail=f"Лимит активных алертов ({limit}) достигнут — апгрейд до Premium снимет ограничение",
        )

    alert = PriceAlert(
        tg_user_id=tg_id,
        market_hash_name=req.market_hash_name,
        condition=req.condition,
        threshold=req.threshold,
        source=req.source,
        is_active=1,
        created_at=int(time.time()),
        fired_count=0,
    )
    session.add(alert)
    await session.commit()
    await session.refresh(alert)
    return _serialize(alert)


@router.patch("/{alert_id}")
async def update_alert(
    alert_id: int,
    req: AlertUpdateRequest,
    tg_user: dict = Depends(get_current_tg_user),
    session: AsyncSession = Depends(get_session),
):
    alert = (await session.execute(
        select(PriceAlert).where(PriceAlert.id == alert_id)
    )).scalar_one_or_none()
    if not alert or alert.tg_user_id != int(tg_user["id"]):
        raise HTTPException(status_code=404, detail="Алерт не найден")

    if req.condition is not None:
        alert.condition = req.condition
    if req.threshold is not None:
        alert.threshold = req.threshold
    if req.source is not None:
        alert.source = req.source
    if req.is_active is not None:
        alert.is_active = 1 if req.is_active else 0

    await session.commit()
    return _serialize(alert)


@router.delete("/{alert_id}")
async def delete_alert(
    alert_id: int,
    tg_user: dict = Depends(get_current_tg_user),
    session: AsyncSession = Depends(get_session),
):
    alert = (await session.execute(
        select(PriceAlert).where(PriceAlert.id == alert_id)
    )).scalar_one_or_none()
    if not alert or alert.tg_user_id != int(tg_user["id"]):
        raise HTTPException(status_code=404, detail="Алерт не найден")
    await session.delete(alert)
    await session.commit()
    return {"ok": True, "deleted_id": alert_id}
