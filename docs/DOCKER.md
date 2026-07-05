# Docker Deploy

This setup runs the built React UI and FastAPI backend in one container. The
container is disposable; all important state lives in the mounted `./data`
directory.

Persistent state in `./data`:

- `lcloud.db` SQLite database
- Telegram userbot session files
- admin keys/JWT secret
- temporary upload/backup files
- JSON DB Telegram backup cursor

## First VPS setup

Install Docker and the compose plugin, then clone the repo:

```bash
git clone https://github.com/mramziddin1228-gif/LCloud.git
cd LCloud
cp .env.docker.example .env.docker
nano .env.docker
mkdir -p data logs
```

Required values:

```env
TG_API_ID=123456
TG_API_HASH=your_hash
LC_PUBLIC_BASE_URL=https://your-domain.com
LC_COOKIE_SECURE=true
```

For direct testing without HTTPS use:

```env
LC_PUBLIC_BASE_URL=http://SERVER_IP:8787
LC_COOKIE_SECURE=false
```

Start:

```bash
docker compose up -d --build
docker compose logs -f lcloud
```

Open:

```text
http://SERVER_IP:8787
```

On first start, log in with Telegram phone/code in the web UI. The container
writes the Telethon session to `./data`, so normal restarts do not require
logging in again.

## Upgrade

```bash
git pull
docker compose up -d --build
docker image prune -f
```

Do not delete `./data` unless you intentionally want a fresh server.

## Health and logs

```bash
docker compose ps
curl http://127.0.0.1:8787/health
docker compose logs -f lcloud
```

## Reverse proxy

Put Nginx/Caddy/Traefik in front of port `8787` for HTTPS. Keep
`LC_COOKIE_SECURE=true` when the public site is HTTPS.

Minimal Nginx upstream target:

```text
proxy_pass http://127.0.0.1:8787;
proxy_set_header Host $host;
proxy_set_header X-Forwarded-Proto $scheme;
proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
```

## JSON DB restore from Telegram

Restore is useful when moving to a new VPS after Telegram backup has been
running. First bootstrap/login the Telegram userbot on the new VPS so the
container can read Saved Messages. Then stop the API while replaying data:

```bash
docker compose stop lcloud
docker compose run --rm lcloud python -m lcloud.userbot.db_restore --target-user-id 1 --dry-run
docker compose run --rm lcloud python -m lcloud.userbot.db_restore --target-user-id 1
docker compose up -d
```

If restoring an old namespace into a specific new local user, add:

```bash
--source-user-id OLD_ID --target-user-id NEW_ID
```

Check backup lag after start:

```bash
curl "$LC_PUBLIC_BASE_URL/api/v1/db/backup/status" \
  -H "Authorization: Bearer $LCLOUD_API_KEY"
```
