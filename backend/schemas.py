from pydantic import BaseModel, Field


class AddItemRequest(BaseModel):
    steam_id: str
    market_hash_name: str
    buy_price: float = Field(ge=0)
    quantity: int = Field(default=1, ge=1)
    buy_source: str = "steam"


class UpdateItemRequest(BaseModel):
    buy_price: float = Field(ge=0)
    quantity: int = Field(default=1, ge=1)
    buy_source: str = "steam"


class LinkSteamRequest(BaseModel):
    steam_id: str = Field(min_length=1, max_length=32)


class UpdateMeRequest(BaseModel):
    locale: str | None = Field(default=None, max_length=8)
    currency: str | None = Field(default=None, max_length=8)
