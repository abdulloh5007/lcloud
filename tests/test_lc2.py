"""Unit tests for LC2 caption format + signature verification."""

from __future__ import annotations

import json
import struct
import time

import pytest
from nacl.signing import SigningKey

from lcloud.auth.seed import derive_keypair, generate_mnemonic
from lcloud.crypto.lc2 import (
    LC2_PREFIX,
    TS_SKEW_SECONDS,
    Lc2Payload,
    canonical_payload,
    parse_caption,
    verify_lc2_payload,
)


def _fresh_signing_key() -> tuple[bytes, SigningKey]:
    ident = derive_keypair(generate_mnemonic(12))
    return ident.pubkey, SigningKey(ident.privkey_seed)


def test_canonical_payload_layout() -> None:
    sha = b"\x11" * 32
    pub = b"\x22" * 32
    ts = 0xDEADBEEF
    p = canonical_payload(sha256=sha, ts=ts, pubkey=pub)
    assert p == sha + struct.pack(">Q", ts) + pub
    assert len(p) == 32 + 8 + 32


def test_canonical_payload_validates_sizes() -> None:
    with pytest.raises(ValueError, match="sha256"):
        canonical_payload(sha256=b"\x00", ts=1, pubkey=b"\x00" * 32)
    with pytest.raises(ValueError, match="pubkey"):
        canonical_payload(sha256=b"\x00" * 32, ts=1, pubkey=b"\x00" * 31)


def test_to_caption_roundtrip() -> None:
    pub, sk = _fresh_signing_key()
    sha = b"\xAA" * 32
    ts = 1700000000
    sig = sk.sign(canonical_payload(sha256=sha, ts=ts, pubkey=pub)).signature

    p = Lc2Payload(pubkey=pub, sha256=sha, signature=sig, ts=ts)
    caption = p.to_caption()
    assert caption.startswith(LC2_PREFIX)
    body = json.loads(caption[len(LC2_PREFIX):])
    assert body["o"] == pub.hex()
    assert body["h"] == sha.hex()
    assert body["s"] == sig.hex()
    assert body["t"] == ts

    parsed = parse_caption(caption)
    assert parsed is not None
    assert parsed.pubkey == pub
    assert parsed.sha256 == sha
    assert parsed.signature == sig
    assert parsed.ts == ts


def test_verify_lc2_payload_happy_path() -> None:
    pub, sk = _fresh_signing_key()
    sha = b"\xCC" * 32
    ts = int(time.time())
    sig = sk.sign(canonical_payload(sha256=sha, ts=ts, pubkey=pub)).signature

    ok, why = verify_lc2_payload(pubkey=pub, sha256=sha, signature=sig, ts=ts)
    assert ok is True
    assert why is None


def test_verify_lc2_rejects_wrong_signature() -> None:
    pub, _ = _fresh_signing_key()
    _, other_sk = _fresh_signing_key()
    sha = b"\xCC" * 32
    ts = int(time.time())
    bad_sig = other_sk.sign(canonical_payload(sha256=sha, ts=ts, pubkey=pub)).signature

    ok, why = verify_lc2_payload(
        pubkey=pub, sha256=sha, signature=bad_sig, ts=ts
    )
    assert ok is False
    assert why == "bad_signature"


def test_verify_lc2_rejects_tampered_sha256() -> None:
    pub, sk = _fresh_signing_key()
    sha = b"\xCC" * 32
    ts = int(time.time())
    sig = sk.sign(canonical_payload(sha256=sha, ts=ts, pubkey=pub)).signature

    tampered = b"\xDD" * 32
    ok, why = verify_lc2_payload(
        pubkey=pub, sha256=tampered, signature=sig, ts=ts
    )
    assert ok is False
    assert why == "bad_signature"


def test_verify_lc2_rejects_wrong_pubkey() -> None:
    pub_a, sk = _fresh_signing_key()
    pub_b, _ = _fresh_signing_key()
    sha = b"\xCC" * 32
    ts = int(time.time())
    sig = sk.sign(canonical_payload(sha256=sha, ts=ts, pubkey=pub_a)).signature

    ok, why = verify_lc2_payload(
        pubkey=pub_b, sha256=sha, signature=sig, ts=ts
    )
    assert ok is False
    assert why == "bad_signature"


def test_verify_lc2_ts_skew_too_old() -> None:
    pub, sk = _fresh_signing_key()
    sha = b"\xCC" * 32
    server_now = 1700000000
    ts = server_now - TS_SKEW_SECONDS - 60  # past skew
    sig = sk.sign(canonical_payload(sha256=sha, ts=ts, pubkey=pub)).signature

    ok, why = verify_lc2_payload(
        pubkey=pub, sha256=sha, signature=sig, ts=ts, server_now=server_now
    )
    assert ok is False
    assert why == "ts_skew"


def test_verify_lc2_ts_skew_future() -> None:
    pub, sk = _fresh_signing_key()
    sha = b"\xCC" * 32
    server_now = 1700000000
    ts = server_now + TS_SKEW_SECONDS + 60  # future skew
    sig = sk.sign(canonical_payload(sha256=sha, ts=ts, pubkey=pub)).signature

    ok, why = verify_lc2_payload(
        pubkey=pub, sha256=sha, signature=sig, ts=ts, server_now=server_now
    )
    assert ok is False
    assert why == "ts_skew"


def test_verify_lc2_rejects_bad_sizes() -> None:
    ok, why = verify_lc2_payload(
        pubkey=b"\x00" * 16, sha256=b"\x00" * 32, signature=b"\x00" * 64, ts=1
    )
    assert ok is False
    assert why == "bad_pubkey_len"

    ok, why = verify_lc2_payload(
        pubkey=b"\x00" * 32, sha256=b"\x00" * 16, signature=b"\x00" * 64, ts=1
    )
    assert ok is False
    assert why == "bad_sha256_len"

    ok, why = verify_lc2_payload(
        pubkey=b"\x00" * 32, sha256=b"\x00" * 32, signature=b"\x00" * 16, ts=1
    )
    assert ok is False
    assert why == "bad_signature_len"


def test_verify_lc2_rejects_zero_ts() -> None:
    pub, sk = _fresh_signing_key()
    sha = b"\xCC" * 32
    sig = sk.sign(canonical_payload(sha256=sha, ts=0, pubkey=pub)).signature

    ok, why = verify_lc2_payload(pubkey=pub, sha256=sha, signature=sig, ts=0)
    assert ok is False
    assert why == "bad_ts"


def test_parse_caption_rejects_lc1() -> None:
    assert parse_caption('LC1:{"foo":"bar"}') is None


def test_parse_caption_rejects_garbage() -> None:
    assert parse_caption("") is None
    assert parse_caption("not a caption") is None
    assert parse_caption("LC2:not-json") is None
    assert parse_caption("LC2:{}") is None
    assert parse_caption('LC2:{"o":"zz","h":"abc","s":"abc","t":1}') is None
