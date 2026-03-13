from datetime import datetime
from pydantic import BaseModel
from __future__ import annotations

class PriceEvent(BaseModel):
    symbol: str
    price: float
    volume: float
    timestamp: datetime
    source: str = "unknown"
    
    # --- THÊM 4 DÒNG NÀY (Optional) DÀNH CHO BỘ GOM NẾN ---
    open: float | None = None
    high: float | None = None
    low: float | None = None
    ticks_count: int | None = None