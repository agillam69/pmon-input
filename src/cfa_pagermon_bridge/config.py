"""Configuration loading from environment variables."""

import os
import sys
from pathlib import Path
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class Config:
    # Source
    cfa_source_url: str
    poll_interval_seconds: int
    http_connect_timeout_seconds: int
    http_read_timeout_seconds: int
    max_response_bytes: int
    user_agent: str

    # PagerMon
    pagermon_base_url: str
    pagermon_api_key: str
    pagermon_address: str
    pagermon_source: str

    # State
    state_db_path: str

    # Behaviour
    log_level: str
    max_delivery_attempts: int
    max_message_length: int
    no_message_warning_seconds: int
    dry_run: bool


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        print(f"ERROR: {name} must be an integer, got {raw!r}", file=sys.stderr)
        sys.exit(1)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes")


def load_config(env_file: str | None = None) -> Config:
    """Load configuration from environment variables.

    If *env_file* is given, load it first.  Variables already set in the
    environment take precedence over the file.
    """
    if env_file:
        path = Path(env_file)
        if path.is_file():
            load_dotenv(path, override=False)
        else:
            print(f"WARNING: env file {env_file} not found, using environment only",
                  file=sys.stderr)
    else:
        # Try default locations
        for candidate in (".env", "bridge.env"):
            p = Path(candidate)
            if p.is_file():
                load_dotenv(p, override=False)
                break

    cfg = Config(
        cfa_source_url=os.environ.get(
            "CFA_SOURCE_URL",
            "https://mazzanet.net.au/cfa/?reg=state&magickey=cfastream",
        ),
        poll_interval_seconds=_env_int("POLL_INTERVAL_SECONDS", 20),
        http_connect_timeout_seconds=_env_int("HTTP_CONNECT_TIMEOUT_SECONDS", 5),
        http_read_timeout_seconds=_env_int("HTTP_READ_TIMEOUT_SECONDS", 15),
        max_response_bytes=_env_int("MAX_RESPONSE_BYTES", 2 * 1024 * 1024),
        user_agent=os.environ.get("USER_AGENT", "CFA-PagerMon-Bridge/1.0"),
        pagermon_base_url=os.environ.get("PAGERMON_BASE_URL", "http://127.0.0.1:3000"),
        pagermon_api_key=os.environ.get("PAGERMON_API_KEY", ""),
        pagermon_address=os.environ.get("PAGERMON_ADDRESS", "9990001"),
        pagermon_source=os.environ.get("PAGERMON_SOURCE", "mazzanet-cfa"),
        state_db_path=os.environ.get("STATE_DB_PATH", "data/state.sqlite3"),
        log_level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        max_delivery_attempts=_env_int("MAX_DELIVERY_ATTEMPTS", 20),
        max_message_length=_env_int("MAX_MESSAGE_LENGTH", 2000),
        no_message_warning_seconds=_env_int("NO_MESSAGE_WARNING_SECONDS", 600),
        dry_run=_env_bool("DRY_RUN", False),
    )

    return cfg


def validate_config(cfg: Config) -> list[str]:
    """Return a list of validation error strings (empty means valid)."""
    errors: list[str] = []

    if not cfg.cfa_source_url.startswith(("http://", "https://")):
        errors.append("CFA_SOURCE_URL must start with http:// or https://")

    if cfg.poll_interval_seconds < 5:
        errors.append("POLL_INTERVAL_SECONDS must be >= 5")

    if not cfg.pagermon_base_url.startswith(("http://", "https://")):
        errors.append("PAGERMON_BASE_URL must start with http:// or https://")

    if not cfg.pagermon_api_key or cfg.pagermon_api_key == "replace_me":
        errors.append("PAGERMON_API_KEY must be set to a real API key")

    if not cfg.pagermon_address:
        errors.append("PAGERMON_ADDRESS must be set")

    if cfg.max_delivery_attempts < 1:
        errors.append("MAX_DELIVERY_ATTEMPTS must be >= 1")

    return errors
