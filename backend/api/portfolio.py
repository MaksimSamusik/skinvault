import asyncio
import time

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Portfolio
from db.session import get_session
from schemas import AddItemRequest, UpdateItemRequest
from services import inventory_cache
from services.pricing import SOURCE_KEY_MAP, get_cached_price
from services.steam import resolve_steam_id

router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])


def _normalize_source(s: str | None) -> str:
    s = (s or "steam").strip().lower()
    return s if s in SOURCE_KEY_MAP else "steam"


async def _tracked_qty(session: AsyncSession, steam_id: str, name: str, exclude_lot_id: int | None = None) -> int:
    stmt = select(func.coalesce(func.sum(Portfolio.quantity), 0)).where(
        Portfolio.steam_id == steam_id,
        Portfolio.market_hash_name == name,
    )
    if exclude_lot_id is not None:
        stmt = stmt.where(Portfolio.id != exclude_lot_id)
    return int((await session.execute(stmt)).scalar_one() or 0)


async def _ensure_capacity(
    session: AsyncSession,
    steam_id: str,
    name: str,
    requested_qty: int,
    exclude_lot_id: int | None = None,
) -> None:
    """Бросает 400 если tracked qty + requested > inventory qty."""
    inv_qty = await inventory_cache.get_quantity_for(steam_id, name)
    if inv_qty <= 0:
        raise HTTPException(
            status_code=400,
            detail=f"Предмет '{name}' не найден в инвентаре Steam (возможно инвентарь не загружен)",
        )
    already = await _tracked_qty(session, steam_id, name, exclude_lot_id)
    if already + requested_qty > inv_qty:
        available = max(0, inv_qty - already)
        raise HTTPException(
            status_code=400,
            detail=(
                f"Превышено количество: в инвентаре {inv_qty} шт., уже отслеживается {already}, "
                f"доступно {available}, запрошено {requested_qty}"
            ),
        )


@router.get("/{steam_id}")
async def get_portfolio(steam_id: str, session: AsyncSession = Depends(get_session)):
    resolved = await resolve_steam_id(steam_id)
    if not resolved.isdigit():
        raise HTTPException(status_code=400, detail="Не удалось определить SteamID64.")

    rows = (await session.execute(
        select(Portfolio)
        .where(Portfolio.steam_id == resolved)
        .order_by(Portfolio.added_at.asc())
    )).scalars().all()

    by_name: dict[str, list[Portfolio]] = {}
    for row in rows:
        by_name.setdefault(row.market_hash_name, []).append(row)

    names = list(by_name.keys())
    prices_list = await asyncio.gather(*[get_cached_price(n, session) for n in names]) if names else []
    name_to_price = dict(zip(names, prices_list))

    items = []
    total_current = 0.0
    total_invested = 0.0

    for name, lots in by_name.items():
        pd = name_to_price[name]

        total_qty = sum(l.quantity for l in lots)
        total_inv = sum(l.buy_price * l.quantity for l in lots)
        avg_buy   = (total_inv / total_qty) if total_qty > 0 else 0.0

        latest = max(lots, key=lambda l: l.added_at)
        primary_source = _normalize_source(latest.buy_source)
        source_key = SOURCE_KEY_MAP[primary_source]

        current_on_source = pd.get(source_key)
        price_unavailable = current_on_source is None or current_on_source <= 0
        if price_unavailable:
            current_on_source = pd.get("best_price") or 0.0

        value   = current_on_source * total_qty
        pnl     = value - total_inv
        pnl_pct = (pnl / total_inv * 100) if total_inv > 0 else 0.0

        total_current  += value
        total_invested += total_inv

        items.append({
            "market_hash_name":  name,
            "quantity":          total_qty,
            "buy_price":         round(avg_buy, 4),
            "buy_source":        primary_source,
            "avg_buy_price":     round(avg_buy, 4),
            "lot_count":         len(lots),
            "lots": [
                {
                    "id":         l.id,
                    "buy_price":  l.buy_price,
                    "quantity":   l.quantity,
                    "buy_source": _normalize_source(l.buy_source),
                    "added_at":   l.added_at,
                } for l in sorted(lots, key=lambda x: x.added_at)
            ],
            "price_steam":       pd.get("price_steam"),
            "price_lisskins":    pd.get("price_lisskins"),
            "price_market_csgo": pd.get("price_market_csgo"),
            "best_price":        round(pd.get("best_price") or 0.0, 2),
            "best_source":       pd.get("best_source"),
            "current_price":     round(current_on_source, 2),
            "price_source_used": primary_source,
            "price_unavailable": price_unavailable,
            "total_value":       round(value, 2),
            "invested":          round(total_inv, 2),
            "pnl":               round(pnl, 2),
            "pnl_pct":           round(pnl_pct, 1),
            "image_url":         pd.get("image_url"),
            "wear":              "",
            "rarity":            "",
            "added_at":          latest.added_at,
        })

    items.sort(key=lambda x: x["added_at"], reverse=True)

    total_pnl = total_current - total_invested
    return {
        "steam_id": resolved,
        "items":    items,
        "summary": {
            "total_value":    round(total_current, 2),
            "total_invested": round(total_invested, 2),
            "total_pnl":      round(total_pnl, 2),
            "total_pnl_pct":  round((total_pnl / total_invested * 100) if total_invested > 0 else 0, 1),
        },
    }


@router.get("/{steam_id}/{market_hash_name:path}/remaining")
async def get_remaining(
    steam_id: str,
    market_hash_name: str,
    session: AsyncSession = Depends(get_session),
):
    """Сколько ещё штук этого предмета можно добавить в портфолио."""
    resolved = await resolve_steam_id(steam_id)
    if not resolved.isdigit():
        raise HTTPException(status_code=400, detail="Не удалось определить SteamID64.")
    inv_qty = await inventory_cache.get_quantity_for(resolved, market_hash_name)
    tracked = await _tracked_qty(session, resolved, market_hash_name)
    return {
        "market_hash_name": market_hash_name,
        "inventory_quantity": inv_qty,
        "tracked_quantity": tracked,
        "remaining": max(0, inv_qty - tracked),
    }


@router.post("/item")
async def add_lot(req: AddItemRequest, session: AsyncSession = Depends(get_session)):
    """Всегда создаёт новый лот, даже если такой предмет уже отслеживается."""
    resolved = await resolve_steam_id(req.steam_id)
    if not resolved.isdigit():
        raise HTTPException(status_code=400, detail=f"Не удалось определить SteamID64 для '{req.steam_id}'.")

    await _ensure_capacity(session, resolved, req.market_hash_name, req.quantity)

    lot = Portfolio(
        steam_id=resolved,
        market_hash_name=req.market_hash_name,
        buy_price=req.buy_price,
        quantity=req.quantity,
        buy_source=_normalize_source(req.buy_source),
        added_at=int(time.time()),
    )
    session.add(lot)
    await session.commit()
    await session.refresh(lot)
    return {"ok": True, "lot_id": lot.id, "steam_id": resolved}


@router.put("/lot/{lot_id}")
async def update_lot(
    lot_id: int,
    req: UpdateItemRequest,
    session: AsyncSession = Depends(get_session),
):
    lot = (await session.execute(
        select(Portfolio).where(Portfolio.id == lot_id)
    )).scalar_one_or_none()
    if not lot:
        raise HTTPException(status_code=404, detail="Лот не найден")

    await _ensure_capacity(
        session, lot.steam_id, lot.market_hash_name, req.quantity, exclude_lot_id=lot.id
    )

    lot.buy_price  = req.buy_price
    lot.quantity   = req.quantity
    lot.buy_source = _normalize_source(req.buy_source)
    await session.commit()
    return {"ok": True, "lot_id": lot.id}


@router.delete("/lot/{lot_id}")
async def delete_lot(lot_id: int, session: AsyncSession = Depends(get_session)):
    res = await session.execute(delete(Portfolio).where(Portfolio.id == lot_id))
    await session.commit()
    return {"ok": True, "deleted": res.rowcount or 0}


@router.delete("/{steam_id}/{market_hash_name:path}")
async def delete_all_lots(
    steam_id: str,
    market_hash_name: str,
    session: AsyncSession = Depends(get_session),
):
    """Удаляет все лоты этого предмета у пользователя (legacy by-name delete)."""
    resolved = await resolve_steam_id(steam_id)
    res = await session.execute(
        delete(Portfolio).where(
            Portfolio.steam_id == resolved,
            Portfolio.market_hash_name == market_hash_name,
        )
    )
    await session.commit()
    return {"ok": True, "deleted": res.rowcount or 0}
