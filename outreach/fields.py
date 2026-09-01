"""Custom model fields used by the outreach application."""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import models


_ENCRYPTED_PREFIX = "enc::"


def _mailbox_cipher() -> MultiFernet:
    """Build the mailbox cipher, including optional old keys for rotation."""
    primary = getattr(settings, "MAILBOX_ENCRYPTION_KEY", "")
    if not primary:
        raise ImproperlyConfigured("MAILBOX_ENCRYPTION_KEY must be configured.")

    old_keys = getattr(settings, "MAILBOX_ENCRYPTION_OLD_KEYS", [])
    secrets = [primary, *old_keys]
    ciphers = []
    for secret in secrets:
        digest = hashlib.sha256(str(secret).encode("utf-8")).digest()
        ciphers.append(Fernet(base64.urlsafe_b64encode(digest)))
    return MultiFernet(ciphers)


class EncryptedTextField(models.TextField):
    """Encrypt text before it reaches the database and decrypt it on reads.

    Legacy plaintext values remain readable so the accompanying data migration can
    transparently rewrite them. New ciphertext is prefixed to avoid double
    encryption and to make key/configuration mistakes fail loudly.
    """

    description = "Text encrypted with MAILBOX_ENCRYPTION_KEY"

    def from_db_value(self, value, expression, connection):
        return self.to_python(value)

    def to_python(self, value):
        if value is None or not isinstance(value, str):
            return value
        if not value.startswith(_ENCRYPTED_PREFIX):
            return value
        token = value[len(_ENCRYPTED_PREFIX) :]
        try:
            return _mailbox_cipher().decrypt(token.encode("ascii")).decode("utf-8")
        except (InvalidToken, ValueError) as exc:
            raise ImproperlyConfigured(
                "Could not decrypt a mailbox credential. Check "
                "MAILBOX_ENCRYPTION_KEY and MAILBOX_ENCRYPTION_OLD_KEYS."
            ) from exc

    def get_prep_value(self, value):
        value = super().get_prep_value(value)
        if not value or value.startswith(_ENCRYPTED_PREFIX):
            return value
        token = _mailbox_cipher().encrypt(value.encode("utf-8")).decode("ascii")
        return f"{_ENCRYPTED_PREFIX}{token}"
