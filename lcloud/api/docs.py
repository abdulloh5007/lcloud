"""Custom API docs UIs — mobile-friendly + explicit UTF-8.

Three pages, three trade-offs:

- `/docs` → **RapiDoc** (default). Mobile-first web component with
  built-in dark mode and much better touch UX than Swagger UI on phones.
- `/docs/legacy` → Swagger UI with mobile CSS overrides. For users who
  specifically want the classic Swagger look.
- `/redoc` → ReDoc, read-only browsing.

All three load `/openapi.json` (which we serve with explicit
`application/json; charset=utf-8` to avoid Cyrillic display issues
in some browsers).
"""

from __future__ import annotations

from fastapi import APIRouter, FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

router = APIRouter(include_in_schema=False)


# ============================================================
# RapiDoc — primary /docs page (mobile-first)
# ============================================================

RAPIDOC_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=5">
  <title>LCloud API</title>
  <link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E%3Ctext y='80' font-size='80'%3E%E2%98%81%EF%B8%8F%3C/text%3E%3C/svg%3E">
  <link href="https://fonts.googleapis.com/css?family=Inter:400,500,600,700|JetBrains+Mono:400,500&display=swap" rel="stylesheet">
  <script type="module" src="https://unpkg.com/rapidoc@9.3.4/dist/rapidoc-min.js"></script>
  <style>
    html, body { margin: 0; padding: 0; height: 100%; }
    body { font-family: 'Inter', system-ui, -apple-system, sans-serif; }
    rapi-doc { display: block; width: 100%; height: 100vh; }
  </style>
</head>
<body>
  <rapi-doc
    spec-url="/openapi.json"
    theme="dark"
    render-style="focused"
    show-header="false"
    allow-server-selection="false"
    allow-spec-url-load="false"
    allow-spec-file-load="false"
    show-method-in-nav-bar="as-colored-block"
    use-path-in-nav-bar="true"
    nav-bg-color="#0f172a"
    nav-text-color="#cbd5e1"
    nav-hover-bg-color="#1e293b"
    nav-hover-text-color="#fff"
    nav-accent-color="#10b981"
    primary-color="#10b981"
    bg-color="#0a0a0a"
    text-color="#e5e7eb"
    header-color="#0f172a"
    regular-font="Inter, system-ui, sans-serif"
    mono-font="JetBrains Mono, monospace"
    font-size="default"
    schema-style="table"
    default-schema-tab="example"
    response-area-height="300px"
    persist-auth="true"
    layout="column"
    sort-tags="true"
    sort-endpoints-by="path"
    show-info="true"
    info-description-headings-in-navbar="true"
  >
    <div slot="logo" style="display:flex;align-items:center;gap:8px;padding:0 12px;">
      <span style="font-size:24px;">☁️</span>
      <span style="font-weight:600;color:#fff;font-size:18px;">LCloud API</span>
    </div>
  </rapi-doc>
  <script>
    document.addEventListener('DOMContentLoaded', () => {
      const rapi = document.querySelector('rapi-doc');
      if (window.matchMedia('(prefers-color-scheme: light)').matches) {
        rapi.setAttribute('theme', 'light');
        rapi.setAttribute('bg-color', '#fafafa');
        rapi.setAttribute('text-color', '#1e293b');
        rapi.setAttribute('header-color', '#0f172a');
        rapi.setAttribute('nav-bg-color', '#f1f5f9');
        rapi.setAttribute('nav-text-color', '#475569');
      }
    });
  </script>
</body>
</html>"""


# ============================================================
# Legacy Swagger UI — /docs/legacy
# ============================================================

SWAGGER_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=5, user-scalable=yes">
  <title>LCloud API — Swagger UI (legacy)</title>
  <link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E%3Ctext y='80' font-size='80'%3E%E2%98%81%EF%B8%8F%3C/text%3E%3C/svg%3E">
  <link rel="stylesheet" href="https://unpkg.com/swagger-ui-dist@5.17.14/swagger-ui.css">
  <style>
    *, *::before, *::after { box-sizing: border-box; }
    html, body {
      margin: 0; padding: 0;
      background: #fafafa;
      overflow-x: hidden;
      -webkit-text-size-adjust: 100%;
    }
    @media (prefers-color-scheme: dark) { html, body { background: #0a0a0a; } }

    .swagger-ui { max-width: 100% !important; padding: 0 !important; font-size: 13px; }
    .swagger-ui .wrapper { max-width: 100% !important; padding: 0 12px !important; }

    .swagger-ui .topbar { background: #0f172a; padding: 6px 10px; }
    .swagger-ui .topbar .download-url-wrapper { display: none; }
    .swagger-ui .topbar-wrapper a span { color: #fff; font-size: 16px; }

    .swagger-ui .info { margin: 12px 0 16px; }
    .swagger-ui .info .title { font-size: 20px; font-weight: 600; word-wrap: break-word; }
    .swagger-ui .info .description { font-size: 13px; line-height: 1.5; }
    .swagger-ui .info .description code {
      background: rgba(0,0,0,0.06);
      padding: 1px 4px; border-radius: 3px;
      font-size: 11px; word-break: break-all;
    }
    .swagger-ui .info .description table {
      display: block; overflow-x: auto; width: 100%; font-size: 12px;
    }

    .swagger-ui .scheme-container { padding: 8px; box-shadow: none; background: rgba(0,0,0,0.02); }

    .swagger-ui .opblock { margin: 0 0 6px; border-radius: 4px; }
    .swagger-ui .opblock-summary {
      padding: 6px 10px; gap: 6px; flex-wrap: wrap; min-height: auto;
    }
    .swagger-ui .opblock-summary-method {
      min-width: 60px; padding: 4px 0; font-size: 11px;
      flex-shrink: 0;
    }
    .swagger-ui .opblock-summary-path {
      font-size: 12px; word-break: break-all; flex: 1 1 200px; min-width: 0;
    }
    .swagger-ui .opblock-summary-description {
      font-size: 11px; color: #475569; flex: 1 1 100%; padding-top: 2px;
    }

    .swagger-ui .parameters-container,
    .swagger-ui .responses-wrapper { overflow-x: auto; -webkit-overflow-scrolling: touch; }

    .swagger-ui table.parameters,
    .swagger-ui table.responses-table { font-size: 12px; }

    .swagger-ui input[type=text], .swagger-ui input[type=password],
    .swagger-ui input[type=email], .swagger-ui input[type=file],
    .swagger-ui textarea, .swagger-ui select {
      font-size: 14px !important;
      max-width: 100% !important;
      box-sizing: border-box !important;
    }

    /* Smaller, neater buttons */
    .swagger-ui .btn {
      padding: 5px 10px;
      font-size: 11px;
      min-height: 0;
      box-shadow: none;
    }
    .swagger-ui .try-out__btn,
    .swagger-ui .execute,
    .swagger-ui .btn.cancel {
      padding: 6px 12px;
      font-size: 12px;
    }
    .swagger-ui .btn.authorize {
      padding: 4px 10px;
      font-size: 11px;
    }
    .swagger-ui .auth-wrapper { padding: 0 4px; }

    .swagger-ui pre, .swagger-ui .highlight-code {
      max-width: 100%;
      overflow-x: auto;
      -webkit-overflow-scrolling: touch;
      font-size: 11px !important;
    }

    .swagger-ui .dialog-ux .modal-ux {
      max-width: 95vw !important;
      width: 95vw !important;
      max-height: 90vh;
      overflow-y: auto;
    }

    @media (max-width: 640px) {
      .swagger-ui .wrapper { padding: 0 6px !important; }
      .swagger-ui .info .title { font-size: 17px; }
      .swagger-ui .info .description { font-size: 12px; }
      .swagger-ui .info .description h2 { font-size: 14px; margin: 10px 0 4px; }
      .swagger-ui .info .info__contact,
      .swagger-ui .info .info__tos,
      .swagger-ui .info .info__license { display: none; }

      .swagger-ui .opblock-summary { padding: 6px 8px; }
      .swagger-ui .opblock-summary-method { min-width: 52px; font-size: 10px; padding: 3px 0; }
      .swagger-ui .opblock-summary-path { font-size: 11px; }
      .swagger-ui .opblock-summary-description { display: none; }

      .swagger-ui .opblock-tag { padding: 6px 8px; font-size: 14px; }
      .swagger-ui .opblock-tag small { font-size: 10px; }

      .swagger-ui table.parameters > tbody > tr,
      .swagger-ui table.responses-table > tbody > tr {
        display: block; padding: 6px 0;
        border-bottom: 1px solid rgba(0,0,0,0.1);
      }
      .swagger-ui table.parameters > tbody > tr > td,
      .swagger-ui table.responses-table > tbody > tr > td {
        display: block; padding: 2px 0; width: 100% !important;
      }
      .swagger-ui table.parameters > thead,
      .swagger-ui table.responses-table > thead { display: none; }

      .swagger-ui .btn { padding: 4px 8px; font-size: 10px; }
      .swagger-ui .try-out__btn,
      .swagger-ui .execute { padding: 5px 10px; font-size: 11px; }
    }

    @media (max-width: 380px) {
      .swagger-ui .wrapper { padding: 0 4px !important; }
      .swagger-ui .info .title { font-size: 15px; }
      .swagger-ui .opblock-summary-method { min-width: 46px; font-size: 9px; }
      .swagger-ui .opblock-summary-path { font-size: 10px; }
    }

    @media (prefers-color-scheme: dark) {
      .swagger-ui, .swagger-ui .info .title, .swagger-ui .info .description,
      .swagger-ui .opblock-tag, .swagger-ui .scheme-container,
      .swagger-ui .opblock-section-header h4,
      .swagger-ui .renderedMarkdown,
      .swagger-ui .parameter__name, .swagger-ui .parameter__type {
        color: #e2e8f0 !important;
      }
      .swagger-ui .opblock,
      .swagger-ui .opblock .opblock-section-header {
        background: #0f172a !important; border-color: #334155 !important;
      }
      .swagger-ui .info .description code {
        background: rgba(255,255,255,0.1); color: #fbbf24;
      }
      .swagger-ui .scheme-container { background: rgba(255,255,255,0.03); box-shadow: none; }
      .swagger-ui select, .swagger-ui input, .swagger-ui textarea {
        background: #1e293b !important; color: #e2e8f0 !important;
        border-color: #475569 !important;
      }
      .swagger-ui pre, .swagger-ui .highlight-code, .swagger-ui .microlight {
        background: #0f172a !important; color: #e2e8f0 !important;
      }
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
        presets: [SwaggerUIBundle.presets.apis, SwaggerUIStandalonePreset],
        plugins: [SwaggerUIBundle.plugins.DownloadUrl],
        layout: "BaseLayout",
        docExpansion: "none",
        defaultModelsExpandDepth: 0,
        defaultModelExpandDepth: 1,
        tryItOutEnabled: true,
        persistAuthorization: true,
        filter: true,
      });
    };
  </script>
</body>
</html>"""


# ============================================================
# ReDoc — read-only browsing
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
      "colors": {"primary": {"main": "#10b981"}},
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
async def custom_rapidoc() -> HTMLResponse:
    """RapiDoc — primary docs page, mobile-first by design."""
    return HTMLResponse(content=RAPIDOC_HTML, media_type="text/html; charset=utf-8")


@router.get("/docs/legacy", response_class=HTMLResponse)
async def custom_swagger_ui() -> HTMLResponse:
    """Legacy Swagger UI for users who prefer the classic look."""
    return HTMLResponse(content=SWAGGER_HTML, media_type="text/html; charset=utf-8")


@router.get("/redoc", response_class=HTMLResponse)
async def custom_redoc() -> HTMLResponse:
    """ReDoc — three-column read-only browsing."""
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
