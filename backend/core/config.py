import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(override=False)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
FRONTEND_DIR = PROJECT_ROOT / "frontend"
DATABASE_DIR = PROJECT_ROOT / "database"

DATABASE_URL = os.environ.get("DATABASE_URL", "")

STEAM_API_KEY = os.getenv("STEAM_API_KEY", "")
# Если пусто — auth.py соберёт из request headers (правильное поведение за прокси Railway).
# Задавать вручную имеет смысл только при тестах с tunnel/ngrok когда Host неверный.
STEAM_OPENID_RETURN = os.getenv("STEAM_OPENID_RETURN", "")
CSGOMARKET_API_KEY = os.getenv("CSGOMARKET_API_KEY", "")
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")

STEAM_PRICE_URL      = "https://steamcommunity.com/market/priceoverview/"
STEAM_SEARCH_URL     = "https://steamcommunity.com/market/search/render/"
STEAM_INVENTORY_URL  = "https://steamcommunity.com/inventory/{steam_id}/730/2"
STEAM_RESOLVE_URL    = "https://api.steampowered.com/ISteamUser/ResolveVanityURL/v1/"
STEAM_CDN            = "https://community.akamai.steamstatic.com/economy/image/"
STEAM_OPENID_LOGIN   = "https://steamcommunity.com/openid/login"
MARKET_CSGO_ITEM_URL = "https://market.csgo.com/api/v2/search-item-by-hash-name-specific"

LISSKINS_HOMEPAGE = "https://lis-skins.com/"
LISSKINS_BULK_URL = "https://lis-skins.com/market_export_json/api_csgo_full.json"

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

CACHE_TTL = 3600
LISSKINS_REFRESH_INTERVAL = 3600
PRICE_REFRESH_INTERVAL = 1800

WARMUP_CONCURRENCY = 5
STEAM_RATE_LIMIT_PER_MIN = 18
INVENTORY_PAGE_SIZE = 1000
INVENTORY_MAX_PAGES = 20
