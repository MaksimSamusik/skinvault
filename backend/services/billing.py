"""Подписка / премиум-tier — заглушка до Спринта 3.

Sprint 3 заменит `is_premium()` на реальную проверку Subscription-таблицы +
Telegram Stars invoice flow. Сейчас возвращает False для всех — гейты на free-tier
лимитах работают корректно, премиум-фичи недоступны.
"""

# Лимиты по tier'ам
FREE_MAX_ALERTS = 1
FREE_MAX_PORTFOLIO_ITEMS = 50
FREE_HISTORY_DAYS = 7

PREMIUM_MAX_ALERTS = 100
PREMIUM_MAX_PORTFOLIO_ITEMS = 500
PREMIUM_HISTORY_DAYS = 365


def is_premium(tg_user_id: int) -> bool:
    """Sprint 3 заменит реальной проверкой Subscription.tier и expires_at."""
    return False


def max_alerts(tg_user_id: int) -> int:
    return PREMIUM_MAX_ALERTS if is_premium(tg_user_id) else FREE_MAX_ALERTS
