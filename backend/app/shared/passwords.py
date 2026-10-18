"""Password hashing for ``UserAccount.password_hash`` (standard library scrypt).

Format: ``scrypt$<n>$<r>$<p>$<salt b64>$<hash b64>``. Plaintext passwords are
never stored (architecture §5.3, [T1]).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

_N, _R, _P, _LENGTH = 2**14, 8, 1, 32


def hash_password(password: str) -> str:
    if not password:
        raise ValueError("password must not be empty")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=_LENGTH)
    return "$".join(["scrypt", str(_N), str(_R), str(_P), _b64(salt), _b64(digest)])


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        expected = base64.b64decode(digest)
        actual = hashlib.scrypt(
            password.encode(),
            salt=base64.b64decode(salt),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")
