"""HTTP fetcher for the CFA State Incidents page."""

import logging
import random
import time
from dataclasses import dataclass
from typing import Optional

import requests

from .config import Config

logger = logging.getLogger(__name__)

# Backoff constants
_BASE_BACKOFF_SECONDS = 2
_MAX_BACKOFF_SECONDS = 300  # 5 minutes
_JITTER_FRACTION = 0.25


@dataclass
class FetchResult:
    """Result of a page fetch attempt."""
    success: bool
    html: Optional[str] = None
    status_code: Optional[int] = None
    duration_ms: float = 0.0
    error: Optional[str] = None


class Fetcher:
    """Polls the CFA source URL with backoff on failure."""

    def __init__(self, cfg: Config) -> None:
        self._url = cfg.cfa_source_url
        self._connect_timeout = cfg.http_connect_timeout_seconds
        self._read_timeout = cfg.http_read_timeout_seconds
        self._max_bytes = cfg.max_response_bytes
        self._session = requests.Session()
        self._session.headers["User-Agent"] = cfg.user_agent
        self._consecutive_failures = 0

    def close(self) -> None:
        self._session.close()

    @property
    def backoff_seconds(self) -> float:
        """Current backoff delay based on consecutive failure count."""
        if self._consecutive_failures == 0:
            return 0.0
        base = min(
            _BASE_BACKOFF_SECONDS * (2 ** (self._consecutive_failures - 1)),
            _MAX_BACKOFF_SECONDS,
        )
        jitter = base * _JITTER_FRACTION * random.random()
        return base + jitter

    def fetch(self) -> FetchResult:
        """Fetch the page. Returns a FetchResult (never raises)."""
        start = time.monotonic()
        try:
            resp = self._session.get(
                self._url,
                timeout=(self._connect_timeout, self._read_timeout),
                allow_redirects=True,
                stream=True,
            )

            # Validate final URL scheme after redirects
            if resp.url and not resp.url.startswith(("http://", "https://")):
                self._consecutive_failures += 1
                elapsed = (time.monotonic() - start) * 1000
                return FetchResult(
                    success=False,
                    duration_ms=elapsed,
                    error=f"Unexpected redirect scheme: {resp.url.split(':')[0]}",
                )

            # Read with size cap
            chunks: list[bytes] = []
            total = 0
            for chunk in resp.iter_content(chunk_size=8192):
                total += len(chunk)
                if total > self._max_bytes:
                    resp.close()
                    self._consecutive_failures += 1
                    elapsed = (time.monotonic() - start) * 1000
                    return FetchResult(
                        success=False,
                        duration_ms=elapsed,
                        error=f"Response exceeds {self._max_bytes} byte limit",
                    )
                chunks.append(chunk)

            elapsed = (time.monotonic() - start) * 1000

            if resp.status_code != 200:
                self._consecutive_failures += 1
                return FetchResult(
                    success=False,
                    status_code=resp.status_code,
                    duration_ms=elapsed,
                    error=f"HTTP {resp.status_code}",
                )

            content = b"".join(chunks)
            encoding = resp.encoding or "utf-8"
            html = content.decode(encoding, errors="replace")

            self._consecutive_failures = 0
            return FetchResult(
                success=True,
                html=html,
                status_code=200,
                duration_ms=elapsed,
            )

        except requests.exceptions.Timeout as exc:
            self._consecutive_failures += 1
            elapsed = (time.monotonic() - start) * 1000
            return FetchResult(
                success=False,
                duration_ms=elapsed,
                error=f"Timeout: {exc}",
            )
        except requests.exceptions.ConnectionError as exc:
            self._consecutive_failures += 1
            elapsed = (time.monotonic() - start) * 1000
            return FetchResult(
                success=False,
                duration_ms=elapsed,
                error=f"Connection error: {exc}",
            )
        except requests.exceptions.RequestException as exc:
            self._consecutive_failures += 1
            elapsed = (time.monotonic() - start) * 1000
            return FetchResult(
                success=False,
                duration_ms=elapsed,
                error=f"Request error: {exc}",
            )
