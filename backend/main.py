import asyncio
import time
from contextlib import asynccontextmanager
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException, Query, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import select, delete, update
from sqlalchemy.ext.asyncio import AsyncSession

from database import init_db, get_session
from models import Portfolio, PriceCache, PriceHistory
import os

FRONTEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "../frontend")

# ── Steam constants ────────────────────────────────────────────────────────
STEAM_PRICE_URL  = "https://steamcommunity.com/market/priceoverview/"
STEAM_SEARCH_URL = "https://steamcommunity.com/market/search/render/"
STEAM_INVENTORY_URL = "https://steamcommunity.com/inventory/{steam_id}/730/2"
STEAM_CDN = "https://community.akamai.steamstatic.com/economy/image/"
CACHE_TTL = 3600  # 1 hour

# ── Steam helpers ──────────────────────────────────────────────────────────
async def fetch_item_image(client: httpx.AsyncClient, name: str) -> Optional[str]:
    try:
        resp = await client.get(
            STEAM_SEARCH_URL,
            params={"appid": 730, "query": name, "count": 1,
                    "search_descriptions": 0, "norender": 1},
            timeout=10,
        )
        data = resp.json()
        results = data.get("results", [])
        if results:
            icon = results[0].get("asset_description", {}).get("icon_url", "")
            if icon:
                return STEAM_CDN + icon
    except Exception:
        pass
    return None


async def fetch_market_price(
    client: httpx.AsyncClient,
    name: str,
    session: AsyncSession,
) -> dict:
    now = int(time.time())

    # Check cache
    result = await session.execute(
        select(PriceCache).where(PriceCache.market_hash_name == name)
    )
    cached = result.scalar_one_or_none()
    if cached and (now - cached.fetched_at) < CACHE_TTL:
        return {"price_usd": cached.price_usd, "image_url": cached.image_url}

    # Fetch price from Steam
    price = None
    try:
        resp = await client.get(
            STEAM_PRICE_URL,
            params={"appid": 730, "currency": 1, "market_hash_name": name},
            timeout=10,
        )
        data = resp.json()
        if data.get("success") and data.get("lowest_price"):
            price = float(
                data["lowest_price"].replace("$", "").replace(",", "").strip()
            )
    except Exception:
        pass

    image_url = await fetch_item_image(client, name)

    # Upsert cache
    if cached:
        cached.price_usd = price
        cached.image_url = image_url
        cached.fetched_at = now
    else:
        session.add(PriceCache(
            market_hash_name=name,
            price_usd=price,
            image_url=image_url,
            fetched_at=now,
        ))

    # Write history point
    if price:
        session.add(PriceHistory(
            market_hash_name=name,
            price_usd=price,
            recorded_at=now,
        ))

    await session.commit()
    return {"price_usd": price, "image_url": image_url}


async def fetch_steam_inventory(client: httpx.AsyncClient, steam_id: str) -> list:
    try:
        resp = await client.get(
            STEAM_INVENTORY_URL.format(steam_id=steam_id),
            params={"l": "english", "count": 100},
            timeout=15,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        data = resp.json()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Steam API error: {e}")

    if not data.get("success", False) and data.get("Error"):
        raise HTTPException(
            status_code=404, detail="Inventory is private or Steam ID not found"
        )

    descriptions = {
        f"{d['classid']}_{d['instanceid']}": d
        for d in data.get("descriptions", [])
    }
    items, seen = [], set()
    for asset in data.get("assets", []):
        key = f"{asset['classid']}_{asset['instanceid']}"
        if key in seen:
            continue
        seen.add(key)
        desc = descriptions.get(key, {})
        name = desc.get("market_hash_name", desc.get("name", "Unknown"))
        icon = desc.get("icon_url", "")
        rarity = wear = ""
        for tag in desc.get("tags", []):
            if tag.get("category") == "Rarity":
                rarity = tag.get("internal_name", "").lower().replace("rarity_", "")
            if tag.get("category") == "Exterior":
                wear = tag.get("name", "")
        items.append({
            "market_hash_name": name,
            "image_url": STEAM_CDN + icon if icon else None,
            "rarity": rarity,
            "wear": wear,
            "tradable": desc.get("tradable", 0) == 1,
        })
    return items


# ── App ────────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield

app = FastAPI(title="SkinVault API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Schemas ────────────────────────────────────────────────────────────────
class AddItemRequest(BaseModel):
    steam_id: str
    market_hash_name: str
    buy_price: float
    quantity: int = 1

class UpdateItemRequest(BaseModel):
    buy_price: float
    quantity: int = 1

# ── Routes ─────────────────────────────────────────────────────────────────
@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.get("/api/search")
async def search_items(q: str = Query(..., min_length=2)):
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(
                STEAM_SEARCH_URL,
                params={
                    "appid": 730, "query": q, "count": 10,
                    "search_descriptions": 0, "norender": 1,
                },
                timeout=10,
            )
            data = resp.json()
            results = []
            for item in data.get("results", []):
                desc = item.get("asset_description", {})
                icon = desc.get("icon_url", "")
                results.append({
                    "market_hash_name": item.get("hash_name", ""),
                    "name": item.get("name", ""),
                    "image_url": STEAM_CDN + icon if icon else None,
                    "sell_listings": item.get("sell_listings", 0),
                })
            return {"results": results}
        except Exception as e:
            raise HTTPException(status_code=502, detail=str(e))


@app.get("/api/price/{market_hash_name:path}")
async def get_price(
    market_hash_name: str,
    session: AsyncSession = Depends(get_session),
):
    async with httpx.AsyncClient() as client:
        return await fetch_market_price(client, market_hash_name, session)


@app.get("/api/inventory/{steam_id}")
async def get_inventory(steam_id: str):
    async with httpx.AsyncClient() as client:
        items = await fetch_steam_inventory(client, steam_id)
    return {"steam_id": steam_id, "items": items, "count": len(items)}


@app.get("/api/portfolio/{steam_id}")
async def get_portfolio(
    steam_id: str,
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        select(Portfolio)
        .where(Portfolio.steam_id == steam_id)
        .order_by(Portfolio.added_at.desc())
    )
    rows = result.scalars().all()

    async with httpx.AsyncClient() as client:
        prices = await asyncio.gather(*[
            fetch_market_price(client, r.market_hash_name, session)
            for r in rows
        ])

    items = []
    total_current = total_invested = 0.0

    for row, pd in zip(rows, prices):
        current  = pd.get("price_usd") or 0.0
        invested = row.buy_price * row.quantity
        value    = current * row.quantity
        pnl      = value - invested
        pnl_pct  = (pnl / invested * 100) if invested > 0 else 0.0
        total_current  += value
        total_invested += invested
        items.append({
            "market_hash_name": row.market_hash_name,
            "buy_price":        row.buy_price,
            "quantity":         row.quantity,
            "current_price":    round(current, 2),
            "total_value":      round(value, 2),
            "invested":         round(invested, 2),
            "pnl":              round(pnl, 2),
            "pnl_pct":          round(pnl_pct, 1),
            "image_url":        pd.get("image_url"),
            "wear":             "",
            "rarity":           "",
            "added_at":         row.added_at,
        })

    total_pnl = total_current - total_invested
    return {
        "steam_id": steam_id,
        "items":    items,
        "summary": {
            "total_value":    round(total_current, 2),
            "total_invested": round(total_invested, 2),
            "total_pnl":      round(total_pnl, 2),
            "total_pnl_pct":  round(
                (total_pnl / total_invested * 100) if total_invested > 0 else 0, 1
            ),
        },
    }


@app.post("/api/portfolio/item")
async def add_item(
    req: AddItemRequest,
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        select(Portfolio).where(
            Portfolio.steam_id == req.steam_id,
            Portfolio.market_hash_name == req.market_hash_name,
        )
    )
    existing = result.scalar_one_or_none()
    now = int(time.time())

    if existing:
        existing.buy_price = req.buy_price
        existing.quantity  = req.quantity
    else:
        session.add(Portfolio(
            steam_id=req.steam_id,
            market_hash_name=req.market_hash_name,
            buy_price=req.buy_price,
            quantity=req.quantity,
            added_at=now,
        ))

    await session.commit()
    return {"ok": True}


@app.put("/api/portfolio/{steam_id}/{market_hash_name:path}")
async def update_item(
    steam_id: str,
    market_hash_name: str,
    req: UpdateItemRequest,
    session: AsyncSession = Depends(get_session),
):
    await session.execute(
        update(Portfolio)
        .where(
            Portfolio.steam_id == steam_id,
            Portfolio.market_hash_name == market_hash_name,
        )
        .values(buy_price=req.buy_price, quantity=req.quantity)
    )
    await session.commit()
    return {"ok": True}


@app.delete("/api/portfolio/{steam_id}/{market_hash_name:path}")
async def remove_item(
    steam_id: str,
    market_hash_name: str,
    session: AsyncSession = Depends(get_session),
):
    await session.execute(
        delete(Portfolio).where(
            Portfolio.steam_id == steam_id,
            Portfolio.market_hash_name == market_hash_name,
        )
    )
    await session.commit()
    return {"ok": True}


@app.get("/api/history/{market_hash_name:path}")
async def get_price_history(
    market_hash_name: str,
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        select(PriceHistory)
        .where(PriceHistory.market_hash_name == market_hash_name)
        .order_by(PriceHistory.recorded_at.asc())
        .limit(90)
    )
    rows = result.scalars().all()
    return {
        "market_hash_name": market_hash_name,
        "history": [
            {"price_usd": r.price_usd, "recorded_at": r.recorded_at}
            for r in rows
        ],
    }


# ── Serve frontend (must be last) ──────────────────────────────────────────
if os.path.isdir(FRONTEND_DIR):
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

    @app.get("/")
    async def serve_index():
        return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))
