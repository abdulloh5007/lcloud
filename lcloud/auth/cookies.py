"""Session cookie helpers."""

from __future__ import annotations

from fastapi import Response

from lcloud.config import get_settings

COOKIE_NAME = "lc_session"


def set_session_cookie(response: Response, token: str) -> None:
    s = get_settings()
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=s.lc_session_ttl_seconds,
        httponly=True,
        secure=s.lc_cookie_secure,
        samesite="strict",
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(key=COOKIE_NAME, path="/")
