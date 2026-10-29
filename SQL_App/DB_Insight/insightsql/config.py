from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[1]
load_dotenv(ROOT_DIR / ".env")


@dataclass(frozen=True)
class Settings:
    database_path: Path
    history_path: Path
    openai_api_key: str | None
    openai_model: str
    openai_chat_completions_url: str
    host: str
    port: int
    username: str | None
    password: str | None
    allowed_hosts: tuple[str, ...]
    max_rows: int
    query_timeout_seconds: float
    rate_limit_per_minute: int

    @classmethod
    def from_env(cls) -> Settings:
        database_path = Path(os.getenv("INSIGHTSQL_DATABASE_PATH", "employee_management.db"))
        history_path = Path(os.getenv("INSIGHTSQL_HISTORY_PATH", "data/query_history.sqlite3"))
        if not database_path.is_absolute():
            database_path = ROOT_DIR / database_path
        if not history_path.is_absolute():
            history_path = ROOT_DIR / history_path

        username = os.getenv("INSIGHTSQL_USERNAME") or None
        password = os.getenv("INSIGHTSQL_PASSWORD") or None
        if bool(username) != bool(password):
            raise ValueError("Set both INSIGHTSQL_USERNAME and INSIGHTSQL_PASSWORD, or leave both empty.")

        host = os.getenv("INSIGHTSQL_HOST", "127.0.0.1")
        if host not in {"127.0.0.1", "localhost", "::1"} and not (username and password):
            raise ValueError("Non-loopback binding requires INSIGHTSQL_USERNAME and INSIGHTSQL_PASSWORD.")

        max_rows = int(os.getenv("INSIGHTSQL_MAX_ROWS", "500"))
        timeout = float(os.getenv("INSIGHTSQL_QUERY_TIMEOUT_SECONDS", "2.0"))
        rate_limit = int(os.getenv("INSIGHTSQL_RATE_LIMIT_PER_MINUTE", "30"))
        if not 1 <= max_rows <= 1000:
            raise ValueError("INSIGHTSQL_MAX_ROWS must be between 1 and 1000.")
        if not 0.1 <= timeout <= 10:
            raise ValueError("INSIGHTSQL_QUERY_TIMEOUT_SECONDS must be between 0.1 and 10.")
        if not 1 <= rate_limit <= 300:
            raise ValueError("INSIGHTSQL_RATE_LIMIT_PER_MINUTE must be between 1 and 300.")

        allowed_hosts = tuple(
            value.strip()
            for value in os.getenv("INSIGHTSQL_ALLOWED_HOSTS", "localhost,127.0.0.1,testserver").split(",")
            if value.strip()
        )
        return cls(
            database_path=database_path.resolve(),
            history_path=history_path.resolve(),
            openai_api_key=os.getenv("OPENAI_API_KEY") or None,
            openai_model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            openai_chat_completions_url=os.getenv(
                "OPENAI_CHAT_COMPLETIONS_URL", "https://api.openai.com/v1/chat/completions"
            ),
            host=host,
            port=int(os.getenv("INSIGHTSQL_PORT", "8000")),
            username=username,
            password=password,
            allowed_hosts=allowed_hosts,
            max_rows=max_rows,
            query_timeout_seconds=timeout,
            rate_limit_per_minute=rate_limit,
        )