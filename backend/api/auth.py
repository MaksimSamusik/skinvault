import re
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from core.config import STEAM_OPENID_LOGIN, STEAM_OPENID_RETURN

router = APIRouter(prefix="/api/auth", tags=["auth"])

_CLAIMED_RE = re.compile(r"/openid/id/(\d+)")


def _default_return_url(request: Request) -> str:
    """Если STEAM_OPENID_RETURN не задан — собираем из request (учитываем proxy headers)."""
    if STEAM_OPENID_RETURN:
        return STEAM_OPENID_RETURN
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    host   = request.headers.get("x-forwarded-host", request.headers.get("host", request.url.netloc))
    return f"{scheme}://{host}/api/auth/steam/callback"


@router.get("/steam")
async def steam_login(request: Request):
    return_url = request.query_params.get("return_url") or _default_return_url(request)
    params = {
        "openid.ns":         "http://specs.openid.net/auth/2.0",
        "openid.mode":       "checkid_setup",
        "openid.return_to":  return_url,
        "openid.realm":      return_url.rsplit("/", 1)[0],
        "openid.identity":   "http://specs.openid.net/auth/2.0/identifier_select",
        "openid.claimed_id": "http://specs.openid.net/auth/2.0/identifier_select",
    }
    return RedirectResponse(url=f"{STEAM_OPENID_LOGIN}?{urlencode(params)}")


@router.get("/steam/callback")
async def steam_callback(request: Request):
    params = dict(request.query_params)
    if params.get("openid.mode") != "id_res":
        raise HTTPException(status_code=400, detail="Авторизация отменена")

    match = _CLAIMED_RE.search(params.get("openid.claimed_id", ""))
    if not match:
        raise HTTPException(status_code=400, detail="Не удалось получить SteamID")

    steam_id = match.group(1)
    return_to = params.get("openid.return_to", "/")
    base = return_to.rsplit("/", 1)[0]
    return RedirectResponse(url=f"{base}/?steam_id={steam_id}")
