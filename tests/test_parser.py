"""Tests for CFA HTML dispatch message parser."""

from __future__ import annotations

from pathlib import Path
import pytest

from cfa_pagermon_bridge.parser import parse_messages, DispatchMessage

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def test_drom_fixture():
    html = (FIXTURES_DIR / "drom.html").read_text(encoding="utf-8")
    messages = parse_messages(html)
    assert len(messages) == 1
    msg = messages[0]
    assert isinstance(msg, DispatchMessage)
    assert msg.identifier == "DROM"
    assert msg.dispatch_time == "13:25:49"
    assert msg.dispatch_date == "2026-08-08"
    assert "(DROM) 13:25:49 2026-08-08 ALERT DROM2 INCIC1 ASSIST AV WITH ENTRY 2 LEE ST ARTHURS SEAT /SEAHAZE ST //ARTHURS SEAT RD M 159 G12 (219526) F CDROM F260807324 [DROM]" in msg.text
    assert len(msg.message_hash) == 64


def test_uppe_fixture():
    html = (FIXTURES_DIR / "uppe.html").read_text(encoding="utf-8")
    messages = parse_messages(html)
    assert len(messages) == 1
    msg = messages[0]
    assert msg.identifier == "UPPE"
    assert msg.dispatch_time == "14:10:02"
    assert msg.dispatch_date == "2026-08-08"
    assert msg.text.startswith("(UPPE)")
    assert msg.text.endswith("[UPPE]")


def test_multi_messages_in_source_order():
    html = (FIXTURES_DIR / "multi.html").read_text(encoding="utf-8")
    messages = parse_messages(html)
    assert len(messages) == 3
    assert [m.identifier for m in messages] == ["ONE", "TWO", "THREE"]
    assert messages[0].dispatch_time == "08:00:00"
    assert messages[1].dispatch_time == "08:01:00"
    assert messages[2].dispatch_time == "08:02:00"


def test_wrapped_whitespace_and_nested_spans():
    html = """
    <strong style="font-size: 20">
        <span>(WRAP) 08:00:00 2026-08-08</span>
        <span>LINE ONE</span>
        <span>LINE TWO [WRAP]</span>
    </strong>
    """
    messages = parse_messages(html)
    assert len(messages) == 1
    assert messages[0].identifier == "WRAP"
    assert "LINE ONE LINE TWO" in messages[0].text
    assert messages[0].text == "(WRAP) 08:00:00 2026-08-08 LINE ONE LINE TWO [WRAP]"


def test_ignores_headings_and_weather():
    html = (FIXTURES_DIR / "weather.html").read_text(encoding="utf-8")
    messages = parse_messages(html)
    assert messages == []

    # Combined with valid dispatch
    mixed_html = """
    <strong>CFA State Incidents</strong>
    <strong>Weather forecast for Victoria</strong>
    <strong>(VALID) 09:30:00 2026-08-08 INCIDENT TEST [VALID]</strong>
    """
    messages = parse_messages(mixed_html)
    assert len(messages) == 1
    assert messages[0].identifier == "VALID"


def test_mismatched_identifiers_rejected():
    html = '<strong style="font-size: 20">(DROM) 08:00:00 2026-08-08 CONTENT [UPPE]</strong>'
    assert parse_messages(html) == []


def test_missing_opening_identifier_rejected():
    html = "<strong>08:00:00 2026-08-08 CONTENT [NOPE]</strong>"
    assert parse_messages(html) == []


def test_missing_closing_identifier_rejected():
    html = '<strong style="font-size: 20">(NOPE) 08:00:00 2026-08-08 CONTENT</strong>'
    assert parse_messages(html) == []


def test_malformed_timestamp_rejected():
    html = "<strong>(BAD) 8:00:00 2026-08-08 CONTENT [BAD]</strong>"
    assert parse_messages(html) == []


def test_malformed_date_rejected():
    html = "<strong>(BAD) 08:00:00 2026-8-8 CONTENT [BAD]</strong>"
    assert parse_messages(html) == []


def test_duplicate_elements_in_single_page():
    html = """
    <strong>(SAME) 08:00:00 2026-08-08 DISPATCH [SAME]</strong>
    <strong>(SAME) 08:00:00 2026-08-08 DISPATCH [SAME]</strong>
    """
    messages = parse_messages(html)
    assert len(messages) == 1
    assert messages[0].identifier == "SAME"


def test_empty_and_malformed_html():
    assert parse_messages("") == []
    assert parse_messages("   ") == []
    assert parse_messages("<html><body><p>Nothing here</p></body></html>") == []
    assert parse_messages("<html><body><strong>unclosed tag") == []


def test_oversized_content_rejected():
    long_body = "X" * 3000
    html = f"<strong>(BIG) 08:00:00 2026-08-08 {long_body} [BIG]</strong>"
    assert parse_messages(html, max_message_length=2000) == []
    # But passes when limit is large enough
    assert len(parse_messages(html, max_message_length=4000)) == 1


def test_html_entities_decoded():
    html = '<strong style="font-size: 20">(ENT) 08:00:00 2026-08-08 A &amp; B &lt;C&gt; &quot;QUOTED&quot; [ENT]</strong>'
    messages = parse_messages(html)
    assert len(messages) == 1
    assert messages[0].text == '(ENT) 08:00:00 2026-08-08 A & B <C> "QUOTED" [ENT]'
