"""Auth layer: JWT, cookies, FastAPI dependencies."""

from lcloud.auth.cookies import COOKIE_NAME, clear_session_cookie, set_session_cookie
from lcloud.auth.deps import require_admin
from lcloud.auth.jwt_utils import (
    decode_admin_token,
    ensure_jwt_secret,
    issue_admin_token,
    issue_magic_token,
)

__all__ = [
    "COOKIE_NAME",
    "clear_session_cookie",
    "decode_admin_token",
    "ensure_jwt_secret",
    "issue_admin_token",
    "issue_magic_token",
    "require_admin",
    "set_session_cookie",
]
