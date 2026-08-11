"""§3's auth routes and service (WS-1, frontend_plan.md §3): register, login,
refresh, logout, revocation, me, token type guards.

Tests run fully offline: `UserRepository` and auth functions are real (they are
pure domain logic), but the database is a stub. All tests substitute via
`app.dependency_overrides`, not monkeypatching. No LLM API calls are made.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import jwt
import pytest
from fastapi.testclient import TestClient

from app import main
from app.api.dependencies import get_auth_service, get_current_user, get_health_service
from app.core.config import settings
from app.core.exceptions import (
    InvalidCredentials,
    InvalidToken,
    RegistrationClosed,
)
from app.domain.models import User
from app.domain.ports import UserRepository
from app.infrastructure.auth.hashing import hash_password, verify_password
from app.infrastructure.auth.tokens import (
    create_access_token,
    create_refresh_token,
    decode_token,
)
from app.services.auth_service import AuthService
from app.services.health_service import HealthService

client = TestClient(main.app)


# --- Substituting the service layer ------------------------------------------


class _StubUserRepository:
    """In-memory user store for testing."""

    def __init__(self):
        self.users: dict[int, User] = {}
        self.next_id = 1

    def has_users(self) -> bool:
        return len(self.users) > 0

    def create(self, username: str, password_hash: str) -> User:
        if self.has_users():
            raise RegistrationClosed()
        user = User(
            id=self.next_id,
            username=username,
            password_hash=password_hash,
            token_version=0,
            created_at=datetime.now(timezone.utc),
        )
        self.users[user.id] = user
        self.next_id += 1
        return user

    def get_by_username(self, username: str) -> User | None:
        for user in self.users.values():
            if user.username == username:
                return user
        return None

    def get_by_id(self, user_id: int) -> User | None:
        return self.users.get(user_id)

    def increment_token_version(self, user_id: int) -> None:
        user = self.users.get(user_id)
        if user:
            # Create new user with incremented version
            new_user = User(
                id=user.id,
                username=user.username,
                password_hash=user.password_hash,
                token_version=user.token_version + 1,
                created_at=user.created_at,
            )
            self.users[user_id] = new_user


@pytest.fixture
def user_repo():
    """Inject the stub user repository."""
    repo = _StubUserRepository()
    main.app.dependency_overrides[
        lambda: UserRepository  # type: ignore
    ] = lambda: repo
    yield repo


@pytest.fixture
def auth_service(user_repo):
    """Build a real `AuthService` with the stub repository."""
    service = AuthService(
        users=user_repo,
        hash_password=hash_password,
        verify_password=verify_password,
        dummy_password_hash=lambda: hash_password(
            "knowrag-timing-safety-dummy-password"
        ),
        create_access_token=create_access_token,
        create_refresh_token=create_refresh_token,
        decode_token=decode_token,
    )
    main.app.dependency_overrides[get_auth_service] = lambda: service
    yield service


@pytest.fixture(autouse=True)
def _reset_overrides():
    yield
    main.app.dependency_overrides.clear()


@pytest.fixture
def authenticated_user(user_repo, auth_service):
    """Create a test user and return it with valid tokens."""
    user = auth_service.register("testuser", "password123")
    access_token, expires_in = create_access_token(user.id, user.token_version)
    refresh_token = create_refresh_token(user.id, user.token_version)
    return user, access_token, refresh_token


# --- Registration -------------------------------------------------------


def test_register_succeeds_on_empty_store(auth_service, user_repo):
    response = client.post(
        "/auth/register",
        json={"username": "alice", "password": "secret123"},
    )

    assert response.status_code == 201
    body = response.json()
    assert "id" in body
    assert body["username"] == "alice"
    assert "created_at" in body
    # Must not leak internal fields
    assert "password" not in body
    assert "password_hash" not in body
    assert "token_version" not in body


def test_register_second_attempt_is_forbidden(auth_service, user_repo):
    # First registration succeeds
    client.post(
        "/auth/register",
        json={"username": "alice", "password": "secret123"},
    )

    # Second registration attempt fails
    response = client.post(
        "/auth/register",
        json={"username": "bob", "password": "secret456"},
    )

    assert response.status_code == 403
    assert "Registration is closed" in response.json()["detail"]


# --- Password policy (registration-only enforcement) ------------------


def test_register_with_password_too_short_1_char(auth_service):
    """Passwords shorter than 8 characters are rejected at registration
    with a 422 validation error."""
    response = client.post(
        "/auth/register",
        json={"username": "alice", "password": "a"},
    )

    assert response.status_code == 422


def test_register_with_password_too_short_7_chars(auth_service):
    """Boundary: password of length 7 (just below minimum) is rejected."""
    response = client.post(
        "/auth/register",
        json={"username": "alice", "password": "1234567"},
    )

    assert response.status_code == 422


def test_register_with_password_exactly_8_chars(auth_service):
    """Boundary: password of length 8 (at minimum) is accepted."""
    response = client.post(
        "/auth/register",
        json={"username": "alice", "password": "12345678"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["username"] == "alice"
    assert "id" in body


def test_login_with_short_password_from_pre_policy_user(user_repo, auth_service):
    """Login still works with pre-policy short passwords. This asymmetry is
    deliberate: enforcing the policy at login would reject pre-existing short
    passwords with a 422 instead of 401, and would leak policy to an
    unauthenticated endpoint. This test ensures a future "consistency fix"
    doesn't break that invariant.
    """
    # Inject a user with a short password directly, bypassing registration
    short_password = "short"
    user = User(
        id=1,
        username="legacy_user",
        password_hash=hash_password(short_password),
        token_version=0,
        created_at=datetime.now(timezone.utc),
    )
    user_repo.users[user.id] = user
    user_repo.next_id = 2

    # Logging in with the correct short password succeeds
    response = client.post(
        "/auth/login",
        json={"username": "legacy_user", "password": short_password},
    )

    assert response.status_code == 200
    body = response.json()
    assert "access_token" in body


def test_login_with_wrong_password_to_pre_policy_user_returns_401(user_repo, auth_service):
    """Wrong password on a pre-policy user must return 401, not 422.
    This ensures the policy is enforced only at registration, not at login.
    """
    # Inject a user with a short password directly
    short_password = "short"
    user = User(
        id=1,
        username="legacy_user",
        password_hash=hash_password(short_password),
        token_version=0,
        created_at=datetime.now(timezone.utc),
    )
    user_repo.users[user.id] = user
    user_repo.next_id = 2

    # Logging in with a wrong password returns 401, not 422
    response = client.post(
        "/auth/login",
        json={"username": "legacy_user", "password": "wrong_password"},
    )

    assert response.status_code == 401
    assert "Invalid username or password" in response.json()["detail"]


def test_openapi_exposes_password_minimum_length_constraint():
    """The OpenAPI schema must expose the MIN_PASSWORD_LENGTH constraint
    so the frontend can display it without hardcoding the rule."""
    response = client.get("/openapi.json")

    assert response.status_code == 200
    schema = response.json()

    # Navigate to RegisterRequest in components.schemas
    register_request = schema["components"]["schemas"]["RegisterRequest"]
    password_field = register_request["properties"]["password"]

    # Must have minLength constraint
    assert "minLength" in password_field
    assert password_field["minLength"] == 8

    # Must have the description with the policy message
    assert "description" in password_field
    assert "Minimum 8 characters" in password_field["description"]

    # Verify LoginRequest does NOT have this constraint (asymmetry)
    login_request = schema["components"]["schemas"]["LoginRequest"]
    login_password_field = login_request["properties"]["password"]
    # LoginRequest.password should not have minLength constraint (or be 1)
    assert login_password_field.get("minLength", 1) <= 1


# --- Login -------------------------------------------------------


def test_login_succeeds_with_valid_credentials(auth_service, authenticated_user):
    user, _, _ = authenticated_user
    response = client.post(
        "/auth/login",
        json={"username": "testuser", "password": "password123"},
    )

    assert response.status_code == 200
    body = response.json()
    assert "access_token" in body
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == settings.access_token_ttl_minutes * 60


def test_login_sets_refresh_cookie(auth_service, authenticated_user):
    response = client.post(
        "/auth/login",
        json={"username": "testuser", "password": "password123"},
    )

    assert response.status_code == 200
    # Check the Set-Cookie header
    cookies = response.cookies
    assert "knowrag_refresh" in cookies
    assert cookies["knowrag_refresh"]  # Has a value
    # Check cookie attributes
    cookie_header = response.headers.get("set-cookie", "")
    assert "HttpOnly" in cookie_header
    assert "Path=/auth" in cookie_header
    assert "SameSite=lax" in cookie_header
    # Max-Age should be ~7 days
    assert f"Max-Age={settings.refresh_token_ttl_days * 24 * 60 * 60}" in cookie_header


def test_login_wrong_password_returns_401(auth_service, authenticated_user):
    response = client.post(
        "/auth/login",
        json={"username": "testuser", "password": "wrongpassword"},
    )

    assert response.status_code == 401
    assert "Invalid username or password" in response.json()["detail"]


def test_login_unknown_username_returns_401(auth_service):
    response = client.post(
        "/auth/login",
        json={"username": "nonexistent", "password": "password123"},
    )

    assert response.status_code == 401
    assert "Invalid username or password" in response.json()["detail"]


def test_login_wrong_password_and_unknown_username_have_identical_response(
    auth_service, authenticated_user
):
    """§3's username-enumeration guard: both cases must return the exact same
    response body so an attacker cannot distinguish them."""
    wrong_password = client.post(
        "/auth/login",
        json={"username": "testuser", "password": "wrongpassword"},
    )

    unknown_user = client.post(
        "/auth/login",
        json={"username": "nonexistent", "password": "password123"},
    )

    # Both should be 401
    assert wrong_password.status_code == 401
    assert unknown_user.status_code == 401
    # Both should have identical response bodies
    assert wrong_password.json() == unknown_user.json()


# --- Token type guard --------------------------------------------------


def test_refresh_token_presented_as_bearer_token_is_rejected(
    auth_service, authenticated_user
):
    """A refresh token's `typ="refresh"` must not validate on a route expecting
    `typ="access"`."""
    user, _, refresh_token = authenticated_user

    # Try to use the refresh token as a bearer token
    response = client.post(
        "/query",
        json={"question": "test?"},
        headers={"Authorization": f"Bearer {refresh_token}"},
    )

    assert response.status_code == 401


def test_access_token_presented_as_refresh_token_is_rejected(
    auth_service, authenticated_user
):
    """An access token's `typ="access"` must not validate on `POST /auth/refresh`
    which expects `typ="refresh"`."""
    user, access_token, _ = authenticated_user

    # Try to use the access token in a refresh request
    response = client.post(
        "/auth/refresh",
        cookies={"knowrag_refresh": access_token},
    )

    assert response.status_code == 401


# --- Token validity ----------------------------------------------------


def test_expired_access_token_is_rejected(auth_service, authenticated_user):
    """An access token past its `exp` should not authenticate."""
    user, _, _ = authenticated_user

    # Create an already-expired token
    now = int(time.time())
    expired_payload = {
        "sub": str(user.id),
        "ver": user.token_version,
        "typ": "access",
        "iat": now - 3600,  # Issued 1 hour ago
        "exp": now - 1,  # Expired 1 second ago
    }
    expired_token = jwt.encode(
        expired_payload, settings.jwt_secret, algorithm=settings.jwt_algorithm
    )

    response = client.post(
        "/query",
        json={"question": "test?"},
        headers={"Authorization": f"Bearer {expired_token}"},
    )

    assert response.status_code == 401


def test_token_signed_with_wrong_secret_is_rejected(
    auth_service, authenticated_user
):
    """A token signed with a different secret should not verify."""
    user, _, _ = authenticated_user

    # Create a token with a wrong secret
    now = int(time.time())
    ttl_seconds = settings.access_token_ttl_minutes * 60
    payload = {
        "sub": str(user.id),
        "ver": user.token_version,
        "typ": "access",
        "iat": now,
        "exp": now + ttl_seconds,
    }
    wrong_secret_token = jwt.encode(
        payload, "wrong-secret-key", algorithm=settings.jwt_algorithm
    )

    response = client.post(
        "/query",
        json={"question": "test?"},
        headers={"Authorization": f"Bearer {wrong_secret_token}"},
    )

    assert response.status_code == 401


def test_malformed_token_is_rejected(auth_service, authenticated_user):
    """A structurally invalid JWT should not authenticate."""
    response = client.post(
        "/query",
        json={"question": "test?"},
        headers={"Authorization": "Bearer not.a.jwt"},
    )

    assert response.status_code == 401


# --- Refresh -----------------------------------------------------------


def test_refresh_with_valid_cookie_returns_new_tokens(auth_service, authenticated_user):
    user, _, refresh_token = authenticated_user

    response = client.post(
        "/auth/refresh",
        cookies={"knowrag_refresh": refresh_token},
    )

    assert response.status_code == 200
    body = response.json()
    assert "access_token" in body
    assert body["token_type"] == "bearer"
    # The new access token is different from any previous one
    assert body["access_token"] != refresh_token


def test_refresh_rotates_the_cookie(auth_service, authenticated_user):
    """A refresh response sets a new refresh cookie in the response."""
    user, _, old_refresh = authenticated_user

    response = client.post(
        "/auth/refresh",
        cookies={"knowrag_refresh": old_refresh},
    )

    assert response.status_code == 200
    # Verify that a new refresh cookie is set in the response
    # (a Set-Cookie header was generated for the client)
    cookies = response.cookies
    assert "knowrag_refresh" in cookies
    new_refresh = cookies["knowrag_refresh"]
    # The new refresh token value is present
    assert new_refresh is not None
    assert len(new_refresh) > 0
    # A new token can be used to refresh again
    response2 = client.post(
        "/auth/refresh",
        cookies={"knowrag_refresh": new_refresh},
    )
    assert response2.status_code == 200


def test_refresh_without_cookie_is_rejected(auth_service):
    response = client.post("/auth/refresh")

    assert response.status_code == 401


# --- Logout and revocation --------------------------------------------


def test_logout_returns_204_no_content(auth_service, authenticated_user):
    user, access_token, _ = authenticated_user

    response = client.post(
        "/auth/logout",
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert response.status_code == 204


def test_logout_clears_the_refresh_cookie(auth_service, authenticated_user):
    user, access_token, _ = authenticated_user

    response = client.post(
        "/auth/logout",
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert response.status_code == 204
    # The cookie should be cleared (set to empty with Max-Age=0)
    cookie_header = response.headers.get("set-cookie", "")
    assert "knowrag_refresh" in cookie_header
    assert "Max-Age=0" in cookie_header or "max-age=0" in cookie_header.lower()


def test_access_token_issued_before_logout_is_rejected_after_logout(
    auth_service, authenticated_user
):
    """§3's core revocation mechanism: `token_version` increment invalidates
    all previously-issued tokens for that user."""
    user, access_token, _ = authenticated_user

    # Confirm the token works before logout
    response_before = client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert response_before.status_code == 200

    # Logout
    client.post(
        "/auth/logout",
        headers={"Authorization": f"Bearer {access_token}"},
    )

    # Now try the same token after logout
    response_after = client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert response_after.status_code == 401


def test_refresh_token_issued_before_logout_is_rejected_after_logout(
    auth_service, authenticated_user
):
    """Refresh tokens must also be revoked when `token_version` increments."""
    user, _, old_refresh = authenticated_user

    # Logout with an access token
    response_before = client.post(
        "/auth/refresh",
        cookies={"knowrag_refresh": old_refresh},
    )
    assert response_before.status_code == 200

    # Logout again (now with the new refresh token from above)
    new_refresh = response_before.cookies["knowrag_refresh"]
    access_token_2 = response_before.json()["access_token"]
    client.post(
        "/auth/logout",
        headers={"Authorization": f"Bearer {access_token_2}"},
    )

    # Now try the old refresh token (from before this second refresh)
    response_after = client.post(
        "/auth/refresh",
        cookies={"knowrag_refresh": old_refresh},
    )
    assert response_after.status_code == 401


# --- Route protection matrix ------------------------------------------


@pytest.mark.parametrize(
    "method,path",
    [
        ("POST", "/query"),
        ("POST", "/ingest"),
        ("GET", "/documents"),
        ("GET", "/chunks?ids=1:0"),
    ],
)
def test_protected_routes_require_token(method, path):
    """These routes must return 401 without a token."""
    if method == "POST":
        response = client.post(path, json={"question": "test?"})
    elif method == "GET":
        response = client.get(path)

    assert response.status_code == 401
    assert response.headers.get("WWW-Authenticate") == "Bearer"


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/"),
        ("GET", "/metrics"),
        ("GET", "/health"),
    ],
)
def test_public_routes_do_not_require_token(method, path):
    """These routes must succeed without a token."""
    # Stub the health service since GET /health connects to datastores
    if path == "/health":
        main.app.dependency_overrides[get_health_service] = lambda: HealthService(
            {
                "postgres": lambda: (True, None),
                "qdrant": lambda: (True, None),
                "elasticsearch": lambda: (True, None),
            }
        )

    if method == "GET":
        response = client.get(path)

    assert response.status_code in (200, 204)

    main.app.dependency_overrides.clear()


def test_health_endpoint_does_not_require_token(user_repo):
    """GET /health must be accessible without authentication."""
    main.app.dependency_overrides[get_health_service] = lambda: HealthService(
        {
            "postgres": lambda: (True, None),
            "qdrant": lambda: (True, None),
            "elasticsearch": lambda: (True, None),
        }
    )

    response = client.get("/health")

    assert response.status_code == 200
    main.app.dependency_overrides.clear()


# --- GET /auth/me --------------------------------------------------


def test_me_returns_authenticated_user(auth_service, authenticated_user):
    user, access_token, _ = authenticated_user

    response = client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == user.id
    assert body["username"] == "testuser"
    assert "created_at" in body
    # Must not leak internal fields
    assert "password" not in body
    assert "password_hash" not in body
    assert "token_version" not in body


def test_me_without_token_is_rejected(auth_service):
    response = client.get("/auth/me")

    assert response.status_code == 401
