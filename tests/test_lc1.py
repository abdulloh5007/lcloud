"""LC1 caption build/parse roundtrip + tampering rejection."""

from __future__ import annotations

from lcloud.userbot.lc1 import LC1_PREFIX, build_lc1_caption, parse_lc1_caption


def test_lc1_roundtrip() -> None:
    sha = b"\x10" * 32
    sig = b"\x20" * 64
    pub = b"\x30" * 32
    caption = build_lc1_caption(
        sha256_digest=sha, signature=sig, owner_pubkey=pub, uploaded_at_unix=1700000000
    )
    assert caption.startswith(LC1_PREFIX)
    parsed = parse_lc1_caption(caption)
    assert parsed is not None
    assert parsed.sha256_digest == sha
    assert parsed.signature == sig
    assert parsed.owner_pubkey == pub
    assert parsed.uploaded_at_unix == 1700000000


def test_lc1_rejects_garbage() -> None:
    assert parse_lc1_caption(None) is None
    assert parse_lc1_caption("") is None
    assert parse_lc1_caption("hello world") is None
    assert parse_lc1_caption("LC1:") is None
    assert parse_lc1_caption("LC1:not json") is None
    assert parse_lc1_caption('LC1:["array","not","object"]') is None
    assert parse_lc1_caption('LC1:{"o":"x","s":"y","h":"z","t":1}') is None  # bad b64 lengths


def test_lc1_strips_whitespace() -> None:
    sha = b"\x00" * 32
    sig = b"\x00" * 64
    pub = b"\x00" * 32
    caption = build_lc1_caption(
        sha256_digest=sha, signature=sig, owner_pubkey=pub, uploaded_at_unix=1
    )
    parsed = parse_lc1_caption("\n  " + caption + "  \n")
    assert parsed is not None


def test_lc1_validates_input_lengths() -> None:
    import pytest

    with pytest.raises(ValueError):
        build_lc1_caption(
            sha256_digest=b"\x00" * 16,
            signature=b"\x00" * 64,
            owner_pubkey=b"\x00" * 32,
            uploaded_at_unix=1,
        )
    with pytest.raises(ValueError):
        build_lc1_caption(
            sha256_digest=b"\x00" * 32,
            signature=b"\x00" * 32,
            owner_pubkey=b"\x00" * 32,
            uploaded_at_unix=1,
        )
    with pytest.raises(ValueError):
        build_lc1_caption(
            sha256_digest=b"\x00" * 32,
            signature=b"\x00" * 64,
            owner_pubkey=b"\x00" * 16,
            uploaded_at_unix=1,
        )


def test_lc1_caption_under_telegram_limit() -> None:
    """Telegram caption limit is 1024 chars; LC1 must fit comfortably."""
    caption = build_lc1_caption(
        sha256_digest=b"\xff" * 32,
        signature=b"\xff" * 64,
        owner_pubkey=b"\xff" * 32,
        uploaded_at_unix=9_999_999_999,
    )
    # ~213 chars for max-size payload; well under 1024.
    assert len(caption) < 256
