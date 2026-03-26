import csv
import io
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

import requests

from models.price_event import PriceEvent


REQUEST_TIMEOUT = 30


def _read_bytes(file_path: str | None, url: str | None) -> bytes:
    if file_path:
        return Path(file_path).read_bytes()
    if url:
        response = requests.get(url, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        return response.content
    raise ValueError("Exness source requires either --exness-file or --exness-url.")


def _extract_csv_text(raw_bytes: bytes, file_path: str | None) -> str:
    is_zip = raw_bytes[:2] == b"PK" or (file_path and file_path.lower().endswith(".zip"))
    if not is_zip:
        return raw_bytes.decode("utf-8-sig")

    with zipfile.ZipFile(io.BytesIO(raw_bytes)) as archive:
        csv_names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
        if not csv_names:
            raise ValueError("Exness ZIP does not contain a CSV file.")
        with archive.open(csv_names[0]) as handle:
            return handle.read().decode("utf-8-sig")


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _normalize_row(row: dict[str, str]) -> dict[str, str]:
    return {str(key).strip().lower(): str(value).strip() for key, value in row.items() if key is not None}


def iter_exness_ticks(
    symbols: list[str] | None = None,
    file_path: str | None = None,
    url: str | None = None,
) -> Iterator[PriceEvent]:
    raw_bytes = _read_bytes(file_path=file_path, url=url)
    csv_text = _extract_csv_text(raw_bytes=raw_bytes, file_path=file_path)
    allowed = {symbol.upper() for symbol in symbols or []}

    reader = csv.DictReader(io.StringIO(csv_text))
    for raw_row in reader:
        row = _normalize_row(raw_row)
        symbol = row.get("symbol", "").upper()
        timestamp = row.get("timestamp", "")
        bid = row.get("bid", "")
        ask = row.get("ask", "")
        if not symbol or not timestamp or not bid or not ask:
            continue
        if allowed and symbol not in allowed:
            continue

        bid_price = float(bid)
        ask_price = float(ask)
        yield PriceEvent(
            symbol=symbol,
            price=(bid_price + ask_price) / 2.0,
            volume=1.0,
            timestamp=_parse_timestamp(timestamp),
            source="exness",
        )
