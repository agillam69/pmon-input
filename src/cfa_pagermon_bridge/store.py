"""SQLite-backed durable message store for deduplication and delivery tracking."""

import logging
import sqlite3
import time
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_SCHEMA_VERSION = 1

_CREATE_TABLE = """\
CREATE TABLE IF NOT EXISTS messages (
    message_hash        TEXT PRIMARY KEY,
    message_text        TEXT NOT NULL,
    identifier          TEXT NOT NULL,
    first_seen_utc      REAL NOT NULL,
    dispatch_time       TEXT,
    dispatch_date       TEXT,
    delivery_state      TEXT NOT NULL DEFAULT 'pending',
    attempt_count       INTEGER NOT NULL DEFAULT 0,
    next_attempt_utc    REAL,
    last_attempt_utc    REAL,
    delivered_utc       REAL,
    last_error_category TEXT,
    last_error_desc     TEXT
);
"""

_CREATE_META = """\
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


@dataclass
class PendingMessage:
    """A message awaiting delivery."""
    message_hash: str
    message_text: str
    identifier: str
    dispatch_time: Optional[str]
    dispatch_date: Optional[str]
    attempt_count: int


class MessageStore:
    """Manages the bridge's own SQLite database."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None

    def open(self) -> None:
        """Open (and create if needed) the database."""
        # Ensure parent directory exists
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)

        self._conn = sqlite3.connect(
            self._db_path,
            timeout=10.0,       # busy timeout
            isolation_level=None,  # manual transaction control
        )
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=10000")
        self._init_schema()

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    def _init_schema(self) -> None:
        """Create tables if they don't exist."""
        assert self._conn is not None
        self._conn.execute("BEGIN")
        try:
            self._conn.execute(_CREATE_TABLE)
            self._conn.execute(_CREATE_META)
            # Store schema version
            self._conn.execute(
                "INSERT OR IGNORE INTO meta (key, value) VALUES (?, ?)",
                ("schema_version", str(_SCHEMA_VERSION)),
            )
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise

    def insert_if_new(
        self,
        message_hash: str,
        message_text: str,
        identifier: str,
        dispatch_time: Optional[str],
        dispatch_date: Optional[str],
    ) -> bool:
        """Insert a message as pending if its hash is not already known.

        Returns True if the message was newly inserted, False if it was
        already present (delivered, pending, or dead-lettered).
        """
        assert self._conn is not None
        now = time.time()
        try:
            self._conn.execute("BEGIN")
            self._conn.execute(
                """INSERT INTO messages
                   (message_hash, message_text, identifier,
                    first_seen_utc, dispatch_time, dispatch_date,
                    delivery_state, attempt_count, next_attempt_utc)
                   VALUES (?, ?, ?, ?, ?, ?, 'pending', 0, ?)""",
                (message_hash, message_text, identifier,
                 now, dispatch_time, dispatch_date, now),
            )
            self._conn.execute("COMMIT")
            return True
        except sqlite3.IntegrityError:
            self._conn.execute("ROLLBACK")
            return False
        except Exception:
            self._conn.execute("ROLLBACK")
            raise

    def get_pending(self, limit: int = 50) -> list[PendingMessage]:
        """Return pending messages ready for delivery, oldest first."""
        assert self._conn is not None
        now = time.time()
        rows = self._conn.execute(
            """SELECT message_hash, message_text, identifier,
                      dispatch_time, dispatch_date, attempt_count
               FROM messages
               WHERE delivery_state = 'pending'
                 AND (next_attempt_utc IS NULL OR next_attempt_utc <= ?)
               ORDER BY first_seen_utc ASC
               LIMIT ?""",
            (now, limit),
        ).fetchall()
        return [
            PendingMessage(
                message_hash=r[0],
                message_text=r[1],
                identifier=r[2],
                dispatch_time=r[3],
                dispatch_date=r[4],
                attempt_count=r[5],
            )
            for r in rows
        ]

    def mark_delivered(self, message_hash: str) -> None:
        """Mark a message as successfully delivered."""
        assert self._conn is not None
        now = time.time()
        self._conn.execute(
            """UPDATE messages
               SET delivery_state = 'delivered',
                   delivered_utc = ?,
                   last_attempt_utc = ?
               WHERE message_hash = ?""",
            (now, now, message_hash),
        )

    def mark_failed(
        self,
        message_hash: str,
        error_category: str,
        error_desc: str,
        max_attempts: int,
    ) -> None:
        """Record a delivery failure. Moves to dead_letter if max attempts exceeded."""
        assert self._conn is not None
        now = time.time()

        # Get current attempt count
        row = self._conn.execute(
            "SELECT attempt_count FROM messages WHERE message_hash = ?",
            (message_hash,),
        ).fetchone()
        if row is None:
            return

        new_count = row[0] + 1
        truncated_desc = (error_desc or "")[:500]

        if new_count >= max_attempts:
            self._conn.execute(
                """UPDATE messages
                   SET delivery_state = 'dead_letter',
                       attempt_count = ?,
                       last_attempt_utc = ?,
                       last_error_category = ?,
                       last_error_desc = ?
                   WHERE message_hash = ?""",
                (new_count, now, error_category, truncated_desc, message_hash),
            )
            logger.warning(
                "Message %s...[%s] moved to dead_letter after %d attempts",
                message_hash[:12], error_category, new_count,
            )
        else:
            # Exponential backoff with jitter for next retry
            base = min(2 ** new_count, 300)
            jitter = base * 0.25 * random.random()
            next_attempt = now + base + jitter

            self._conn.execute(
                """UPDATE messages
                   SET attempt_count = ?,
                       last_attempt_utc = ?,
                       next_attempt_utc = ?,
                       last_error_category = ?,
                       last_error_desc = ?
                   WHERE message_hash = ?""",
                (new_count, now, next_attempt, error_category,
                 truncated_desc, message_hash),
            )

    def get_counts(self) -> dict[str, int]:
        """Return counts of messages by delivery state."""
        assert self._conn is not None
        rows = self._conn.execute(
            "SELECT delivery_state, COUNT(*) FROM messages GROUP BY delivery_state"
        ).fetchall()
        counts = {"pending": 0, "delivered": 0, "dead_letter": 0}
        for state, count in rows:
            counts[state] = count
        return counts

    def has_hash(self, message_hash: str) -> bool:
        """Check if a message hash exists in the database."""
        assert self._conn is not None
        row = self._conn.execute(
            "SELECT 1 FROM messages WHERE message_hash = ?",
            (message_hash,),
        ).fetchone()
        return row is not None
