"""Tests for readable-text extraction from fetched bodies."""

from arcana.tools.builtins.web.extract import extract_text


def test_html_strips_scripts_and_keeps_text():
    html = b"<html><head><style>.a{}</style></head><body><script>x()</script><p>Hello</p><p>World</p></body></html>"
    text = extract_text(html, "text/html; charset=utf-8", "utf-8")
    assert "Hello" in text
    assert "World" in text
    assert "x()" not in text
    assert ".a{}" not in text


def test_html_block_tags_become_line_breaks():
    html = b"<div>one</div><div>two</div>"
    assert extract_text(html, "text/html", "utf-8") == "one\ntwo"


def test_html_collapses_whitespace():
    html = b"<p>a\n\n   lot   of\tspace</p>"
    assert extract_text(html, "text/html", "utf-8") == "a lot of space"


def test_plain_text_passthrough():
    assert extract_text(b"just text", "text/plain", "utf-8") == "just text"


def test_json_passthrough():
    assert extract_text(b'{"k": 1}', "application/json", "utf-8") == '{"k": 1}'


def test_structured_suffix_types_are_texty():
    assert extract_text(b"<x/>", "application/atom+xml", "utf-8") == "<x/>"
    assert extract_text(b"{}", "application/ld+json", "utf-8") == "{}"


def test_unsupported_binary_returns_placeholder():
    out = extract_text(b"\x00\x01\x02", "image/png", None)
    assert out == "(unsupported content type: image/png)"


def test_empty_content_type_is_unsupported():
    assert extract_text(b"data", "", None) == "(unsupported content type: unknown)"


def test_bad_bytes_do_not_crash():
    # Undecodable bytes fall back to replacement rather than raising.
    out = extract_text(b"\xff\xfe bad", "text/plain", "utf-8")
    assert isinstance(out, str)
