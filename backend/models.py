from sqlalchemy import Column, String, Float, Integer, BigInteger, Index
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class Portfolio(Base):
    __tablename__ = "portfolios"

    steam_id           = Column(String(32), primary_key=True)
    market_hash_name   = Column(String(255), primary_key=True)
    buy_price          = Column(Float, nullable=False)
    quantity           = Column(Integer, default=1, nullable=False)
    added_at           = Column(BigInteger, default=0, nullable=False)


class PriceCache(Base):
    __tablename__ = "price_cache"

    market_hash_name   = Column(String(255), primary_key=True)
    price_usd          = Column(Float, nullable=True)
    image_url          = Column(String(512), nullable=True)
    fetched_at         = Column(BigInteger, default=0, nullable=False)


class PriceHistory(Base):
    __tablename__ = "price_history"

    id                 = Column(Integer, primary_key=True, autoincrement=True)
    market_hash_name   = Column(String(255), nullable=False, index=True)
    price_usd          = Column(Float, nullable=False)
    recorded_at        = Column(BigInteger, default=0, nullable=False)

    __table_args__ = (
        Index("ix_history_name_time", "market_hash_name", "recorded_at"),
    )