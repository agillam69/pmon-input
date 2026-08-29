"""Tests for main CLI functions and flags."""

from __future__ import annotations

from unittest.mock import MagicMock, patch
import pytest

from cfa_pagermon_bridge.main import main, run_test_delivery, run_check
from cfa_pagermon_bridge.config import Config


@pytest.fixture
def valid_cfg() -> Config:
    return Config(
        cfa_source_url="https://mazzanet.net.au/cfa/?reg=state&magickey=cfastream",
        poll_interval_seconds=20,
        http_connect_timeout_seconds=5,
        http_read_timeout_seconds=15,
        max_response_bytes=2097152,
        user_agent="CFA-PagerMon-Bridge/1.0",
        pagermon_base_url="http://127.0.0.1:3000",
        pagermon_api_key="real_api_key",
        pagermon_address="9990001",
        pagermon_source="mazzanet-cfa",
        state_db_path="data/test_state.sqlite3",
        log_level="INFO",
        max_delivery_attempts=20,
        max_message_length=2000,
        no_message_warning_seconds=600,
        dry_run=True,
        webui_enabled=False,
        webui_host="0.0.0.0",
        webui_port=8585,
        webui_password="",
    )


def test_test_delivery_rejects_unprefixed_message(valid_cfg: Config):
    res = run_test_delivery(valid_cfg, "RANDOM EMERGENCY MESSAGE")
    assert res == 1


def test_test_delivery_accepts_prefixed_message(valid_cfg: Config):
    with patch("cfa_pagermon_bridge.main.PagerMonClient") as mock_client_cls:
        mock_client = MagicMock()
        mock_deliv = MagicMock()
        mock_deliv.success = True
        mock_deliv.pagermon_id = "test-123"
        mock_client.deliver.return_value = mock_deliv
        mock_client_cls.return_value = mock_client

        res = run_test_delivery(valid_cfg, "TEST - CFA WEB BRIDGE - Checking connectivity")
        assert res == 0
        mock_client.deliver.assert_called_once_with("TEST - CFA WEB BRIDGE - Checking connectivity")


def test_main_check_flag(tmp_path):
    with patch("cfa_pagermon_bridge.main.run_check", return_value=0) as mock_check:
        code = main(["--check"])
        assert code == 0
        mock_check.assert_called_once()
