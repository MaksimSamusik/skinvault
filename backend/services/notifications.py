"""Отправка Telegram-уведомлений напрямую через Bot HTTP API.

Используется python-telegram-bot не нужен — `requests/httpx` к
`api.telegram.org/bot<TOKEN>/sendMessage` достаточно. Это позволяет посылать
уведомления из FastAPI-кода не запуская polling-процесс бота параллельно
(`bot.py` крутит polling — здесь у нас только одиночные HTTP-вызовы).
"""
import httpx

from core.config import BOT_TOKEN

TG_API_BASE = "https://api.telegram.org"


async def send_message(
    tg_user_id: int,
    text: str,
    parse_mode: str = "Markdown",
    disable_preview: bool = True,
) -> bool:
    """Шлёт сообщение юзеру. Возвращает True если успех, иначе False (с логом)."""
    if not BOT_TOKEN:
        print("[notifications] BOT_TOKEN не задан, не могу отправить")
        return False
    url = f"{TG_API_BASE}/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": tg_user_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": disable_preview,
    }
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(url, json=payload)
            if r.status_code != 200:
                print(f"[notifications] tg_user_id={tg_user_id} status={r.status_code}: {r.text[:200]}")
                return False
        return True
    except httpx.HTTPError as e:
        print(f"[notifications] tg_user_id={tg_user_id} ошибка: {e}")
        return False
