import os
import sys
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from models import Base
from dotenv import load_dotenv

# override=False — Railway env vars имеют приоритет над .env файлом
load_dotenv(override=False)

DATABASE_URL = os.environ.get("DATABASE_URL")

if not DATABASE_URL:
    print("FATAL: DATABASE_URL не задан. Установи переменную окружения.", file=sys.stderr)
    sys.exit(1)

# Railway иногда отдаёт postgres:// вместо postgresql://
DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+asyncpg://", 1)

# Убеждаемся что используется asyncpg драйвер
if DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)

print(f"INFO: DB подключение: {DATABASE_URL[:40]}...", file=sys.stderr)

engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    pool_size=5,
    max_overflow=10,
    pool_pre_ping=True,
)

AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Миграция: добавить buy_source если таблица уже существовала
        await conn.execute(text("""
            ALTER TABLE portfolios
            ADD COLUMN IF NOT EXISTS buy_source VARCHAR(32) DEFAULT 'steam' NOT NULL
        """))


async def get_session() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session