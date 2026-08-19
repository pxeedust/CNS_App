from django.db import migrations


def encrypt_existing_passwords(apps, schema_editor):
    """
    Re-save every TeamMember through the now-encrypting field so existing
    plaintext app passwords become ciphertext immediately on migrate,
    instead of waiting for each row to be organically re-saved.

    Safe to run repeatedly / with ENCRYPTION_KEY unset: EncryptedCharField's
    encrypt/decrypt helpers are no-ops when no key is configured (local dev
    only — settings.py requires the key whenever DEBUG=False), and
    decrypting an already-plaintext value returns it unchanged rather than
    raising, so this never double-encrypts or corrupts data.
    """
    TeamMember = apps.get_model("outreach", "TeamMember")
    for team_member in TeamMember.objects.all():
        if team_member.mailbox_app_password:
            team_member.save(update_fields=["mailbox_app_password"])


class Migration(migrations.Migration):

    dependencies = [
        ("outreach", "0010_alter_teammember_mailbox_app_password"),
    ]

    operations = [
        migrations.RunPython(encrypt_existing_passwords, migrations.RunPython.noop),
    ]
