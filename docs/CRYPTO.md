# Crypto model

This document explains what cryptographic primitives LCloud uses and
why. Read this if you're a security-conscious user, an auditor, or a
developer adding crypto-touching features.

## TL;DR

- **Identity**: BIP39 mnemonic (12 or 24 words) → Ed25519 keypair
- **Auth**: Challenge-response — server sends nonce, you sign it
- **File ownership**: LC2 caption — client signs `sha256 || ts || pubkey`
  with their Ed25519 privkey, server verifies
- **Server has zero secrets about you** — only your public key
- **API keys** are independently random (not seed-derived) and stored
  as argon2id hashes

## What the seed phrase actually is

A standard BIP39 mnemonic. We use the English wordlist (2048 words).
The chain from words to keypair:

```
"abandon abandon ... art"  (12 or 24 words)
        │
        │  PBKDF2-HMAC-SHA512(mnemonic, "mnemonic" + passphrase, 2048 iters)
        ▼
seed (64 bytes)
        │
        │  take first 32 bytes
        ▼
ed25519_seed (32 bytes)
        │
        │  Ed25519.SigningKey(seed)
        ▼
keypair (privkey 32B, pubkey 32B)
```

This is **the same** as what wallet apps do. You can in principle import
your LCloud seed into a Bitcoin/Ethereum/whatever wallet and they'd
derive a coin-specific key from it. (Don't actually do this — keep
keys siloed by purpose.)

The `passphrase` parameter is BIP39's optional "25th word". LCloud uses
empty passphrase by default. If you want extra security, you'd need to
patch both backend and frontend.

### Word counts

| Words | Entropy | Brute-force at 1B/sec |
|---|---|---|
| 12 | 128 bits | 10^22 years |
| 24 | 256 bits | 10^60 years |

12 is plenty. 24 is plenty^plenty.

## Login: challenge-response

```
Browser                                              Server
─────                                                ──────
1. User pastes seed → derive keypair (in browser)
   pubkey, privkey are now in browser memory only
2. POST /auth/v2/challenge
   { pubkey: "..." }
                              ──────────►
                                                     3. Generate random 32B nonce
                                                     4. Sign challenge_jwt
                                                        (HS256 with server secret,
                                                         contains nonce, exp,
                                                         pubkey, kind)
                                                     5. INSERT auth_challenges row
                              ◄──────────
6. challenge_jwt + nonce returned
7. signature := Ed25519.sign(privkey, nonce_bytes)
8. POST /auth/v2/verify
   { challenge_jwt, signature }
                              ──────────►
                                                     9.  Decode challenge_jwt
                                                         (verify HS256 + exp)
                                                     10. Check auth_challenges
                                                         → not consumed (replay
                                                         protection)
                                                     11. Mark consumed
                                                     12. Ed25519.verify(
                                                          pubkey, signature, nonce)
                                                     13. INSERT users row if new
                                                     14. Issue lc_user_session
                                                         cookie (HS256, kind=user_session,
                                                         user_id, role, exp+7d)
                              ◄──────────
15. {user_id, role, registered}, Set-Cookie
```

### Why challenge-response, not password?

- We never have to store anything secret on the server. Even with full
  DB compromise, attacker can't sign as you because they don't have
  your privkey.
- You can prove ownership of your pubkey to anyone, anywhere, just by
  signing a one-time message. The server is not a special verifier.

### Why a server-signed challenge_jwt?

We could have stored the nonce purely server-side (Redis-style) and
asked the client to send it back. Wrapping it in a JWT lets:

- The challenge be self-contained (no Redis dependency)
- Replay protection is still done via DB row check on `consumed_at`
  (otherwise the same JWT could be re-used until exp)

## File signing: LC2 caption

When a logged-in V2 user uploads a file, their browser signs the file
locally **before** the upload starts. The signature ends up embedded
in the Telegram message caption.

### Canonical signature payload

```
sha256_of_file_bytes  (32 bytes)
+ unix_timestamp      (8 bytes, big-endian unsigned)
+ pubkey              (32 bytes)
─────────────────────────────────
                       72 bytes
```

The user's privkey signs these 72 bytes. Result: 64-byte Ed25519
signature.

### Why these three fields?

- `sha256` — binds the signature to the **content**. If anyone changes
  even one bit of the file, the signature no longer verifies.
- `ts` — proves the signature was made at a specific moment in time
  (or rather, ±24h of server clock). Provides replay-window protection
  if the signature were exposed somewhere.
- `pubkey` — embeds the signer's identity. Without this, a signature
  for `sha256 || ts` could be replayed by a different user with a
  different pubkey trying to claim ownership.

### Why not include `message_id`?

Because the client doesn't know it pre-upload — Telegram assigns
message IDs on send. We could do a two-roundtrip dance (upload →
get message_id → re-sign → edit caption), but the simpler 3-field
canonical payload gives the same security properties as long as you
trust the *(pubkey, ts)* pair to be unique enough. In practice it is.

### Caption format on the wire

The Telegram message caption is exactly:

```
LC2:{"o":"<pub_hex>","h":"<sha256_hex>","s":"<sig_hex>","t":<unix>}
```

About 330 chars total — well under TG's 1024-char caption cap.

### Verification (offline!)

Anyone with the file + pubkey + caption can verify ownership
**without** contacting LCloud:

```python
import hashlib, json, struct
from nacl.signing import VerifyKey
from nacl.exceptions import BadSignatureError

def verify_lc2(file_bytes: bytes, caption: str, expected_pubkey_hex: str) -> bool:
    if not caption.startswith("LC2:"):
        return False
    body = json.loads(caption[4:])
    pubkey = bytes.fromhex(body["o"])
    sha = bytes.fromhex(body["h"])
    sig = bytes.fromhex(body["s"])
    ts = int(body["t"])

    # Pubkey must match the one we expect to be the owner
    if pubkey.hex() != expected_pubkey_hex:
        return False

    # Server-side hash check
    if hashlib.sha256(file_bytes).digest() != sha:
        return False

    # Signature check
    payload = sha + struct.pack(">Q", ts) + pubkey
    try:
        VerifyKey(pubkey).verify(payload, sig)
        return True
    except BadSignatureError:
        return False
```

This means the server is **not** a trusted intermediary for the
ownership claim. If you suspect LCloud is malicious and re-signed your
file, anyone can detect it: the LC2 signature would fail to verify
against your expected pubkey.

What the server *can* do: refuse to upload, or quietly delete files,
or modify the bytes (then verification fails). What it *cannot* do:
forge an LC2 signature claiming you uploaded something you didn't.

## V1 LC1 caption (legacy / fallback)

For uploads without client signing (e.g. raw `curl -F file=@...`
without LC2 fields), the server falls back to:

```
LC1:{"o":"<admin_pub_hex>","s":"<admin_sig_hex>","h":"<sha256_hex>",
     "ts":<unix>,"chat":<chat_id>,"msg":<message_id>}
```

This is signed by the **server's admin private key** (the V1 root of
trust at `data/keys/admin.key`). It proves the file passed through this
LCloud instance, but not who specifically owned it. New clients should
prefer LC2.

## API keys

API keys are **NOT** seed-derived. They are independent CSPRNG output:

```
raw = "lc-" + 14 chars from base32-no-confusion alphabet
              (32 chars: a-z minus o,l,i + 2-9 minus 0,1)
```

- 14 × log2(32) = **70 bits** of entropy
- At 10^9 brute-force guesses per second: ~37 years to crack one key

### Storage

Server stores:

- `api_keys.prefix` — first 8 chars (`lc-` + 5 entropy chars), indexed
- `api_keys.hash` — argon2id of the **full** raw key

argon2 params: time_cost=2, memory_cost=64 MiB, parallelism=2, output=32B.
Empirically ~30 ms per verify on modern hardware.

### Lookup

```python
prefix = raw[:8]
candidates = SELECT * FROM api_keys WHERE prefix = ? AND revoked_at IS NULL
for c in candidates:
    if argon2.verify(c.hash, raw):
        return c.user_id
```

Prefix-indexed → typically 1 candidate per check (collision prob ~1 in
30 million per active prefix). Argon2 is run once per request, slow
enough to make brute force impractical even with the prefix narrowing.

## What the server has on disk

| File | Mode | What |
|---|---|---|
| `data/lcloud.db` | 600 | SQLite — users, clouds, files metadata, **api_keys hashes**, auth_challenges |
| `data/keys/admin.key` | 600 | V1 admin Ed25519 privkey — root of trust for LC1 |
| `data/keys/admin.pub` | 644 | V1 admin pubkey |
| `data/keys/admin.tgid` | 600 | Bootstrap stamp = TG account ID |
| `data/keys/jwt.secret` | 600 | HS256 secret for `lc_session` and `lc_user_session` cookies |
| `data/session.lcloud.session` | 600 | Telethon SQLite — TG auth state |

If `data/keys/jwt.secret` leaks: attacker can forge any cookie. Rotate
it = all live sessions are invalidated, everyone re-logs.

If `data/keys/admin.key` leaks: attacker can forge LC1 captions claiming
the admin uploaded something. Doesn't help them sign LC2 (those are
user keys). Worst case: legacy V1 file ownership becomes ambiguous.

If `data/lcloud.db` leaks: attacker has hashes of API keys (argon2
makes those expensive to crack), and the public keys + storage stats of
all users (no sensitive data). Cannot impersonate users without their
seed phrases.

## Threat model summary

| Attacker has | Can they... |
|---|---|
| User seed phrase | Yes, fully. They become the user. |
| User session cookie | Yes, until cookie expires (7d) or user logs out (epoch bump on V1 only) |
| API key (raw) | Yes, until revoked |
| Server SSH access | Almost everything: read DB, read files, sign as admin (V1), forge sessions. NOT sign as users (LC2). |
| Server DB dump only (no key files) | Read metadata. argon2 hashes resist offline crack. Can't forge sessions (no JWT secret). |
| Network traffic between user and server | Nothing — TLS. |
| One running JS process in user's browser | Read sessionStorage (privkey for current session). Cannot read HttpOnly cookie. |

## Deliberately out of scope (V3+)

- **End-to-end encryption** of file content — the operator can read
  every file. Real E2E would require client-side AES-GCM + key
  agreement.
- **Forward secrecy** for sessions — JWT secret rotation is manual.
- **Multi-device sync of API key revocation** — already works server-side
  but no client-side push.
- **Hardware-backed keys** — privkeys live in browser memory, not
  TEE/secure enclave.
