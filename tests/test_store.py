"""Tests for SQLite MessageStore deduplication and lifecycle tracking."""

from __future__ import annotations

from pathlib import Path
import pytest

from cfa_pagermon_bridge.store import MessageStore, PendingMessage


@pytest.fixture
def store(tmp_path: Path) -> MessageStore:
    db_path = tmp_path / "test_state.sqlite3"
    ms = MessageStore(str(db_path))
    ms.open()
    yield ms
    ms.close()


def test_first_observation_creates_pending(store: MessageStore):
    h = "a" * 64
    inserted = store.insert_if_new(
        message_hash=h,
        message_text="(DROM) 10:00:00 2026-08-08 TEST [DROM]",
        identifier="DROM",
        dispatch_time="10:00:00",
        dispatch_date="2026-08-08",
    )
    assert inserted is True
    counts = store.get_counts()
    assert counts["pending"] == 1
    assert counts["delivered"] == 0
    assert counts["dead_letter"] == 0

    pending = store.get_pending()
    assert len(pending) == 1
    assert pending[0].message_hash == h
    assert pending[0].identifier == "DROM"
    assert pending[0].attempt_count == 0


def test_repeated_observation_not_duplicated(store: MessageStore):
    h = "b" * 64
    assert store.insert_if_new(h, "msg", "ID", "10:00:00", "2026-08-08") is True
    assert store.insert_if_new(h, "msg", "ID", "10:00:00", "2026-08-08") is False
    assert store.get_counts()["pending"] == 1


def test_distinct_text_distinct_hash(store: MessageStore):
    h1 = "1" * 64
    h2 = "2" * 64
    assert store.insert_if_new(h1, "msg1", "ID1", "10:00:00", "2026-08-08") is True
    assert store.insert_if_new(h2, "msg2", "ID2", "10:00:01", "2026-08-08") is True
    assert store.get_counts()["pending"] == 2


def test_successful_delivery_marks_delivered(store: MessageStore):
    h = "c" * 64
    store.insert_if_new(h, "msg", "ID", "10:00:00", "2026-08-08")
    store.mark_delivered(h)

    counts = store.get_counts()
    assert counts["pending"] == 0
    assert counts["delivered"] == 1
    assert counts["dead_letter"] == 0

    # No longer in pending queue
    assert store.get_pending() == []
    # Still tracked for deduplication
    assert store.has_hash(h) is True
    assert store.insert_if_new(h, "msg", "ID", "10:00:00", "2026-08-08") is False


def test_failed_delivery_remains_pending_and_retries(store: MessageStore):
    h = "d" * 64
    store.insert_if_new(h, "msg", "ID", "10:00:00", "2026-08-08")
    store.mark_failed(
        message_hash=h,
        error_category="network",
        error_desc="Connection timed out",
        max_attempts=5,
    )

    counts = store.get_counts()
    assert counts["pending"] == 1
    assert counts["dead_letter"] == 0


def test_dead_letter_after_max_attempts(store: MessageStore):
    h = "e" * 64
    store.insert_if_new(h, "msg", "ID", "10:00:00", "2026-08-08")

    # Fail up to max_attempts = 3
    store.mark_failed(h, "server", "500 Internal Error", max_attempts=3)
    assert store.get_counts()["pending"] == 1

    store.mark_failed(h, "server", "500 Internal Error", max_attempts=3)
    assert store.get_counts()["pending"] == 1

    store.mark_failed(h, "server", "500 Internal Error", max_attempts=3)
    counts = store.get_counts()
    assert counts["pending"] == 0
    assert counts["dead_letter"] == 1

    # Should not be returned by get_pending
    assert store.get_pending() == []


def test_restart_simulation_resumes_pending(tmp_path: Path):
    db_path = str(tmp_path / "resume_test.sqlite3")
    h = "f" * 64

    # Instance 1: write pending message
    store1 = MessageStore(db_path)
    store1.open()
    store1.insert_if_new(h, "resume text", "RES", "12:00:00", "2026-08-08")
    store1.close()

    # Instance 2: reopen and verify pending survives
    store2 = MessageStore(db_path)
    store2.open()
    counts = store2.get_counts()
    assert counts["pending"] == 1
    pending = store2.get_pending()
    assert len(pending) == 1
    assert pending[0].message_hash == h
    assert pending[0].message_text == "resume text"
    store2.close()


def test_has_hash(store: MessageStore):
    h = "9" * 64
    assert store.has_hash(h) is False
    store.insert_if_new(h, "text", "ID", "11:00:00", "2026-08-08")
    assert store.has_hash(h) is True
