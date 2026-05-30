"""API package: routers."""

from lcloud.api.api_keys import router as api_keys_router
from lcloud.api.auth import router as auth_router
from lcloud.api.auth_v2 import router as auth_v2_router
from lcloud.api.clouds import router as clouds_router
from lcloud.api.files import (
    clouds_files_router,
    files_router,
)
from lcloud.api.magic import router as magic_router
from lcloud.api.search import router as search_router
from lcloud.api.tags import file_tags_router, tags_router

__all__ = [
    "api_keys_router",
    "auth_router",
    "auth_v2_router",
    "clouds_files_router",
    "clouds_router",
    "file_tags_router",
    "files_router",
    "magic_router",
    "search_router",
    "tags_router",
]
