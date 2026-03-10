from datetime import datetime

from pydantic import BaseModel


class PriceEvent(BaseModel):
    symbol: str
    price: float
    volume: float
    timestamp: datetime
    source: str
