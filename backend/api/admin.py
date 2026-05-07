import asyncio
import secrets

from fastapi import APIRouter, Header, HTTPException, Query

from core.config import ADMIN_TOKEN
from services import lisskins

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _check_token(x_admin_token: str | None) -> None:
    if not ADMIN_TOKEN:
        raise HTTPException(
            status_code=503,
            detail="Admin endpoints отключены: задайте env var ADMIN_TOKEN чтобы включить",
        )
    if not x_admin_token or not secrets.compare_digest(x_admin_token, ADMIN_TOKEN):
        raise HTTPException(status_code=401, detail="Неверный X-Admin-Token")


@router.post("/refresh-lisskins")
async def trigger_lisskins_refresh(
    wait: bool = Query(False, description="Если true — дождаться завершения и вернуть результат"),
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
):
    """Ручной триггер обновления цен lisskins."""
    _check_token(x_admin_token)
    if wait:
        result = await lisskins.refresh_prices()
        return {"ok": True, "result": result}
    asyncio.create_task(lisskins.refresh_prices())
    return {
        "ok": True,
        "message": "refresh запущен в фоне; следи за логами или вызови с ?wait=true",
    }


@router.get("/lisskins-status")
async def lisskins_status(
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
):
    _check_token(x_admin_token)
    age = lisskins.cache_age_seconds()
    return {
        "cache_size": lisskins.get_lisskins_cache_size(),
        "age_seconds": age,
        "age_hours": round(age / 3600, 2) if age != float("inf") else None,
    }
