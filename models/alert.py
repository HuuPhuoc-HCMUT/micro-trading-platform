from datetime import datetime

from pydantic import BaseModel


class Alert(BaseModel):
    signal_type: str
    symbol: str
    severity: str
    message: str
    triggered_at: datetime
