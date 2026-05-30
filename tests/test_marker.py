"""Tests for the LCLOUD1 chat marker (build/parse/verify)."""

from __future__ import annotations

from nacl.signing import SigningKey

from lcloud.userbot.marker import (
    MARKER_PREFIX,
    build_marker,
    parse_marker,
    verify_marker,
)


def test_build_parse_verify_roundtrip() -> None:
    sk = SigningKey.generate()
    pub = bytes(sk.verify_key)
    chat_id = -1001234567890

    marker = build_marker(signing_key=sk, chat_id=chat_id)
    assert marker.startswith(MARKER_PREFIX)
    assert len(marker) <= 255  # Telegram chat-about limit

    parsed = parse_marker(marker)
    assert parsed is not None
    assert parsed.pubkey == pub
    assert len(parsed.signature) == 64

    assert verify_marker(parsed, chat_id=chat_id, expected_pubkey=pub)


def test_marker_signature_bound_to_chat_id() -> None:
    sk = SigningKey.generate()
    pub = bytes(sk.verify_key)
    marker = build_marker(signing_key=sk, chat_id=-1001)
    parsed = parse_marker(marker)
    assert parsed is not None
    # Same pubkey but different chat_id → verify must fail
    assert not verify_marker(parsed, chat_id=-2002, expected_pubkey=pub)


def test_marker_pubkey_mismatch_rejects() -> None:
    sk = SigningKey.generate()
    other_pub = bytes(SigningKey.generate().verify_key)
    marker = build_marker(signing_key=sk, chat_id=-1001)
    parsed = parse_marker(marker)
    assert parsed is not None
    assert not verify_marker(parsed, chat_id=-1001, expected_pubkey=other_pub)


def test_parse_rejects_garbage() -> None:
    assert parse_marker(None) is None
    assert parse_marker("") is None
    assert parse_marker("hello world") is None
    assert parse_marker("LCLOUD1:") is None
    assert parse_marker("LCLOUD1:onlyonepart") is None
    assert parse_marker("LCLOUD1:!!!:!!!") is None  # invalid base64
    # Right shape but wrong byte lengths
    bad = "LCLOUD1:" + "AAAA" + ":" + "BBBB"
    assert parse_marker(bad) is None


def test_parse_rejects_wrong_prefix() -> None:
    sk = SigningKey.generate()
    marker = build_marker(signing_key=sk, chat_id=-1001)
    swapped = "LCLOUDX:" + marker[len(MARKER_PREFIX) :]
    assert parse_marker(swapped) is None


def test_marker_fits_in_telegram_about_limit() -> None:
    sk = SigningKey.generate()
    # Chat ids can be up to ~13-digit negative; pick a large one.
    marker = build_marker(signing_key=sk, chat_id=-1_009_999_999_999)
    assert len(marker) <= 255
