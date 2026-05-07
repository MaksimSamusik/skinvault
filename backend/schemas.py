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
