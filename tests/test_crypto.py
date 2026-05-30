"""Tests for crypto layer: keypair gen + sign/verify roundtrip + perms."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from lcloud.config import Settings
from lcloud.crypto.keys import admin_key_paths, ensure_admin_keypair
from lcloud.crypto.sign import (
    file_signature_payload,
    sha256_file,
    sign,
    verify,
)


@pytest.fixture
def isolated_settings(tmp_path: Path) -> Settings:
    return Settings(_env_file=None, lc_data_dir=tmp_path)


def test_keypair_creates_files_with_correct_perms(
    isolated_settings: Settings,
) -> None:
    sk, vk = ensure_admin_keypair(isolated_settings)
    priv, pub = admin_key_paths(isolated_settings)

    assert priv.exists()
    assert pub.exists()
    assert priv.stat().st_size == 32
    assert pub.stat().st_size == 32

    priv_mode = priv.stat().st_mode & 0o777
    assert priv_mode == 0o600, f"private key has mode {oct(priv_mode)}"

    # sk/vk are usable Ed25519 keys
    assert len(bytes(sk)) == 32
    assert len(bytes(vk)) == 32


def test_keypair_idempotent(isolated_settings: Settings) -> None:
    sk1, vk1 = ensure_admin_keypair(isolated_settings)
    sk2, vk2 = ensure_admin_keypair(isolated_settings)
    assert bytes(sk1) == bytes(sk2)
    assert bytes(vk1) == bytes(vk2)


def test_keypair_inconsistent_state_raises(isolated_settings: Settings) -> None:
    priv, pub = admin_key_paths(isolated_settings)
    # create only the public file
    priv.parent.mkdir(parents=True, exist_ok=True)
    pub.write_bytes(b"\x00" * 32)
    with pytest.raises(RuntimeError, match="Inconsistent keystore"):
        ensure_admin_keypair(isolated_settings)


def test_sign_verify_roundtrip(isolated_settings: Settings) -> None:
    sk, vk = ensure_admin_keypair(isolated_settings)
    payload = file_signature_payload(
        sha256_digest=b"\x01" * 32,
        chat_id=-100123456,
        message_id=42,
        owner_pubkey=bytes(vk),
        uploaded_at_unix=1700000000,
    )
    sig = sign(sk, payload)
    assert len(sig) == 64
    assert verify(vk, sig, payload)
    # tampered payload must not verify
    assert not verify(vk, sig, payload + b"x")


def test_payload_canonical_layout() -> None:
    p = file_signature_payload(
        sha256_digest=b"\x00" * 32,
        chat_id=1,
        message_id=2,
        owner_pubkey=b"\x00" * 32,
        uploaded_at_unix=3,
    )
    # 32 (sha) + 8 (chat_id) + 8 (msg_id) + 32 (pub) + 8 (ts) = 88
    assert len(p) == 88


def test_payload_rejects_wrong_lengths() -> None:
    with pytest.raises(ValueError, match="sha256_digest"):
        file_signature_payload(
            sha256_digest=b"\x00" * 16,
            chat_id=1,
            message_id=2,
            owner_pubkey=b"\x00" * 32,
            uploaded_at_unix=3,
        )
    with pytest.raises(ValueError, match="owner_pubkey"):
        file_signature_payload(
            sha256_digest=b"\x00" * 32,
            chat_id=1,
            message_id=2,
            owner_pubkey=b"\x00" * 16,
            uploaded_at_unix=3,
        )


def test_sha256_file_streaming(tmp_path: Path) -> None:
    import hashlib

    f = tmp_path / "blob.bin"
    data = os.urandom(2_500_000)  # >chunk to exercise streaming
    f.write_bytes(data)
    digest = sha256_file(f, chunk_size=64 * 1024)
    assert digest == hashlib.sha256(data).digest()
