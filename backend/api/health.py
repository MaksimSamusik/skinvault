import os

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from db.session import IS_POSTGRES, get_session
from services import lisskins

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health")
async def health(session: AsyncSession = Depends(get_session)):
    db_type = "postgres" if IS_POSTGRES else "sqlite"
    try:
        await session.execute(text("SELECT 1"))
        db_ok: object = True
    except Exception as e:
        db_ok = str(e)

    db_url_env = os.environ.get("DATABASE_URL", "")
    age = lisskins.cache_age_seconds()
    return {
        "status": "ok",
        "db_type": db_type,
        "db_ok": db_ok,
        "has_database_url_env": bool(db_url_env),
        "db_url_prefix": (db_url_env[:35] + "...") if db_url_env else None,
        "lisskins_cached": lisskins.get_lisskins_cache_size(),
        "lisskins_age_hours": round(age / 3600, 2) if age != float("inf") else None,
    }
