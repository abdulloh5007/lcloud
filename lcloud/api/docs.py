"""Custom Swagger UI + ReDoc serving — mobile-friendly + explicit UTF-8.

We intentionally don't use FastAPI's default `docs_url` / `redoc_url`
because:

1. The default ReDoc CDN sometimes serves cached HTML without explicit
   `<meta charset="utf-8">`, breaking Cyrillic in the description.
2. The default Swagger UI uses fixed widths that overflow on phones.
3. We want a consistent dark-mode-aware look matching the main app.

Both pages fetch `/openapi.json` with explicit UTF-8 handling. The
JSON itself is served with `application/json; charset=utf-8` by a
thin route override below.
"""

from __future__ import annotations

from fastapi import APIRouter, FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

router = APIRouter(include_in_schema=False)


# ------------------------------------------------------------ HTML pages


SWAGGER_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=5, user-scalable=yes">
  <title>LCloud API — Swagger UI</title>
  <link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E%3Ctext y='80' font-size='80'%3E%E2%98%81%EF%B8%8F%3C/text%3E%3C/svg%3E">
  <link rel="stylesheet" href="https://unpkg.com/swagger-ui-dist@5.17.14/swagger-ui.css">
  <style>
    /* ============================================================
       LCloud Swagger UI — mobile-first overrides

       The default swagger-ui styles assume a desktop browser. We
       override aggressively so things stay usable down to ~320px.
       ============================================================ */

    *, *::before, *::after { box-sizing: border-box; }

    html, body {
      margin: 0; padding: 0;
      background: #fafafa;
      overflow-x: hidden;            /* never let anything bust horizontal scroll */
    }
    body { -webkit-text-size-adjust: 100%; }

    @media (prefers-color-scheme: dark) {
      html, body { background: #0a0a0a; }
    }

    /* Base container — responsive width */
    .swagger-ui {
      max-width: 100% !important;
      padding: 0 !important;
    }
    .swagger-ui .wrapper {
      max-width: 100% !important;
      padding: 0 12px !important;
    }

    /* Topbar */
    .swagger-ui .topbar {
      background: #0f172a;
      border-bottom: 1px solid #1e293b;
      padding: 8px 12px;
    }
    .swagger-ui .topbar .download-url-wrapper { display: none; }
    .swagger-ui .topbar-wrapper { padding: 0; }
    .swagger-ui .topbar-wrapper a span { color: #fff; font-size: 18px; }

    /* Info section */
    .swagger-ui .info { margin: 16px 0 24px; }
    .swagger-ui .info hgroup.main { margin: 0 0 12px; }
    .swagger-ui .info .title {
      font-size: 24px;
      font-weight: 600;
      word-wrap: break-word;
      line-height: 1.2;
    }
    .swagger-ui .info .description {
      font-size: 14px;
      line-height: 1.5;
    }
    .swagger-ui .info .description p { margin: 8px 0; }
    .swagger-ui .info .description h2 {
      font-size: 18px;
      margin: 20px 0 8px;
    }
    .swagger-ui .info .description code {
      background: rgba(0,0,0,0.06);
      padding: 1px 4px;
      border-radius: 3px;
      font-size: 12px;
      word-break: break-all;
    }
    .swagger-ui .info .description table {
      display: block;
      overflow-x: auto;
      width: 100%;
      font-size: 13px;
    }
    .swagger-ui .info .description table thead th,
    .swagger-ui .info .description table tbody td {
      padding: 6px 8px;
      white-space: nowrap;
    }

    /* Server selector — fits viewport on mobile */
    .swagger-ui .scheme-container {
      padding: 12px;
      box-shadow: none;
      background: rgba(0,0,0,0.02);
    }
    .swagger-ui .scheme-container .schemes-title { display: none; }
    .swagger-ui .servers > label { font-size: 13px; }
    .swagger-ui .servers > label select {
      max-width: 100%;
    }

    /* Operation block (the GET / POST rows) */
    .swagger-ui .opblock {
      margin: 0 0 10px;
      border-radius: 6px;
    }
    .swagger-ui .opblock-summary {
      padding: 8px 12px;
      gap: 8px;
      flex-wrap: wrap;            /* let path drop below method on tiny screens */
    }
    .swagger-ui .opblock-summary-method {
      min-width: 70px;
      padding: 6px 0;
      font-size: 12px;
      flex-shrink: 0;
    }
    .swagger-ui .opblock-summary-path,
    .swagger-ui .opblock-summary-path__deprecated {
      font-size: 13px;
      word-break: break-all;
      overflow-wrap: break-word;
      flex: 1 1 200px;
      min-width: 0;
    }
    .swagger-ui .opblock-summary-description {
      font-size: 12px;
      color: #475569;
      flex: 1 1 100%;
      padding-top: 4px;
    }

    /* Parameters / responses tables — stack on mobile */
    .swagger-ui .parameters-container,
    .swagger-ui .responses-wrapper {
      overflow-x: auto;
      -webkit-overflow-scrolling: touch;
    }
    .swagger-ui table.parameters,
    .swagger-ui table.responses-table {
      font-size: 13px;
      width: 100%;
    }
    .swagger-ui .parameter__name {
      font-size: 13px;
      word-break: break-word;
    }
    .swagger-ui .parameter__type,
    .swagger-ui .parameter__deprecated,
    .swagger-ui .parameter__in {
      font-size: 11px;
    }

    /* Inputs — 16px font prevents iOS zoom on focus */
    .swagger-ui input[type=text],
    .swagger-ui input[type=password],
    .swagger-ui input[type=email],
    .swagger-ui input[type=file],
    .swagger-ui textarea,
    .swagger-ui select {
      font-size: 16px !important;
      max-width: 100% !important;
      box-sizing: border-box !important;
    }

    /* Try-it-out button + execute */
    .swagger-ui .btn {
      padding: 8px 14px;
      font-size: 13px;
      min-height: 36px;
    }
    .swagger-ui .try-out__btn,
    .swagger-ui .execute {
      min-height: 40px;
    }

    /* Code samples / responses — better wrapping */
    .swagger-ui .highlight-code,
    .swagger-ui pre {
      max-width: 100%;
      overflow-x: auto;
      -webkit-overflow-scrolling: touch;
      font-size: 12px !important;
    }
    .swagger-ui pre code {
      word-break: normal;
      white-space: pre;
    }
    .swagger-ui .response-col_status { min-width: 56px; font-size: 12px; }
    .swagger-ui .response-col_description__inner div.markdown,
    .swagger-ui .response-col_description__inner div.renderedMarkdown {
      font-size: 12px;
    }

    /* Authorize modal — fit small viewports */
    .swagger-ui .dialog-ux .modal-ux {
      max-width: 95vw !important;
      width: 95vw !important;
      max-height: 90vh;
      overflow-y: auto;
    }
    .swagger-ui .dialog-ux .modal-ux-header h3 {
      font-size: 16px;
    }

    /* Schema models section */
    .swagger-ui section.models { font-size: 13px; }
    .swagger-ui section.models h4 { font-size: 14px; }

    /* ============================================================
       SMALL SCREEN (≤640px) — even tighter
       ============================================================ */
    @media (max-width: 640px) {
      .swagger-ui .wrapper { padding: 0 8px !important; }

      .swagger-ui .info { margin: 12px 0 16px; }
      .swagger-ui .info .title { font-size: 20px; }
      .swagger-ui .info .description { font-size: 13px; }
      .swagger-ui .info .description h2 { font-size: 16px; margin: 14px 0 6px; }

      /* Hide tags/contact details on very small screens — show on tap */
      .swagger-ui .info .info__contact,
      .swagger-ui .info .info__tos,
      .swagger-ui .info .info__license { display: none; }

      .swagger-ui .opblock-summary { padding: 8px 10px; }
      .swagger-ui .opblock-summary-method {
        min-width: 56px;
        font-size: 11px;
        padding: 4px 0;
      }
      .swagger-ui .opblock-summary-path { font-size: 12px; }
      .swagger-ui .opblock-summary-description { display: none; }

      .swagger-ui .opblock .opblock-section-header {
        padding: 8px 10px;
      }
      .swagger-ui .opblock .opblock-section-header h4 { font-size: 12px; }

      /* Tag header — collapse */
      .swagger-ui .opblock-tag {
        padding: 8px 10px;
        font-size: 16px;
      }
      .swagger-ui .opblock-tag small { font-size: 11px; }

      /* Parameters: row → block layout */
      .swagger-ui table.parameters > tbody > tr,
      .swagger-ui table.responses-table > tbody > tr {
        display: block;
        padding: 8px 0;
        border-bottom: 1px solid rgba(0,0,0,0.1);
      }
      .swagger-ui table.parameters > tbody > tr > td,
      .swagger-ui table.responses-table > tbody > tr > td {
        display: block;
        padding: 4px 0;
        width: 100% !important;
      }
      .swagger-ui table.parameters > thead,
      .swagger-ui table.responses-table > thead {
        display: none;
      }

      /* Schema definitions — hide deep nesting on tiny screens */
      .swagger-ui .model-box { padding: 4px; font-size: 11px; }

      /* Authorize button in topbar */
      .swagger-ui .auth-wrapper .authorize {
        margin-left: 4px;
        padding: 4px 8px;
        font-size: 11px;
      }
      .swagger-ui .auth-wrapper .authorize span { font-size: 11px; }
    }

    /* ============================================================
       VERY SMALL (≤380px) — phones in portrait
       ============================================================ */
    @media (max-width: 380px) {
      .swagger-ui .wrapper { padding: 0 4px !important; }
      .swagger-ui .info .title { font-size: 18px; }
      .swagger-ui .opblock-summary-method { min-width: 50px; font-size: 10px; }
      .swagger-ui .opblock-summary-path { font-size: 11px; }
      .swagger-ui .btn { padding: 6px 10px; font-size: 12px; }
    }

    /* ============================================================
       DARK MODE
       ============================================================ */
    @media (prefers-color-scheme: dark) {
      .swagger-ui,
      .swagger-ui .info .title,
      .swagger-ui .info .description,
      .swagger-ui .opblock-tag,
      .swagger-ui .scheme-container,
      .swagger-ui .opblock-section-header h4,
      .swagger-ui .response-col_description__inner div.renderedMarkdown,
      .swagger-ui .renderedMarkdown,
      .swagger-ui .parameter__name,
      .swagger-ui .parameter__type {
        color: #e2e8f0 !important;
      }
      .swagger-ui .opblock,
      .swagger-ui .opblock .opblock-section-header {
        background: #0f172a !important;
        border-color: #334155 !important;
      }
      .swagger-ui .info .description code {
        background: rgba(255,255,255,0.1);
        color: #fbbf24;
      }
      .swagger-ui .scheme-container { background: rgba(255,255,255,0.03); box-shadow: none; }
      .swagger-ui select,
      .swagger-ui input,
      .swagger-ui textarea {
        background: #1e293b !important;
        color: #e2e8f0 !important;
        border-color: #475569 !important;
      }
      .swagger-ui pre,
      .swagger-ui .highlight-code,
      .swagger-ui .microlight {
        background: #0f172a !important;
        color: #e2e8f0 !important;
      }
      .swagger-ui table.parameters > tbody > tr,
      .swagger-ui table.responses-table > tbody > tr {
        border-bottom-color: rgba(255,255,255,0.1);
      }
      .swagger-ui .opblock-summary-description { color: #94a3b8; }
    }
  </style>
</head>
<body>
  <div id="swagger-ui"></div>
  <script src="https://unpkg.com/swagger-ui-dist@5.17.14/swagger-ui-bundle.js"></script>
  <script src="https://unpkg.com/swagger-ui-dist@5.17.14/swagger-ui-standalone-preset.js"></script>
  <script>
    window.onload = () => {
      window.ui = SwaggerUIBundle({
        url: "/openapi.json",
        dom_id: "#swagger-ui",
        deepLinking: true,
        presets: [
          SwaggerUIBundle.presets.apis,
          SwaggerUIStandalonePreset,
        ],
        plugins: [SwaggerUIBundle.plugins.DownloadUrl],
        layout: "BaseLayout",
        // Tag groups collapsed by default — easier to scan on phones.
        docExpansion: "none",
        defaultModelsExpandDepth: 0,
        tryItOutEnabled: true,
        persistAuthorization: true,
        // Smaller default model rendering
        defaultModelExpandDepth: 1,
        // Filter input — handy on a long endpoint list
        filter: true,
      });
    };
  </script>
</body>
</html>"""


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
    body {
      margin: 0;
      padding: 0;
      font-family: 'Inter', system-ui, -apple-system, sans-serif;
    }
    /* Mobile: ReDoc collapses sidebar by default but tighten typography */
    @media (max-width: 640px) {
      redoc { font-size: 14px; }
    }
    @media (prefers-color-scheme: dark) {
      body { background: #0a0a0a; color: #e2e8f0; }
    }
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
      "colors": {
        "primary": {"main": "#10b981"}
      },
      "sidebar": {"width": "260px"}
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
async def custom_swagger_ui() -> HTMLResponse:
    """Custom Swagger UI with explicit UTF-8 + mobile-responsive CSS."""
    return HTMLResponse(content=SWAGGER_HTML, media_type="text/html; charset=utf-8")


@router.get("/redoc", response_class=HTMLResponse)
async def custom_redoc() -> HTMLResponse:
    """Custom ReDoc with explicit UTF-8 + mobile typography."""
    return HTMLResponse(content=REDOC_HTML, media_type="text/html; charset=utf-8")


def serve_openapi(app: FastAPI) -> None:
    """Override `/openapi.json` to set `application/json; charset=utf-8`.

    FastAPI's default route returns `application/json` without charset,
    which can confuse some browsers and force a re-encode round-trip.
    """

    @app.get("/openapi.json", include_in_schema=False)
    async def openapi_json() -> JSONResponse:
        spec = app.openapi()
        return JSONResponse(
            content=spec,
            media_type="application/json; charset=utf-8",
            headers={
                # Long browser cache OK because version string is part of
                # the spec; clients can refresh on app version bump.
                "Cache-Control": "no-cache, must-revalidate",
            },
        )


__all__ = ["router", "serve_openapi"]
