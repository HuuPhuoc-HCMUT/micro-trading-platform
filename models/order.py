from datetime import datetime
from pydantic import BaseModel

class Order(BaseModel):
    side: str          # "BUY" or "SELL"
    symbol: str        
    quantity: float    
    price: float       
    status: str        # "PENDING", "FILLED", or "REJECTED"
    timestamp: datetime