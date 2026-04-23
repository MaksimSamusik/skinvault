import asyncio
import re
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

# ── Constants ──────────────────────────────────────────────────────────────
STEAM_PRICE_URL     = "https://steamcommunity.com/market/priceoverview/"
STEAM_SEARCH_URL    = "https://steamcommunity.com/market/search/render/"
STEAM_INVENTORY_URL = "https://steamcommunity.com/inventory/{steam_id}/730/2"
STEAM_RESOLVE_URL   = "https://api.steampowered.com/ISteamUser/ResolveVanityURL/v1/"
STEAM_CDN           = "https://community.akamai.steamstatic.com/economy/image/"
STEAM_API_KEY       = os.getenv("STEAM_API_KEY", "")

# ── Constants (добавить/заменить) ──────────────────────────────────────────
LISSKINS_API_KEY    = os.getenv("LISSKINS_API_KEY", "")
CSGOMARKET_API_KEY  = os.getenv("CSGOMARKET_API_KEY", "")

# market.csgo.com — прайслист с ключом (USD)
MARKET_CSGO_PRICES_URL = "https://market.csgo.com/api/v2/prices/USD.json"

# market.csgo.com — цена конкретного предмета (используем если bulk недоступен)
MARKET_CSGO_ITEM_URL   = "https://market.csgo.com/api/v2/search-item-by-hash-name-specific"

# Lisskins — прайслист с ключом
LISSKINS_PRICES_URL = "https://lis-skins.ru/market_pricelist/json_cs2/?currency=USD"

CACHE_TTL        = 3600   # 1 час для Steam (rate-limit)
CACHE_TTL_FAST   = 1800   # 30 мин для сторонних маркетов

# In-memory кэш прайслистов (чтобы не скачивать весь файл каждый раз)
_market_csgo_prices: dict  = {}   # market_hash_name -> price_usd
_market_csgo_loaded: float = 0.0

_lisskins_prices: dict     = {}
_lisskins_loaded: float    = 0.0


# ── Bulk price loaders ────────────────────────────────────────────────────

async def load_market_csgo_prices(client: httpx.AsyncClient) -> dict:
    """Загружает весь прайслист market.csgo.com (USD) в память."""
    global _market_csgo_prices, _market_csgo_loaded
    now = time.time()
    if _market_csgo_prices and (now - _market_csgo_loaded) < CACHE_TTL_FAST:
        return _market_csgo_prices

    try:
        # С ключом — лимиты выше и данные актуальнее
        params = {"key": CSGOMARKET_API_KEY} if CSGOMARKET_API_KEY else {}
        resp = await client.get(MARKET_CSGO_PRICES_URL, params=params, timeout=30)
        data = resp.json()

        if data.get("success") and data.get("items"):
            result = {}
            items = data["items"]

            # Формат может быть dict {"classid_instanceid": {...}} или list
            if isinstance(items, dict):
                for item in items.values():
                    name = item.get("market_hash_name")
                    price_str = item.get("price")
                    if name and price_str:
                        try:
                            result[name] = float(price_str)
                        except (ValueError, TypeError):
                            pass
            elif isinstance(items, list):
                for item in items:
                    name = item.get("market_hash_name")
                    price_str = item.get("price")
                    if name and price_str:
                        try:
                            result[name] = float(price_str)
                        except (ValueError, TypeError):
                            pass

            if result:
                _market_csgo_prices = result
                _market_csgo_loaded = now
                print(f"[market.csgo] Загружено {len(result)} предметов")

    except Exception as e:
        print(f"[market.csgo] Ошибка загрузки прайслиста: {e}")

    return _market_csgo_prices


async def load_lisskins_prices(client: httpx.AsyncClient) -> dict:
    """Загружает весь прайслист lisskins в память."""
    global _lisskins_prices, _lisskins_loaded
    now = time.time()
    if _lisskins_prices and (now - _lisskins_loaded) < CACHE_TTL_FAST:
        return _lisskins_prices

    headers = {"User-Agent": "Mozilla/5.0"}
    if LISSKINS_API_KEY:
        headers["Authorization"] = f"Bearer {LISSKINS_API_KEY}"

    try:
        resp = await client.get(LISSKINS_PRICES_URL, timeout=30, headers=headers)
        data = resp.json()

        result = {}
        # Формат: { "success": true, "items": [ { "name": "...", "price": 1.23 } ] }
        # или просто список
        items = data if isinstance(data, list) else data.get("items", [])

        for item in items:
            name  = item.get("name") or item.get("market_hash_name")
            price = item.get("price") or item.get("steam_price") or item.get("price_usd")
            if name and price:
                try:
                    result[name] = float(price)
                except (ValueError, TypeError):
                    pass

        if result:
            _lisskins_prices = result
            _lisskins_loaded = now
            print(f"[lisskins] Загружено {len(result)} предметов")
        else:
            print(f"[lisskins] Пустой ответ. status={resp.status_code}, body={resp.text[:300]}")

    except Exception as e:
        print(f"[lisskins] Ошибка загрузки прайслиста: {e}")

    return _lisskins_prices


async def fetch_market_csgo_item_price(client: httpx.AsyncClient, name: str) -> Optional[float]:
    """Фоллбэк: цена конкретного предмета напрямую с market.csgo (если bulk не сработал)."""
    if not CSGOMARKET_API_KEY:
        return None
    try:
        resp = await client.get(
            MARKET_CSGO_ITEM_URL,
            params={"key": CSGOMARKET_API_KEY, "hash_name": name},
            timeout=10,
        )
        data = resp.json()
        if data.get("success") and data.get("data"):
            # data — список лотов, берём минимальную цену
            prices = [float(lot["price"]) / 1000 for lot in data["data"] if lot.get("price")]
            return min(prices) if prices else None
    except Exception as e:
        print(f"[market.csgo single] {name}: {e}")
    return None


# ── Per-item price fetcher ─────────────────────────────────────────────────

async def fetch_all_prices(
    client: httpx.AsyncClient,
    name: str,
    session: AsyncSession,
) -> dict:
    now = int(time.time())

    result = await session.execute(
        select(PriceCache).where(PriceCache.market_hash_name == name)
    )
    cached = result.scalar_one_or_none()
    if cached and (now - cached.fetched_at) < CACHE_TTL:
        return _build_price_response(cached)

    price_steam = None
    image_url = cached.image_url if cached else None

    try:
        resp = await client.get(
            STEAM_PRICE_URL,
            params={"appid": 730, "currency": 1, "market_hash_name": name},
            timeout=10,
        )
        data = resp.json()
        if data.get("success") and data.get("lowest_price"):
            price_steam = float(
                data["lowest_price"].replace("$", "").replace(",", "").strip()
            )
    except Exception:
        pass

    if not image_url:
        image_url = await fetch_item_image(client, name)

    # market.csgo: сначала bulk, потом single-item фоллбэк
    market_csgo_map = await load_market_csgo_prices(client)
    price_market_csgo = market_csgo_map.get(name)
    if price_market_csgo is None:
        price_market_csgo = await fetch_market_csgo_item_price(client, name)

    # lisskins bulk
    lisskins_map = await load_lisskins_prices(client)
    price_lisskins = lisskins_map.get(name)

    if cached:
        cached.price_steam       = price_steam
        cached.price_lisskins    = price_lisskins
        cached.price_market_csgo = price_market_csgo
        cached.image_url         = image_url
        cached.fetched_at        = now
    else:
        cached = PriceCache(
            market_hash_name=name,
            price_steam=price_steam,
            price_lisskins=price_lisskins,
            price_market_csgo=price_market_csgo,
            image_url=image_url,
            fetched_at=now,
        )
        session.add(cached)

    best = _best_price(price_steam, price_lisskins, price_market_csgo)
    if best:
        session.add(PriceHistory(
            market_hash_name=name,
            price_usd=best,
            source="best",
            recorded_at=now,
        ))

    await session.commit()
    return _build_price_response(cached)


def _best_price(steam, lisskins, market_csgo) -> Optional[float]:
    """Возвращает наименьшую доступную цену среди источников."""
    prices = [p for p in [steam, lisskins, market_csgo] if p is not None and p > 0]
    return min(prices) if prices else None


def _build_price_response(cached: PriceCache) -> dict:
    best = _best_price(cached.price_steam, cached.price_lisskins, cached.price_market_csgo)
    return {
        "price_usd":          best,                    # для обратной совместимости
        "price_steam":        cached.price_steam,
        "price_lisskins":     cached.price_lisskins,
        "price_market_csgo":  cached.price_market_csgo,
        "best_price":         best,
        "best_source":        _best_source(cached.price_steam, cached.price_lisskins, cached.price_market_csgo),
        "image_url":          cached.image_url,
    }


def _best_source(steam, lisskins, market_csgo) -> str:
    candidates = {
        "steam":       steam,
        "lisskins":    lisskins,
        "market_csgo": market_csgo,
    }
    valid = {k: v for k, v in candidates.items() if v is not None and v > 0}
    if not valid:
        return "steam"
    return min(valid, key=lambda k: valid[k])


# ── Steam helpers ─────────────────────────────────────────────────────────

async def resolve_steam_id(client: httpx.AsyncClient, steam_id_or_name: str) -> str:
    if steam_id_or_name.isdigit():
        return steam_id_or_name
    if STEAM_API_KEY:
        try:
            resp = await client.get(
                STEAM_RESOLVE_URL,
                params={"key": STEAM_API_KEY, "vanityurl": steam_id_or_name},
                timeout=10,
            )
            data = resp.json()
            response = data.get("response", {})
            if response.get("success") == 1:
                return response["steamid"]
        except Exception:
            pass
    try:
        resp = await client.get(
            f"https://steamcommunity.com/id/{steam_id_or_name}",
            params={"xml": 1}, timeout=10,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        match = re.search(r"<steamID64>(\d+)</steamID64>", resp.text)
        if match:
            return match.group(1)
    except Exception:
        pass
    return steam_id_or_name


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


async def fetch_steam_inventory(client: httpx.AsyncClient, steam_id: str) -> list:
    try:
        resp = await client.get(
            STEAM_INVENTORY_URL.format(steam_id=steam_id),
            params={"l": "english", "count": 100}, timeout=15,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        try:
            data = resp.json()
        except Exception:
            data = None
        if data is None:
            raise HTTPException(
                status_code=502,
                detail=f"Steam вернул некорректный ответ (статус {resp.status_code}).",
            )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Ошибка Steam API: {e}")

    if not data.get("success", False) and data.get("Error"):
        raise HTTPException(status_code=404, detail="Инвентарь закрыт или SteamID не найден")

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
            "rarity": rarity, "wear": wear,
            "tradable": desc.get("tradable", 0) == 1,
        })
    return items


# ── App ────────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield

app = FastAPI(title="SkinVault API", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ── Schemas ────────────────────────────────────────────────────────────────
class AddItemRequest(BaseModel):
    steam_id: str
    market_hash_name: str
    buy_price: float
    quantity: int = 1
    buy_source: str = "steam"   # steam | lisskins | market_csgo

class UpdateItemRequest(BaseModel):
    buy_price: float
    quantity: int = 1
    buy_source: str = "steam"


# ── Routes ─────────────────────────────────────────────────────────────────

@app.get("/api/health")
async def health(session: AsyncSession = Depends(get_session)):
    from database import DATABASE_URL, IS_POSTGRES
    import os
    db_type = "postgres" if IS_POSTGRES else "sqlite"
    try:
        from sqlalchemy import text as sa_text
        await session.execute(sa_text("SELECT 1"))
        db_ok = True
    except Exception as e:
        db_ok = str(e)
    return {
        "status": "ok",
        "db_type": db_type,
        "db_ok": db_ok,
        "has_database_url_env": bool(os.environ.get("DATABASE_URL")),
        "db_url_prefix": DATABASE_URL[:35] + "..." if DATABASE_URL else None,
    }


@app.get("/api/resolve/{vanity_url}")
async def resolve_vanity(vanity_url: str):
    if vanity_url.isdigit():
        return {"vanity_url": vanity_url, "steam_id": vanity_url}
    async with httpx.AsyncClient() as client:
        resolved = await resolve_steam_id(client, vanity_url)
    if not resolved.isdigit():
        raise HTTPException(status_code=404, detail=f"Steam-аккаунт '{vanity_url}' не найден.")
    return {"vanity_url": vanity_url, "steam_id": resolved}


@app.get("/api/search")
async def search_items(q: str = Query(..., min_length=2)):
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(
                STEAM_SEARCH_URL,
                params={"appid": 730, "query": q, "count": 10,
                        "search_descriptions": 0, "norender": 1},
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
        return await fetch_all_prices(client, market_hash_name, session)


@app.get("/api/inventory/{steam_id}")
async def get_inventory(steam_id: str):
    async with httpx.AsyncClient() as client:
        resolved = await resolve_steam_id(client, steam_id)
        if not resolved.isdigit():
            raise HTTPException(status_code=400, detail=f"Не удалось определить SteamID64 для '{steam_id}'.")
        items = await fetch_steam_inventory(client, resolved)
    return {"steam_id": resolved, "items": items, "count": len(items)}


@app.get("/api/portfolio/{steam_id}")
async def get_portfolio(
    steam_id: str,
    session: AsyncSession = Depends(get_session),
):
    async with httpx.AsyncClient() as client:
        steam_id = await resolve_steam_id(client, steam_id)
    if not steam_id.isdigit():
        raise HTTPException(status_code=400, detail="Не удалось определить SteamID64.")

    result = await session.execute(
        select(Portfolio)
        .where(Portfolio.steam_id == steam_id)
        .order_by(Portfolio.added_at.desc())
    )
    rows = result.scalars().all()

    async with httpx.AsyncClient() as client:
        prices = await asyncio.gather(*[
            fetch_all_prices(client, r.market_hash_name, session)
            for r in rows
        ])

    SOURCE_KEY_MAP = {
        "steam":       "price_steam",
        "lisskins":    "price_lisskins",
        "market_csgo": "price_market_csgo",
    }

    items = []
    total_current = total_invested = 0.0

    for row, pd in zip(rows, prices):
        buy_source = (row.buy_source or "steam").strip().lower()
        if buy_source not in SOURCE_KEY_MAP:
            buy_source = "steam"

        source_key = SOURCE_KEY_MAP[buy_source]

        # Цена на площадке покупки — именно по ней считаем P&L
        current_on_source = pd.get(source_key)
        if current_on_source is None or current_on_source <= 0:
            # Площадка временно недоступна — фоллбэк на best_price,
            # но помечаем что данные неточные
            current_on_source = pd.get("best_price") or 0.0
            price_unavailable = True
        else:
            price_unavailable = False

        best_price = pd.get("best_price") or 0.0

        invested = row.buy_price * row.quantity
        value    = current_on_source * row.quantity
        pnl      = value - invested
        pnl_pct  = (pnl / invested * 100) if invested > 0 else 0.0

        total_current  += value
        total_invested += invested

        items.append({
            "market_hash_name":   row.market_hash_name,
            "buy_price":          row.buy_price,
            "buy_source":         buy_source,
            "quantity":           row.quantity,
            # Все цены по источникам
            "price_steam":        pd.get("price_steam"),
            "price_lisskins":     pd.get("price_lisskins"),
            "price_market_csgo":  pd.get("price_market_csgo"),
            "best_price":         round(best_price, 2),
            "best_source":        pd.get("best_source"),
            # P&L считается строго по площадке покупки
            "current_price":      round(current_on_source, 2),
            "price_source_used":  buy_source,          # какая цена реально использована
            "price_unavailable":  price_unavailable,   # флаг: пришлось использовать фоллбэк
            "total_value":        round(value, 2),
            "invested":           round(invested, 2),
            "pnl":                round(pnl, 2),
            "pnl_pct":            round(pnl_pct, 1),
            "image_url":          pd.get("image_url"),
            "wear":               "",
            "rarity":             "",
            "added_at":           row.added_at,
        })

    total_pnl = total_current - total_invested
    return {
        "steam_id": steam_id,
        "items": items,
        "summary": {
            "total_value":    round(total_current, 2),
            "total_invested": round(total_invested, 2),
            "total_pnl":      round(total_pnl, 2),
            "total_pnl_pct":  round((total_pnl / total_invested * 100) if total_invested > 0 else 0, 1),
        },
    }


@app.post("/api/portfolio/item")
async def add_item(
    req: AddItemRequest,
    session: AsyncSession = Depends(get_session),
):
    async with httpx.AsyncClient() as client:
        resolved_id = await resolve_steam_id(client, req.steam_id)
    if not resolved_id.isdigit():
        raise HTTPException(status_code=400, detail=f"Не удалось определить SteamID64 для '{req.steam_id}'.")

    result = await session.execute(
        select(Portfolio).where(
            Portfolio.steam_id == resolved_id,
            Portfolio.market_hash_name == req.market_hash_name,
        )
    )
    existing = result.scalar_one_or_none()
    now = int(time.time())

    if existing:
        existing.buy_price  = req.buy_price
        existing.quantity   = req.quantity
        existing.buy_source = req.buy_source
    else:
        session.add(Portfolio(
            steam_id=resolved_id,
            market_hash_name=req.market_hash_name,
            buy_price=req.buy_price,
            quantity=req.quantity,
            buy_source=req.buy_source,
            added_at=now,
        ))

    await session.commit()
    return {"ok": True, "steam_id": resolved_id}


@app.put("/api/portfolio/{steam_id}/{market_hash_name:path}")
async def update_item(
    steam_id: str,
    market_hash_name: str,
    req: UpdateItemRequest,
    session: AsyncSession = Depends(get_session),
):
    async with httpx.AsyncClient() as client:
        steam_id = await resolve_steam_id(client, steam_id)
    await session.execute(
        update(Portfolio)
        .where(Portfolio.steam_id == steam_id, Portfolio.market_hash_name == market_hash_name)
        .values(buy_price=req.buy_price, quantity=req.quantity, buy_source=req.buy_source)
    )
    await session.commit()
    return {"ok": True}


@app.delete("/api/portfolio/{steam_id}/{market_hash_name:path}")
async def remove_item(
    steam_id: str,
    market_hash_name: str,
    session: AsyncSession = Depends(get_session),
):
    async with httpx.AsyncClient() as client:
        steam_id = await resolve_steam_id(client, steam_id)
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
        "history": [{"price_usd": r.price_usd, "recorded_at": r.recorded_at} for r in rows],
    }


# ── Serve frontend (must be last) ──────────────────────────────────────────
if os.path.isdir(FRONTEND_DIR):
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

    @app.get("/")
    async def serve_index():
        return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))