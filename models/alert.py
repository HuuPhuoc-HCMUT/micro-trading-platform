from datetime import datetime

from pydantic import BaseModel, Field


class Alert(BaseModel):
    signal_type: str
    symbol: str
    severity: str
    message: str
    triggered_at: datetime
    direction: str | None = None
    confidence: float = 0.0
    score: float = 0.0
    expected_return: float = 0.0
    metadata: dict[str, float | int | str | bool] = Field(default_factory=dict)
