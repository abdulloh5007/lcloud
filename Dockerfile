# syntax=docker/dockerfile:1.7

FROM node:22-bookworm-slim AS web-build
WORKDIR /app/web
COPY web/package*.json ./
RUN npm ci
COPY web ./
RUN npm run build

FROM python:3.12-slim AS runtime
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    LC_HOST=0.0.0.0 \
    LC_PORT=8787 \
    LC_DATA_DIR=/app/data \
    LC_SESSION_FILE=/app/data/session.lcloud \
    LC_DB_URL=sqlite+aiosqlite:////app/data/lcloud.db

WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock alembic.ini ./
COPY alembic ./alembic
COPY lcloud ./lcloud
COPY README.md LICENSE ./
COPY docs ./docs
COPY --from=web-build /app/web/dist ./web/dist

RUN pip install --upgrade pip \
    && pip install -e . \
    && mkdir -p /app/data /app/logs

EXPOSE 8787
VOLUME ["/app/data", "/app/logs"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS "http://127.0.0.1:${LC_PORT}/health" || exit 1

CMD ["lcloud"]
