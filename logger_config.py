import logging

RESET = "\033[0m"
COLORS = {
    logging.DEBUG: "\033[36m",       # Cyan
    logging.INFO: "\033[32m",        # Green
    logging.WARNING: "\033[33m",     # Yellow
    logging.ERROR: "\033[31m",       # Red
    logging.CRITICAL: "\033[1;31m",  # Bold Red
}


class ColorFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        color = COLORS.get(record.levelno, RESET)
        record.levelname = f"{color}{record.levelname}{RESET}"
        record.msg = f"{color}{record.msg}{RESET}"
        return super().format(record)


def setup_logging(level: int = logging.INFO) -> None:
    """Configure root logger with colored console output."""
    handler = logging.StreamHandler()
    handler.setFormatter(
        ColorFormatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )
    logging.basicConfig(level=level, handlers=[handler])
