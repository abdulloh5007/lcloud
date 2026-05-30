"""BIP39 mnemonic + Ed25519 keypair derivation.

Used by:
- Admin bootstrap: server generates a 12-word phrase on first userbot
  login, sends to Saved Messages, derives Ed25519 keypair, stamps
  pubkey to `data/keys/admin.pub` and stores fingerprint in users table.
- Self-serve registration (V2 frontend): browser generates phrase
  client-side, derives keypair, only `pubkey` lands on server.

Derivation chain (matches what the browser does with `@scure/bip39`):

    mnemonic (12 or 24 EN words)
        ↓ BIP39: PBKDF2-HMAC-SHA512(mnemonic, "mnemonic" + passphrase, 2048)
    seed (64 bytes)
        ↓ first 32 bytes
    Ed25519 seed
        ↓ SigningKey(seed)
    keypair
"""

from __future__ import annotations

from dataclasses import dataclass

from mnemonic import Mnemonic
from nacl.signing import SigningKey, VerifyKey

DEFAULT_LANG = "english"
ALLOWED_WORD_COUNTS = (12, 24)
ALLOWED_STRENGTHS = {12: 128, 24: 256}


@dataclass(frozen=True)
class DerivedIdentity:
    mnemonic: str  # space-separated words
    pubkey: bytes  # 32 bytes
    privkey_seed: bytes  # 32 bytes — Ed25519 seed (do NOT persist for users)


def generate_mnemonic(words: int = 12) -> str:
    """Generate a fresh BIP39 mnemonic. `words` ∈ {12, 24}."""
    if words not in ALLOWED_WORD_COUNTS:
        raise ValueError(f"words must be one of {ALLOWED_WORD_COUNTS}, got {words}")
    strength_bits = ALLOWED_STRENGTHS[words]
    return Mnemonic(DEFAULT_LANG).generate(strength=strength_bits)


def derive_keypair(
    mnemonic: str, *, passphrase: str = ""
) -> DerivedIdentity:
    """Derive an Ed25519 keypair from a BIP39 mnemonic.

    `passphrase` is the BIP39 "25th word" — keep empty unless you know what
    you're doing. The browser side defaults to empty too.
    """
    m = Mnemonic(DEFAULT_LANG)
    if not m.check(mnemonic):
        raise ValueError("invalid BIP39 mnemonic")
    seed = m.to_seed(mnemonic, passphrase=passphrase)
    ed25519_seed = bytes(seed[:32])
    sk = SigningKey(ed25519_seed)
    return DerivedIdentity(
        mnemonic=mnemonic,
        pubkey=bytes(sk.verify_key),
        privkey_seed=ed25519_seed,
    )


def is_valid_mnemonic(mnemonic: str) -> bool:
    return Mnemonic(DEFAULT_LANG).check(mnemonic)


def pubkey_to_fingerprint(pubkey: bytes) -> str:
    """Short hex fingerprint of pubkey for human-readable identifiers."""
    if len(pubkey) != 32:
        raise ValueError("pubkey must be 32 bytes")
    return pubkey.hex()[:16]


def verify_signature(pubkey: bytes, payload: bytes, signature: bytes) -> bool:
    """Detached Ed25519 signature verification. Returns False on any mismatch."""
    from nacl.exceptions import BadSignatureError

    if len(pubkey) != 32 or len(signature) != 64:
        return False
    try:
        VerifyKey(pubkey).verify(payload, signature)
        return True
    except BadSignatureError:
        return False
