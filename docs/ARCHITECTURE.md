# Architecture

This is the contributor guide. If you want to understand how LCloud is
put together, fix a bug, or add a feature — start here.

## Big picture

```
┌──────────┐     HTTPS     ┌─────────────────────┐    MTProto   ┌──────────┐
│ Browser  │ ◄──────────► │  FastAPI (LCloud)   │ ◄──────────► │ Telegram │
│ React UI │               │  + Telethon userbot │              │  servers │
└──────────┘               └─────────────────────┘              └──────────┘
                                  │
                                  ├── SQLite (data/lcloud.db)
                                  └── Files in TG supergroups
```

Everything runs in **one Python process**:

- FastAPI HTTP server (uvicorn)
- Telethon `TelegramClient` (asyncio, same event loop)
- SQLite via SQLAlchemy 2.0 async (aiosqlite)

Files are stored as Telegram documents in supergroups owned by the
operator's TG account. LCloud's DB stores only metadata + content
hashes + signatures — never file bytes.

## Data flow: file upload (V2 / LC2)

```
Client                                        Server
──────                                        ──────
1. User selects file
2. Browser:
   a. SHA-256 over file bytes
   b. ts := now()
   c. payload := sha256 || ts(8B BE) || pubkey   (72 B)
   d. sig := Ed25519.sign(privkey, payload)      (64 B)
3. POST multipart:
   - file (binary)
   - client_sha256 (hex 64)
   - signature (hex 128)
   - ts (int)
                            ──────────────►
                                              4. Stream file to disk,
                                                 server-side SHA-256.
                                              5. Verify client_sha256 == server_sha256
                                              6. Verify Ed25519.verify(
                                                   pubkey, payload, sig)
                                              7. Quota check (assert_can_store)
                                              8. send_file() to TG → message_id
                                              9. edit_message_caption(
                                                   "LC2:{o,h,s,t}")
                                              10. INSERT files row
                                                  + UPDATE users.storage_used_bytes
                            ◄──────────────
11. Response: file metadata + caption_kind="LC2"
```

The server **never sees the user's private key**. The signature proves
the file's content + its owner identity in a way anyone with just the
pubkey can verify, without contacting LCloud.

## Auth state machine

```
                         ┌─────────────────────┐
                         │ Browser opens / page│
                         └──────────┬──────────┘
                                    │
                          GET /auth/v2/me
                                    │
                  ┌─────────────────┴────────────────┐
                  ▼                                  ▼
          200 me {pubkey, role}              401 no_credentials
                  │                                  │
                  ▼                                  ▼
         Show main app UI         GET /auth/state (legacy V1)
                                                     │
                            ┌───────────────────────┼───────────────────────┐
                            ▼                       ▼                       ▼
                  userbot_authed=true        userbot_authed=false        bootstrap=true
                            │                       │                       │
                            ▼                       ▼                       ▼
              "Войти по сид-фразе"       Phone+code form (V1 admin)    Same — first-time
              or "Создать новый"         (one-time bootstrap)
                            │                       │                       │
                            ▼                       ▼                       ▼
              POST /auth/v2/challenge     POST /auth/telegram/start    Same flow
              POST /auth/v2/verify        POST /auth/telegram/code     ↓
                            │             POST /auth/telegram/password Auto-generates
                            ▼                       │                  12-word seed,
                  Sets lc_user_session              ▼                  sends to Saved Messages
                            │             Sets lc_session cookie
                            ▼             userbot stamps admin.tgid
                       App loads          Phase 2: user re-logins via seed
```

## Database schema

```
owners                       (V1, single admin)
  id              PK
  pubkey          UNIQUE      Ed25519 admin keypair
  role            'admin'
  ...

users                        (V2, multi-user)
  id              PK
  pubkey          UNIQUE      Ed25519 from BIP39
  role            'user'/'admin'
  storage_used_bytes
  storage_quota_bytes
  suspended_at
  ...

api_keys
  id              PK
  user_id         FK → users
  hash            argon2id of full key
  prefix          first 8 chars (indexed)
  ...

auth_challenges
  nonce           UNIQUE      Server-generated random 32B
  pubkey          The pubkey that requested the challenge
  expires_at, consumed_at

clouds
  id              PK
  chat_id         UNIQUE      Telegram chat ID
  owner_id        FK → owners (admin = TG-side signer)
  owner_user_id   FK → users  (V2 logical owner; NULL = admin legacy)
  name, about     ...

files
  id              PK
  cloud_id        FK
  message_id                 TG message ID inside that cloud
  owner_id        FK → owners (admin = TG-side signer)
  owner_user_id   FK → users  (V2 logical owner; NULL = admin legacy)
  original_name, mime, size_bytes
  sha256                     (32 bytes BLOB)
  signature                  (64 bytes BLOB — LC1 admin sig OR LC2 user sig)
  uploaded_at, deleted_at

tags, file_tags              (many-to-many)
files_fts                    (SQLite FTS5 virtual table on original_name)

used_tokens                  (replay protection for magic links)
auth_state                   (per-owner epoch for cookie revocation)
```

## Module map

### Backend (`lcloud/`)

```
lcloud/
├── main.py              FastAPI app factory, lifespan, SPA mount
├── config.py            Settings (pydantic-settings, env-driven)
│
├── api/
│   ├── auth.py          V1 phone+code login (POST /auth/telegram/*)
│   ├── auth_v2.py       V2 BIP39 challenge-response
│   ├── api_keys.py      /api/v1/keys CRUD
│   ├── v2_clouds.py     /api/v1/clouds (per-user CRUD)
│   ├── v2_files.py      /api/v1/files (LC1+LC2 upload, list, dl, delete, quota)
│   ├── clouds.py        V1 admin clouds (legacy)
│   ├── files.py         V1 admin files (legacy)
│   ├── tags.py          Tags CRUD + assign
│   ├── search.py        FTS5 search
│   └── magic.py         Magic-link admin login
│
├── auth/
│   ├── seed.py          BIP39 ↔ Ed25519 derivation
│   ├── api_keys.py      lc-XXX key mint/verify (argon2id)
│   ├── deps.py          V1 require_admin (cookie-only)
│   ├── v2_deps.py       V2 get_current_user (cookie OR Bearer)
│   ├── jwt_utils.py     HS256 secret + token issue/decode
│   ├── cookies.py       Cookie name/flags helpers
│   └── storage_quota.py Per-user quota helpers
│
├── crypto/
│   ├── keys.py          Admin Ed25519 keypair on disk (V1 root of trust)
│   ├── lc1.py           V1 caption format + signature payload
│   └── lc2.py           V2 caption format + canonical_payload + verify
│
├── db/
│   ├── base.py          Async engine, session factory
│   ├── models.py        SQLAlchemy ORM models
│   └── bootstrap.py     run_migrations + ensure_admin_owner
│
├── userbot/
│   ├── client.py        UserbotManager — Telethon connect + state machine
│   ├── handlers.py      NewMessage handler (incoming files → DB)
│   ├── commands.py      Saved Messages /help /admin etc.
│   ├── inchat.py        /lc_connect /lc_disconnect inside any chat
│   ├── files.py         Upload/download via Telethon
│   ├── files_lc2.py     LC2-flavoured upload (no server signing)
│   ├── clouds.py        Create/clear cloud supergroups
│   ├── admin_bootstrap.py  Generate + send admin seed phrase
│   ├── scan.py          Background dialog scan to discover existing clouds
│   ├── login.py         CLI fallback for phone+code
│   └── session.py       Telethon session file management
│
└── workers/
    ├── pool.py          Async semaphore (cap N concurrent TG ops)
    └── rate_limiter.py  MTProto token bucket + FloodWait retry
```

### Frontend (`web/src/`)

```
web/src/
├── App.tsx              Top-level component, auth gate
├── api/
│   ├── client.ts        V1+V2 fetch wrapper, files.upload (XHR + LC2 sign)
│   ├── v2_client.ts     Standalone V2 typed client
│   └── types.ts         Shared TS types
├── auth/
│   ├── seed.ts          BIP39 + Ed25519 + SHA-256 (browser)
│   └── lc2.ts           canonicalPayload + signFileForUpload
├── components/
│   ├── LoginScreen.tsx        Orchestrator (bootstrap / login / create)
│   ├── PasteSeedLogin.tsx     Existing-account login
│   ├── CreateAccountScreen.tsx  Generate fresh seed
│   ├── BootstrapAdminTGForm.tsx Phone+code (V1 admin one-time)
│   ├── Sidebar.tsx            Cloud list + create/delete
│   ├── FilesPanel.tsx         Grid/list view, drag-drop upload, search
│   ├── FilePreviewModal.tsx   Image/video/audio/pdf preview + rename
│   ├── SettingsModal.tsx      3-tab: General / API keys / Account
│   ├── ApiKeysSection.tsx
│   ├── AccountSection.tsx
│   ├── Tags.tsx               AssignTagsModal + EditTagModal
│   └── ui/                    Reusable Button, Modal, TextField, ...
└── hooks/
    ├── useAuth.ts             V1 admin (cookie polling)
    └── useAuthV2.ts           V2 user (cookie + sessionStorage keypair)
```

## Storage isolation

There is **one Telegram account** running the userbot. All clouds and
files physically live there. The "multi-user" aspect is purely logical:

- `clouds.owner_user_id` / `files.owner_user_id` are FK to `users`
- Endpoint filters: regular users see only `WHERE owner_user_id = me`,
  admins see everything
- Quota tracking is per-user via `users.storage_used_bytes`
- Files are still readable by the operator (admin can `GET /api/v1/files/{id}/download`
  on any user's file)

True end-to-end encryption (operator can't read user files) would require
client-side encryption before upload — that's a V3 feature, not done.

## Lifespan / startup order

```python
# lcloud/main.py
async with lifespan(app):
    1. ensure_runtime_dirs()              # data/, data/keys/, data/tmp/
    2. ensure_admin_keypair()             # data/keys/admin.{key,pub} (V1)
    3. ensure_jwt_secret()                # data/keys/jwt.secret (HS256)
    4. init_engine() + run_migrations()   # SQLite + Alembic
    5. ensure_admin_owner()               # owners table single row
    6. UserbotManager.start()             # Telethon connect (no auth needed)
    7. _post_login_scan_if_authorized()   # if userbot already authed:
       a. attach NewMessage / Saved Messages handlers
       b. schedule_scan() — discover clouds in dialogs
       c. ensure_admin_seed_delivered() — V2 admin user + seed via TG
    yield
    8. UserbotManager.stop()
    9. dispose_engine()
```

## Tests

`tests/` — 202 tests covering:

- DB migration round-trips
- BIP39 + Ed25519 derivation
- LC2 canonical payload + signature verify (12 cases)
- V2 auth challenge/verify (10 cases inc. replay, bad sig, ts skew)
- API keys mint/list/revoke + Bearer auth (15 cases)
- V2 clouds + files + quota e2e (15 cases inc. cross-user 403)
- Admin seed bootstrap (3 cases)
- V1 endpoints + Telethon mocks (rest)

Run:

```bash
.venv/bin/pytest                # 202 tests
.venv/bin/ruff check .          # lint
.venv/bin/mypy lcloud           # strict type check
```

## Adding a new V2 endpoint

1. Add the route in `lcloud/api/v2_*.py`. Use:

   ```python
   from lcloud.auth.v2_deps import CurrentUser

   @router.get("/some/path", summary="...", description="...")
   async def my_endpoint(user: CurrentUser) -> dict[str, Any]:
       ...
   ```

2. The `CurrentUser` dep handles cookie + Bearer auth and returns a
   `User` ORM row. Use `user.role` to check admin, `user.id` to filter
   by owner.

3. If your endpoint touches Telegram, take `manager: UserbotManager =
   Depends(get_userbot_manager)` and call `_ensure_userbot_authorized()`
   first.

4. Wrap TG calls in `pool.submit(coro)` to respect the worker pool.

5. Write a test in `tests/`. Use the `app_with_userbot` fixture pattern
   from `tests/test_v2_clouds_files.py` (mocks Telethon + login flow).

6. Update `docs/API.md` with curl/Python example.

## Adding a new DB migration

```bash
.venv/bin/alembic revision -m "add foo column to bar"
# Edit alembic/versions/000X_*.py
# IMPORTANT: SQLite needs batch_alter_table for ALTER TABLE
.venv/bin/alembic upgrade head     # apply locally
.venv/bin/pytest tests/test_db.py  # roundtrip test
```

`run_migrations()` at startup auto-applies pending migrations to
production.

## Safety invariants

These will break things if you mess with them:

- `data/keys/admin.key` — V1 admin Ed25519 privkey. **Never delete**;
  V1 file signatures depend on it.
- `data/keys/jwt.secret` — HS256 secret. Rotate = invalidates ALL
  sessions and challenges.
- `data/keys/admin.tgid` — bootstrap stamp, mode 600.
- `data/session.lcloud.session` — Telethon SQLite. **Never delete
  while running** = userbot loses auth.
- `lcloud.service` systemd: do NOT add `ProtectSystem=strict` or
  `ProtectHome=read-only` — they make Telethon's keepalive write
  attempts fail with "readonly database" errors.
