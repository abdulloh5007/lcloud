# Quickstart

This is the 5-minute guide for end-users who want to use a deployed
LCloud instance. If you're deploying it yourself, see
[OPERATOR.md](OPERATOR.md) first.

## 1. Open the app

Visit your LCloud URL (e.g. `https://lcloud.example.com/`).

You'll see one of two things depending on whether the server's userbot
is connected to a Telegram account.

### 1a. First time ever (admin bootstrap)

If the userbot is not yet connected to any Telegram account, the home
page shows a phone+code form. **This is a one-time operation done by
the server owner**, not regular users:

1. Enter your phone number (must be a real Telegram account)
2. Telegram sends a login code → paste it
3. If 2FA is enabled, you'll be asked for your cloud password
4. The userbot is now connected. It immediately sends a 12-word seed
   phrase to **your own Saved Messages** in Telegram.

The 12 words are the credentials for the **admin web account**. Save
them somewhere safe (paper, password manager). Then proceed to step 2.

### 1b. Userbot already connected

You'll see the seed-phrase login screen. Two options:

- **"Войти по сид-фразе"** — paste an existing 12 or 24-word phrase
- **"Создать новый аккаунт"** — generate a fresh phrase (regular user)

## 2. Create a new account (non-admin users)

1. Click **"Создать новый аккаунт"**
2. Pick word count: 12 (default, 128 bits) or 24 (256 bits)
3. Click **"Сгенерировать"** — 12 or 24 random words appear
4. **Save them**. Click 📋 to copy, or write down on paper.
5. Tick the "I saved it" checkbox
6. Click **"Войти"** — you're in.

⚠️ **There is no password reset.** If you lose the words, your account
and all your files are unreachable. There is no email, no security
question, nothing. Treat the seed phrase like a private key — because
it literally is one.

## 3. Log in (existing account)

1. Click **"Войти по сид-фразе"**
2. Paste your 12/24-word phrase
3. The textarea validates the BIP39 checksum live — green ✓ means OK,
   amber ⚠️ means typo
4. Click **"Войти"** — you're in.

## 4. Inside the app

### Sidebar (left)

- **List of your clouds**. A cloud is essentially a folder for files;
  technically each cloud is a Telegram supergroup managed by the
  server's userbot.
- Click **"+ Новый cloud"** to create one. Give it a name.
- Click **🗑️** next to a cloud to disconnect it (the supergroup stays
  in Telegram, but LCloud forgets about it).
- **⚙️ Настройки** at the bottom opens the settings modal.

### Main area

- Drag & drop files (or click "Загрузить") to upload to the selected
  cloud.
- During upload you'll see two phases:
  - **🔐 Подписываем…** — your browser computes SHA-256 of the file
    and signs it with your private key
  - **📤 Загружается…** — the file is being uploaded to Telegram
- Files appear in a grid with thumbnails (for images) and a 🔐 LC2
  badge (for client-signed files).
- Click any file to open the preview modal (download, rename, tag,
  delete).
- Search bar at the top filters by filename via FTS.

### Settings modal

Three tabs:

#### Общие
- Image quality: Low (fastest), Medium (server-resized to 800px), HD
  (full-resolution from Telegram)
- Video quality: same idea (currently HD only in production)

#### API-ключи
- See your existing API keys (prefix only, raw is shown only at
  creation time)
- Create new keys for programmatic access — see the [API guide](API.md)
- Revoke any key (services using it will stop working)

#### Аккаунт
- Your role (user / admin), user ID, pubkey fingerprint, creation date
- Storage usage bar (green < 70%, amber < 90%, red >= 90%)
- "Выйти из аккаунта" button

## 5. Use the API from scripts

1. Settings → API-ключи → "+ Создать ключ"
2. Optional label (for your own bookkeeping)
3. The raw key appears in an emerald banner — **copy it now**
4. Use as `Authorization: Bearer lc-XXXXXXXXXXXXXX` header

```bash
# Example: list your clouds
curl -H "Authorization: Bearer lc-abcdefghij2345" \
     https://your-host/api/v1/clouds

# Upload a file
curl -X POST \
     -H "Authorization: Bearer lc-abcdefghij2345" \
     -F "file=@/path/to/file.pdf" \
     https://your-host/api/v1/clouds/1/files
```

See [API.md](API.md) for the full reference with Python and JS examples.

## 6. Common troubleshooting

| Symptom | What to do |
|---|---|
| "Юзербот не авторизован" + phone form | Server lost its Telethon session; admin should re-login phone+code |
| BIP39 textarea always shows "невалидная фраза" | Words don't match the BIP39 wordlist — check for typos / wrong language |
| Upload stuck on "Подписываем…" | Browser is computing SHA-256 of a large file; ~1s per 1 GB |
| 413 quota_exceeded | You hit your storage quota. Settings → Аккаунт shows usage. Delete files or ask admin to bump quota. |
| 401 on API call | Cookie expired or API key revoked. Re-login or mint new key. |
| File shows 🔐 **LC2** badge | Uploaded by a client that did Ed25519 signing locally — proves owner |
| File shows just **LC1** badge | Uploaded via `curl` without signing fields — admin-key signed (legacy) |

## Security notes for users

- Your seed phrase is **never sent to the server**. Verify by checking
  network requests in browser devtools.
- The session cookie is HttpOnly + SameSite=Strict — JS can't read it,
  cross-site requests can't send it.
- Your private key is held in **sessionStorage** (cleared on tab close).
  An attacker with running JS in your tab CAN read it. Don't paste your
  seed on untrusted machines.
- API keys grant full access to your account. Don't commit them. Revoke
  immediately if leaked.
