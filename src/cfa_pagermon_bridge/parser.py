"""Extract and validate CFA dispatch messages from HTML."""

import hashlib
import logging
import re
from dataclasses import dataclass
from typing import Optional

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# Compiled validation regex.
# Matches: (ID) HH:MM:SS YYYY-MM-DD <body> [ID]
# where opening and closing identifiers must match.
_DISPATCH_RE = re.compile(
    r"^\(([A-Z0-9]{2,12})\)\s+"       # Opening identifier e.g. (DROM)
    r"\d{2}:\d{2}:\d{2}\s+"           # Time HH:MM:SS
    r"\d{4}-\d{2}-\d{2}\s+"           # Date YYYY-MM-DD
    r".+"                              # Message body (at least one char)
    r"\[(\1)\]$"                       # Closing identifier must match opening
)

# Timestamp extraction (for the source dispatch time)
_TIMESTAMP_RE = re.compile(
    r"^\([A-Z0-9]{2,12}\)\s+"
    r"(\d{2}):(\d{2}):(\d{2})\s+"
    r"(\d{4})-(\d{2})-(\d{2})"
)


@dataclass(frozen=True)
class DispatchMessage:
    """A validated dispatch message extracted from the CFA page."""
    text: str
    identifier: str
    dispatch_time: Optional[str]  # "HH:MM:SS" from the message
    dispatch_date: Optional[str]  # "YYYY-MM-DD" from the message
    message_hash: str             # SHA-256 hex digest of normalized text


def _normalize_text(raw: str) -> str:
    """Collapse whitespace, trim, preserve punctuation and case."""
    return re.sub(r"\s+", " ", raw).strip()


def _compute_hash(text: str) -> str:
    """SHA-256 hex digest of the normalized message text."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _extract_timestamp(text: str) -> tuple[Optional[str], Optional[str]]:
    """Extract time and date strings from a validated dispatch message."""
    m = _TIMESTAMP_RE.match(text)
    if not m:
        return None, None
    hh, mm, ss = m.group(1), m.group(2), m.group(3)
    yyyy, mo, dd = m.group(4), m.group(5), m.group(6)
    # Basic plausibility checks
    if not (0 <= int(hh) <= 23 and 0 <= int(mm) <= 59 and 0 <= int(ss) <= 59):
        return None, None
    if not (1 <= int(mo) <= 12 and 1 <= int(dd) <= 31):
        return None, None
    return f"{hh}:{mm}:{ss}", f"{yyyy}-{mo}-{dd}"


def parse_messages(
    html: str,
    max_message_length: int = 2000,
) -> list[DispatchMessage]:
    """Parse HTML and return validated dispatch messages in source order.

    Returns an empty list if no valid messages are found.  Never raises
    on malformed input.
    """
    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception:
        logger.warning("Failed to parse HTML")
        return []

    seen_hashes: set[str] = set()
    results: list[DispatchMessage] = []

    for strong in soup.find_all("strong"):
        # Get all text content, joining nested nodes with spaces
        raw = strong.get_text(separator=" ", strip=False)
        text = _normalize_text(raw)

        if not text:
            continue

        # Length check
        if len(text) > max_message_length:
            logger.debug("Skipping oversized element (%d chars)", len(text))
            continue

        # Regex validation
        m = _DISPATCH_RE.match(text)
        if not m:
            continue

        identifier = m.group(1)

        # Extract timestamp
        dispatch_time, dispatch_date = _extract_timestamp(text)

        # Deduplicate within a single page
        msg_hash = _compute_hash(text)
        if msg_hash in seen_hashes:
            continue
        seen_hashes.add(msg_hash)

        results.append(DispatchMessage(
            text=text,
            identifier=identifier,
            dispatch_time=dispatch_time,
            dispatch_date=dispatch_date,
            message_hash=msg_hash,
        ))

    return results
