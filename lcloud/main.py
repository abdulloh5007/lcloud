"""FastAPI entrypoint.

Lifespan order:
    1. ensure_runtime_dirs       — data/, data/keys/, data/tmp/
    2. ensure_admin_keypair      — Ed25519 keys (mode 600 / 644)
    3. ensure_jwt_secret         — HS256 secret in data/keys/jwt.secret (mode 600)
    4. init_engine + run_migrations + ensure_admin_owner
    5. UserbotManager.start()    — Telethon connect (no auth required to start)
    6. If already authorized as the admin → fire-and-forget cloud scan +
       attach NewMessage handler.
    7. Mount the React SPA at `/` (and `/assets/*`) if `web/dist` exists.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import sqlalchemy as sa
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from lcloud import __version__
from lcloud.api import (
    api_keys_router,
    auth_router,
    auth_v2_router,
    clouds_files_router,
    clouds_router,
    file_tags_router,
    files_router,
    magic_router,
    search_router,
    tags_router,
)
from lcloud.auth.jwt_utils import ensure_jwt_secret
from lcloud.config import get_settings
from lcloud.crypto.keys import ensure_admin_keypair
from lcloud.db.base import dispose_engine, get_sessionmaker, init_engine
from lcloud.db.bootstrap import ensure_admin_owner, run_migrations
from lcloud.db.models import Owner
from lcloud.userbot.admin_bootstrap import ensure_admin_seed_delivered
from lcloud.userbot.client import get_userbot_manager
from lcloud.userbot.commands import (
    CommandContext,
    register_saved_messages_handlers,
)
from lcloud.userbot.handlers import IngestContext, register_userbot_handlers
from lcloud.userbot.inchat import InChatContext, register_in_chat_handlers
from lcloud.userbot.scan import schedule_scan
from lcloud.workers import (
    init_mtproto_limiter,
    init_worker_pool,
    reset_mtproto_limiter,
    reset_worker_pool,
)

logger = logging.getLogger("lcloud")


async def _post_login_scan_if_authorized() -> None:
    """If the userbot is admin-authorized, attach handlers + kick off scan."""
    manager = get_userbot_manager()
    if not manager.is_started:
        return
    if not await manager.is_admin_authorized():
        return
    settings = get_settings()
    sk, vk = ensure_admin_keypair(settings)
    sm = get_sessionmaker()
    async with sm() as sess:
        result = await sess.execute(
            sa.select(Owner).where(Owner.role == "admin").limit(1)
        )
        owner = result.scalar_one_or_none()
    if owner is None:
        logger.warning("admin owner row missing; skipping post-login bootstrap")
        return

    # Attach NewMessage ingestion handler (idempotent: Telethon allows re-add
    # but our use-case calls this once per admin-auth + once per app start).
    ctx = IngestContext(
        sessionmaker=sm,
        signing_key=sk,
        settings=settings,
        owner_id=owner.id,
    )
    register_userbot_handlers(manager.client, ctx)

    # Attach Saved-Messages command handler (/help /status /revoke /clouds
    # /createcloud /connect /disconnect)
    cmd_ctx = CommandContext(
        sessionmaker=sm,
        owner_id=owner.id,
        signing_key=sk,
        settings=settings,
    )
    register_saved_messages_handlers(manager.client, cmd_ctx)

    # Attach in-chat /lc_connect /lc_disconnect handler
    inchat_ctx = InChatContext(
        sessionmaker=sm, owner_id=owner.id, signing_key=sk
    )
    register_in_chat_handlers(manager.client, inchat_ctx)

    # Background dialog scan
    schedule_scan(
        client=manager.client,
        sessionmaker=sm,
        owner_id=owner.id,
        expected_pubkey=bytes(vk),
    )

    # V2: ensure admin user row + BIP39 seed delivered to Saved Messages
    try:
        await ensure_admin_seed_delivered(
            client=manager.client,
            sessionmaker=sm,
            public_base_url=settings.lc_public_base_url,
        )
    except Exception:
        logger.exception("admin seed bootstrap failed (non-fatal)")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    settings.ensure_runtime_dirs()
    logger.info("LCloud starting (version %s)", __version__)

    # 1. Keypair (Ed25519 admin keys)
    sk, vk = ensure_admin_keypair(settings)
    logger.info("admin keypair ready")

    # 2. JWT HS256 secret
    ensure_jwt_secret(settings)

    # 3. DB
    init_engine(settings)
    await run_migrations(settings)
    owner_id = await ensure_admin_owner(bytes(vk))
    logger.info("admin owner row id=%s", owner_id)

    # 4. Worker pool + MTProto rate limiter (P4 infra; used from P5 onward)
    init_worker_pool(settings)
    init_mtproto_limiter(settings)

    # 5. Telethon (degraded if creds unset)
    manager = get_userbot_manager()
    await manager.start()

    # 6. Background scan if already authorized
    await _post_login_scan_if_authorized()

    try:
        yield
    finally:
        await manager.stop()
        await dispose_engine()
        reset_worker_pool()
        reset_mtproto_limiter()
        logger.info("LCloud shutting down")


def create_app() -> FastAPI:
    app = FastAPI(
        title="LCloud",
        version=__version__,
        lifespan=lifespan,
    )
    app.include_router(auth_router)
    app.include_router(auth_v2_router)
    app.include_router(api_keys_router)
    app.include_router(clouds_router)
    app.include_router(clouds_files_router)
    app.include_router(files_router)
    app.include_router(tags_router)
    app.include_router(file_tags_router)
    app.include_router(search_router)
    # Magic-link endpoint must be registered BEFORE the SPA fallback so that
    # `GET /admin?token=…` doesn't get caught by the catch-all `/{full_path}`.
    app.include_router(magic_router)

    @app.get("/health")
    async def health() -> JSONResponse:
        return JSONResponse({"status": "ok", "version": __version__})

    # Mount the built React SPA, if present. In production the frontend is
    # built into `web/dist/`; in dev, the Vite server runs separately on
    # 8788 and proxies API calls to us.
    settings = get_settings()
    dist_dir = settings.project_root / "web" / "dist"
    assets_dir = dist_dir / "assets"
    index_file = dist_dir / "index.html"

    if assets_dir.is_dir():
        app.mount(
            "/assets",
            StaticFiles(directory=str(assets_dir)),
            name="spa-assets",
        )

    if index_file.is_file():
        # Serve index for the SPA root and any unknown GET path so client-side
        # routing works on hard-refresh. API paths are matched by their routers
        # first (Starlette tries routes in registration order).
        @app.get("/", include_in_schema=False)
        async def spa_root() -> FileResponse:
            return FileResponse(index_file)

        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa_fallback(full_path: str) -> FileResponse:
            # Anything inside our known API prefixes that didn't match a real
            # route should 404, not silently return the SPA index.
            for prefix in (
                "auth/",
                "admin",
                "clouds",
                "files/",
                "tags",
                "search",
                "health",
                "openapi.json",
                "docs",
                "redoc",
                "assets/",
            ):
                if full_path.startswith(prefix):
                    raise HTTPException(404)
            return FileResponse(index_file)
    else:
        # Frontend not built — keep a JSON info root so the deployment is
        # still inspectable via curl.
        @app.get("/", include_in_schema=False)
        async def server_info() -> JSONResponse:
            return JSONResponse(
                {
                    "name": "LCloud",
                    "version": __version__,
                    "frontend": "not_built",
                    "docs": "/docs",
                    "health": "/health",
                }
            )

    return app


app = create_app()


def run() -> None:
    """Console entrypoint: `lcloud` runs the API server."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
    )
    settings = get_settings()
    uvicorn.run(
        "lcloud.main:app",
        host=settings.lc_host,
        port=settings.lc_port,
        reload=False,
        log_level="info",
        # Trust X-Forwarded-* from the docker bridge (shop-nginx upstream).
        # Listening on 0.0.0.0 in production is safe because ufw blocks
        # public access to LC_PORT; only 172.18.0.0/16 is allowed in.
        proxy_headers=True,
        forwarded_allow_ips="127.0.0.1,172.18.0.0/16",
    )


if __name__ == "__main__":
    run()
