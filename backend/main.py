from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from api import admin, alerts, auth, health, inventory, me, portfolio, prices
from core.config import FRONTEND_DIR
from core.lifespan import lifespan

app = FastAPI(title="SkinVault API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(prices.router)
app.include_router(portfolio.router)
app.include_router(inventory.router)
app.include_router(auth.router)
app.include_router(admin.router)
app.include_router(me.router)
app.include_router(alerts.router)


if FRONTEND_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

    @app.get("/")
    async def serve_index():
        return FileResponse(str(FRONTEND_DIR / "index.html"))
