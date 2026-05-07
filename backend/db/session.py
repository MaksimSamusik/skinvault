import sys
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.config import DATABASE_DIR, DATABASE_URL as _ENV_DATABASE_URL, PROJECT_ROOT
from db.models import Base


def _resolve_database_url() -> str:
    url = _ENV_DATABASE_URL

    if not url:
        DATABASE_DIR.mkdir(parents=True, exist_ok=True)
        url = f"sqlite+aiosqlite:///{DATABASE_DIR / 'app.db'}"
        print(f"INFO: DATABASE_URL не задан, используем SQLite: {url}", file=sys.stderr)

    if url.startswith("sqlite"):
        prefix = "sqlite+aiosqlite:///"
        if url.startswith(prefix):
            rel = url[len(prefix):]
            db_path = Path(rel)
            if not db_path.is_absolute():
                db_path = PROJECT_ROOT / rel
                db_path.parent.mkdir(parents=True, exist_ok=True)
                url = f"sqlite+aiosqlite:///{db_path}"
        return url

    url = url.replace("postgres://", "postgresql+asyncpg://", 1)
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


DATABASE_URL = _resolve_database_url()
IS_POSTGRES = "postgresql" in DATABASE_URL

if IS_POSTGRES:
    print(f"INFO: DB подключение: {DATABASE_URL[:40]}...", file=sys.stderr)
    engine = create_async_engine(
        DATABASE_URL,
        echo=False,
        pool_size=5,
        max_overflow=10,
        pool_pre_ping=True,
    )
else:
    print(f"INFO: DB подключение (SQLite): {DATABASE_URL}", file=sys.stderr)
    engine = create_async_engine(
        DATABASE_URL,
        echo=False,
        connect_args={"check_same_thread": False},
    )

AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def init_db() -> None:
    async with engine.begin() as conn:
        if not IS_POSTGRES:
            await _migrate_sqlite_portfolio(conn)
        await conn.run_sync(Base.metadata.create_all)
        if IS_POSTGRES:
            await conn.execute(text(
                "ALTER TABLE portfolios "
                "ADD COLUMN IF NOT EXISTS buy_source VARCHAR(32) DEFAULT 'steam' NOT NULL"
            ))
            await _migrate_postgres_portfolio_id(conn)


async def _migrate_sqlite_portfolio(conn) -> None:
    """SQLite: PK был (steam_id, name), теперь id autoincrement.
    Меняем через rename+create+copy+drop."""
    res = await conn.execute(text(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='portfolios'"
    ))
    if not res.fetchone():
        return
    cols = await conn.execute(text("PRAGMA table_info(portfolios)"))
    if any(r[1] == "id" for r in cols.fetchall()):
        return
    print("[migration] portfolios: SQLite → добавляем id PK", flush=True)
    await conn.execute(text("ALTER TABLE portfolios RENAME TO portfolios_old"))
    await conn.run_sync(Base.metadata.create_all)
    await conn.execute(text(
        "INSERT INTO portfolios (steam_id, market_hash_name, buy_price, quantity, added_at, buy_source) "
        "SELECT steam_id, market_hash_name, buy_price, quantity, "
        "       COALESCE(added_at, 0), COALESCE(buy_source, 'steam') "
        "FROM portfolios_old"
    ))
    await conn.execute(text("DROP TABLE portfolios_old"))


async def _migrate_postgres_portfolio_id(conn) -> None:
    """Postgres: добавляем id SERIAL PK если его нет."""
    res = await conn.execute(text(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_name='portfolios' AND column_name='id'"
    ))
    if res.fetchone():
        return
    print("[migration] portfolios: Postgres → добавляем id SERIAL PK", flush=True)
    await conn.execute(text("ALTER TABLE portfolios DROP CONSTRAINT IF EXISTS portfolios_pkey"))
    await conn.execute(text("ALTER TABLE portfolios ADD COLUMN id SERIAL PRIMARY KEY"))


async def get_session() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session
