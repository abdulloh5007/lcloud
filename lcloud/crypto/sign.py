"""Ed25519 sign / verify helpers + canonical signature payload (goal.md §5).

The signed payload for a stored file is the byte concatenation:

    sha256(file) || chat_id || message_id || owner_pubkey || uploaded_at_unix

Integers are serialized as little-endian int64 (`<q`) for forward-stability
across platforms.
"""

from __future__ import annotations

import hashlib
import struct
from pathlib import Path

from nacl.exceptions import BadSignatureError
from nacl.signing import SigningKey, VerifyKey


def sha256_file(path: Path, *, chunk_size: int = 1024 * 1024) -> bytes:
    """Stream `path` through sha256 and return the 32-byte digest."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            buf = f.read(chunk_size)
            if not buf:
                break
            h.update(buf)
    return h.digest()


def file_signature_payload(
    *,
    sha256_digest: bytes,
    chat_id: int,
    message_id: int,
    owner_pubkey: bytes,
    uploaded_at_unix: int,
) -> bytes:
    if len(sha256_digest) != 32:
        raise ValueError("sha256_digest must be 32 bytes")
    if len(owner_pubkey) != 32:
        raise ValueError("owner_pubkey must be 32 bytes (Ed25519)")
    return (
        sha256_digest
        + struct.pack("<q", chat_id)
        + struct.pack("<q", message_id)
        + owner_pubkey
        + struct.pack("<q", uploaded_at_unix)
    )


def sign(sk: SigningKey, payload: bytes) -> bytes:
    """Return the 64-byte detached Ed25519 signature for `payload`."""
    return bytes(sk.sign(payload).signature)


def verify(vk: VerifyKey, signature: bytes, payload: bytes) -> bool:
    """Return True iff `signature` is a valid Ed25519 sig for `payload`."""
    try:
        vk.verify(payload, signature)
        return True
    except BadSignatureError:
        return False
