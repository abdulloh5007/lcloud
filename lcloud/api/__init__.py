"""API package: routers."""

from lcloud.api.api_keys import router as api_keys_router
from lcloud.api.auth import router as auth_router
from lcloud.api.auth_v2 import router as auth_v2_router
from lcloud.api.clouds import router as clouds_router
from lcloud.api.files import (
    clouds_files_router,
    files_router,
)
from lcloud.api.json_db import router as json_db_router
from lcloud.api.magic import router as magic_router
from lcloud.api.payments import admin_router as payments_admin_router
from lcloud.api.payments import public_router as payments_public_router
from lcloud.api.pin_recovery import router as pin_recovery_router
from lcloud.api.search import router as search_router
from lcloud.api.shares import (
    public_share_router,
    shares_router,
)
from lcloud.api.tags import file_tags_router, tags_router
from lcloud.api.v2_clouds import router as v2_clouds_router
from lcloud.api.v2_files import (
    clouds_files_router as v2_clouds_files_router,
)
from lcloud.api.v2_files import (
    files_router as v2_files_router,
)
from lcloud.api.versions import router as versions_router

__all__ = [
    "api_keys_router",
    "auth_router",
    "auth_v2_router",
    "clouds_files_router",
    "clouds_router",
    "file_tags_router",
    "files_router",
    "json_db_router",
    "magic_router",
    "payments_admin_router",
    "payments_public_router",
    "pin_recovery_router",
    "public_share_router",
    "search_router",
    "shares_router",
    "tags_router",
    "v2_clouds_files_router",
    "v2_clouds_router",
    "v2_files_router",
    "versions_router",
]
