"""JWT encode/decode — `PyJWT` directly, not `python-jose` (less maintained).

Reads `settings.jwt_secret`/`jwt_algorithm`/`access_token_ttl_minutes`/
`refresh_token_ttl_days` straight from `app.core.config`, the same way
`app.infrastructure.db.session` reads `settings.database_url` — importing
the settings object is not an environment read (`app.core.config` already
did that; see `tests/test_architecture.py::test_only_config_reads_the_environment`),
so this module has no reason to take those values as parameters instead.

The one rule every function here exists to enforce: **`typ` is load-bearing**
(frontend_plan.md §3). An access token and a refresh token are both valid,
correctly-signed JWTs with a `sub` and a `ver` claim — nothing about their
*shape* distinguishes them. Without a `typ` claim checked on every decode, a
refresh token (7-day lifetime, meant to only ever be read back out of an
httpOnly cookie by `POST /auth/refresh`) would work anywhere an access token
is accepted, turning a long-lived credential into a bearer token an XSS
payload could steal and reuse directly against `/query`, `/ingest`, etc.
"""

from __future__ import annotations

import time
from typing import Literal

import jwt

from app.core.config import settings
from app.core.exceptions import InvalidToken

TokenType = Literal["access", "refresh"]


def create_access_token(user_id: int, token_version: int) -> tuple[str, int]:
    """Returns `(token, expires_in_seconds)` — the second element is exactly
    what `TokenResponse.expires_in` (`app.api.dto`) reports, computed once
    here rather than re-derived from `settings` a second time at the call
    site, which would risk the two drifting if the TTL setting ever changes.
    """
    ttl_seconds = settings.access_token_ttl_minutes * 60
    now = int(time.time())
    payload = {
        "sub": str(user_id),
        "ver": token_version,
        "typ": "access",
        "iat": now,
        "exp": now + ttl_seconds,
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return token, ttl_seconds


def create_refresh_token(user_id: int, token_version: int) -> str:
    """No `expires_in` returned: the refresh token never appears on the JSON
    body, only as the `knowrag_refresh` cookie's value (`app.api.routes.auth`
    sets its `max_age` from `settings.refresh_token_ttl_days` directly), so
    there is no caller that needs this token's lifetime as a return value.
    """
    ttl_seconds = settings.refresh_token_ttl_days * 24 * 60 * 60
    now = int(time.time())
    payload = {
        "sub": str(user_id),
        "ver": token_version,
        "typ": "refresh",
        "iat": now,
        "exp": now + ttl_seconds,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str, *, expected_type: TokenType) -> dict:
    """Verify signature and expiry, then enforce `typ == expected_type`.

    Every failure mode — bad signature, malformed token, expired `exp`, or a
    `typ` mismatch — raises the same `InvalidToken`. `app.services.auth_service`
    and `app.api.dependencies.get_current_user` both call this and neither
    needs to distinguish *why* a token failed to decide what to do next: any
    of them means "this token does not authenticate anyone", and
    `app.api.errors` maps `InvalidToken` to a single 401 either way.
    """
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError as exc:
        raise InvalidToken(f"Token could not be verified: {exc}") from exc

    if payload.get("typ") != expected_type:
        raise InvalidToken(
            f"Expected a {expected_type!r} token, got {payload.get('typ')!r}."
        )

    return payload
