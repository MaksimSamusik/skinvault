from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import PriceHistory
from db.session import get_session
from services import steam
from services.pricing import fetch_all_prices

router = APIRouter(prefix="/api", tags=["prices"])


@router.get("/search")
async def search_items(q: str = Query(..., min_length=2)):
    return {"results": await steam.search_market(q)}


@router.get("/price/{market_hash_name:path}")
async def get_price(market_hash_name: str, session: AsyncSession = Depends(get_session)):
    return await fetch_all_prices(market_hash_name, session)


@router.get("/history/{market_hash_name:path}")
async def get_price_history(
    market_hash_name: str,
    session: AsyncSession = Depends(get_session),
):
    rows = (await session.execute(
        select(PriceHistory)
        .where(PriceHistory.market_hash_name == market_hash_name)
        .order_by(PriceHistory.recorded_at.asc())
        .limit(90)
    )).scalars().all()
    return {
        "market_hash_name": market_hash_name,
        "history": [{"price_usd": r.price_usd, "recorded_at": r.recorded_at} for r in rows],
    }
