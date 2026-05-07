"""Telegram Stars invoice creation.

Использует Bot HTTP API метод `createInvoiceLink` (без polling). Возвращает URL
вида `https://t.me/$xxx`, который фронт открывает через `Telegram.WebApp.openInvoice`.

Spec: https://core.telegram.org/bots/api#createinvoicelink
Stars: currency='XTR', цена в LabeledPrice — целое число Stars.

Payload — наш внутренний идентификатор вида `sub:{tier}:{tg_user_id}`. Telegram
вернёт его в `successful_payment.invoice_payload` чтобы мы знали кому что выдавать.
"""
import json

import httpx

from core.config import BOT_TOKEN
from services.billing import PRICING

TG_API_BASE = "https://api.telegram.org"


def make_payload(tier: str, tg_user_id: int) -> str:
    return f"sub:{tier}:{tg_user_id}"


def parse_payload(payload: str) -> tuple[str | None, int | None]:
    """Возвращает (tier, tg_user_id) или (None, None) если payload не наш."""
    try:
        kind, tier, uid = payload.split(":", 2)
    except ValueError:
        return None, None
    if kind != "sub" or tier not in PRICING:
        return None, None
    try:
        return tier, int(uid)
    except ValueError:
        return None, None


async def create_stars_invoice_link(tier: str, tg_user_id: int) -> str:
    """Создаёт Stars-invoice. Возвращает URL для openInvoice."""
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN не задан")
    if tier not in PRICING:
        raise ValueError(f"Неизвестный tier: {tier}")

    cfg = PRICING[tier]
    payload = {
        "title":         f"SkinVault {cfg['label']}",
        "description":   f"Подписка SkinVault {cfg['label']} — снимает лимиты алертов, портфолио, истории цен.",
        "payload":       make_payload(tier, tg_user_id),
        "currency":      "XTR",
        "prices":        json.dumps([{"label": cfg["label"], "amount": cfg["stars"]}]),
        "provider_token": "",
    }
    url = f"{TG_API_BASE}/bot{BOT_TOKEN}/createInvoiceLink"
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(url, data=payload)
        r.raise_for_status()
        body = r.json()
    if not body.get("ok"):
        raise RuntimeError(f"createInvoiceLink: {body}")
    return body["result"]


async def answer_pre_checkout(query_id: str, ok: bool = True, error: str | None = None) -> None:
    if not BOT_TOKEN:
        return
    payload: dict = {"pre_checkout_query_id": query_id, "ok": ok}
    if not ok and error:
        payload["error_message"] = error
    async with httpx.AsyncClient(timeout=10) as client:
        await client.post(f"{TG_API_BASE}/bot{BOT_TOKEN}/answerPreCheckoutQuery", json=payload)
