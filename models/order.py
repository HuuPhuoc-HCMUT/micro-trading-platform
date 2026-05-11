from datetime import datetime
from pydantic import BaseModel

class Order(BaseModel):
    side: str          # "BUY" or "SELL"
    symbol: str        
    quantity: float    
    price: float       
    status: str        # "PENDING", "FILLED", or "REJECTED"
    timestamp: datetime
    trade_pnl: float | None = None
    fee: float = 0.0
    tax: float = 0.0
    slippage: float = 0.0
    reason: str | None = None
