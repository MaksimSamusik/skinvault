import os
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from models import Base
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.environ.get("DATABASE_URL")

if DATABASE_URL:
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+asyncpg://", 1)
else:
    DB_PATH = os.environ.get("DB_PATH", "skinvault.db")
    DATABASE_URL = f"sqlite+aiosqlite:///{DB_PATH}"

IS_POSTGRES = DATABASE_URL.startswith("postgresql")

engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    **({
        "pool_size": 5,
        "max_overflow": 10,
        "pool_pre_ping": True,
    } if IS_POSTGRES else {
        "connect_args": {"check_same_thread": False},
    })
)

AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

        # Миграция: добавить колонку buy_source если её нет в таблице
        # (нужно для уже существующих БД, созданных до этого поля)
        if IS_POSTGRES:
            await conn.execute(text("""
                ALTER TABLE portfolios
                ADD COLUMN IF NOT EXISTS buy_source VARCHAR(32) DEFAULT 'steam' NOT NULL
            """))
        else:
            # SQLite не поддерживает IF NOT EXISTS в ALTER TABLE
            # Проверяем через PRAGMA
            result = await conn.execute(text("PRAGMA table_info(portfolios)"))
            columns = [row[1] for row in result.fetchall()]
            if "buy_source" not in columns:
                await conn.execute(text(
                    "ALTER TABLE portfolios ADD COLUMN buy_source VARCHAR(32) DEFAULT 'steam' NOT NULL"
                ))


async def get_session() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session