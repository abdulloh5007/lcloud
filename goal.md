# ЗАДАЧА: разработать LCloud — Telegram-userbot + веб-админка как личный cloud

Ты — автономный coding-агент. Работаешь в `/root/LCloud`. Реализуй проект по
этой спеке, не задавая повторных вопросов о уже зафиксированных решениях.
Если возникнет техническая развилка вне спеки — выбери разумный дефолт и
зафиксируй в `DECISIONS.md`.

## 1. Видение

LCloud — персональный «облачный» файл-менеджер на базе Telegram. Один Telegram-аккаунт
(userbot) выступает хранилищем: каждый «cloud» = отдельный Telegram-чат
(супергруппа), куда складываются файлы. Управление — через красивую web-админку.
В будущем — публичный API, где другие пользователи смогут использовать ту же
инфраструктуру, видя только свои данные через крипто-разделение.

## 2. Жёсткие ограничения

- ОДИН Telegram-аккаунт = ОДНА MTProto-сессия. Никаких альтернативных аккаунтов.
- Лимит файла: 1 GiB (валидация на бэке, ошибка 413 выше порога).
- Деплой: эта же машина, путь `/root/LCloud`, домен пока нет — слушать на
  `127.0.0.1:8787` для бэкенда и `127.0.0.1:8788` для dev-фронта; продакшн
  фронт — статика, отдаётся бэком.
- Никакого Bot API. Только MTProto через Telethon.
- Никакого облачного хранилища, БД и брокеров — всё локально.

## 3. Технологический стек (зафиксирован)

- Python 3.11+ (`uv` для зависимостей)
- Telethon — userbot
- FastAPI + Uvicorn — REST API
- SQLite + WAL + SQLAlchemy 2.x (async) — БД, миграции через Alembic
- PyNaCl — Ed25519 подписи / X25519 при необходимости
- React 18 + Vite + TypeScript + Tailwind + shadcn/ui — фронт
- libsodium-wrappers — крипта в браузере (для будущего API)
- Lucide-react — иконки тегов
- pytest + httpx + ruff + mypy — тесты и качество

## 4. Архитектура

Один процесс Python, внутри:
- Telethon-клиент (одна сессия)
- FastAPI-приложение (общий event-loop с Telethon)
- Worker pool: `asyncio.Queue` + N воркеров, `MAX_WORKERS=10` (env)
- Rate-limiter перед MTProto-вызовами (token bucket, лови `FloodWaitError`,
  ретрай с backoff)
- Telethon сам параллелит чанки одного файла; воркеры параллелят разные файлы

Хранилище:
- Telegram = blob-storage (содержимое файлов)
- SQLite = метаданные (mapping `chat_id`+`message_id` → запись о файле)
- Локальный диск `/root/LCloud/data/` — БД, сессия Telethon, ключи, временные
  файлы при загрузке

## 5. Крипто-модель: SIGN-ONLY

- У админа Ed25519-keypair, генерится при первом запуске.
- Приватник: `/root/LCloud/data/keys/admin.key`, права 600, root-only.
- Публичник: `/root/LCloud/data/keys/admin.pub` + дублируется в БД таблицу
  `owners(pubkey, label, role)`.
- Каждый файл при сохранении подписывается:
  `sig = Ed25519_Sign(privkey, sha256(file) || chat_id || message_id ||
  owner_pubkey || uploaded_at_unix)`
- Подпись + метаданные кладутся в caption Telegram-сообщения в виде однострочной
  JSON-структуры с префиксом-маркером, например:
  `LC1:{"o":"<pubkey_b64>","s":"<sig_b64>","h":"<sha256_b64>","t":<ts>}`
- Та же запись дублируется в БД (для быстрых запросов без чтения caption).
- API (включая будущий публичный) при выдаче файла требует от клиента подписать
  challenge-nonce приватным ключом. Сервер проверяет сигнатуру → если pubkey
  совпадает с `owner_pubkey` файла, доступ есть.

## 6. Маркер в описании чата

Когда админка создаёт/подключает cloud-чат, userbot устанавливает `chat.about`:
`LCLOUD1:<pubkey_b64>:<sign(privkey, chat_id_str)_b64>`

При старте userbot сканирует все доступные чаты, парсит маркер, наполняет таблицу
`clouds(chat_id, owner_pubkey, name, created_at, …)`. Чаты без валидного маркера
игнорируются.

## 7. Userbot-поведение

7.1. Авторизация: первый запуск — интерактив через CLI (`python -m lcloud.login`),
запросит номер + код, сессию сохранит в `/root/LCloud/data/session.lcloud`.

7.2. Команды в Saved Messages (только от self, проверяй `from_id == me`):
- `/admin` → генерит one-time JWT (15 мин), пишет ссылку
  `http://127.0.0.1:8787/admin?token=...` в Saved Messages
- `/revoke` → инвалидирует все активные admin-cookies
- `/status` → краткая статистика: clouds, files, размер, активные воркеры
- `/help` → справка

7.3. Обработка входящего файла в cloud-чате (event handler `NewMessage` с фильтром
по `chat_id ∈ clouds`):
- Если caption уже начинается с `LC1:` — игнорировать (это наша же загрузка)
- Иначе: вычислить sha256 (скачать → hash → не обязательно держать в памяти,
  стримом), подписать, отредактировать caption с добавлением `LC1:{...}`
- Записать в БД `files(...)`
- Если размер > 1 GiB — удалить сообщение, отправить в Saved Messages алерт
  «файл превышает лимит»

7.4. Создание нового cloud: админка вызывает API → userbot
`CreateChannel` (супергруппа), ставит маркер в about, добавляет себя
(уже владелец), сохраняет в БД.

## 8. Аутентификация веб-админки

8.1. `/admin?token=<JWT>` — magic-link:
- JWT подписан HS256 секретом из `data/keys/jwt.secret` (генерится при старте)
- payload: `{sub: "admin", jti: <uuid>, exp: now+15min}`
- одноразовый: `jti` пишется в таблицу `used_tokens`, повторное использование
  отвергается

8.2. После валидного токена — HttpOnly Secure SameSite=Strict cookie
`lc_session`, JWT с `exp: now+7d`, тоже HS256.

8.3. `/revoke` в Saved Messages → инкремент `auth_epoch` в БД, текущие cookie
становятся невалидны (epoch вшит в JWT).

## 9. API-эндпоинты (минимум для V1)

```
POST   /auth/magic           ← из /admin?token=…, выдаёт cookie
POST   /auth/logout
GET    /clouds               список cloud'ов админа
POST   /clouds               { name } → userbot создаёт супергруппу
DELETE /clouds/{id}          отвязать (не удаляет чат в TG, только маркер)
POST   /clouds/{id}/connect  подключить существующий чат по invite/id
GET    /clouds/{id}/files    список с фильтрами по тегам, поиском по имени
POST   /clouds/{id}/files    multipart upload, до 1 GiB
GET    /files/{id}/download  стримит из TG → клиенту
DELETE /files/{id}           удаляет сообщение в TG + запись в БД
GET    /tags                 список пользовательских тегов
POST   /tags                 { name, color, icon, bg_color }
PATCH  /tags/{id}
DELETE /tags/{id}
POST   /files/{id}/tags      { tag_ids: [...] } — set всех тегов файла
GET    /search?q=...&tags=...
GET    /me                   pubkey, статистика
```

## 10. Схема БД (SQLite, миграции Alembic)

```sql
owners(id, pubkey, label, role, created_at)
clouds(id, chat_id UNIQUE, owner_id FK, name, about, created_at)
files(
  id, cloud_id FK, message_id, owner_id FK,
  original_name, mime, size_bytes, sha256,
  signature, uploaded_at, deleted_at NULL
)
tags(id, owner_id FK, name, color, icon, bg_color, created_at)
file_tags(file_id FK, tag_id FK, PRIMARY KEY(file_id, tag_id))
used_tokens(jti PRIMARY KEY, used_at)
auth_state(owner_id FK, epoch INT)
files_fts (виртуальная FTS5 на original_name)
```

## 11. Web UI

- Layout: левый сайдбар со списком cloud'ов + «+ New Cloud», правая панель —
  файловый менеджер (grid/list toggle).
- Тег-чипы в стиле iOS Files: круглая иконка с фоном + название.
- Создание тега: модалка с palette (12 базовых цветов + custom), icon-picker
  (~50 lucide-иконок + emoji input), preview.
- Drag-n-drop загрузка, прогресс-бар, отмена.
- Превью: картинки/видео/аудио inline; pdf через `<embed>`; остальное — иконка
  по mime + кнопка Download.
- Поиск: input в топ-баре, мгновенная фильтрация (debounce 200ms).
- Кнопка «Connect existing chat» — модалка с вводом @username/invite-link.
- Тёмная/светлая тема, респонсив.

## 12. Структура репозитория

```
/root/LCloud/
├── pyproject.toml
├── README.md
├── DECISIONS.md
├── .env.example
├── alembic.ini
├── alembic/
├── lcloud/
│   ├── __init__.py
│   ├── config.py            pydantic-settings
│   ├── main.py              FastAPI app + Telethon startup
│   ├── login.py             one-shot CLI для первого логина
│   ├── db/                  модели, session
│   ├── crypto/              keys, sign, verify
│   ├── userbot/             handlers, marker, scan
│   ├── workers/             pool, queue, rate_limiter
│   ├── api/                 routers (auth, clouds, files, tags, search)
│   ├── auth/                jwt, magic-link
│   └── utils/
├── web/
│   ├── package.json
│   ├── vite.config.ts
│   ├── src/
│   └── dist/                собирается, отдаётся FastAPI как StaticFiles
├── data/                    .gitignore
│   ├── lcloud.db
│   ├── session.lcloud
│   └── keys/
└── tests/
```

## 13. Фазы реализации (исполнять последовательно, после каждой — `git commit`)

P0. Скелет: pyproject, директории, ruff/mypy/pytest конфиги, FastAPI hello,
    React vite hello, Alembic init.

P1. Telethon-логин CLI, генерация admin-keypair, базовая БД, owner-запись.

P2. Userbot-команды в Saved Messages (`/admin`, `/status`, `/revoke`, `/help`),
    magic-link auth, защищённые роуты.

P3. Создание cloud (`POST /clouds` → CreateChannel + marker), листинг clouds,
    сканирование чатов при старте.

P4. Worker pool + rate-limiter + FloodWait handling.

P5. Загрузка файла через UI (multipart → temp file → sha256 → sign → отправка
    в TG → запись в БД), скачивание (stream из TG).

P6. Хэндлер прямой загрузки в cloud-чат: edit caption, запись в БД, лимит 1 GiB.

P7. Теги: CRUD, привязка к файлам, фильтрация в листинге.

P8. Поиск по имени (FTS5), комбинация с тегами.

P9. UI-полировка: drag-n-drop, превью, темы, респонсив, эмпти-стейты.

P10. README + DECISIONS.md финал, инструкция запуска, systemd unit
     `/etc/systemd/system/lcloud.service`.

## 14. Acceptance criteria (V1 готов когда)

- `systemctl start lcloud` — userbot и API живые
- `/admin` в Saved Messages → ссылка → открывается админка
- Создаю cloud в UI → появляется супергруппа в Telegram с маркером в about
- Загружаю файл 500 MB → виден в UI с подписью, скачивается обратно с тем же
  sha256
- Кидаю файл из Telegram-приложения прямо в cloud-чат → в течение 30 сек
  caption обновляется, файл появляется в UI
- Создаю тег «Important» (красный, иконка star) → вешаю на 3 файла →
  фильтр по тегу показывает ровно их
- `/revoke` инвалидирует cookie, F5 в админке → редирект на `/admin?token=...`
- Файл 1.5 GiB через UI → 413; через TG напрямую → удаляется + алерт в Saved
- `pytest` зелёный (минимум: crypto, marker parse, jwt, file CRUD)
- `ruff check` + `mypy` без ошибок

## 15. Что НЕ делать

- НЕ реализовывать публичный API для других юзеров в V1 (заложить расширяемость
  через `owner_id`, но эндпоинты и регистрацию — нет)
- НЕ делать E2E-шифрование контента (договорились sign-only)
- НЕ изобретать свою крипту: только PyNaCl/libsodium
- НЕ хранить приватник в БД
- НЕ ставить Redis/Celery/Postgres — asyncio.Queue + SQLite достаточно
- НЕ добавлять Docker в V1 (просто systemd-юнит)
- НЕ редактировать чужие сообщения — только сообщения userbot'а (а они все его)

## 16. Старт работы

1. Прочитай эту спеку целиком.
2. Создай `DECISIONS.md` с пустыми секциями по фазам.
3. Начни с P0. После каждой фазы — самоотчёт: что сделано, что отложено,
   и переходи к следующей.
4. По любым неоднозначным мелочам — выбирай разумный дефолт и фиксируй
   в `DECISIONS.md`. Не блокируйся, не задавай мне 10 вопросов подряд.

---

## SPEC AMENDMENTS (added after P0, before P1)

These amendments override the corresponding paragraphs above. Reason: admin
flow simplified — instead of a CLI login + Saved-Messages magic-link, the
web UI itself is the login surface, and the admin identity is pinned to a
single Telegram user_id from `.env`.

### A1. Admin identity — replaces §8 (web auth)

The admin is identified by their Telegram user_id, configured in `.env` as
`LC_ADMIN_TG_ID`. Logical model:

- One userbot session = one Telegram account. That account's owner IS the
  admin. There is no separate "admin user" abstraction.
- After successful Telegram login (see A2), the server calls
  `client.get_me()` and verifies `me.id == settings.lc_admin_tg_id`. On
  mismatch: wipe `data/session.lcloud`, log out, return 403 to the UI.
- On match: set HttpOnly Secure SameSite=Strict cookie `lc_session`
  (HS256-JWT, ttl `LC_SESSION_TTL_SECONDS`, payload includes `auth_epoch`
  for revocation, same as original §8.3).
- `/auth/logout` clears the cookie. `/revoke` (Saved-Messages command from
  §7.2) still bumps `auth_epoch` and invalidates all live cookies.
- The Saved-Messages `/admin` magic-link command from §7.2 is **dropped**
  in V1 (web login replaces it). `LC_MAGIC_LINK_TTL_SECONDS` is kept in
  config for a possible future fallback but is not wired up.

### A2. Web-based Telegram login — replaces §7.1 (CLI login)

First-time setup is done entirely in the web admin, not via
`python -m lcloud.login`. The CLI script `lcloud-login` stays as an
emergency / headless fallback but is no longer the primary path.

UI flow when `data/session.lcloud` is absent or unauthorized:

1. App boots in "locked" mode: backend reports `GET /auth/state` →
   `{authorized: false}`; UI renders a single page "Подключить Telegram
   аккаунт" with phone-number input.
2. User submits phone → `POST /auth/telegram/start { phone }`. Backend
   uses Telethon `client.send_code_request(phone)`, returns
   `{ phone_code_hash }` (opaque to client) plus session-side state.
3. User enters the SMS / Telegram code → `POST /auth/telegram/code
   { phone, code, phone_code_hash }`. Backend calls `client.sign_in(...)`.
   - On `SessionPasswordNeededError`: respond `{ need_password: true }`.
   - On other Telethon errors: surface message, allow retry.
4. If 2FA password requested → `POST /auth/telegram/password { password }`
   → `client.sign_in(password=...)`.
5. On success: `me = await client.get_me()`. If `me.id !=
   LC_ADMIN_TG_ID` → see step 5a. Otherwise: persist session, issue
   `lc_session` cookie, return `{ authorized: true, me: { id,
   first_name, username } }`.

5a. **Wrong-account branch** (chosen policy: archive): rename
   `data/session.lcloud` → `data/session.rejected.<unix_ts>.lcloud`
   (mode 600), call `client.log_out()`, return 403 with reason
   `wrong_account`. Archived sessions are never re-loaded by the app;
   they exist only for manual inspection. Cleanup of old archives is
   the operator's responsibility (no automatic GC in V1).
6. Once authorized:
   - The same Telethon client stays running; userbot event handlers
     (file ingestion, Saved-Messages commands per §7.2 minus `/admin`,
     marker-scanner per §6) attach immediately.
   - The web UI unlocks and behaves per §11 (clouds list, files, tags…).

State machine (server-side, single Telethon client instance):

```
NO_SESSION  ── start ──▶  CODE_SENT  ── code ──▶  AUTHORIZED
                              │                      ▲
                              └── 2FA needed ──▶  PWD_NEEDED ──▶ AUTHORIZED
                                                                    │
                              wrong account / logout ◀──────────────┘
```

A2.1. The login-flow endpoints are unauthenticated by definition (no cookie
exists yet) but are rate-limited per remote IP (token bucket, e.g. 5 attempts
per 5 min) and refuse to accept new code requests while a flow is mid-state
unless an explicit `cancel` is sent.

A2.2. There is no separate phone-number whitelist: the only whitelist is
`LC_ADMIN_TG_ID` checked AFTER successful login. A wrong phone simply ends
in a wrong-account 403 with the session wiped — no information leakage about
which phone belongs to the admin.

### A3. New env var

`LC_ADMIN_TG_ID` (int, required from P1) — added to `Settings` in
`lcloud/config.py` and to `.env` / `.env.example`. `0` means unset
(treated as "refuse all logins" — server is in degraded "needs setup" mode).

### A4. Acceptance criteria addendum (folded into §14 list)

- Open `http://127.0.0.1:8787/` with no session → see "Подключить Telegram
  аккаунт" page; backend `GET /auth/state` returns `{authorized: false}`.
- Complete phone+code flow with the configured admin account → land in the
  full admin UI; cookie is set; refresh stays authorized.
- Repeat flow with a non-admin Telegram account → 403 `wrong_account`,
  `data/session.lcloud` is archived as
  `data/session.rejected.<ts>.lcloud` (mode 600), UI bounces back to
  login page.
- Restart process → session persists, no re-login needed.

## SPEC AMENDMENT A5 — magic-link auth re-introduced (post-V1, mid-flight)

After V1 shipped with the web phone+code flow as the only entrypoint, the
operator asked for a parallel magic-link path. A5 reinstates the
Saved-Messages `/admin` command from the original §7.2 (which was dropped
by A1) and runs it **alongside** the phone+code flow — both work.

### A5.1. Logical model

- **Bootstrap account** = the single Telegram account that runs the
  userbot. All clouds for all users live in supergroups owned by THIS
  account. There is exactly one such account per LCloud deployment.
  Phone+code login bootstraps it (and stamps `data/keys/admin.tgid`).
- **Magic-link** = a convenience for re-entering the admin web UI without
  re-typing phone/code. Bound to the already-bootstrapped admin's
  Telegram-id; no other user_id can ever issue or accept a magic-link.
- **Both ways are always available**: the login screen shows the
  phone+code form AND a hint about `/admin`; once a session cookie is
  present, neither matters until it expires.

### A5.2. Token mechanics

- Single-use HS256 JWT signed by `data/keys/jwt.secret`.
- Payload: `{sub: "admin", kind: "magic", owner_id, ae, jti, iat,
  exp: iat + LC_MAGIC_LINK_TTL_SECONDS}` (default 15 min).
- `kind: "magic"` distinguishes it from the long-lived session cookie
  (`kind: "session"`), so accidentally pasting a session JWT into
  `?token=` is rejected and vice-versa.
- `jti` is recorded in `used_tokens` on first redemption; replays return
  401.
- `ae` (auth_epoch) is checked against `auth_state.epoch`; a `/revoke`
  bumps the epoch and invalidates outstanding magic-links along with
  cookies.

### A5.3. UX

`/admin` in Saved Messages → userbot replies with a one-line message
containing `https://<LC_PUBLIC_BASE_URL>/admin?token=<jwt>` and a TTL
note. Clicking the link hits `GET /admin?token=…`, the server validates,
sets the `lc_session` cookie, then 302s to `/`.

If a stranger somehow obtains the link before the admin uses it (worst
case: leaked to Telegram link-preview infrastructure), the single-use
`jti` and 15-min TTL keep the blast radius minimal. The link travels
inside Telegram's E2E-ish encrypted channel only — no email, no SMS.

### A5.4. Endpoints

- `GET /admin?token=<jwt>` — public (no `require_admin` dep). Validates,
  marks `jti` used, issues `lc_session` cookie, 302 → `/`. Failures
  (expired / replayed / wrong-kind / bad-sig / wrong-account) return 401
  with a JSON `detail.reason`.
- Saved Messages `/admin` command (already in §7.2 catalogue; restored).

### A5.5. Tests

Unit tests cover token roundtrip, expiry, replay rejection,
wrong-kind rejection, and integration through the `/admin` GET. The
Saved-Messages `/admin` command is tested with the existing FakeClient
to assert the reply contains a token URL.

---

## Post-V1 Roadmap (planned, NOT implemented yet)

The schema and API surface of V1 are deliberately shaped to extend along
these axes without breaking changes. **Do not implement these without
explicit go-ahead** — they are listed here so future contributors keep
the V1 design space compatible.

### V2 — Multi-user accounts

- **Account model**: each user gets their own Ed25519 keypair. The web
  flow generates the keypair client-side (via `libsodium-wrappers`),
  stores the public key on the server, holds the private key in the
  user's browser (IndexedDB, encrypted with a passphrase / device key).
- **Auth**: challenge-response. Server emits a random nonce; client
  signs with the private key; server verifies against the stored
  pubkey. Replaces phone+code for non-admin users.
- **Clouds**: each user has their own clouds. The userbot still runs on
  the bootstrap account and physically owns every supergroup, but the
  `clouds.owner_id` column already partitions by user. New clouds for
  a non-admin user are created in the bootstrap account's TG namespace,
  with the user's pubkey embedded in the LCLOUD1 marker (the marker
  format already supports this — see §6).
- **File-level access**: existing LC1 caption already includes
  `owner_pubkey`. The download endpoint adds a challenge-signature
  check before streaming bytes (already designed in §5; just not
  enforced for V1's single-admin case).
- **No invitation system** in V2 baseline; admin grants access by
  emailing a registration token URL. Self-serve signup is V2.5.

### V3 — Public REST API (imgbb-style)

- **API keys**: each user can mint API keys (similar to GitHub PAT).
  Stored as `argon2(api_key)` hashes; transmitted as
  `Authorization: Bearer lck_…`.
- **Surface**: a parallel `/api/v1/...` router covering the same
  cloud / file / tag operations, JSON-only, no cookie auth.
- **Rate limiting**: token bucket per API key (separate from the login
  rate limiter). Initial limits generous; tighten when usage shows.
- **Public direct links**: `GET /api/v1/files/{id}/raw` returns the
  file with content-disposition derived from the original name.
  Optional `?expires_in=...&signature=…` for shareable URLs.
- **Free at launch**: no quotas. Storage costs are absorbed by the
  bootstrap account's free Telegram tier (~unlimited document storage
  as of 2024).

### V4 — Pricing tiers (deferred)

- Decided post-V3 once usage patterns are visible. Likely shape:
  free tier with per-month upload cap; paid tiers raise the cap and add
  per-file size beyond 1 GiB if Telegram premium is enabled on the
  bootstrap account. Not designed yet — placeholder.

### Compatibility constraints carried forward

- `owners.pubkey` UNIQUE — already in V1 schema; no migration needed.
- LCLOUD1 marker carries owner pubkey — V1 already verifies it.
- LC1 caption includes owner pubkey — V1 already signs/embeds it.
- `auth_epoch` revocation works per-owner — same shape will gate API
  keys' validity (a user's `/revoke` invalidates ALL their keys).
- `OWNER_ID` partitioning is on every query in V1 — drop-in for V2
  without relaxing tests.

The point: V1 is correct multi-tenant code with an admin-only feature
flag. V2 = enable the gates. V3 = expose them via API.
