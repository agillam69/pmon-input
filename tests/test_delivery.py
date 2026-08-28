"""Tests for PagerMon REST delivery adapter."""

from __future__ import annotations

from unittest.mock import MagicMock, patch
import requests
import pytest

from cfa_pagermon_bridge.config import Config
from cfa_pagermon_bridge.pagermon import PagerMonClient, DeliveryResult


@pytest.fixture
def cfg() -> Config:
    return Config(
        cfa_source_url="https://mazzanet.net.au/cfa/?reg=state&magickey=cfastream",
        poll_interval_seconds=20,
        http_connect_timeout_seconds=5,
        http_read_timeout_seconds=15,
        max_response_bytes=2 * 1024 * 1024,
        user_agent="CFA-PagerMon-Bridge/1.0",
        pagermon_base_url="http://127.0.0.1:3000",
        pagermon_api_key="test-api-key",
        pagermon_address="9990001",
        pagermon_source="mazzanet-cfa",
        state_db_path="data/state.sqlite3",
        log_level="INFO",
        max_delivery_attempts=20,
        max_message_length=2000,
        no_message_warning_seconds=600,
        dry_run=False,
    )


def test_build_payload(cfg: Config):
    client = PagerMonClient(cfg)
    payload = client.build_payload(
        message_text="(DROM) 13:25:49 2026-08-08 TEST [DROM]",
        dispatch_date="2026-08-08",
        dispatch_time="13:25:49",
    )
    assert payload["address"] == "9990001"
    assert payload["source"] == "mazzanet-cfa"
    assert payload["message"] == "(DROM) 13:25:49 2026-08-08 TEST [DROM]"
    assert isinstance(payload["datetime"], int)
    client.close()


def test_successful_delivery_returns_id(cfg: Config):
    client = PagerMonClient(cfg)
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = "12345"

    with patch.object(client._session, "post", return_value=mock_resp) as mock_post:
        res = client.deliver("(DROM) 13:25:49 2026-08-08 TEST [DROM]")
        assert res.success is True
        assert res.pagermon_id == "12345"
        mock_post.assert_called_once()
        headers = client._session.headers
        assert headers["apikey"] == "test-api-key"
        assert headers["Content-Type"] == "application/json"
    client.close()


def test_duplicate_response_treated_as_success(cfg: Config):
    client = PagerMonClient(cfg)
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = "Ignoring duplicate"

    with patch.object(client._session, "post", return_value=mock_resp):
        res = client.deliver("duplicate message")
        assert res.success is True
        assert res.pagermon_id == "duplicate"
    client.close()


def test_filtered_response_treated_as_success(cfg: Config):
    client = PagerMonClient(cfg)
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = "Ignoring filtered"

    with patch.object(client._session, "post", return_value=mock_resp):
        res = client.deliver("filtered message")
        assert res.success is True
        assert res.pagermon_id == "filtered"
    client.close()


def test_401_auth_failure_marked_config_error(cfg: Config):
    client = PagerMonClient(cfg)
    mock_resp = MagicMock()
    mock_resp.status_code = 401
    mock_resp.text = '{"error": "Authentication failed."}'

    with patch.object(client._session, "post", return_value=mock_resp):
        res = client.deliver("auth fail test")
        assert res.success is False
        assert res.is_config_error is True
        assert res.error_category == "auth"
    client.close()


def test_403_forbidden_marked_config_error(cfg: Config):
    client = PagerMonClient(cfg)
    mock_resp = MagicMock()
    mock_resp.status_code = 403
    mock_resp.text = "Forbidden"

    with patch.object(client._session, "post", return_value=mock_resp):
        res = client.deliver("forbidden test")
        assert res.success is False
        assert res.is_config_error is True
        assert res.error_category == "auth"
    client.close()


def test_429_rate_limiting(cfg: Config):
    client = PagerMonClient(cfg)
    mock_resp = MagicMock()
    mock_resp.status_code = 429
    mock_resp.text = "Too Many Requests"

    with patch.object(client._session, "post", return_value=mock_resp):
        res = client.deliver("rate limited test")
        assert res.success is False
        assert res.is_config_error is False
        assert res.error_category == "server"
    client.close()


def test_500_missing_fields_marked_format_config_error(cfg: Config):
    client = PagerMonClient(cfg)
    mock_resp = MagicMock()
    mock_resp.status_code = 500
    mock_resp.text = '{"message": "Error - address or message missing"}'

    with patch.object(client._session, "post", return_value=mock_resp):
        res = client.deliver("bad fields")
        assert res.success is False
        assert res.is_config_error is True
        assert res.error_category == "format"
    client.close()


def test_network_timeout(cfg: Config):
    client = PagerMonClient(cfg)
    with patch.object(client._session, "post", side_effect=requests.exceptions.Timeout("timed out")):
        res = client.deliver("timeout test")
        assert res.success is False
        assert res.error_category == "network"
        assert "Timeout" in (res.error_desc or "")
    client.close()


def test_check_reachable(cfg: Config):
    client = PagerMonClient(cfg)
    mock_resp = MagicMock()
    mock_resp.status_code = 200

    with patch.object(client._session, "get", return_value=mock_resp):
        ok, msg = client.check_reachable()
        assert ok is True
        assert "200" in msg

    with patch.object(client._session, "get", side_effect=requests.exceptions.ConnectionError("offline")):
        ok, msg = client.check_reachable()
        assert ok is False
        assert "Not reachable" in msg
    client.close()
