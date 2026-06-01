"""Single-user password auth via a signed session cookie."""

from __future__ import annotations

from fastapi import HTTPException, Request


def current_user(request: Request) -> str | None:
    return request.session.get("user")


def require_login(request: Request) -> None:
    """Dependency for protected routes: redirect to /login when unauthenticated."""
    if current_user(request):
        return
    if request.headers.get("HX-Request"):
        raise HTTPException(status_code=401, headers={"HX-Redirect": "/login"})
    raise HTTPException(status_code=307, headers={"Location": "/login"})
