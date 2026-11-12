"""Configuration manager for AlphaQuant Dashboard."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BASE_DIR.parent
ROOT_ENV_PATH = PROJECT_ROOT / ".env"
ENV_PATH = BASE_DIR / ".env"
DASHBOARD_ENV_PATH = BASE_DIR / "dashboard" / ".env"


def _load_env_files() -> None:
    for env_file in (ROOT_ENV_PATH, ENV_PATH, DASHBOARD_ENV_PATH):
        if env_file.exists():
            load_dotenv(env_file, override=True)


_load_env_files()


class Config:
    """Manages application environment variables and runtime configurations."""

    def __init__(self) -> None:
        self.gemini_api_key: str = ""
        self.ai_model: str = "gemini-flash-lite-latest"
        self.binance_api_key: str = ""
        self.binance_api_secret: str = ""
        self.paper_balance: float = 1000.0
        self.host: str = "127.0.0.1"
        self.port: int = 8000
        self.reload()

    def reload(self) -> None:
        """Reloads settings from local environment files and environment variables."""
        _load_env_files()
        self.gemini_api_key = os.getenv("GEMINI_API_KEY", "").strip()
        self.ai_model = os.getenv("AI_MODEL", "gemini-flash-lite-latest").strip()
        self.binance_api_key = os.getenv("BINANCE_API_KEY", "").strip()
        self.binance_api_secret = os.getenv("BINANCE_API_SECRET", "").strip()
        self.paper_balance = float(os.getenv("PAPER_BALANCE", "1000.0"))
        self.host = os.getenv("HOST", "127.0.0.1")
        self.port = int(os.getenv("PORT", "8000"))

    def update_keys(
        self,
        gemini_api_key: str | None = None,
        ai_model: str | None = None,
        binance_key: str | None = None,
        binance_secret: str | None = None,
    ) -> None:
        """Updates runtime configuration keys and persists them to .env."""
        if gemini_api_key is not None:
            self.gemini_api_key = gemini_api_key.strip()
            os.environ["GEMINI_API_KEY"] = self.gemini_api_key
        if ai_model is not None:
            self.ai_model = ai_model.strip()
            os.environ["AI_MODEL"] = self.ai_model
        if binance_key is not None:
            self.binance_api_key = binance_key.strip()
            os.environ["BINANCE_API_KEY"] = self.binance_api_key
        if binance_secret is not None:
            self.binance_api_secret = binance_secret.strip()
            os.environ["BINANCE_API_SECRET"] = self.binance_api_secret

        # Persist to .env file
        lines = [
            f"GEMINI_API_KEY={self.gemini_api_key}",
            f"AI_MODEL={self.ai_model}",
            f"BINANCE_API_KEY={self.binance_api_key}",
            f"BINANCE_API_SECRET={self.binance_api_secret}",
            f"PAPER_BALANCE={self.paper_balance}",
            f"HOST={self.host}",
            f"PORT={self.port}",
        ]

        content = "\n".join(lines) + "\n"
        for env_file in (ROOT_ENV_PATH, ENV_PATH, DASHBOARD_ENV_PATH):
            try:
                with env_file.open("w", encoding="utf-8") as f:
                    f.write(content)
            except OSError:
                pass

    def to_dict(self) -> dict[str, Any]:
        """Returns serializable configuration state with masked API keys."""
        masked = ""
        if len(self.gemini_api_key) > 10:
            masked = f"{self.gemini_api_key[:6]}...{self.gemini_api_key[-4:]}"
        elif self.gemini_api_key:
            masked = "Configured"

        return {
            "has_gemini_key": bool(self.gemini_api_key),
            "gemini_key_masked": masked,
            "ai_model": self.ai_model,
            "has_binance_key": bool(self.binance_api_key),
            "paper_balance": self.paper_balance,
            "host": self.host,
            "port": self.port,
        }


config = Config()
