import zipfile
from pathlib import Path

from data_source.exness_history import iter_exness_ticks


def test_iter_exness_ticks_reads_zip_csv() -> None:
    csv_content = "Symbol,Timestamp,Bid,Ask\nXAUUSD,2026-03-24T00:00:00.000Z,3020.10,3020.40\n"
    archive_path = Path("tests") / "_tmp_exness_ticks.zip"

    try:
        with zipfile.ZipFile(archive_path, "w") as archive:
            archive.writestr("ticks.csv", csv_content)

        events = list(iter_exness_ticks(symbols=["XAUUSD"], file_path=str(archive_path)))

        assert len(events) == 1
        assert events[0].symbol == "XAUUSD"
        assert events[0].price == 3020.25
        assert events[0].volume == 1.0
    finally:
        archive_path.unlink(missing_ok=True)
