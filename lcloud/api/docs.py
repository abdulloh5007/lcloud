"""Custom API docs UIs — mobile-friendly + explicit UTF-8.

Three pages:

- `/docs`        → **Beautiful custom landing**: hero, quickstart, code
                   examples by category, FAQ. Mobile-first, smooth
                   animations, syntax-highlighted code. Has a button to
                   jump to the full Swagger UI for power users.
- `/docs/swagger` → Swagger UI (default desktop look, no mobile tweaks).
- `/redoc`       → ReDoc, three-column read-only browsing.
"""

from __future__ import annotations

from fastapi import APIRouter, FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

router = APIRouter(include_in_schema=False)


# ============================================================
# /docs — beautiful custom landing
# ============================================================

DOCS_HTML = r"""<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=5">
  <title>LCloud API — Документация</title>
  <link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E%3Ctext y='80' font-size='80'%3E%E2%98%81%EF%B8%8F%3C/text%3E%3C/svg%3E">
  <link href="https://fonts.googleapis.com/css?family=Inter:400,500,600,700,800|JetBrains+Mono:400,500,600&display=swap" rel="stylesheet">
  <link href="https://cdn.jsdelivr.net/npm/prismjs@1.29.0/themes/prism-tomorrow.min.css" rel="stylesheet">
  <style>
    :root {
      --bg: #ffffff;
      --bg-soft: #f8fafc;
      --bg-panel: #ffffff;
      --border: #e2e8f0;
      --border-strong: #cbd5e1;
      --text: #0f172a;
      --text-soft: #475569;
      --text-muted: #94a3b8;
      --accent: #10b981;
      --accent-soft: #d1fae5;
      --accent-text: #065f46;
      --code-bg: #1e293b;
      --code-bg-inline: #f1f5f9;
      --get: #3b82f6;
      --post: #10b981;
      --del: #ef4444;
      --patch: #f59e0b;
      --shadow: 0 1px 3px rgba(0,0,0,0.08), 0 4px 12px rgba(0,0,0,0.04);
      --shadow-lg: 0 10px 40px rgba(0,0,0,0.12);
    }

    @media (prefers-color-scheme: dark) {
      :root {
        --bg: #0a0a0f;
        --bg-soft: #0f172a;
        --bg-panel: #111827;
        --border: #1f2937;
        --border-strong: #334155;
        --text: #f1f5f9;
        --text-soft: #cbd5e1;
        --text-muted: #64748b;
        --accent: #34d399;
        --accent-soft: rgba(52, 211, 153, 0.12);
        --accent-text: #6ee7b7;
        --code-bg: #0f172a;
        --code-bg-inline: #1e293b;
      }
    }

    * { box-sizing: border-box; }
    html, body { margin: 0; padding: 0; }

    html { scroll-behavior: smooth; scroll-padding-top: 60px; }

    body {
      font-family: 'Inter', system-ui, -apple-system, sans-serif;
      font-size: 16px;
      line-height: 1.6;
      color: var(--text);
      background: var(--bg);
      -webkit-text-size-adjust: 100%;
      -webkit-font-smoothing: antialiased;
    }

    a { color: var(--accent); text-decoration: none; }
    a:hover { text-decoration: underline; }

    .container { max-width: 1024px; margin: 0 auto; padding: 0 16px; }

    /* ============================================================
       Sticky navbar
       ============================================================ */
    .navbar {
      position: sticky;
      top: 0;
      z-index: 50;
      background: rgba(255, 255, 255, 0.85);
      backdrop-filter: saturate(180%) blur(20px);
      -webkit-backdrop-filter: saturate(180%) blur(20px);
      border-bottom: 1px solid var(--border);
    }
    @media (prefers-color-scheme: dark) {
      .navbar { background: rgba(10, 10, 15, 0.85); }
    }
    .navbar-inner {
      display: flex;
      align-items: center;
      justify-content: space-between;
      height: 56px;
      max-width: 1024px;
      margin: 0 auto;
      padding: 0 16px;
    }
    .navbar-brand {
      display: flex; align-items: center; gap: 8px;
      font-weight: 700; font-size: 16px;
      color: var(--text);
      text-decoration: none;
    }
    .navbar-brand:hover { text-decoration: none; }
    .navbar-actions { display: flex; gap: 8px; align-items: center; }
    .btn {
      display: inline-flex; align-items: center; gap: 6px;
      padding: 8px 14px;
      border-radius: 8px;
      font-size: 13px;
      font-weight: 500;
      transition: all 0.2s ease;
      cursor: pointer;
      text-decoration: none;
      border: 1px solid transparent;
      white-space: nowrap;
    }
    .btn-primary {
      background: var(--accent);
      color: white;
    }
    .btn-primary:hover {
      background: var(--accent);
      filter: brightness(1.1);
      transform: translateY(-1px);
      text-decoration: none;
    }
    .btn-ghost {
      color: var(--text-soft);
      background: transparent;
    }
    .btn-ghost:hover {
      background: var(--bg-soft);
      color: var(--text);
      text-decoration: none;
    }
    .btn-secondary {
      background: var(--bg-soft);
      color: var(--text);
      border-color: var(--border);
    }
    .btn-secondary:hover {
      border-color: var(--border-strong);
      text-decoration: none;
    }

    /* ============================================================
       Hero
       ============================================================ */
    .hero {
      padding: 56px 0 48px;
      text-align: center;
      background:
        radial-gradient(circle at 50% 0%, var(--accent-soft) 0%, transparent 60%);
    }
    .hero-emoji {
      display: inline-block;
      font-size: 64px;
      margin-bottom: 16px;
      animation: float 3s ease-in-out infinite;
    }
    @keyframes float {
      0%, 100% { transform: translateY(0); }
      50%      { transform: translateY(-8px); }
    }
    .hero-title {
      font-size: clamp(28px, 5vw, 44px);
      font-weight: 800;
      letter-spacing: -0.02em;
      margin: 0 0 12px;
      background: linear-gradient(135deg, var(--text) 0%, var(--accent) 100%);
      -webkit-background-clip: text;
      background-clip: text;
      -webkit-text-fill-color: transparent;
      color: transparent;
    }
    .hero-subtitle {
      font-size: clamp(15px, 2.5vw, 18px);
      color: var(--text-soft);
      max-width: 540px;
      margin: 0 auto 28px;
    }
    .hero-actions {
      display: flex; justify-content: center; gap: 10px;
      flex-wrap: wrap;
    }
    .hero-actions .btn { padding: 12px 20px; font-size: 14px; }

    /* ============================================================
       Sections
       ============================================================ */
    section {
      padding: 48px 0;
      animation: fadeUp 0.5s ease-out;
    }
    @keyframes fadeUp {
      from { opacity: 0; transform: translateY(12px); }
      to   { opacity: 1; transform: translateY(0); }
    }
    .section-title {
      font-size: clamp(22px, 4vw, 28px);
      font-weight: 700;
      margin: 0 0 8px;
      letter-spacing: -0.01em;
    }
    .section-lead {
      color: var(--text-soft);
      margin: 0 0 24px;
      font-size: 15px;
    }

    /* ============================================================
       Quickstart cards
       ============================================================ */
    .qs-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
      gap: 16px;
    }
    .qs-card {
      background: var(--bg-panel);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 20px;
      transition: all 0.25s ease;
      position: relative;
      overflow: hidden;
    }
    .qs-card::before {
      content: '';
      position: absolute;
      top: 0; left: 0; right: 0;
      height: 3px;
      background: linear-gradient(90deg, var(--accent), transparent);
      opacity: 0;
      transition: opacity 0.25s ease;
    }
    .qs-card:hover {
      border-color: var(--accent);
      transform: translateY(-2px);
      box-shadow: var(--shadow-lg);
    }
    .qs-card:hover::before { opacity: 1; }
    .qs-num {
      display: inline-flex;
      align-items: center; justify-content: center;
      width: 32px; height: 32px;
      border-radius: 8px;
      background: var(--accent-soft);
      color: var(--accent-text);
      font-weight: 700;
      margin-bottom: 12px;
    }
    .qs-card h3 { font-size: 16px; margin: 0 0 6px; }
    .qs-card p { font-size: 14px; color: var(--text-soft); margin: 0; }

    /* ============================================================
       Endpoint cards
       ============================================================ */
    .ep-list {
      display: flex; flex-direction: column; gap: 12px;
    }
    .ep {
      background: var(--bg-panel);
      border: 1px solid var(--border);
      border-radius: 12px;
      overflow: hidden;
      transition: all 0.2s ease;
    }
    .ep[open] { box-shadow: var(--shadow); border-color: var(--border-strong); }
    .ep summary {
      padding: 14px 16px;
      cursor: pointer;
      display: flex; align-items: center; gap: 12px;
      flex-wrap: wrap;
      list-style: none;
      user-select: none;
    }
    .ep summary::-webkit-details-marker { display: none; }
    .method {
      display: inline-flex; align-items: center;
      padding: 4px 10px;
      border-radius: 6px;
      font-family: 'JetBrains Mono', monospace;
      font-size: 11px;
      font-weight: 700;
      letter-spacing: 0.05em;
      color: white;
      flex-shrink: 0;
    }
    .method.GET { background: var(--get); }
    .method.POST { background: var(--post); }
    .method.DELETE { background: var(--del); }
    .method.PATCH { background: var(--patch); }
    .ep-path {
      font-family: 'JetBrains Mono', monospace;
      font-size: 13px;
      color: var(--text);
      font-weight: 500;
      word-break: break-all;
      flex: 1 1 200px;
      min-width: 0;
    }
    .ep-desc {
      flex: 1 1 100%;
      font-size: 13px;
      color: var(--text-soft);
      margin: 4px 0 0 0;
    }
    .chevron {
      color: var(--text-muted);
      transition: transform 0.2s ease;
      flex-shrink: 0;
    }
    .ep[open] .chevron { transform: rotate(180deg); }
    .ep-body {
      padding: 0 16px 16px;
      border-top: 1px solid var(--border);
      animation: fadeUp 0.3s ease-out;
    }
    .ep-body p { font-size: 14px; color: var(--text-soft); }
    .ep-body code { font-family: 'JetBrains Mono', monospace; }

    /* Inline code */
    code:not(pre code) {
      background: var(--code-bg-inline);
      padding: 2px 6px;
      border-radius: 4px;
      font-size: 0.9em;
      font-family: 'JetBrains Mono', monospace;
      color: var(--text);
      word-break: break-all;
    }

    /* ============================================================
       Code blocks (Prism)
       ============================================================ */
    .code-block {
      position: relative;
      margin: 12px 0;
      border-radius: 8px;
      overflow: hidden;
      border: 1px solid var(--border);
    }
    .code-block-header {
      display: flex; justify-content: space-between; align-items: center;
      background: var(--bg-soft);
      padding: 6px 12px;
      font-size: 11px;
      color: var(--text-muted);
      border-bottom: 1px solid var(--border);
      font-family: 'JetBrains Mono', monospace;
      text-transform: uppercase;
      letter-spacing: 0.05em;
    }
    .copy-btn {
      background: transparent;
      border: 1px solid var(--border);
      color: var(--text-soft);
      padding: 3px 8px;
      border-radius: 4px;
      font-size: 11px;
      cursor: pointer;
      font-family: inherit;
      transition: all 0.15s ease;
    }
    .copy-btn:hover {
      border-color: var(--accent);
      color: var(--accent);
    }
    .copy-btn.copied {
      background: var(--accent);
      border-color: var(--accent);
      color: white;
    }
    .code-block pre {
      margin: 0 !important;
      border-radius: 0 !important;
      max-height: 400px;
      overflow-x: auto;
    }
    .code-block pre code {
      font-size: 13px !important;
      font-family: 'JetBrains Mono', monospace !important;
    }

    /* ============================================================
       FAQ
       ============================================================ */
    .faq-list { display: flex; flex-direction: column; gap: 8px; }
    .faq {
      background: var(--bg-panel);
      border: 1px solid var(--border);
      border-radius: 10px;
      overflow: hidden;
      transition: all 0.2s ease;
    }
    .faq:hover { border-color: var(--border-strong); }
    .faq[open] { background: var(--bg-soft); }
    .faq summary {
      padding: 14px 16px;
      cursor: pointer;
      list-style: none;
      font-weight: 500;
      display: flex; gap: 10px; align-items: center;
      font-size: 15px;
    }
    .faq summary::-webkit-details-marker { display: none; }
    .faq-q-icon {
      flex-shrink: 0;
      width: 24px; height: 24px;
      display: inline-flex; align-items: center; justify-content: center;
      border-radius: 50%;
      background: var(--accent-soft);
      color: var(--accent-text);
      font-weight: 700;
      font-size: 12px;
    }
    .faq-body {
      padding: 0 16px 16px 50px;
      font-size: 14px;
      color: var(--text-soft);
      animation: fadeUp 0.3s ease-out;
    }
    .faq-body p { margin: 0 0 8px; }
    .faq-body p:last-child { margin: 0; }

    /* ============================================================
       Footer
       ============================================================ */
    footer {
      padding: 32px 0 48px;
      text-align: center;
      color: var(--text-muted);
      font-size: 13px;
      border-top: 1px solid var(--border);
      margin-top: 32px;
    }
    footer a { color: var(--text-soft); margin: 0 6px; }

    /* ============================================================
       Mobile
       ============================================================ */
    @media (max-width: 640px) {
      .hero { padding: 32px 0 24px; }
      .hero-emoji { font-size: 48px; }
      section { padding: 32px 0; }
      .navbar-actions .btn-secondary { display: none; }
      .container { padding: 0 12px; }
      .ep summary { padding: 12px; gap: 8px; }
      .qs-card { padding: 16px; }
    }

    @media (max-width: 380px) {
      .hero-title { font-size: 22px; }
      .ep-path { font-size: 11px; }
      .method { font-size: 10px; padding: 3px 7px; }
      body { font-size: 14px; }
    }
  </style>
</head>
<body>
  <!-- ============================================================ NAVBAR -->
  <nav class="navbar">
    <div class="navbar-inner">
      <a href="#top" class="navbar-brand">
        <span style="font-size:22px">☁️</span>
        <span>LCloud API</span>
      </a>
      <div class="navbar-actions">
        <a href="/docs/swagger" class="btn btn-secondary">Swagger UI</a>
        <a href="/redoc" class="btn btn-ghost">ReDoc</a>
      </div>
    </div>
  </nav>

  <!-- ============================================================ HERO -->
  <section class="hero" id="top">
    <div class="container">
      <div class="hero-emoji">☁️</div>
      <h1 class="hero-title">LCloud API</h1>
      <p class="hero-subtitle">
        Личное облако в Telegram. Авторизация через сид-фразу,
        client-side подпись файлов, простой REST API.
      </p>
      <div class="hero-actions">
        <a href="#quickstart" class="btn btn-primary">🚀 Начать за 3 шага</a>
        <a href="/docs/swagger" class="btn btn-secondary">Swagger UI →</a>
      </div>
    </div>
  </section>

  <!-- ============================================================ QUICKSTART -->
  <section id="quickstart">
    <div class="container">
      <h2 class="section-title">🚀 Быстрый старт</h2>
      <p class="section-lead">От нуля до первого запроса за две минуты.</p>

      <div class="qs-grid">
        <div class="qs-card">
          <div class="qs-num">1</div>
          <h3>Получи API-ключ</h3>
          <p>В веб-интерфейсе: ⚙️ Настройки → API-ключи → «Создать ключ». Сохрани <code>lc-XXXXXXXXXXXXXX</code> — он показывается один раз.</p>
        </div>
        <div class="qs-card">
          <div class="qs-num">2</div>
          <h3>Добавь заголовок</h3>
          <p>В каждый запрос: <code>Authorization: Bearer lc-XXXXXXXXXXXXXX</code></p>
        </div>
        <div class="qs-card">
          <div class="qs-num">3</div>
          <h3>Делай запросы</h3>
          <p>GET — получить, POST — создать, DELETE — удалить. Сервер отвечает JSON.</p>
        </div>
      </div>

      <div class="code-block" style="margin-top:24px;">
        <div class="code-block-header">
          <span>Первый запрос — узнать кто я</span>
          <button class="copy-btn" data-copy>📋 Копировать</button>
        </div>
        <pre><code class="language-bash">curl -H "Authorization: Bearer lc-abcdefghij2345" \
     https://tg-lcloud.duckdns.org/auth/v2/me</code></pre>
      </div>
    </div>
  </section>

  <!-- ============================================================ AUTH -->
  <section id="auth">
    <div class="container">
      <h2 class="section-title">🔐 Авторизация</h2>
      <p class="section-lead">Два способа: cookie (для браузера) или Bearer-токен (для API).</p>

      <details class="ep" open>
        <summary>
          <span class="method GET">GET</span>
          <span class="ep-path">/auth/v2/me</span>
          <span class="chevron">▼</span>
          <p class="ep-desc">Текущий пользователь, квота, дата создания</p>
        </summary>
        <div class="ep-body">
          <p>Возвращает информацию о залогиненном пользователе.</p>
          <div class="code-block">
            <div class="code-block-header">
              <span>curl</span>
              <button class="copy-btn" data-copy>📋</button>
            </div>
            <pre><code class="language-bash">curl -H "Authorization: Bearer lc-abc..." \
     https://tg-lcloud.duckdns.org/auth/v2/me</code></pre>
          </div>
          <div class="code-block">
            <div class="code-block-header"><span>Ответ</span></div>
            <pre><code class="language-json">{
  "user_id": 42,
  "role": "user",
  "pubkey": "5eb36f5d...",
  "storage_used_bytes": 1234567,
  "storage_quota_bytes": 5368709120,
  "created_at": "2026-05-30T08:00:00+00:00"
}</code></pre>
          </div>
        </div>
      </details>

      <details class="ep">
        <summary>
          <span class="method POST">POST</span>
          <span class="ep-path">/auth/v2/logout</span>
          <span class="chevron">▼</span>
          <p class="ep-desc">Завершить сессию</p>
        </summary>
        <div class="ep-body">
          <p>Удаляет cookie <code>lc_user_session</code>. Bearer-токены не аннулируются — для отзыва используй Settings → API-ключи → «Отозвать».</p>
          <div class="code-block">
            <div class="code-block-header"><span>curl</span><button class="copy-btn" data-copy>📋</button></div>
            <pre><code class="language-bash">curl -X POST -b cookies.txt \
     https://tg-lcloud.duckdns.org/auth/v2/logout</code></pre>
          </div>
        </div>
      </details>
    </div>
  </section>

  <!-- ============================================================ CLOUDS -->
  <section id="clouds">
    <div class="container">
      <h2 class="section-title">📂 Облака</h2>
      <p class="section-lead">Облако = папка верхнего уровня для файлов. Технически — супергруппа в Telegram.</p>

      <div class="ep-list">
        <details class="ep">
          <summary>
            <span class="method GET">GET</span>
            <span class="ep-path">/api/v1/clouds</span>
            <span class="chevron">▼</span>
            <p class="ep-desc">Список ваших облаков</p>
          </summary>
          <div class="ep-body">
            <div class="code-block">
              <div class="code-block-header"><span>curl</span><button class="copy-btn" data-copy>📋</button></div>
              <pre><code class="language-bash">curl -H "Authorization: Bearer lc-abc..." \
     https://tg-lcloud.duckdns.org/api/v1/clouds</code></pre>
            </div>
            <div class="code-block">
              <div class="code-block-header"><span>Ответ</span></div>
              <pre><code class="language-json">[
  {
    "id": 1,
    "chat_id": -1001555000123,
    "name": "MyPhotos",
    "owner_user_id": 42,
    "created_at": "2026-05-30T08:01:00+00:00"
  }
]</code></pre>
            </div>
          </div>
        </details>

        <details class="ep">
          <summary>
            <span class="method POST">POST</span>
            <span class="ep-path">/api/v1/clouds</span>
            <span class="chevron">▼</span>
            <p class="ep-desc">Создать новое облако</p>
          </summary>
          <div class="ep-body">
            <div class="code-block">
              <div class="code-block-header"><span>curl</span><button class="copy-btn" data-copy>📋</button></div>
              <pre><code class="language-bash">curl -X POST \
     -H "Authorization: Bearer lc-abc..." \
     -H "Content-Type: application/json" \
     -d '{"name": "Documents"}' \
     https://tg-lcloud.duckdns.org/api/v1/clouds</code></pre>
            </div>
          </div>
        </details>

        <details class="ep">
          <summary>
            <span class="method DELETE">DELETE</span>
            <span class="ep-path">/api/v1/clouds/{id}</span>
            <span class="chevron">▼</span>
            <p class="ep-desc">Отключить облако</p>
          </summary>
          <div class="ep-body">
            <p>Сама TG-супергруппа НЕ удаляется — только запись в LCloud.</p>
            <div class="code-block">
              <div class="code-block-header"><span>curl</span><button class="copy-btn" data-copy>📋</button></div>
              <pre><code class="language-bash">curl -X DELETE \
     -H "Authorization: Bearer lc-abc..." \
     https://tg-lcloud.duckdns.org/api/v1/clouds/1</code></pre>
            </div>
          </div>
        </details>
      </div>
    </div>
  </section>

  <!-- ============================================================ FILES -->
  <section id="files">
    <div class="container">
      <h2 class="section-title">📦 Файлы</h2>
      <p class="section-lead">Загрузка, скачивание, удаление. По умолчанию картинки сжимаются — это можно отключить.</p>

      <div class="ep-list">
        <details class="ep">
          <summary>
            <span class="method GET">GET</span>
            <span class="ep-path">/api/v1/clouds/{id}/files</span>
            <span class="chevron">▼</span>
            <p class="ep-desc">Список файлов в облаке (пагинация)</p>
          </summary>
          <div class="ep-body">
            <p>Параметры: <code>?limit=50&offset=0</code> (по умолчанию 50/0).</p>
            <div class="code-block">
              <div class="code-block-header"><span>curl</span><button class="copy-btn" data-copy>📋</button></div>
              <pre><code class="language-bash">curl -H "Authorization: Bearer lc-abc..." \
     "https://tg-lcloud.duckdns.org/api/v1/clouds/1/files?limit=50"</code></pre>
            </div>
          </div>
        </details>

        <details class="ep">
          <summary>
            <span class="method POST">POST</span>
            <span class="ep-path">/api/v1/clouds/{id}/files</span>
            <span class="chevron">▼</span>
            <p class="ep-desc">Загрузить файл (multipart)</p>
          </summary>
          <div class="ep-body">
            <p><strong>По умолчанию</strong> картинки сжимаются (JPEG q=85). Для оригинала — добавьте <code>compress=false</code>.</p>
            <div class="code-block">
              <div class="code-block-header"><span>Со сжатием (по умолчанию)</span><button class="copy-btn" data-copy>📋</button></div>
              <pre><code class="language-bash">curl -X POST \
     -H "Authorization: Bearer lc-abc..." \
     -F "file=@photo.jpg" \
     https://tg-lcloud.duckdns.org/api/v1/clouds/1/files</code></pre>
            </div>
            <div class="code-block">
              <div class="code-block-header"><span>Без сжатия (оригинал)</span><button class="copy-btn" data-copy>📋</button></div>
              <pre><code class="language-bash">curl -X POST \
     -H "Authorization: Bearer lc-abc..." \
     -F "file=@photo.jpg" \
     -F "compress=false" \
     https://tg-lcloud.duckdns.org/api/v1/clouds/1/files</code></pre>
            </div>
            <div class="code-block">
              <div class="code-block-header"><span>Ответ при сжатии</span></div>
              <pre><code class="language-json">{
  "id": 17,
  "name": "photo.jpg",
  "size": 350000,
  "compressed": true,
  "original_size_bytes": 1200000,
  "compression_ratio": 0.292,
  "caption_kind": "LC1"
}</code></pre>
            </div>
          </div>
        </details>

        <details class="ep">
          <summary>
            <span class="method GET">GET</span>
            <span class="ep-path">/api/v1/files/{id}/download</span>
            <span class="chevron">▼</span>
            <p class="ep-desc">Скачать файл</p>
          </summary>
          <div class="ep-body">
            <div class="code-block">
              <div class="code-block-header"><span>curl</span><button class="copy-btn" data-copy>📋</button></div>
              <pre><code class="language-bash">curl -H "Authorization: Bearer lc-abc..." \
     -o photo.jpg \
     https://tg-lcloud.duckdns.org/api/v1/files/17/download</code></pre>
            </div>
          </div>
        </details>

        <details class="ep">
          <summary>
            <span class="method DELETE">DELETE</span>
            <span class="ep-path">/api/v1/files/{id}</span>
            <span class="chevron">▼</span>
            <p class="ep-desc">Удалить файл (с освобождением квоты)</p>
          </summary>
          <div class="ep-body">
            <div class="code-block">
              <div class="code-block-header"><span>curl</span><button class="copy-btn" data-copy>📋</button></div>
              <pre><code class="language-bash">curl -X DELETE \
     -H "Authorization: Bearer lc-abc..." \
     https://tg-lcloud.duckdns.org/api/v1/files/17</code></pre>
            </div>
          </div>
        </details>

        <details class="ep">
          <summary>
            <span class="method GET">GET</span>
            <span class="ep-path">/api/v1/files/quota</span>
            <span class="chevron">▼</span>
            <p class="ep-desc">Сколько места занято / свободно</p>
          </summary>
          <div class="ep-body">
            <div class="code-block">
              <div class="code-block-header"><span>Ответ</span></div>
              <pre><code class="language-json">{
  "used_bytes": 1234567,
  "quota_bytes": 5368709120,
  "free_bytes": 5367474553
}</code></pre>
            </div>
          </div>
        </details>
      </div>
    </div>
  </section>

  <!-- ============================================================ KEYS -->
  <section id="keys">
    <div class="container">
      <h2 class="section-title">🔑 API-ключи</h2>
      <p class="section-lead">Создание/отзыв ключей. Также можно через UI: ⚙️ → API-ключи.</p>

      <div class="ep-list">
        <details class="ep">
          <summary>
            <span class="method POST">POST</span>
            <span class="ep-path">/api/v1/keys</span>
            <span class="chevron">▼</span>
            <p class="ep-desc">Создать новый ключ</p>
          </summary>
          <div class="ep-body">
            <p>Raw-ключ показывается <strong>один раз</strong>. Лимит: 25 активных ключей.</p>
            <div class="code-block">
              <div class="code-block-header"><span>curl</span><button class="copy-btn" data-copy>📋</button></div>
              <pre><code class="language-bash">curl -X POST \
     -b cookies.txt \
     -H "Content-Type: application/json" \
     -d '{"label": "production-bot"}' \
     https://tg-lcloud.duckdns.org/api/v1/keys</code></pre>
            </div>
            <div class="code-block">
              <div class="code-block-header"><span>Ответ</span></div>
              <pre><code class="language-json">{
  "id": 1,
  "raw": "lc-abcdefghij2345",
  "prefix": "lc-abcde",
  "label": "production-bot",
  "created_at": "2026-05-30T08:00:00+00:00",
  "last_used_at": null,
  "revoked_at": null
}</code></pre>
            </div>
          </div>
        </details>

        <details class="ep">
          <summary>
            <span class="method GET">GET</span>
            <span class="ep-path">/api/v1/keys</span>
            <span class="chevron">▼</span>
            <p class="ep-desc">Список ваших ключей (без raw)</p>
          </summary>
          <div class="ep-body">
            <div class="code-block">
              <div class="code-block-header"><span>curl</span><button class="copy-btn" data-copy>📋</button></div>
              <pre><code class="language-bash">curl -H "Authorization: Bearer lc-abc..." \
     https://tg-lcloud.duckdns.org/api/v1/keys</code></pre>
            </div>
          </div>
        </details>

        <details class="ep">
          <summary>
            <span class="method DELETE">DELETE</span>
            <span class="ep-path">/api/v1/keys/{id}</span>
            <span class="chevron">▼</span>
            <p class="ep-desc">Отозвать ключ</p>
          </summary>
          <div class="ep-body">
            <p>После этого все запросы с этим ключом будут получать 401.</p>
          </div>
        </details>
      </div>
    </div>
  </section>

  <!-- ============================================================ FAQ -->
  <section id="faq">
    <div class="container">
      <h2 class="section-title">💡 Частые вопросы</h2>
      <p class="section-lead">Если не нашли ответ — пишите в issues на GitHub.</p>

      <div class="faq-list">
        <details class="faq">
          <summary><span class="faq-q-icon">?</span>Что делать, если потерял сид-фразу?</summary>
          <div class="faq-body">
            <p><strong>Восстановить нельзя</strong> — это не пароль, который сбрасывается через email. Сид-фраза = ваш приватный ключ, и только из него выводится pubkey.</p>
            <p>Решение: создать новый аккаунт, перенести нужные файлы скачиванием/загрузкой. Для серверного администратора есть путь сброса (см. <a href="https://github.com/mramziddin1228-gif/LCloud/blob/main/docs/OPERATOR.md" target="_blank">OPERATOR.md</a>).</p>
          </div>
        </details>

        <details class="faq">
          <summary><span class="faq-q-icon">?</span>В чём разница compress=true и compress=false?</summary>
          <div class="faq-body">
            <p><strong>compress=true (по умолчанию):</strong> сервер пересжимает картинки в JPEG q=85, экономит ~70% места. Качество визуально почти не отличается.</p>
            <p><strong>compress=false:</strong> файл загружается байт-в-байт, без обработки. Полное сохранение оригинального качества + EXIF.</p>
            <p>На видео и не-картинки флаг не действует — они всегда идут как есть.</p>
          </div>
        </details>

        <details class="faq">
          <summary><span class="faq-q-icon">?</span>Какой максимальный размер файла?</summary>
          <div class="faq-body">
            <p>1 GiB по умолчанию (настраивается через <code>LC_MAX_FILE_BYTES</code>). Дополнительно действует квота на пользователя — обычно 5 GiB. Посмотреть текущую квоту: <code>GET /api/v1/files/quota</code>.</p>
          </div>
        </details>

        <details class="faq">
          <summary><span class="faq-q-icon">?</span>Что значит badge LC1 / LC2 на файлах?</summary>
          <div class="faq-body">
            <p><strong>LC2</strong> — файл подписан Ed25519-ключом пользователя <em>в браузере</em> до загрузки. Подпись + sha256 + timestamp embed в caption Telegram-сообщения. Сервер никогда не видит приватный ключ — это «настоящая криптография».</p>
            <p><strong>LC1</strong> — подпись делает сервер своим ключом (для curl-загрузок без client-side crypto). Менее строго, но всё равно проверяемо.</p>
          </div>
        </details>

        <details class="faq">
          <summary><span class="faq-q-icon">?</span>Безопасно ли держать API-ключ в коде?</summary>
          <div class="faq-body">
            <p>Не очень. Особенно в публичных репозиториях GitHub. Лучше — в env-переменных:</p>
            <div class="code-block">
              <div class="code-block-header"><span>bash</span><button class="copy-btn" data-copy>📋</button></div>
              <pre><code class="language-bash">export LCLOUD_KEY="lc-abc..."
curl -H "Authorization: Bearer $LCLOUD_KEY" ...</code></pre>
            </div>
            <p>Если ключ утёк — отзовите немедленно через UI или DELETE /api/v1/keys/&lt;id&gt;.</p>
          </div>
        </details>

        <details class="faq">
          <summary><span class="faq-q-icon">?</span>Какие лимиты на запросы?</summary>
          <div class="faq-body">
            <p><code>/auth/v2/challenge</code> и <code>/verify</code>: 10 запросов / 5 минут / IP.</p>
            <p>Прочие endpoint-ы: лимита нет, но Telegram MTProto на стороне сервера ограничивает ~30 операций в секунду — при превышении сервер ждёт.</p>
            <p>Активных API-ключей на пользователя: 25.</p>
          </div>
        </details>

        <details class="faq">
          <summary><span class="faq-q-icon">?</span>Как использовать API из Python?</summary>
          <div class="faq-body">
            <div class="code-block">
              <div class="code-block-header"><span>python</span><button class="copy-btn" data-copy>📋</button></div>
              <pre><code class="language-python">import os, requests

KEY = os.environ["LCLOUD_KEY"]
BASE = "https://tg-lcloud.duckdns.org"
H = {"Authorization": f"Bearer {KEY}"}

# Список облаков
clouds = requests.get(f"{BASE}/api/v1/clouds", headers=H).json()
print(clouds)

# Загрузить файл
with open("photo.jpg", "rb") as f:
    r = requests.post(
        f"{BASE}/api/v1/clouds/1/files",
        headers=H,
        files={"file": f},
    )
    print(r.json())</code></pre>
            </div>
          </div>
        </details>

        <details class="faq">
          <summary><span class="faq-q-icon">?</span>Что такое 401 / 403 / 413 ответы?</summary>
          <div class="faq-body">
            <p><strong>401</strong> — нет валидной авторизации. Проверь Bearer-токен или cookie.</p>
            <p><strong>403</strong> — токен валиден, но к этому ресурсу нельзя (например, чужой файл).</p>
            <p><strong>413</strong> — файл слишком большой ИЛИ исчерпана квота. Тело ошибки скажет что именно.</p>
            <p>Тело ошибки всегда: <code>{"detail": {"reason": "...", ...}}</code></p>
          </div>
        </details>

        <details class="faq">
          <summary><span class="faq-q-icon">?</span>Где Swagger UI для интерактивного тестирования?</summary>
          <div class="faq-body">
            <p><a href="/docs/swagger">/docs/swagger</a> — полный Swagger UI. Жмёшь <strong>Authorize</strong>, вставляешь Bearer-токен, нажимаешь <strong>Try it out</strong> — выполняется живьём.</p>
            <p>Альтернативно <a href="/redoc">/redoc</a> — для browse-only чтения спеки.</p>
          </div>
        </details>
      </div>
    </div>
  </section>

  <footer>
    <div class="container">
      <p>
        <a href="/docs/swagger">Swagger UI</a> ·
        <a href="/redoc">ReDoc</a> ·
        <a href="/openapi.json">OpenAPI JSON</a> ·
        <a href="https://github.com/mramziddin1228-gif/LCloud" target="_blank">GitHub</a>
      </p>
      <p style="margin-top:12px;font-size:12px;">LCloud · Telegram-userbot personal cloud</p>
    </div>
  </footer>

  <!-- Prism for syntax highlighting -->
  <script src="https://cdn.jsdelivr.net/npm/prismjs@1.29.0/prism.min.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/prismjs@1.29.0/components/prism-bash.min.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/prismjs@1.29.0/components/prism-json.min.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/prismjs@1.29.0/components/prism-python.min.js"></script>

  <!-- Copy buttons -->
  <script>
    document.querySelectorAll('.copy-btn').forEach((btn) => {
      btn.addEventListener('click', () => {
        const codeEl = btn.closest('.code-block').querySelector('pre code');
        if (!codeEl) return;
        navigator.clipboard.writeText(codeEl.textContent).then(() => {
          const orig = btn.textContent;
          btn.textContent = '✓ Скопировано';
          btn.classList.add('copied');
          setTimeout(() => {
            btn.textContent = orig;
            btn.classList.remove('copied');
          }, 1500);
        });
      });
    });

    // Smooth fade-in on scroll
    const observer = new IntersectionObserver((entries) => {
      entries.forEach((e) => {
        if (e.isIntersecting) {
          e.target.style.opacity = '1';
          e.target.style.transform = 'translateY(0)';
        }
      });
    }, { threshold: 0.1 });

    document.querySelectorAll('section').forEach((s) => {
      s.style.opacity = '0';
      s.style.transform = 'translateY(20px)';
      s.style.transition = 'opacity 0.5s ease, transform 0.5s ease';
      observer.observe(s);
    });
  </script>
</body>
</html>"""


# ============================================================
# /docs/swagger — vanilla Swagger UI (desktop)
# ============================================================

SWAGGER_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>LCloud API — Swagger UI</title>
  <link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E%3Ctext y='80' font-size='80'%3E%E2%98%81%EF%B8%8F%3C/text%3E%3C/svg%3E">
  <link rel="stylesheet" href="https://unpkg.com/swagger-ui-dist@5.17.14/swagger-ui.css">
</head>
<body style="margin:0;padding:0;">
  <div id="swagger-ui"></div>
  <script src="https://unpkg.com/swagger-ui-dist@5.17.14/swagger-ui-bundle.js"></script>
  <script src="https://unpkg.com/swagger-ui-dist@5.17.14/swagger-ui-standalone-preset.js"></script>
  <script>
    window.onload = () => {
      window.ui = SwaggerUIBundle({
        url: "/openapi.json",
        dom_id: "#swagger-ui",
        deepLinking: true,
        presets: [SwaggerUIBundle.presets.apis, SwaggerUIStandalonePreset],
        plugins: [SwaggerUIBundle.plugins.DownloadUrl],
        layout: "BaseLayout",
        docExpansion: "list",
        defaultModelsExpandDepth: 0,
        tryItOutEnabled: true,
        persistAuthorization: true,
      });
    };
  </script>
</body>
</html>"""


# ============================================================
# /redoc
# ============================================================

REDOC_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta http-equiv="Content-Type" content="text/html; charset=UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=5">
  <title>LCloud API — ReDoc</title>
  <link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E%3Ctext y='80' font-size='80'%3E%E2%98%81%EF%B8%8F%3C/text%3E%3C/svg%3E">
  <link href="https://fonts.googleapis.com/css?family=Inter:400,500,600,700|JetBrains+Mono:400,500&display=swap" rel="stylesheet">
  <style>
    body { margin: 0; padding: 0; font-family: 'Inter', system-ui, -apple-system, sans-serif; }
    @media (max-width: 640px) { redoc { font-size: 14px; } }
    @media (prefers-color-scheme: dark) { body { background: #0a0a0a; color: #e2e8f0; } }
  </style>
</head>
<body>
  <redoc
    spec-url="/openapi.json"
    theme='{
      "typography": {
        "fontFamily": "Inter, system-ui, -apple-system, sans-serif",
        "code": {"fontFamily": "JetBrains Mono, monospace"}
      },
      "colors": {"primary": {"main": "#10b981"}}
    }'
    expand-responses="200,201"
    json-sample-expand-level="2"
    hide-loading
  ></redoc>
  <script src="https://cdn.redoc.ly/redoc/latest/bundles/redoc.standalone.js"></script>
</body>
</html>"""


# ------------------------------------------------------------ routes


@router.get("/docs", response_class=HTMLResponse)
async def custom_docs() -> HTMLResponse:
    """Beautiful custom landing page — quickstart + endpoint cards + FAQ."""
    return HTMLResponse(content=DOCS_HTML, media_type="text/html; charset=utf-8")


@router.get("/docs/swagger", response_class=HTMLResponse)
async def swagger_ui() -> HTMLResponse:
    """Vanilla Swagger UI for interactive testing on desktop."""
    return HTMLResponse(content=SWAGGER_HTML, media_type="text/html; charset=utf-8")


@router.get("/redoc", response_class=HTMLResponse)
async def custom_redoc() -> HTMLResponse:
    """ReDoc — read-only browsing of the OpenAPI spec."""
    return HTMLResponse(content=REDOC_HTML, media_type="text/html; charset=utf-8")


def serve_openapi(app: FastAPI) -> None:
    """Override /openapi.json to set application/json; charset=utf-8."""

    @app.get("/openapi.json", include_in_schema=False)
    async def openapi_json() -> JSONResponse:
        spec = app.openapi()
        return JSONResponse(
            content=spec,
            media_type="application/json; charset=utf-8",
            headers={"Cache-Control": "no-cache, must-revalidate"},
        )


__all__ = ["router", "serve_openapi"]
