"""CryptoBot (@CryptoBot) Crypto Pay API интеграция.

Spec: https://help.crypt.bot/crypto-pay-api

Создание invoice: POST /createInvoice — возвращает {invoice_id, mini_app_invoice_url, ...}.
Webhook (invoice_paid) приходит на наш URL который зарегистрирован в @CryptoBot →
/pay → My Apps → app → Webhook URL. Проверка подписи: HMAC-SHA256 на body,
ключ = SHA256(api_token), сверяем с заголовком `crypto-pay-api-signature`.
"""
import hashlib
import hmac

import httpx

from core.config import CRYPTO_PAY_API, CRYPTO_PAY_TOKEN
from services.billing import PRICING
from services.telegram_pay import make_payload


async def _api(method: str, payload: dict) -> dict:
    if not CRYPTO_PAY_TOKEN:
        raise RuntimeError("CRYPTO_PAY_TOKEN не задан")
    url = f"{CRYPTO_PAY_API}/{method}"
    headers = {"Crypto-Pay-API-Token": CRYPTO_PAY_TOKEN}
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(url, json=payload, headers=headers)
        r.raise_for_status()
        body = r.json()
    if not body.get("ok"):
        raise RuntimeError(f"CryptoPay {method}: {body}")
    return body["result"]


async def create_crypto_invoice(tier: str, tg_user_id: int) -> dict:
    """Создаёт счёт в USDT. Возвращает dict с `invoice_id`, `mini_app_invoice_url`, `bot_invoice_url`."""
    if tier not in PRICING:
        raise ValueError(f"Неизвестный tier: {tier}")
    cfg = PRICING[tier]
    return await _api("createInvoice", {
        "currency_type": "crypto",
        "asset":         "USDT",
        "amount":        cfg["usdt"],
        "description":   f"SkinVault {cfg['label']}",
        "payload":       make_payload(tier, tg_user_id),
        "paid_btn_name": "openBot",
        "paid_btn_url":  "https://t.me/",
        "expires_in":    1800,
    })


def verify_webhook_signature(body_bytes: bytes, received_sig: str) -> bool:
    """HMAC-SHA256(body, key=SHA256(token)). Заголовок `crypto-pay-api-signature`."""
    if not CRYPTO_PAY_TOKEN or not received_sig:
        return False
    secret = hashlib.sha256(CRYPTO_PAY_TOKEN.encode()).digest()
    computed = hmac.new(secret, body_bytes, hashlib.sha256).hexdigest()
    return hmac.compare_digest(computed, received_sig)
