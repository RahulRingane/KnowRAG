"""§3's auth routes (WS-1, frontend_plan.md): register, login, refresh,
logout, me.

Same discipline as every other router in this package: each body calls
exactly one `AuthService` method and shapes the result. The one thing every
route here does beyond that is cookie handling — reading or writing
`knowrag_refresh` is HTTP shape, not business logic, the same way
`app.api.routes.ingest`'s upload handler builds a temp path before calling
the service; the decision of *whether* a cookie is valid still lives entirely
in `AuthService`/`app.infrastructure.auth`.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response, status

from app.api.dependencies import get_auth_service, get_current_user
from app.api.dto import LoginRequest, RegisterRequest, TokenResponse, UserResponse
from app.core.config import settings
from app.domain.models import User
from app.services.auth_service import AuthService

router = APIRouter(prefix="/auth", tags=["auth"])

# Name and `Path` are part of the contract WS-B (frontend) builds against —
# scoped to `/auth` so the browser never attaches this cookie to `/query`,
# `/ingest`, or any other request that has no business reading it.
REFRESH_COOKIE_NAME = "knowrag_refresh"
REFRESH_COOKIE_PATH = "/auth"


def _set_refresh_cookie(response: Response, refresh_token: str) -> None:
    """Shared by `/login` and `/refresh` — both mint a new refresh token and
    both need it to reach the client the same way. `secure`/`samesite` come
    from `Settings` (never hardcoded) so a production deploy can move to
    `Secure=true; SameSite=None` for a cross-domain frontend without a code
    change — see `app.core.config.Settings.refresh_cookie_secure`.
    """
    response.set_cookie(
        key=REFRESH_COOKIE_NAME,
        value=refresh_token,
        httponly=True,
        secure=settings.refresh_cookie_secure,
        samesite=settings.refresh_cookie_samesite,
        path=REFRESH_COOKIE_PATH,
        max_age=settings.refresh_token_ttl_days * 24 * 60 * 60,
    )


@router.post("/register", status_code=status.HTTP_201_CREATED, response_model=UserResponse)
def register(request: RegisterRequest, service: AuthService = Depends(get_auth_service)):
    """Create the first account. `AuthService.register()` raises
    `RegistrationClosed` once any user exists, which `app.api.errors` maps to
    403 — this body has no branch of its own.
    """
    return UserResponse.from_user(service.register(request.username, request.password))


@router.post("/login", response_model=TokenResponse)
def login(
    request: LoginRequest,
    response: Response,
    service: AuthService = Depends(get_auth_service),
):
    """Verify credentials, hand back an access token, and set the rotating
    refresh cookie. A wrong password and an unknown username both raise
    `InvalidCredentials` from inside `AuthService.login()` — indistinguishable
    on this side on purpose (§3: login must not leak which usernames exist).
    """
    access_token, expires_in, refresh_token = service.login(request.username, request.password)
    _set_refresh_cookie(response, refresh_token)
    return TokenResponse(access_token=access_token, expires_in=expires_in)


@router.post("/refresh", response_model=TokenResponse)
def refresh(
    request: Request,
    response: Response,
    service: AuthService = Depends(get_auth_service),
):
    """Rotate the refresh cookie and mint a fresh access token.

    Reading `request.cookies.get(...)` is the only HTTP-shape work this body
    does; a missing, expired, wrong-`typ`, or revoked cookie is
    `AuthService.refresh()`'s call to make; it raises `InvalidToken`
    (`app.core.exceptions`) either way, which `app.api.errors` maps to 401.
    """
    access_token, expires_in, rotated_refresh_token = service.refresh(
        request.cookies.get(REFRESH_COOKIE_NAME)
    )
    _set_refresh_cookie(response, rotated_refresh_token)
    return TokenResponse(access_token=access_token, expires_in=expires_in)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    response: Response,
    user: User = Depends(get_current_user),
    service: AuthService = Depends(get_auth_service),
):
    """Revoke every outstanding token for the caller (`token_version`
    increment) and clear the refresh cookie client-side. Requires a valid
    access token — an already-logged-out caller has nothing left to revoke.
    """
    service.logout(user.id)
    response.delete_cookie(REFRESH_COOKIE_NAME, path=REFRESH_COOKIE_PATH)


@router.get("/me", response_model=UserResponse)
def me(user: User = Depends(get_current_user)):
    """Who the bearer token belongs to. All the work — decode, `typ` check,
    revocation check — already happened in `get_current_user`; this body
    only shapes what comes back.
    """
    return UserResponse.from_user(user)
