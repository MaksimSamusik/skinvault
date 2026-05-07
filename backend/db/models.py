from sqlalchemy import BigInteger, Column, Float, Index, Integer, String
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class User(Base):
    """Связка Telegram tg_user_id ↔ Steam steam_id для алертов и подписки."""
    __tablename__ = "users"

    tg_user_id    = Column(BigInteger, primary_key=True)
    steam_id      = Column(String(32),  nullable=True)
    locale        = Column(String(8),   nullable=False, default="ru")
    currency      = Column(String(8),   nullable=False, default="USD")
    created_at    = Column(BigInteger,  nullable=False, default=0)
    last_seen_at  = Column(BigInteger,  nullable=False, default=0)

    __table_args__ = (
        Index("ix_users_steam_id", "steam_id"),
    )


class Portfolio(Base):
    """Один ряд = один лот покупки. На (steam_id, market_hash_name) может быть много лотов."""
    __tablename__ = "portfolios"

    id                = Column(Integer, primary_key=True, autoincrement=True)
    steam_id          = Column(String(32),  nullable=False)
    market_hash_name  = Column(String(255), nullable=False)
    buy_price         = Column(Float,       nullable=False)
    quantity          = Column(Integer,     nullable=False, default=1)
    added_at          = Column(BigInteger,  nullable=False, default=0)
    buy_source        = Column(String(32),  nullable=False, default="steam")

    __table_args__ = (
        Index("ix_portfolios_steam", "steam_id"),
        Index("ix_portfolios_steam_name", "steam_id", "market_hash_name"),
    )


class PriceCache(Base):
    __tablename__ = "price_cache"

    market_hash_name   = Column(String(255), primary_key=True)
    price_steam        = Column(Float, nullable=True)
    price_lisskins     = Column(Float, nullable=True)
    price_market_csgo  = Column(Float, nullable=True)
    image_url          = Column(String(512), nullable=True)
    fetched_at         = Column(BigInteger, nullable=False, default=0)


class PriceHistory(Base):
    __tablename__ = "price_history"

    id                = Column(Integer, primary_key=True, autoincrement=True)
    market_hash_name  = Column(String(255), nullable=False, index=True)
    price_usd         = Column(Float, nullable=False)
    source            = Column(String(32), nullable=False, default="steam")
    recorded_at       = Column(BigInteger, nullable=False, default=0)

    __table_args__ = (
        Index("ix_history_name_time", "market_hash_name", "recorded_at"),
    )


class PriceAlert(Base):
    """Прайс-алерт юзера: уведомить когда цена X пересекает threshold в направлении condition."""
    __tablename__ = "price_alerts"

    id                = Column(Integer,    primary_key=True, autoincrement=True)
    tg_user_id        = Column(BigInteger, nullable=False, index=True)
    market_hash_name  = Column(String(255), nullable=False)
    condition         = Column(String(8),  nullable=False)
    threshold         = Column(Float,      nullable=False)
    source            = Column(String(32), nullable=False, default="best")
    is_active         = Column(Integer,    nullable=False, default=1)
    created_at        = Column(BigInteger, nullable=False, default=0)
    last_fired_at     = Column(BigInteger, nullable=True)
    fired_count       = Column(Integer,    nullable=False, default=0)

    __table_args__ = (
        Index("ix_alerts_user_active", "tg_user_id", "is_active"),
        Index("ix_alerts_name", "market_hash_name"),
    )
