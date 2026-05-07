"""Lisskins price loader: streaming httpx → ijson, без записи bulk JSON на диск.

Happy path — streaming: качаем bulk JSON и сразу парсим через ijson, RAM ~10 MB,
диск не используется (важно для Railway free-плана с эфемерным диском <1 GB).
Fallback path — disk-based: качаем 840 MB на диск и парсим через 3 prefix-а
(если streaming с PRIMARY_PREFIX вернул 0 позиций, например изменился формат).
Playwright остался как fallback на случай возврата Cloudflare защиты."""
import asyncio
import json
import os
import time
from pathlib import Path
from typing import Optional

import httpx
import ijson

from core.config import (
    DATABASE_DIR,
    DEFAULT_USER_AGENT,
    LISSKINS_BULK_URL,
    LISSKINS_HOMEPAGE,
)

PRICES_FILE = DATABASE_DIR / "lisskins_prices.json"
RAW_FILE    = DATABASE_DIR / "lisskins_raw.json"

PRIMARY_PREFIX = "items.item"
_PARSE_PREFIXES = ("items.item", "item", "data.item")

_prices: dict[str, float] = {}
_loaded_at: float = 0.0
_lock = asyncio.Lock()


def get_lisskins_price(name: str) -> Optional[float]:
    return _prices.get(name)


def get_lisskins_cache_size() -> int:
    return len(_prices)


def cache_age_seconds() -> float:
    """Возраст кэша lisskins в секундах. Возвращает inf если файла нет."""
    if not PRICES_FILE.exists():
        return float("inf")
    return time.time() - PRICES_FILE.stat().st_mtime


def load_from_file() -> dict[str, float]:
    """Быстрая загрузка кэшированных цен с диска при старте."""
    global _prices, _loaded_at
    if not PRICES_FILE.exists():
        return {}
    try:
        with open(PRICES_FILE, "r", encoding="utf-8") as f:
            _prices = json.load(f)
        _loaded_at = os.path.getmtime(PRICES_FILE)
        print(f"[lisskins] Загружено {len(_prices)} цен из файла (mtime: {time.ctime(_loaded_at)})")
        return _prices
    except Exception as e:
        print(f"[lisskins] Ошибка загрузки из файла: {e}")
        return {}


class _AsyncHttpxFile:
    """Async file-like обёртка над httpx aiter_bytes для ijson.items_async.

    ijson.items_async ожидает объект с `async def read(n)` — httpx response
    отдаёт async-итератор чанков, но не file-like. Этот класс мост между ними."""

    def __init__(self, response: httpx.Response, chunk_size: int = 1 << 16):
        self._iterator = response.aiter_bytes(chunk_size)
        self._buf = bytearray()
        self._eof = False
        self._bytes_read = 0

    @property
    def bytes_read(self) -> int:
        return self._bytes_read

    async def read(self, n: int = -1) -> bytes:
        if n < 0:
            async for chunk in self._iterator:
                self._buf.extend(chunk)
                self._bytes_read += len(chunk)
            self._eof = True
            data = bytes(self._buf)
            self._buf.clear()
            return data

        while not self._eof and len(self._buf) < n:
            try:
                chunk = await self._iterator.__anext__()
            except StopAsyncIteration:
                self._eof = True
                break
            self._buf.extend(chunk)
            self._bytes_read += len(chunk)

        data = bytes(self._buf[:n])
        del self._buf[:n]
        return data


def _accumulate_item(out: dict[str, float], item: object) -> None:
    if not isinstance(item, dict):
        return
    name = item.get("name") or item.get("market_hash_name")
    price_raw = item.get("price")
    if not name or price_raw is None:
        return
    try:
        price = float(price_raw)
    except (TypeError, ValueError):
        return
    if price <= 0:
        return
    cur = out.get(name)
    if cur is None or price < cur:
        out[name] = price


async def _stream_download_and_parse() -> Optional[dict[str, float]]:
    """Streaming download + parse за один проход — без записи 840 MB на диск.

    Возвращает:
        None  — direct fetch заблокирован (403/503), нужен Playwright fallback
        {}    — parse дал 0 позиций (формат сменился) — нужен disk-based fallback
        dict  — успех
    """
    headers = {
        "User-Agent": DEFAULT_USER_AGENT,
        "Accept": "application/json, text/plain, */*",
        "Referer": LISSKINS_HOMEPAGE,
    }
    out: dict[str, float] = {}
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(600, connect=30),
        follow_redirects=True,
        http2=False,
    ) as client:
        async with client.stream("GET", LISSKINS_BULK_URL, headers=headers) as response:
            if response.status_code in (403, 503):
                print(f"[lisskins] streaming: заблокирован ({response.status_code})")
                return None
            response.raise_for_status()
            ct = response.headers.get("content-type", "")
            if "json" not in ct:
                snippet = (await response.aread())[:200].decode("utf-8", "replace")
                raise RuntimeError(f"Lisskins вернул не-JSON ({ct}): {snippet}")

            reader = _AsyncHttpxFile(response)
            try:
                async for item in ijson.items_async(reader, PRIMARY_PREFIX):
                    _accumulate_item(out, item)
            except (ijson.IncompleteJSONError, ValueError) as e:
                print(f"[lisskins] streaming parse error (prefix={PRIMARY_PREFIX}): {e}")
                return {}

            mb = reader.bytes_read // (1024 * 1024)
            print(f"[lisskins] streaming: {len(out)} позиций, скачано {mb} MB (prefix={PRIMARY_PREFIX})")
            return out


async def _download_direct() -> Optional[Path]:
    """Качаем bulk JSON на диск (используется как fallback когда streaming парсит 0)."""
    RAW_FILE.parent.mkdir(parents=True, exist_ok=True)
    headers = {
        "User-Agent": DEFAULT_USER_AGENT,
        "Accept": "application/json, text/plain, */*",
        "Referer": LISSKINS_HOMEPAGE,
    }
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(600, connect=30),
        follow_redirects=True,
        http2=False,
    ) as client:
        async with client.stream("GET", LISSKINS_BULK_URL, headers=headers) as response:
            if response.status_code in (403, 503):
                print(f"[lisskins] direct fetch заблокирован ({response.status_code}), пробуем Playwright fallback")
                return None
            response.raise_for_status()
            ct = response.headers.get("content-type", "")
            if "json" not in ct:
                snippet = (await response.aread())[:200].decode("utf-8", "replace")
                raise RuntimeError(f"Lisskins вернул не-JSON ({ct}): {snippet}")
            written = 0
            with open(RAW_FILE, "wb") as f:
                async for chunk in response.aiter_bytes(1 << 16):
                    f.write(chunk)
                    written += len(chunk)
            print(f"[lisskins] direct: скачано {written // (1024 * 1024)} MB")
            return RAW_FILE


async def _download_with_playwright() -> Path:
    """Fallback: получаем Cloudflare cookies через headless Chromium и качаем.
    Доступен только если установлен пакет `playwright` (опционален в prod)."""
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        raise RuntimeError(
            "playwright не установлен — direct fetch заблокирован, fallback недоступен. "
            "Установи `pip install playwright && playwright install chromium`"
        )

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            context = await browser.new_context(user_agent=DEFAULT_USER_AGENT)
            page = await context.new_page()
            await page.goto(LISSKINS_HOMEPAGE, wait_until="networkidle", timeout=30000)
            await page.wait_for_timeout(2000)
            cookies = {c["name"]: c["value"] for c in await context.cookies()}
        finally:
            await browser.close()

    RAW_FILE.parent.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(timeout=httpx.Timeout(600, connect=30), follow_redirects=True) as client:
        async with client.stream(
            "GET",
            LISSKINS_BULK_URL,
            cookies=cookies,
            headers={"User-Agent": DEFAULT_USER_AGENT, "Referer": LISSKINS_HOMEPAGE},
        ) as response:
            response.raise_for_status()
            with open(RAW_FILE, "wb") as f:
                async for chunk in response.aiter_bytes(1 << 16):
                    f.write(chunk)
    return RAW_FILE


def _parse_bulk(file_path: Path) -> dict[str, float]:
    """Стримящий парсинг bulk JSON. Берём минимальную цену по name."""
    for prefix in _PARSE_PREFIXES:
        result = _try_parse(file_path, prefix)
        if result:
            print(f"[lisskins] Спарсено {len(result)} уникальных позиций (prefix={prefix})")
            return result
    print("[lisskins] Не удалось разобрать ни одним из известных prefix-ов")
    return {}


def _try_parse(file_path: Path, prefix: str) -> dict[str, float]:
    out: dict[str, float] = {}
    try:
        with open(file_path, "rb") as f:
            for item in ijson.items(f, prefix):
                if not isinstance(item, dict):
                    continue
                name = item.get("name") or item.get("market_hash_name")
                price_raw = item.get("price")
                if not name or price_raw is None:
                    continue
                try:
                    price = float(price_raw)
                except (TypeError, ValueError):
                    continue
                if price <= 0:
                    continue
                cur = out.get(name)
                if cur is None or price < cur:
                    out[name] = price
    except (ijson.IncompleteJSONError, ValueError) as e:
        print(f"[lisskins] parse error (prefix={prefix}): {e}")
        return {}
    return out


async def _refresh_via_disk_fallback() -> tuple[dict[str, float], str]:
    """Fallback: качаем 840 MB на диск, парсим через _parse_bulk (multi-prefix).
    Используется только если streaming не сработал — например сменился формат JSON.
    Возвращает (result_dict, method_label)."""
    method = "disk"
    raw_file = await _download_direct()
    if raw_file is None:
        method = "playwright"
        raw_file = await _download_with_playwright()
    try:
        result = await asyncio.to_thread(_parse_bulk, raw_file)
    finally:
        try:
            if raw_file and raw_file.exists():
                raw_file.unlink()
        except OSError:
            pass
    return result, method


async def refresh_prices() -> dict:
    """Полное обновление: streaming → disk fallback → Playwright fallback → save.

    Streaming не пишет 840 MB на диск (RAM ~10 MB) — happy path для Railway free.
    Disk fallback используется если streaming вернул 0 позиций или его заблокировали.

    Возвращает {"updated": N, "elapsed": secs, "method": "streaming|disk|playwright"}."""
    global _prices, _loaded_at

    async with _lock:
        started = time.monotonic()
        method = "streaming"
        try:
            print("[lisskins] refresh: streaming download + parse...")
            result = await _stream_download_and_parse()

            if result is None:
                print("[lisskins] streaming заблокирован — Playwright fallback")
                raw_file = await _download_with_playwright()
                method = "playwright"
                try:
                    result = await asyncio.to_thread(_parse_bulk, raw_file)
                finally:
                    try:
                        if raw_file.exists():
                            raw_file.unlink()
                    except OSError:
                        pass
            elif not result:
                print("[lisskins] streaming вернул 0 — disk fallback с multi-prefix")
                result, method = await _refresh_via_disk_fallback()

            if not result:
                msg = "парсинг вернул пустой результат, кэш не обновлён"
                print(f"[lisskins] refresh: {msg}")
                return {"updated": 0, "elapsed": time.monotonic() - started, "method": method, "error": msg}

            PRICES_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(PRICES_FILE, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False)

            _prices = result
            _loaded_at = time.time()
            elapsed = time.monotonic() - started
            print(f"[lisskins] refresh: обновлено {len(result)} цен за {elapsed:.1f}s ({method})")
            return {"updated": len(result), "elapsed": elapsed, "method": method}

        except Exception as e:
            elapsed = time.monotonic() - started
            print(f"[lisskins] refresh: ошибка после {elapsed:.1f}s: {e}")
            return {"updated": 0, "elapsed": elapsed, "method": method, "error": str(e)}
