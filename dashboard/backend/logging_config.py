"""Enterprise logging configuration with log rotation and credential redaction."""

from __future__ import annotations

import logging
import os
import re
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
LOG_DIR = PROJECT_ROOT / "runtime" / "logs"

_INITIALIZED = False


class CredentialFilter(logging.Filter):
    """Filter that scrubs API keys, auth tokens, and secrets from log messages."""

    PATTERNS = [
        re.compile(r"AIza[0-9A-Za-z\-_]{30,45}"),  # Google / Gemini API key
        re.compile(r"(?:Bearer\s+)[A-Za-z0-9_\-\.]{15,}", re.IGNORECASE),
        re.compile(r"((?:api[_-]?key|secret|password|token)\s*[:=]\s*['\"]?)([^'\"\s]{6,})(['\"]?)", re.IGNORECASE),
    ]

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = self._redact(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {k: self._redact(v) if isinstance(v, str) else v for k, v in record.args.items()}
            elif isinstance(record.args, (list, tuple)):
                record.args = tuple(self._redact(v) if isinstance(v, str) else v for v in record.args)
        return True

    @classmethod
    def _redact(cls, text: str) -> str:
        text = re.sub(r"AIza[0-9A-Za-z\-_]{30,45}", "[REDACTED_KEY]", text)
        text = re.sub(r"(Bearer\s+)[A-Za-z0-9_\-\.]{15,}", r"\1[REDACTED_TOKEN]", text, flags=re.IGNORECASE)
        text = re.sub(r"((?:api[_-]?key|secret|password|token)\s*[:=]\s*['\"]?)([^'\"\s]{6,})(['\"]?)", r"\1***REDACTED***\3", text, flags=re.IGNORECASE)
        return text


def setup_logging(log_level: int = logging.INFO) -> None:
    """Configures structured, rotating file logging and console output."""
    global _INITIALIZED
    if _INITIALIZED:
        return
    _INITIALIZED = True

    LOG_DIR.mkdir(parents=True, exist_ok=True)

    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    cred_filter = CredentialFilter()

    # 1. Console Handler (only if stdout is available, e.g. not running under pythonw)
    if sys.stdout is not None:
        has_console = any(isinstance(h, logging.StreamHandler) and not isinstance(h, RotatingFileHandler) for h in root_logger.handlers)
        if not has_console:
            console_handler = logging.StreamHandler(sys.stdout)
            console_handler.setLevel(log_level)
            console_handler.setFormatter(formatter)
            console_handler.addFilter(cred_filter)
            root_logger.addHandler(console_handler)

    # Helper to create rotating file handler
    def _create_handler(filename: str, level: int = logging.INFO) -> RotatingFileHandler:
        filepath = LOG_DIR / filename
        handler = RotatingFileHandler(
            filepath,
            maxBytes=10 * 1024 * 1024,  # 10 MB per file
            backupCount=5,               # 5 rolling backup files
            encoding="utf-8",
        )
        handler.setLevel(level)
        handler.setFormatter(formatter)
        handler.addFilter(cred_filter)
        return handler

    # 2. server.log (Root, FastAPI, Uvicorn)
    server_handler = _create_handler("server.log", level=logging.INFO)
    root_logger.addHandler(server_handler)

    # 3. errors.log (WARNING and above from any component)
    errors_handler = _create_handler("errors.log", level=logging.WARNING)
    root_logger.addHandler(errors_handler)

    # 4. binance.log (Market data, klines, scanner)
    binance_handler = _create_handler("binance.log", level=logging.INFO)
    for name in ("market_data", "market_scanner"):
        l = logging.getLogger(name)
        l.addHandler(binance_handler)

    # 5. websocket.log (Websocket feed and real-time broadcasts)
    ws_handler = _create_handler("websocket.log", level=logging.INFO)
    ws_logger = logging.getLogger("websocket")
    ws_logger.addHandler(ws_handler)

    # 6. ai.log (Gemini / AI assistant interactions)
    ai_handler = _create_handler("ai.log", level=logging.INFO)
    ai_logger = logging.getLogger("ai_assistant")
    ai_logger.addHandler(ai_handler)

    # 7. trades.log (Paper trading and signal tracking)
    trades_handler = _create_handler("trades.log", level=logging.INFO)
    for name in ("trades", "signal_tracker"):
        l = logging.getLogger(name)
        l.addHandler(trades_handler)

    # Quiet external third-party chatty loggers if needed
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
