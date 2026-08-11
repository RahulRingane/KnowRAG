"""Password hashing and JWT encode/decode — the two vendor SDKs auth needs.

    hashing.py  bcrypt.hashpw/checkpw, plus the timing-safety dummy hash
    tokens.py   PyJWT encode/decode, with the access/refresh `typ` guard

Both import a third-party SDK (`bcrypt`, `jwt`) directly, which is exactly
why they live in `app.infrastructure` and not in `app.services.auth_service`
— `tests/test_architecture.py`'s "no vendor SDK above infrastructure" rule
covers both (see its `VENDOR_SDKS` set).
"""

from app.infrastructure.auth.hashing import dummy_password_hash, hash_password, verify_password
from app.infrastructure.auth.tokens import create_access_token, create_refresh_token, decode_token

__all__ = [
    "create_access_token",
    "create_refresh_token",
    "decode_token",
    "dummy_password_hash",
    "hash_password",
    "verify_password",
]
