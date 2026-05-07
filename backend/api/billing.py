"""Биллинг: создание invoice + webhook'и от Telegram (Stars) и CryptoBot."""
import hmac

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth import get_current_tg_user
from core.config import TELEGRAM_WEBHOOK_SECRET
from db.session import get_session
from services import cryptopay, telegram_pay
from services.billing import (
    PRICING,
    current_tier,
    extend_subscription,
    get_active_subscription,
)

router = APIRouter(prefix="/api/billing", tags=["billing"])


@router.get("/pricing")
async def get_pricing():
    return {"tiers": PRICING}


@router.post("/checkout")
async def checkout(
    body: dict,
    tg_user: dict = Depends(get_current_tg_user),
):
    """Создаёт invoice выбранного типа. body = {tier: 'premium'|'pro', method: 'stars'|'crypto'}."""
    tier   = body.get("tier")
    method = body.get("method")
    if tier not in PRICING:
        raise HTTPException(status_code=400, detail=f"Неизвестный tier: {tier}")
    if method not in ("stars", "crypto"):
        raise HTTPException(status_code=400, detail=f"Неизвестный method: {method}")

    tg_id = int(tg_user["id"])
    try:
        if method == "stars":
            url = await telegram_pay.create_stars_invoice_link(tier, tg_id)
            return {"method": "stars", "url": url, "stars": PRICING[tier]["stars"]}
        else:
            inv = await cryptopay.create_crypto_invoice(tier, tg_id)
            return {
                "method":      "crypto",
                "url":         inv.get("mini_app_invoice_url") or inv.get("bot_invoice_url"),
                "invoice_id":  inv.get("invoice_id"),
                "amount_usdt": PRICING[tier]["usdt"],
            }
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Создание invoice: {e}")


@router.post("/telegram-webhook")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None, alias="X-Telegram-Bot-Api-Secret-Token"),
    session: AsyncSession = Depends(get_session),
):
    """Принимает обновления от Telegram. Обрабатываем только pre_checkout_query и
    successful_payment — остальное игнорим (бот /start крутится в bot.py polling-режиме
    либо тоже сюда позже добавится)."""
    if TELEGRAM_WEBHOOK_SECRET and not (
        x_telegram_bot_api_secret_token
        and hmac.compare_digest(x_telegram_bot_api_secret_token, TELEGRAM_WEBHOOK_SECRET)
    ):
        raise HTTPException(status_code=403, detail="Bad webhook secret")

    update = await request.json()

    pre_checkout = update.get("pre_checkout_query")
    if pre_checkout:
        await telegram_pay.answer_pre_checkout(pre_checkout["id"], ok=True)
        return {"ok": True}

    msg = update.get("message") or {}
    sp = msg.get("successful_payment")
    if sp:
        tier, uid = telegram_pay.parse_payload(sp.get("invoice_payload", ""))
        if tier and uid:
            await extend_subscription(
                tg_user_id=uid,
                tier=tier,
                payment_method="stars",
                payment_id=sp.get("telegram_payment_charge_id"),
                amount=float(sp.get("total_amount", 0)),
                currency=sp.get("currency"),
                session=session,
            )
            print(f"[billing] Stars: {tier} продлён для tg_user_id={uid}")
        return {"ok": True}

    return {"ok": True}


@router.post("/cryptopay-webhook")
async def cryptopay_webhook(
    request: Request,
    crypto_pay_api_signature: str | None = Header(default=None, alias="crypto-pay-api-signature"),
    session: AsyncSession = Depends(get_session),
):
    """CryptoBot webhook. Нас интересует update_type='invoice_paid'."""
    body_bytes = await request.body()
    if not cryptopay.verify_webhook_signature(body_bytes, crypto_pay_api_signature or ""):
        raise HTTPException(status_code=403, detail="Bad CryptoPay signature")

    update = await request.json()
    if update.get("update_type") != "invoice_paid":
        return {"ok": True}

    payload = update.get("payload") or {}
    inv = payload.get("payload") or ""
    tier, uid = telegram_pay.parse_payload(inv)
    if not (tier and uid):
        print(f"[billing] CryptoPay payload не распознан: {inv}")
        return {"ok": True}

    invoice_id = str(payload.get("invoice_id") or "")
    await extend_subscription(
        tg_user_id=uid,
        tier=tier,
        payment_method="crypto",
        payment_id=f"cryptopay:{invoice_id}",
        amount=float(payload.get("amount") or 0),
        currency=payload.get("asset"),
        session=session,
    )
    print(f"[billing] CryptoPay: {tier} продлён для tg_user_id={uid}")
    return {"ok": True}


@router.get("/me")
async def my_subscription(
    tg_user: dict = Depends(get_current_tg_user),
    session: AsyncSession = Depends(get_session),
):
    """Текущая подписка юзера + tier. Используется фронтом для гейтов и upsell-UI."""
    tg_id = int(tg_user["id"])
    sub = await get_active_subscription(tg_id, session)
    tier = await current_tier(tg_id, session)
    return {
        "tier":       tier,
        "expires_at": sub.expires_at if sub else None,
        "started_at": sub.started_at if sub else None,
        "method":     sub.payment_method if sub else None,
    }
