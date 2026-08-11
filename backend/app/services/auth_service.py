"""The auth use case — register, login, refresh, logout, whoami (WS-1,
frontend_plan.md §3).

In-memory access JWT + httpOnly rotating refresh cookie, first-user-registers-
then-closed signup. This class holds every decision in §3 that is not HTTP
shape (that's `app.api.routes.auth`) and not a vendor SDK call (that's
`app.infrastructure.auth`):

- whether registration is still open (`UserRepository.has_users()`),
- that a login's outcome must not be distinguishable, in content or in
  timing, between "no such user" and "wrong password",
- that a refresh or access token is only good until the user's
  `token_version` moves out from under it (`POST /auth/logout`'s entire
  effect).

Hashing and token functions arrive through the constructor as plain
callables rather than this module importing `app.infrastructure.auth` at
module scope — `tests/test_architecture.py`'s
`test_services_reach_infrastructure_only_from_composition_roots` is what
that buys: the concretions are chosen once, inside `build_default_auth_service()`,
which is the composition root for this service exactly as
`build_default_ingestion_service()` is for `IngestionService`.
"""

from __future__ import annotations

from typing import Callable

from app.core.exceptions import InvalidCredentials, InvalidToken, RegistrationClosed
from app.domain.models import User
from app.domain.ports import UserRepository


class AuthService:
    """Register, authenticate, and revoke — the whole of §3's backend."""

    def __init__(
        self,
        users: UserRepository,
        hash_password: Callable[[str], str],
        verify_password: Callable[[str, str], bool],
        dummy_password_hash: Callable[[], str],
        create_access_token: Callable[[int, int], tuple[str, int]],
        create_refresh_token: Callable[[int, int], str],
        decode_token: Callable[..., dict],
    ):
        self._users = users
        self._hash_password = hash_password
        self._verify_password = verify_password
        self._dummy_password_hash = dummy_password_hash
        self._create_access_token = create_access_token
        self._create_refresh_token = create_refresh_token
        self._decode_token = decode_token

    def register(self, username: str, password: str) -> User:
        """`POST /auth/register`: only while the `users` table is empty.

        The `has_users()` check-then-`create()` is not perfectly race-free
        against two concurrent *first* registrations; `SqlUserRepository`'s
        `username` uniqueness constraint is the backstop for that case, and
        raises the same `RegistrationClosed` on the loser, so this method's
        contract ("succeeds once, then 403 forever after") holds either way.
        """
        if self._users.has_users():
            raise RegistrationClosed()

        password_hash = self._hash_password(password)
        return self._users.create(username=username, password_hash=password_hash)

    def login(self, username: str, password: str) -> tuple[str, int, str]:
        """`POST /auth/login`: returns `(access_token, expires_in, refresh_token)`.

        Deliberately does the same amount of work — one bcrypt comparison,
        one `InvalidCredentials` — whether `username` exists or not. Without
        the dummy-hash branch, an unknown username would return in
        microseconds while a wrong password spends bcrypt's full cost, and
        that timing gap is enough to enumerate valid usernames with no
        access to the database at all.
        """
        user = self._users.get_by_username(username)

        if user is None:
            self._verify_password(password, self._dummy_password_hash())
            raise InvalidCredentials()

        if not self._verify_password(password, user.password_hash):
            raise InvalidCredentials()

        access_token, expires_in = self._create_access_token(user.id, user.token_version)
        refresh_token = self._create_refresh_token(user.id, user.token_version)
        return access_token, expires_in, refresh_token

    def refresh(self, refresh_token: str | None) -> tuple[str, int, str]:
        """`POST /auth/refresh`: rotate the cookie, mint a fresh access token.

        Takes the cookie value as `str | None` rather than requiring the
        route to check for a missing cookie first — `app.api.routes.auth`'s
        body stays a single call into this method either way, matching
        `app.api`'s "route bodies call exactly one service method" rule.

        `InvalidToken` covers a missing cookie, a malformed or expired JWT
        (`decode_token`), a `typ` other than `"refresh"` (rejects an access
        token presented here — the reverse of what `authenticate()` guards),
        and a `ver` claim that no longer matches — the last one is what makes
        `POST /auth/logout` revoke a refresh token immediately rather than
        only once its own `exp` eventually arrives.
        """
        if refresh_token is None:
            raise InvalidToken("No refresh token was provided.")

        payload = self._decode_token(refresh_token, expected_type="refresh")
        user = self._users.get_by_id(int(payload["sub"]))
        if user is None or user.token_version != payload.get("ver"):
            raise InvalidToken("Refresh token has been revoked or its user no longer exists.")

        access_token, expires_in = self._create_access_token(user.id, user.token_version)
        rotated_refresh_token = self._create_refresh_token(user.id, user.token_version)
        return access_token, expires_in, rotated_refresh_token

    def logout(self, user_id: int) -> None:
        """`POST /auth/logout`: revoke every outstanding token for this user.

        One increment, no token/session table to clean up — `authenticate()`
        and `refresh()` both compare a token's `ver` claim against the value
        this call just moved past, so every token issued before it (access
        *and* refresh, still-live or not) stops verifying on its very next use.
        """
        self._users.increment_token_version(user_id)

    def authenticate(self, access_token: str) -> User:
        """`GET /auth/me` and `get_current_user` (`app.api.dependencies`):
        turn a bearer token into the `User` who holds it.

        Named distinctly from the FastAPI dependency of the same concept
        (`get_current_user`) so neither reads as calling the other. Rejects
        a `typ="refresh"` token the same way `refresh()` rejects a
        `typ="access"` one — the §3 guard is symmetric on purpose.
        """
        payload = self._decode_token(access_token, expected_type="access")
        user = self._users.get_by_id(int(payload["sub"]))
        if user is None or user.token_version != payload.get("ver"):
            raise InvalidToken("Access token has been revoked or its user no longer exists.")
        return user


def build_default_auth_service() -> AuthService:
    """The production object graph, in one place. See `IngestionService`'s twin."""
    from app.infrastructure.auth.hashing import dummy_password_hash, hash_password, verify_password
    from app.infrastructure.auth.tokens import create_access_token, create_refresh_token, decode_token
    from app.infrastructure.db.user_repository import SqlUserRepository

    return AuthService(
        users=SqlUserRepository(),
        hash_password=hash_password,
        verify_password=verify_password,
        dummy_password_hash=dummy_password_hash,
        create_access_token=create_access_token,
        create_refresh_token=create_refresh_token,
        decode_token=decode_token,
    )
