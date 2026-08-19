"""
Field-level encryption for secrets stored in the DB (currently just
TeamMember.mailbox_app_password, previously stored as plaintext).

Key management is deliberately simple: a single Fernet key from the
ENCRYPTION_KEY env var (see settings.py). Fernet already handles
authenticated encryption (tamper detection) and key rotation isn't in scope
here — this closes "plaintext app passwords readable directly from the DB
or admin" without pulling in a full secrets-manager dependency.
"""

import logging

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings

logger = logging.getLogger(__name__)

_fernet = None


def _get_fernet():
    global _fernet
    if _fernet is None:
        key = getattr(settings, "ENCRYPTION_KEY", "")
        if not key:
            return None
        _fernet = Fernet(key.encode() if isinstance(key, str) else key)
    return _fernet


def encrypt_secret(plaintext: str) -> str:
    """Encrypt a string for storage. Returns the plaintext unchanged if no
    ENCRYPTION_KEY is configured (local dev only — settings.py enforces the
    key is set whenever DEBUG=False)."""
    if not plaintext:
        return plaintext
    fernet = _get_fernet()
    if fernet is None:
        return plaintext
    return fernet.encrypt(plaintext.encode()).decode()


def decrypt_secret(token: str) -> str:
    """
    Decrypt a stored value. Falls back to returning the value unchanged when
    it isn't a valid Fernet token — this is what makes the migration from
    plaintext to encrypted storage zero-downtime: an old plaintext row is
    just treated as already "decrypted" rather than raising, and gets
    upgraded to real ciphertext the next time it's saved.
    """
    if not token:
        return token
    fernet = _get_fernet()
    if fernet is None:
        return token
    try:
        return fernet.decrypt(token.encode()).decode()
    except (InvalidToken, ValueError):
        return token
