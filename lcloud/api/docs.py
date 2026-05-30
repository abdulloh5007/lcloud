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
  <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=5">
  <title>LCloud API — Swagger UI</title>
  <link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E%3Ctext y='80' font-size='80'%3E%E2%98%81%EF%B8%8F%3C/text%3E%3C/svg%3E">
  <link rel="stylesheet" href="https://unpkg.com/swagger-ui-dist@5.17.14/swagger-ui.css">
  <style>
    /* Reset + responsive base */
    html, body { margin: 0; padding: 0; background: #fafafa; }
    @media (prefers-color-scheme: dark) {
      html, body { background: #0a0a0a; }
    }

    /* Swagger UI container */
    .swagger-ui { max-width: 100%; padding: 0 8px; }

    /* Topbar — make it more compact and brand it */
    .swagger-ui .topbar {
      background: #0f172a;
      border-bottom: 1px solid #1e293b;
      padding: 8px 12px;
    }
    .swagger-ui .topbar .download-url-wrapper { display: none; }
    .swagger-ui .topbar-wrapper a span { color: #fff; }

    /* Info section */
    .swagger-ui .info { margin: 24px 0 32px; }
    .swagger-ui .info .title { font-size: 28px; font-weight: 600; }
    .swagger-ui .info .description p { font-size: 15px; line-height: 1.6; }
    .swagger-ui .info .description code {
      background: rgba(0,0,0,0.06);
      padding: 1px 5px;
      border-radius: 3px;
      font-size: 13px;
    }
    @media (prefers-color-scheme: dark) {
      .swagger-ui .info .description code {
        background: rgba(255,255,255,0.1);
        color: #fbbf24;
      }
    }

    /* Operation blocks — slightly more padding for touch */
    .swagger-ui .opblock-summary { padding: 8px 12px; min-height: 52px; }
    .swagger-ui .opblock-summary-method {
      min-width: 70px;
      padding: 8px 0;
      font-size: 13px;
    }
    .swagger-ui .opblock-summary-path {
      font-size: 13px;
      word-break: break-all;
    }
    .swagger-ui .opblock-summary-description {
      font-size: 13px;
      color: #475569;
    }

    /* Try-it-out form, parameters, responses — readable on small screens */
    .swagger-ui table.parameters,
    .swagger-ui table.responses-table { font-size: 13px; }
    .swagger-ui input, .swagger-ui textarea, .swagger-ui select {
      font-size: 14px !important;  /* prevents iOS zoom on focus */
    }

    /* Mobile: stack everything tighter */
    @media (max-width: 640px) {
      .swagger-ui .info .title { font-size: 22px; }
      .swagger-ui .scheme-container { padding: 12px 8px; }
      .swagger-ui .opblock-summary-method { min-width: 60px; font-size: 11px; }
      .swagger-ui .opblock-summary-path { font-size: 12px; }
      .swagger-ui .opblock-summary-description { display: none; }
      .swagger-ui .opblock .opblock-section-header h4 { font-size: 13px; }
      .swagger-ui .response-col_status { min-width: 60px; }
      .swagger-ui table.parameters > tbody > tr,
      .swagger-ui table.responses-table > tbody > tr {
        display: block;
        margin-bottom: 8px;
      }
      .swagger-ui .parameter__name { font-size: 12px; }
      .swagger-ui .parameter__type { font-size: 11px; }
    }

    /* Dark mode adjustments */
    @media (prefers-color-scheme: dark) {
      .swagger-ui, .swagger-ui .info .title,
      .swagger-ui .scheme-container, .swagger-ui .opblock-tag,
      .swagger-ui .info .description { color: #e2e8f0; }
      .swagger-ui .opblock { border-color: #334155; background: #0f172a; }
      .swagger-ui .opblock .opblock-section-header { background: #1e293b; }
      .swagger-ui .scheme-container { background: transparent; box-shadow: none; }
      .swagger-ui select, .swagger-ui input, .swagger-ui textarea {
        background: #1e293b !important; color: #e2e8f0 !important;
        border-color: #475569 !important;
      }
      .swagger-ui .response-col_description__inner div.markdown,
      .swagger-ui .response-col_description__inner div.renderedMarkdown { color: #cbd5e1; }
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
        docExpansion: "list",
        defaultModelsExpandDepth: 0,
        tryItOutEnabled: true,
        persistAuthorization: true,
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
