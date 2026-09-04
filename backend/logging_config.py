import logging
import os
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path


LOGGER_NAME = "ducklingo"
_configured_root: Path | None = None
_SECRET_RE = re.compile(r"(?i)(api[_ -]?key|authorization|cookie|password)(\s*[:=]\s*)[^\s,;]+")


def _redact(value: object) -> object:
    return _SECRET_RE.sub(r"\1\2[REDACTED]", value) if isinstance(value, str) else value


def summarize_error(exc: BaseException, limit: int = 200) -> str:
    summary = _redact(str(exc) or type(exc).__name__)
    return str(summary).replace("\r", " ").replace("\n", " ")[:limit]


class _RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = _redact(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(_redact(arg) for arg in record.args)
        return True


def shutdown_logging() -> None:
    global _configured_root
    app_logger = get_logger()
    for handler in app_logger.handlers[:]:
        try:
            handler.close()
        except Exception:
            pass
        app_logger.removeHandler(handler)
    _configured_root = None


def get_logger(name: str | None = None) -> logging.Logger:
    return logging.getLogger(f"{LOGGER_NAME}.{name}" if name else LOGGER_NAME)


def configure_logging(root: str | Path = ".") -> logging.Logger:
    """Configure console + rotating file logging once per directory."""
    global _configured_root
    root = Path(root).resolve()
    app_logger = get_logger()
    if _configured_root == root and app_logger.handlers:
        return app_logger
    shutdown_logging()

    log_dir = root / "data" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    level = getattr(logging, os.environ.get("DUCKLINGO_LOG_LEVEL", "INFO").upper(), logging.INFO)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    console.addFilter(_RedactFilter())
    file_handler = RotatingFileHandler(log_dir / "ducklingo.log", maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8")
    file_handler.setFormatter(formatter)
    file_handler.addFilter(_RedactFilter())

    app_logger.setLevel(level)
    app_logger.propagate = False
    app_logger.addHandler(console)
    app_logger.addHandler(file_handler)
    _configured_root = root
    return app_logger
