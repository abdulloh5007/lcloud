# Operator guide

How to deploy and run LCloud yourself.

## Prerequisites

- Linux box with Python 3.12, Node 20+
- A Telegram account (will be the userbot account hosting all files)
- TG API ID + hash from <https://my.telegram.org/apps>
- A domain + TLS termination (we use nginx + Let's Encrypt)

## Install

```bash
git clone https://github.com/<owner>/LCloud
cd LCloud

python3.12 -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'

# Frontend
cd web
npm install
npm run build
cd ..

# Configure
cp .env.example .env
# Edit:
#   TG_API_ID, TG_API_HASH       — from my.telegram.org
#   LC_ADMIN_TG_ID=0              — bootstrap mode (first phone+code login claims it)
#   LC_HOST=127.0.0.1            — bind interface
#   LC_PORT=8787
#   LC_PUBLIC_BASE_URL=https://your-domain
#   LC_COOKIE_SECURE=true        — flip to false ONLY for plain-HTTP dev
```

## Run as systemd service

```ini
# /etc/systemd/system/lcloud.service
[Unit]
Description=LCloud
After=network-online.target

[Service]
Type=simple
WorkingDirectory=/root/LCloud
ExecStart=/root/LCloud/.venv/bin/lcloud
Restart=always
RestartSec=3
StandardOutput=append:/root/LCloud/logs/lcloud.log
StandardError=append:/root/LCloud/logs/lcloud.err
NoNewPrivileges=true
PrivateTmp=true
RestrictSUIDSGID=true

[Install]
WantedBy=multi-user.target
```

⚠️ **Do NOT add** `ProtectSystem=strict` or `ProtectHome=read-only` —
they break Telethon's keepalive (it gets `readonly database` errors
trying to update its session SQLite).

```bash
mkdir -p /root/LCloud/logs
systemctl daemon-reload
systemctl enable --now lcloud
journalctl -u lcloud -f
```

## Reverse proxy (nginx)

```nginx
# /etc/nginx/sites-available/lcloud
server {
    listen 443 ssl http2;
    server_name your-domain;

    ssl_certificate     /etc/letsencrypt/live/your-domain/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/your-domain/privkey.pem;

    client_max_body_size 1500M;  # match LC_MAX_FILE_BYTES

    location / {
        proxy_pass http://127.0.0.1:8787;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_request_buffering off;       # streaming uploads
        proxy_buffering          off;
        proxy_read_timeout       300s;
    }
}

server {
    listen 80;
    server_name your-domain;
    return 301 https://$host$request_uri;
}
```

## First-run bootstrap

1. Open `https://your-domain/` in a browser.
2. You'll see a phone+code form ("Юзербот не авторизован — войдите
   своим Telegram-аккаунтом").
3. Enter your Telegram phone, paste the code Telegram sends (and 2FA
   password if enabled).
4. Open Saved Messages in Telegram → you should see a message from
   yourself with **12 BIP39 words**. **Save them**.
5. Refresh the LCloud page → switch to "Войти по сид-фразе" → paste
   the 12 words → you're admin.

## Configuration reference

All env vars also work with `LC_` prefix in code (handled by pydantic).

| Var | Default | Meaning |
|---|---|---|
| `TG_API_ID` | (required) | From my.telegram.org |
| `TG_API_HASH` | (required) | From my.telegram.org |
| `LC_ADMIN_TG_ID` | `0` | TG numeric ID of admin. `0` = bootstrap mode (first login claims it via stamp file) |
| `LC_HOST` | `127.0.0.1` | Bind interface |
| `LC_PORT` | `8787` | Bind port |
| `LC_DATA_DIR` | `data/` | Where DB, keys, session live |
| `LC_DB_URL` | `sqlite+aiosqlite:///data/lcloud.db` | SQLAlchemy URL |
| `LC_PUBLIC_BASE_URL` | `http://localhost:8787` | Used in magic links |
| `LC_COOKIE_SECURE` | `true` | Set false ONLY for plain-HTTP dev |
| `LC_MAX_FILE_BYTES` | `1073741824` (1 GiB) | Upload cap |
| `LC_LOG_LEVEL` | `INFO` | Python logging |

## Day-to-day operations

### See logs

```bash
journalctl -u lcloud -f
tail -f /root/LCloud/logs/lcloud.{log,err}
```

### Restart

```bash
systemctl restart lcloud
```

### Database backups

The DB is just SQLite at `data/lcloud.db`. Plain `cp` works (briefly stop
the service for consistency, or use `sqlite3 .backup`):

```bash
sqlite3 /root/LCloud/data/lcloud.db ".backup /tmp/lcloud-$(date +%F).db"
```

⚠️ **Also back up** `data/keys/` (admin.key, admin.pub, jwt.secret,
admin.tgid) and `data/session.lcloud.session`. Lose admin.key →
historical V1 file signatures unverifiable. Lose Telethon session →
need re-login phone+code.

### Suspending a user

```sql
-- Via sqlite3 directly, until we add an admin UI:
UPDATE users SET suspended_at = datetime('now') WHERE id = 42;
```

### Bumping a user's quota

```sql
UPDATE users SET storage_quota_bytes = 50 * 1024 * 1024 * 1024  -- 50 GiB
WHERE id = 42;
```

### Recompute storage_used (after manual deletes)

```python
# python -c
from lcloud.auth.storage_quota import recompute_used
import asyncio
asyncio.run(recompute_used(42))  # user_id
```

### Resetting an admin who lost their seed phrase

The admin keypair stamp lives in `users` table. To re-bootstrap:

```bash
sqlite3 data/lcloud.db "DELETE FROM users WHERE role='admin';"
systemctl restart lcloud
```

On next start, `ensure_admin_seed_delivered()` will detect the missing
admin row, generate fresh 12 words, and send them to Saved Messages
again.

⚠️ This invalidates the previous admin's pubkey. Existing files signed
under that key (V2 LC2) can't be verified anymore against the new
admin pubkey. V1 LC1 file signatures are unaffected (those use
`data/keys/admin.key`, not the V2 user pubkey).

## Performance tuning

- Default worker pool caps Telegram MTProto concurrency at **10**.
  Raise in `lcloud/workers/__init__.py` if needed (Telegram tolerates
  ~30 req/sec from one account).
- argon2 verify takes ~30ms — if you see API key auth latency
  bothering you, lower memory_cost in `lcloud/auth/api_keys.py` (NOT
  recommended for security).
- For SQLite + many concurrent writes, switch to PostgreSQL via
  `LC_DB_URL=postgresql+asyncpg://...` (alembic migrations should work
  but FTS5 trigger won't — needs adaptation).

## Monitoring

- `GET /health` returns `{"status":"ok","version":"..."}` — wire to
  your healthcheck system (uptime-kuma, blackbox-exporter, …)
- `GET /auth/state` (no auth) returns userbot connection state
- `journalctl -u lcloud -p err` for error stream
- Telegram errors / FloodWaits show up as warnings in `lcloud.err`

## Upgrade

```bash
cd /root/LCloud
git pull

. .venv/bin/activate
pip install -e '.[dev]'    # if pyproject changed
.venv/bin/alembic upgrade head  # auto-runs at startup anyway

cd web
npm install                 # if package.json changed
npm run build

systemctl restart lcloud
.venv/bin/pytest            # 202 should pass
```

## Common operator issues

| Symptom | Fix |
|---|---|
| `attempt to write a readonly database` in lcloud.err | systemd has `ProtectSystem=strict` or `ProtectHome=read-only` — REMOVE both |
| Userbot keeps disconnecting | Network issue, Telethon will retry. Check `lcloud.err` for FloodWait |
| Users hit 5GB cap | UPDATE users.storage_quota_bytes |
| Admin lost seed and can't log in | DELETE FROM users WHERE role='admin'; restart → fresh 12 words sent |
| /health returns 502 from nginx | LCloud was restarted, give it 5-10 sec to load FTS + connect Telethon |
| Tests fail with 'pubkey clash' | Stale prod DB rows — only run tests with isolated tmp DB (the fixtures already do) |
