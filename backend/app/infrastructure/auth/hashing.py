"""Password hashing — `bcrypt` directly, no wrapper library.

Deliberately not `passlib`: it is unmaintained, and its `CryptContext` raises
`AttributeError: module 'bcrypt' has no attribute '__about__'` against
bcrypt >= 4.1, which is what a fresh install of this project's dependencies
resolves to. `bcrypt.hashpw`/`bcrypt.checkpw` is a two-function API and a
wrapper buys nothing here.
"""

from __future__ import annotations

from functools import lru_cache

import bcrypt


def hash_password(password: str) -> str:
    """Hash `password` with a fresh random salt. Returns the bcrypt string
    (algorithm, cost, salt and hash all encoded together), safe to store as
    `UserRow.password_hash` and to hand back to `verify_password()` later —
    bcrypt reads its own salt back out of that string, so nothing else needs
    to be stored alongside it.
    """
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    """Check `password` against a hash `hash_password()` produced."""
    return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))


@lru_cache(maxsize=1)
def dummy_password_hash() -> str:
    """A bcrypt hash of a fixed, never-used password — computed once, lazily,
    and cached for the life of the process.

    Exists for `AuthService.login()`'s timing-safety requirement (§3): a
    login attempt for a username that does not exist must still spend
    bcrypt's comparison cost before returning `InvalidCredentials`, or an
    attacker can enumerate valid usernames purely from response latency
    (unknown-user replies fast, wrong-password replies slow). Hashing a
    fresh dummy value on every failed attempt would defeat the point —
    bcrypt's whole cost is deliberately expensive — so this is hashed once
    and every "unknown user" login reuses the same value as the thing it
    compares against.
    """
    return hash_password("knowrag-timing-safety-dummy-password")
