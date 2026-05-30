# LCloud HTTP API

> **Base URL**: `https://your-host` (whatever your deployment is)
> **OpenAPI spec**: `GET /openapi.json`
> **Live Swagger UI**: `GET /docs` (interactive)
> **ReDoc**: `GET /redoc`

This document gives runnable curl / Python / JS examples for every
endpoint. For the auto-generated, always-up-to-date schema use
the `/docs` Swagger UI.

## Authentication

LCloud accepts **two** kinds of credentials, in this priority order:

1. **Cookie `lc_user_session`** — automatic after a browser login at
   `/auth/v2/verify`. JWT, HS256, 7-day TTL, HttpOnly + SameSite=Strict.
2. **`Authorization: Bearer lc-XXXXXXXXXXXXXX`** — API keys, format
   `lc-` + 14 chars (17 chars total). Mint at Settings → API keys.

If neither is present (or both are invalid), every protected endpoint
returns:

```json
{"detail": {"reason": "no_credentials"}}
```

with HTTP 401 and `WWW-Authenticate: Bearer realm="LCloud"`.

V1 admin endpoints (`/clouds`, `/files`, etc.) accept only
`lc_session` cookie (V1 admin auth) — left for legacy compatibility,
new clients should use `/api/v1/*` (V2).

## Endpoint summary

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `POST` | `/auth/v2/challenge` | none | Step 1 of seed-phrase login |
| `POST` | `/auth/v2/verify` | none | Step 2 — sets `lc_user_session` |
| `GET` | `/auth/v2/me` | session | Current user info |
| `POST` | `/auth/v2/logout` | session | Clear cookie |
| `POST` | `/api/v1/keys` | session | Mint API key (raw shown once) |
| `GET` | `/api/v1/keys` | session/key | List your keys |
| `DELETE` | `/api/v1/keys/{id}` | session/key | Revoke key |
| `GET` | `/api/v1/clouds` | session/key | List your clouds |
| `POST` | `/api/v1/clouds` | session/key | Create cloud |
| `DELETE` | `/api/v1/clouds/{id}` | session/key | Disconnect cloud |
| `GET` | `/api/v1/clouds/{id}/files` | session/key | List files (paginated) |
| `POST` | `/api/v1/clouds/{id}/files` | session/key | Upload (LC1 or LC2) |
| `GET` | `/api/v1/files/{id}/download` | session/key | Stream file bytes |
| `DELETE` | `/api/v1/files/{id}` | session/key | Soft-delete file |
| `GET` | `/api/v1/files/quota` | session/key | Storage usage |
| `GET` | `/health` | none | Service heartbeat |

## Auth: V2 challenge-response

This is what happens behind the scenes on the web login page. You only
need to do it manually if you're writing a non-browser client.

**Step 1** — get a challenge:

```bash
curl -X POST https://your-host/auth/v2/challenge \
     -H "Content-Type: application/json" \
     -d '{"pubkey": "5eb36f5d...64hex"}'
```

```json
{
  "challenge_jwt": "eyJhbGc...",
  "nonce": "1f5a8c...64hex",
  "expires_in": 60
}
```

**Step 2** — sign the raw `nonce` with your Ed25519 private key, send
back:

```bash
curl -X POST https://your-host/auth/v2/verify \
     -H "Content-Type: application/json" \
     -c cookies.txt \
     -d '{
       "challenge_jwt": "eyJhbGc...",
       "signature": "<128 hex of Ed25519 sig over nonce_bytes>"
     }'
```

```json
{"user_id": 42, "role": "user", "registered": false}
```

The cookie file `cookies.txt` now has `lc_user_session`. Subsequent
requests can use it.

### Python example (full flow)

```python
import requests
from mnemonic import Mnemonic
from nacl.signing import SigningKey

# 1. Derive keypair from your seed phrase
m = Mnemonic("english")
words = "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon art"
seed = m.to_seed(words)[:32]
sk = SigningKey(seed)
pubkey_hex = bytes(sk.verify_key).hex()

# 2. Get challenge
r = requests.post(
    "https://your-host/auth/v2/challenge",
    json={"pubkey": pubkey_hex},
)
ch = r.json()

# 3. Sign nonce
nonce_bytes = bytes.fromhex(ch["nonce"])
sig_hex = sk.sign(nonce_bytes).signature.hex()

# 4. Verify → get session cookie
sess = requests.Session()
sess.post(
    "https://your-host/auth/v2/verify",
    json={"challenge_jwt": ch["challenge_jwt"], "signature": sig_hex},
)

# 5. Use it
me = sess.get("https://your-host/auth/v2/me").json()
print(me)
```

## API keys

For long-lived programmatic access, mint an API key (web UI) and use
`Authorization: Bearer lc-XXX...` instead of the challenge dance.

```bash
KEY="lc-abcdefghij2345"
curl -H "Authorization: Bearer $KEY" \
     https://your-host/auth/v2/me
```

### Mint via API

```bash
curl -X POST https://your-host/api/v1/keys \
     -b cookies.txt \
     -H "Content-Type: application/json" \
     -d '{"label": "production-bot"}'
```

```json
{
  "id": 1,
  "raw": "lc-abcdefghij2345",
  "prefix": "lc-abcde",
  "label": "production-bot",
  "created_at": "2026-05-30T08:00:00+00:00",
  "last_used_at": null,
  "revoked_at": null
}
```

⚠️ **Save `raw` now.** Subsequent `GET /api/v1/keys` returns only the
prefix.

### List & revoke

```bash
# List
curl -H "Authorization: Bearer $KEY" \
     https://your-host/api/v1/keys

# Revoke
curl -X DELETE -H "Authorization: Bearer $KEY" \
     https://your-host/api/v1/keys/1
```

## Clouds

A "cloud" is a Telegram supergroup. The server's userbot creates and
manages it; you (the V2 user) get a logical owner record.

### List your clouds

```bash
curl -H "Authorization: Bearer $KEY" \
     https://your-host/api/v1/clouds
```

```json
[
  {
    "id": 1,
    "chat_id": -1001555000123,
    "name": "MyPhotos",
    "owner_user_id": 42,
    "created_at": "2026-05-30T08:01:00+00:00"
  }
]
```

Admin role sees all clouds across all users.

### Create

```bash
curl -X POST https://your-host/api/v1/clouds \
     -H "Authorization: Bearer $KEY" \
     -H "Content-Type: application/json" \
     -d '{"name": "Documents"}'
```

Returns 201 with the new cloud row.

### Disconnect

```bash
curl -X DELETE \
     -H "Authorization: Bearer $KEY" \
     https://your-host/api/v1/clouds/1
```

204 on success. The TG supergroup is **not** deleted — only the
LCloud DB record + the LCLOUD1 marker in chat description.

## Files

### List (paginated)

```bash
curl -H "Authorization: Bearer $KEY" \
     "https://your-host/api/v1/clouds/1/files?limit=50&offset=0"
```

```json
{
  "items": [
    {
      "id": 17,
      "cloud_id": 1,
      "message_id": 5234,
      "owner_user_id": 42,
      "name": "report.pdf",
      "mime": "application/pdf",
      "size": 234567,
      "uploaded_at": "2026-05-30T09:15:23+00:00",
      "deleted_at": null
    }
  ],
  "total": 1,
  "limit": 50,
  "offset": 0
}
```

### Upload (LC1 — server-signed, easy)

```bash
curl -X POST \
     -H "Authorization: Bearer $KEY" \
     -F "file=@photo.jpg" \
     https://your-host/api/v1/clouds/1/files
```

Returns 201 with `caption_kind: "LC1"`.

### Upload (LC2 — client-signed, recommended)

For real cryptographic ownership, sign the file locally with your
Ed25519 private key. Three extra form fields:

- `client_sha256` (hex 64): SHA-256 of file bytes
- `signature` (hex 128): Ed25519 sig over `sha256_bytes || ts(8B BE) || pubkey_bytes`
- `ts` (int): Unix timestamp you used when signing

```python
import hashlib, requests, struct, time
from nacl.signing import SigningKey

sk = SigningKey(...)  # your key
file_path = "photo.jpg"

# Compute SHA-256 of file
with open(file_path, "rb") as f:
    sha = hashlib.sha256(f.read()).digest()

# Build canonical payload
ts = int(time.time())
pub = bytes(sk.verify_key)
payload = sha + struct.pack(">Q", ts) + pub  # 32 + 8 + 32 = 72 bytes

# Sign
sig = sk.sign(payload).signature

# Upload
with open(file_path, "rb") as f:
    r = requests.post(
        "https://your-host/api/v1/clouds/1/files",
        files={"file": ("photo.jpg", f, "image/jpeg")},
        data={
            "client_sha256": sha.hex(),
            "signature": sig.hex(),
            "ts": str(ts),
        },
        headers={"Authorization": f"Bearer {KEY}"},
    )
print(r.json())  # {"caption_kind": "LC2", ...}
```

The server verifies your signature and writes a `LC2:{...}` caption to
the Telegram message. Anyone holding your pubkey + the file + the
caption can verify ownership offline.

### Download

```bash
curl -H "Authorization: Bearer $KEY" \
     -o downloaded.pdf \
     https://your-host/api/v1/files/17/download
```

### Delete (soft)

```bash
curl -X DELETE \
     -H "Authorization: Bearer $KEY" \
     https://your-host/api/v1/files/17
```

204 on success. The TG message is also deleted (best effort). Frees
quota.

### Quota

```bash
curl -H "Authorization: Bearer $KEY" \
     https://your-host/api/v1/files/quota
```

```json
{
  "used_bytes": 1234567,
  "quota_bytes": 5368709120,
  "free_bytes": 5367474553
}
```

## Errors

All error responses are JSON of the form:

```json
{"detail": {"reason": "<reason_code>", ...extra context}}
```

Common reason codes:

| HTTP | Reason | Meaning |
|---|---|---|
| 401 | `no_credentials` | Missing cookie + missing/invalid Bearer |
| 401 | `bad_signature` | V2 verify: nonce signature didn't match pubkey |
| 401 | `challenge_replay` | Same nonce verified twice |
| 401 | `challenge_expired` | challenge_jwt past its 60-sec TTL |
| 403 | `suspended` | User account suspended |
| 403 | `forbidden` | Trying to access another user's resource |
| 404 | `cloud_not_found` / `file_not_found` | … |
| 400 | `lc2_sha256_mismatch` | Server hash != client hash |
| 400 | `lc2_verify_failed` | Signature verification failed (with `why` field) |
| 400 | `key_limit_reached` | 25 active API keys already |
| 413 | `file_too_large` | Above `LC_MAX_FILE_BYTES` |
| 413 | `quota_exceeded` | Would exceed user's storage quota |
| 429 | `rate_limited` | Auth endpoints rate-limit 10/5min/IP |
| 502 | `telegram_upload_failed` / `telegram_rpc_error` | TG-side issue |
| 503 | `userbot_not_started` / `userbot_not_authorized` | Server's TG session is down |

## Rate limits

| Scope | Limit |
|---|---|
| `/auth/v2/challenge` + `/verify` | 10 / 5 min / IP |
| `/auth/telegram/start` (V1 admin) | 5 / hour |
| Telegram MTProto calls | server-side queue, ~30 req/sec to TG |
| API keys per user | 25 active |

## OpenAPI / Swagger

For an interactive playground hitting your real instance:

- **Swagger UI**: `https://your-host/docs`
- **ReDoc**: `https://your-host/redoc`
- **Raw JSON**: `https://your-host/openapi.json`

You can `Authorize` in Swagger UI by pasting your `lc-XXX` Bearer token
(top-right lock icon).

## Versioning

V2 endpoints (`/api/v1/*` and `/auth/v2/*`) are stable. V1 admin
endpoints (`/clouds`, `/files`, ...) are legacy — kept for backwards
compat with the original admin web UI but not recommended for new
clients.
