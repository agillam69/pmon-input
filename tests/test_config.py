"""Tests for configuration parsing and validation."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch
import pytest

from cfa_pagermon_bridge.config import load_config, validate_config, Config


def test_default_config():
    with patch.dict(os.environ, {}, clear=True):
        cfg = load_config(env_file=None)
        assert cfg.poll_interval_seconds == 20
        assert cfg.pagermon_address == "9990001"
        assert cfg.pagermon_source == "mazzanet-cfa"
        assert cfg.dry_run is False


def test_env_override():
    custom_env = {
        "CFA_SOURCE_URL": "https://custom.source/url",
        "POLL_INTERVAL_SECONDS": "45",
        "PAGERMON_BASE_URL": "http://192.168.1.50:3000",
        "PAGERMON_API_KEY": "custom_key_123",
        "PAGERMON_ADDRESS": "8880001",
        "PAGERMON_SOURCE": "custom-source",
        "DRY_RUN": "1",
    }
    with patch.dict(os.environ, custom_env, clear=True):
        cfg = load_config(env_file=None)
        assert cfg.cfa_source_url == "https://custom.source/url"
        assert cfg.poll_interval_seconds == 45
        assert cfg.pagermon_base_url == "http://192.168.1.50:3000"
        assert cfg.pagermon_api_key == "custom_key_123"
        assert cfg.pagermon_address == "8880001"
        assert cfg.pagermon_source == "custom-source"
        assert cfg.dry_run is True


def test_validate_config():
    valid_cfg = Config(
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
        state_db_path="data/state.sqlite3",
        log_level="INFO",
        max_delivery_attempts=20,
        max_message_length=2000,
        no_message_warning_seconds=600,
        dry_run=False,
    )
    assert validate_config(valid_cfg) == []

    invalid_cfg = Config(
        cfa_source_url="ftp://invalid.url",
        poll_interval_seconds=2,
        http_connect_timeout_seconds=5,
        http_read_timeout_seconds=15,
        max_response_bytes=2097152,
        user_agent="CFA-PagerMon-Bridge/1.0",
        pagermon_base_url="invalid_url",
        pagermon_api_key="replace_me",
        pagermon_address="",
        pagermon_source="mazzanet-cfa",
        state_db_path="data/state.sqlite3",
        log_level="INFO",
        max_delivery_attempts=0,
        max_message_length=2000,
        no_message_warning_seconds=600,
        dry_run=False,
    )
    errors = validate_config(invalid_cfg)
    assert len(errors) >= 5
    assert any("CFA_SOURCE_URL" in e for e in errors)
    assert any("POLL_INTERVAL_SECONDS" in e for e in errors)
    assert any("PAGERMON_API_KEY" in e for e in errors)
    assert any("PAGERMON_ADDRESS" in e for e in errors)
    assert any("MAX_DELIVERY_ATTEMPTS" in e for e in errors)
