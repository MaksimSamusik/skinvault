"""Telegram Mini App initData verification.

Spec: https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app

Frontend передаёт `Telegram.WebApp.initData` (URL-encoded query string) в заголовке
`X-Telegram-Init-Data`. Сервер парсит, считает HMAC по схеме Telegram и сверяет
с полем `hash`. Если совпадает — данные действительно от Telegram, можно доверять
полю `user.id` как tg_user_id.
"""
import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl

from fastapi import Header, HTTPException

from core.config import BOT_TOKEN

INIT_DATA_MAX_AGE = 24 * 3600


def _verify(init_data: str, bot_token: str, max_age: int) -> dict:
    parsed = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = parsed.pop("hash", None)
    if not received_hash:
        raise HTTPException(status_code=401, detail="initData без поля hash")

    auth_date = int(parsed.get("auth_date") or 0)
    if not auth_date or (time.time() - auth_date) > max_age:
        raise HTTPException(status_code=401, detail="initData просрочена")

    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    computed_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(computed_hash, received_hash):
        raise HTTPException(status_code=401, detail="initData hash неверный")

    user_raw = parsed.get("user")
    if not user_raw:
        raise HTTPException(status_code=401, detail="initData без поля user")
    try:
        return json.loads(user_raw)
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=401, detail=f"initData user не JSON: {e}")


async def get_current_tg_user(
    x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data"),
) -> dict:
    """FastAPI dependency: возвращает верифицированный TG-юзер dict ({id, first_name, ...})."""
    if not BOT_TOKEN:
        raise HTTPException(status_code=503, detail="BOT_TOKEN не задан, авторизация недоступна")
    if not x_telegram_init_data:
        raise HTTPException(status_code=401, detail="Заголовок X-Telegram-Init-Data отсутствует")
    return _verify(x_telegram_init_data, BOT_TOKEN, INIT_DATA_MAX_AGE)
