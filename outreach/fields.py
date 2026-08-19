from django.db import models

from .crypto import decrypt_secret, encrypt_secret


class EncryptedCharField(models.CharField):
    """
    A CharField that's encrypted at rest via Fernet (see crypto.py).
    Transparent to callers — reading/writing the field on a model instance
    works with plain strings, same as a regular CharField.
    """

    def get_prep_value(self, value):
        value = super().get_prep_value(value)
        if value is None:
            return value
        return encrypt_secret(value)

    def from_db_value(self, value, expression, connection):
        if value is None:
            return value
        return decrypt_secret(value)
