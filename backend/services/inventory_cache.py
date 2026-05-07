"""In-memory TTL кэш инвентаря: name -> quantity.

Нужен чтобы проверять при добавлении лотов сколько у юзера реально предметов
без обращения к Steam при каждом запросе."""
import asyncio
import time
from typing import Optional

from services.steam import fetch_inventory

INVENTORY_TTL = 300  # 5 минут

_locks: dict[str, asyncio.Lock] = {}
_cache: dict[str, dict] = {}  # steam_id -> {fetched_at, qty_by_name}


def _lock_for(steam_id: str) -> asyncio.Lock:
    lock = _locks.get(steam_id)
    if lock is None:
        lock = asyncio.Lock()
        _locks[steam_id] = lock
    return lock


def get_cached(steam_id: str) -> Optional[dict[str, int]]:
    entry = _cache.get(steam_id)
    if not entry:
        return None
    if time.time() - entry["fetched_at"] > INVENTORY_TTL:
        return None
    return entry["qty_by_name"]


async def get_quantities(steam_id: str, force: bool = False) -> dict[str, int]:
    """Возвращает {market_hash_name: total_quantity} из кэша (или fetch если просрочен)."""
    if not force:
        cached = get_cached(steam_id)
        if cached is not None:
            return cached

    async with _lock_for(steam_id):
        if not force:
            cached = get_cached(steam_id)
            if cached is not None:
                return cached

        items = await fetch_inventory(steam_id)
        qty_by_name: dict[str, int] = {}
        for it in items:
            qty_by_name[it["market_hash_name"]] = (
                qty_by_name.get(it["market_hash_name"], 0) + it.get("quantity", 1)
            )
        _cache[steam_id] = {"fetched_at": time.time(), "qty_by_name": qty_by_name}
        return qty_by_name


def store_quantities(steam_id: str, items: list[dict]) -> None:
    """Запись в кэш из уже полученного fetch_inventory (используется в /api/inventory роуте)."""
    qty_by_name: dict[str, int] = {}
    for it in items:
        qty_by_name[it["market_hash_name"]] = (
            qty_by_name.get(it["market_hash_name"], 0) + it.get("quantity", 1)
        )
    _cache[steam_id] = {"fetched_at": time.time(), "qty_by_name": qty_by_name}


async def get_quantity_for(steam_id: str, name: str) -> int:
    qmap = await get_quantities(steam_id)
    return qmap.get(name, 0)
