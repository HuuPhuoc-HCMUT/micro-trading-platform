import time
import random
from datetime import datetime
from pydantic import BaseModel

class PriceEvent(BaseModel):
    symbol: str
    price: float
    volume: float
    timestamp: datetime

def generate_price_stream(symbol: str = "BTC/USDT", start_price: float = 40000.0):
    current_price = start_price
    
    print(f"Bắt đầu mô phỏng stream cho {symbol} với giá gốc: ${current_price}")

    while True:
        change_percent = random.uniform(-0.1, 0.1)
        current_price = current_price * (1 + change_percent)
        
        current_volume = random.uniform(0.5, 10.0)
        
        event = PriceEvent(
            symbol=symbol,
            price=round(current_price, 2),
            volume=round(current_volume, 4),
            timestamp=datetime.now()
        )
        
        yield event
        
        time.sleep(1)

if __name__ == "__main__":
    stream = generate_price_stream()
    
    try:
        while True:
            event = next(stream)
            print(event.model_dump_json(indent=2))
    except KeyboardInterrupt:
        print("\nĐã dừng mô phỏng.")