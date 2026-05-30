"""LCLOUD1 chat marker: build / parse / verify (goal.md §6).

Marker format placed in `chat.about` of every cloud supergroup:

    LCLOUD1:<pubkey_b64url>:<sig_b64url>

where `sig = Ed25519_Sign(privkey, str(chat_id).encode("ascii"))`.

Encoding is base64-urlsafe without padding (kept short, URL-safe, ≤255 chars
limit on Telegram chat-about).
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass

from nacl.exceptions import BadSignatureError
from nacl.signing import SigningKey, VerifyKey

MARKER_PREFIX = "LCLOUD1:"


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def build_marker(*, signing_key: SigningKey, chat_id: int) -> str:
    """Build a fresh marker for `chat_id` using the admin signing key."""
    pubkey = bytes(signing_key.verify_key)
    payload = str(chat_id).encode("ascii")
    sig = bytes(signing_key.sign(payload).signature)
    return f"{MARKER_PREFIX}{_b64url_encode(pubkey)}:{_b64url_encode(sig)}"


@dataclass(frozen=True)
class ParsedMarker:
    pubkey: bytes  # 32 bytes
    signature: bytes  # 64 bytes


def parse_marker(about: str | None) -> ParsedMarker | None:
    """Parse `chat.about` into a ParsedMarker, or None on any malformed input."""
    if not about or not about.startswith(MARKER_PREFIX):
        return None
    body = about[len(MARKER_PREFIX) :]
    parts = body.split(":")
    if len(parts) != 2:
        return None
    try:
        pub = _b64url_decode(parts[0])
        sig = _b64url_decode(parts[1])
    except (ValueError, binascii.Error):
        return None
    if len(pub) != 32 or len(sig) != 64:
        return None
    return ParsedMarker(pubkey=pub, signature=sig)


def verify_marker(
    marker: ParsedMarker, *, chat_id: int, expected_pubkey: bytes
) -> bool:
    """Return True iff `marker` is a valid signature over `str(chat_id)` by
    `expected_pubkey`. Mismatch on either pubkey or signature → False."""
    if marker.pubkey != expected_pubkey:
        return False
    try:
        VerifyKey(marker.pubkey).verify(
            str(chat_id).encode("ascii"), marker.signature
        )
        return True
    except BadSignatureError:
        return False
