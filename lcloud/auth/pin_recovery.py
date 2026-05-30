"""PIN-protected seed-phrase recovery.

Storage model:
    users.pin_hash         — argon2id(PIN); used only to verify PIN, never to derive keys
    users.seed_salt        — random 16 bytes per user; mixed into the KDF for the encryption key
    users.encrypted_seed   — Box-encrypted blob: 24-byte nonce + ciphertext+MAC

Flow:
    set_pin(user, pin, mnemonic):
        salt = secrets.token_bytes(16)
        kdf_key = argon2id_kdf(pin, salt, ops=3, mem=64MiB) -> 32 bytes
        cipher  = SecretBox(kdf_key).encrypt(mnemonic_bytes)  -> nonce|ct|mac
        users.pin_hash       = argon2_hash(pin)               -> separate, for verify
        users.seed_salt      = salt
        users.encrypted_seed = cipher

    recover(user, pin):
        argon2_verify(users.pin_hash, pin)  # constant-time, slow on purpose
        kdf_key = argon2id_kdf(pin, users.seed_salt, ...) -> 32 bytes
        mnemonic_bytes = SecretBox(kdf_key).decrypt(users.encrypted_seed)
        return mnemonic_bytes.decode()

A 4-digit PIN by itself is brittle (10 000 possibilities). Defenses:
    1. argon2id costs ~30ms/attempt → online brute force is 333/sec/host
    2. pin_failed_attempts++ on every miss; ≥5 → pin_locked_until = +1h
    3. /auth/v2/pin/recover has IP rate limit of 10/hour
    4. Even if the encrypted_seed blob leaks, attacker still has to do
       ~10000 * 30ms = 5min of CPU per user offline → still not catastrophic
       and not directly online-exploitable.
"""

from __future__ import annotations

import logging
import secrets

from argon2 import PasswordHasher
from argon2 import exceptions as argon2_exc
from nacl.exceptions import CryptoError
from nacl.pwhash import argon2id
from nacl.secret import SecretBox

logger = logging.getLogger(__name__)

# argon2id parameters for KDF (encryption key derivation).
# These match libsodium's INTERACTIVE profile — ~30 ms per derivation.
_KDF_OPS = argon2id.OPSLIMIT_INTERACTIVE
_KDF_MEM = argon2id.MEMLIMIT_INTERACTIVE
KDF_KEY_LEN = SecretBox.KEY_SIZE  # 32 bytes

# Separate hasher for PIN verification (argon2id with similar params).
_ph = PasswordHasher(
    time_cost=2,
    memory_cost=64 * 1024,  # 64 MiB
    parallelism=2,
    hash_len=32,
)


PIN_LENGTH = 4
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_SECONDS = 3600


def is_valid_pin(pin: str) -> bool:
    """Tight validator: exactly N digits, no whitespace."""
    return len(pin) == PIN_LENGTH and pin.isdigit()


def hash_pin(pin: str) -> str:
    """argon2id hash for verification (returned as the canonical $argon2... string)."""
    return _ph.hash(pin)


def verify_pin(pin: str, stored_hash: str) -> bool:
    """Constant-time + slow verification."""
    try:
        return _ph.verify(stored_hash, pin)
    except (
        argon2_exc.VerifyMismatchError,
        argon2_exc.InvalidHashError,
        argon2_exc.VerificationError,
    ):
        return False


def derive_kdf_key(pin: str, salt: bytes) -> bytes:
    """argon2id-based key derivation for SecretBox (deterministic for same PIN+salt)."""
    if len(salt) < 16:
        raise ValueError("salt must be at least 16 bytes")
    return argon2id.kdf(
        KDF_KEY_LEN,
        pin.encode("utf-8"),
        salt[: argon2id.SALTBYTES],
        opslimit=_KDF_OPS,
        memlimit=_KDF_MEM,
    )


def encrypt_seed(pin: str, mnemonic: str) -> tuple[bytes, bytes]:
    """Encrypt `mnemonic` with a key derived from `pin`.

    Returns (salt, ciphertext) where ciphertext is `nonce || ct || mac`.
    Caller persists both alongside the user record.
    """
    salt = secrets.token_bytes(argon2id.SALTBYTES)
    key = derive_kdf_key(pin, salt)
    box = SecretBox(key)
    ciphertext = box.encrypt(mnemonic.encode("utf-8"))
    # nacl returns an EncryptedMessage which behaves like bytes (nonce||ct||mac).
    return salt, bytes(ciphertext)


def decrypt_seed(pin: str, salt: bytes, ciphertext: bytes) -> str | None:
    """Try to decrypt the stored ciphertext. Returns None on any error.

    The caller is responsible for verifying the PIN hash separately
    (this function does NOT panic on wrong PIN, but `decrypt` will fail
    its MAC check and we catch CryptoError to return None).
    """
    try:
        key = derive_kdf_key(pin, salt)
        box = SecretBox(key)
        plain = box.decrypt(ciphertext)
        return plain.decode("utf-8")
    except (CryptoError, ValueError, UnicodeDecodeError):
        return None


__all__ = [
    "LOCKOUT_SECONDS",
    "MAX_FAILED_ATTEMPTS",
    "PIN_LENGTH",
    "decrypt_seed",
    "derive_kdf_key",
    "encrypt_seed",
    "hash_pin",
    "is_valid_pin",
    "verify_pin",
]
