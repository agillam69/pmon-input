"""PagerMon delivery adapter.

Isolated behind a small interface so the payload format can be
adjusted without changing fetching, parsing, or deduplication.
"""

import calendar
import logging
import time
from dataclasses import dataclass
from typing import Optional

import requests

from .config import Config

logger = logging.getLogger(__name__)


@dataclass
class DeliveryResult:
    """Outcome of a PagerMon delivery attempt."""
    success: bool
    pagermon_id: Optional[str] = None
    error_category: Optional[str] = None  # "auth", "server", "network", "format"
    error_desc: Optional[str] = None
    is_config_error: bool = False  # True for auth/format errors - stop retrying


class PagerMonClient:
    """Sends messages to PagerMon via its confirmed REST API."""

    def __init__(self, cfg: Config) -> None:
        self._base_url = cfg.pagermon_base_url.rstrip("/")
        self._api_key = cfg.pagermon_api_key
        self._address = cfg.pagermon_address
        self._source = cfg.pagermon_source
        self._session = requests.Session()
        self._session.headers.update({
            "X-Requested-With": "XMLHttpRequest",
            "User-Agent": cfg.user_agent,
            "apikey": self._api_key,
            "Content-Type": "application/json",
        })

    def close(self) -> None:
        self._session.close()

    @property
    def endpoint(self) -> str:
        return f"{self._base_url}/api/messages"

    def build_payload(
        self,
        message_text: str,
        dispatch_date: Optional[str] = None,
        dispatch_time: Optional[str] = None,
    ) -> dict:
        """Build the JSON payload for PagerMon.

        PagerMon expects:
          address  - string (capcode, typically 7-digit)
          message  - string (the dispatch text)
          datetime - integer (unix timestamp)
          source   - string (identifier)
        """
        unix_ts = self._parse_dispatch_timestamp(dispatch_date, dispatch_time)

        return {
            "address": self._address,
            "message": message_text,
            "datetime": unix_ts,
            "source": self._source,
        }

    def deliver(
        self,
        message_text: str,
        dispatch_date: Optional[str] = None,
        dispatch_time: Optional[str] = None,
    ) -> DeliveryResult:
        """Send a message to PagerMon. Never raises."""
        payload = self.build_payload(message_text, dispatch_date, dispatch_time)

        try:
            resp = self._session.post(
                self.endpoint,
                json=payload,
                timeout=(5, 15),
            )
        except requests.exceptions.Timeout as exc:
            return DeliveryResult(
                success=False,
                error_category="network",
                error_desc=f"Timeout: {exc}",
            )
        except requests.exceptions.ConnectionError as exc:
            return DeliveryResult(
                success=False,
                error_category="network",
                error_desc=f"Connection error: {exc}",
            )
        except requests.exceptions.RequestException as exc:
            return DeliveryResult(
                success=False,
                error_category="network",
                error_desc=f"Request error: {exc}",
            )

        body = resp.text.strip()

        # Authentication failure
        if resp.status_code == 401:
            return DeliveryResult(
                success=False,
                error_category="auth",
                error_desc="Authentication failed (401). Check PAGERMON_API_KEY.",
                is_config_error=True,
            )
        if resp.status_code == 403:
            return DeliveryResult(
                success=False,
                error_category="auth",
                error_desc="Forbidden (403). Check PAGERMON_API_KEY permissions.",
                is_config_error=True,
            )

        # Rate limiting
        if resp.status_code == 429:
            return DeliveryResult(
                success=False,
                error_category="server",
                error_desc="Rate limited (429)",
            )

        # Server error
        if resp.status_code >= 500:
            # Check for the specific "missing fields" error
            if "address or message missing" in body.lower():
                return DeliveryResult(
                    success=False,
                    error_category="format",
                    error_desc="PagerMon rejected payload: address or message missing",
                    is_config_error=True,
                )
            return DeliveryResult(
                success=False,
                error_category="server",
                error_desc=f"Server error ({resp.status_code}): {body[:200]}",
            )

        # Non-200 unexpected
        if resp.status_code != 200:
            return DeliveryResult(
                success=False,
                error_category="server",
                error_desc=f"Unexpected status {resp.status_code}: {body[:200]}",
            )

        # 200 responses
        if body == "Ignoring duplicate":
            logger.info("PagerMon already has this message (duplicate)")
            return DeliveryResult(success=True, pagermon_id="duplicate")

        if body == "Ignoring filtered":
            logger.info("PagerMon filtered this message")
            return DeliveryResult(success=True, pagermon_id="filtered")

        # Success - body should be the new message ID
        return DeliveryResult(success=True, pagermon_id=body)

    def check_reachable(self) -> tuple[bool, str]:
        """Check if PagerMon is reachable without injecting a message.

        Uses GET on the messages endpoint.  Returns (ok, message).
        """
        try:
            resp = self._session.get(
                self.endpoint,
                timeout=(5, 10),
            )
            if resp.status_code in (200, 401):
                return True, f"PagerMon reachable (HTTP {resp.status_code})"
            return False, f"Unexpected status {resp.status_code}"
        except requests.exceptions.RequestException as exc:
            return False, f"Not reachable: {exc}"

    @staticmethod
    def _parse_dispatch_timestamp(
        dispatch_date: Optional[str],
        dispatch_time: Optional[str],
    ) -> int:
        """Convert dispatch date/time strings to a unix timestamp.

        Returns current time if parsing fails.
        """
        if not dispatch_date or not dispatch_time:
            return int(time.time())
        try:
            dt_str = f"{dispatch_date} {dispatch_time}"
            t = time.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
            return int(calendar.timegm(t))
        except (ValueError, OverflowError):
            return int(time.time())
