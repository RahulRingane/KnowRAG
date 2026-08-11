"""`UserRepository` over the `users` table (WS-1, frontend_plan.md §3).

Same shape as `SqlDocumentRepository` and `SqlChunkRepository`: each method
owns its own short session, and every return crosses the boundary as the
domain type `User`, never a detached `UserRow`.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import RegistrationClosed
from app.domain.models import User
from app.infrastructure.db.orm import UserRow
from app.infrastructure.db.session import SessionLocal


def _to_domain(row: UserRow) -> User:
    return User(
        id=row.id,
        username=row.username,
        password_hash=row.password_hash,
        token_version=row.token_version,
        created_at=row.created_at,
    )


class SqlUserRepository:
    """Postgres-backed `app.domain.ports.UserRepository`."""

    def __init__(self, session_factory: sessionmaker = SessionLocal):
        self._session_factory = session_factory

    def has_users(self) -> bool:
        """`SELECT 1 ... LIMIT 1` rather than a count — this only ever needs
        to know "any rows at all", and a full count is a table scan this
        query doesn't need to pay for."""
        db = self._session_factory()
        try:
            return db.query(UserRow.id).first() is not None
        finally:
            db.close()

    def get_by_username(self, username: str) -> User | None:
        db = self._session_factory()
        try:
            row = db.query(UserRow).filter(UserRow.username == username).first()
            return _to_domain(row) if row is not None else None
        finally:
            db.close()

    def get_by_id(self, user_id: int) -> User | None:
        db = self._session_factory()
        try:
            row = db.query(UserRow).filter(UserRow.id == user_id).first()
            return _to_domain(row) if row is not None else None
        finally:
            db.close()

    def create(self, username: str, password_hash: str) -> User:
        """Insert the row `AuthService.register()` builds after its
        `has_users()` gate passes.

        The gate is check-then-act and therefore not airtight against two
        concurrent *first* registrations — the `username` column's `UNIQUE`
        constraint is the backstop. An `IntegrityError` here means someone
        else's insert won the race (or, less likely, a duplicate username
        slipped through), and either way `RegistrationClosed` is the
        accurate caller-facing answer: an account exists now, so this
        request no longer gets to be the first.
        """
        db = self._session_factory()
        try:
            row = UserRow(
                username=username,
                password_hash=password_hash,
                token_version=0,
                created_at=datetime.now(timezone.utc),
            )
            db.add(row)
            db.commit()
            db.refresh(row)
            return _to_domain(row)
        except IntegrityError as exc:
            db.rollback()
            raise RegistrationClosed() from exc
        finally:
            db.close()

    def increment_token_version(self, user_id: int) -> None:
        """`POST /auth/logout`'s entire effect: bump `ver` so every token
        issued before this call fails the `ver` check in `AuthService`.

        A missing `user_id` is silently a no-op, matching
        `SqlDocumentRepository.set_status()`'s precedent for the same
        situation — the caller here is always `AuthService.logout()` acting
        on a user id it just authenticated, so this branch is defensive
        rather than reachable in practice.
        """
        db = self._session_factory()
        try:
            row = db.query(UserRow).filter(UserRow.id == user_id).first()
            if row is None:
                return
            row.token_version += 1
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
