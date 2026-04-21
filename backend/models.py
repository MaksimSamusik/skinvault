from sqlalchemy import Column, String, Float, Integer, BigInteger
from sqlalchemy.orm import DeclarativeBase

class Base(DeclarativeBase):
    pass

class Portfolio(Base):
    __tablename__ = "portfolios"
    steam_id           = Column(String, primary_key=True)
    market_hash_name   = Column(String, primary_key=True)
    buy_price          = Column(Float, nullable=False)
    quantity           = Column(Integer, default=1)
    added_at           = Column(BigInteger, default=0)

class PriceCache(Base):
    __tablename__ = "price_cache"
    market_hash_name   = Column(String, primary_key=True)
    price_usd          = Column(Float, nullable=True)
    image_url          = Column(String, nullable=True)
    fetched_at         = Column(BigInteger, default=0)

class PriceHistory(Base):
    __tablename__ = "price_history"
    id                 = Column(Integer, primary_key=True, autoincrement=True)
    market_hash_name   = Column(String, nullable=False)
    price_usd          = Column(Float, nullable=False)
    recorded_at        = Column(BigInteger, default=0)
