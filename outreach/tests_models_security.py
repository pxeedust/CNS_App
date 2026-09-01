from django.contrib.auth.models import User
from django.db import connection
from django.test import TestCase, override_settings
from django.utils import timezone

from .models import ActionLog, Client, EmailReply, OutboundEmail, TeamMember


@override_settings(MAILBOX_ENCRYPTION_KEY="unit-test-mailbox-key")
class EncryptedMailboxFieldTests(TestCase):
    def test_mailbox_password_is_encrypted_in_database_and_decrypts_for_use(self):
        user = User.objects.create_user(username="cipher-user")
        profile = TeamMember.objects.create(
            user=user,
            mailbox_email="cipher@example.test",
            mailbox_app_password="plain-secret-value",
        )

        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT mailbox_app_password FROM outreach_teammember WHERE id = %s",
                [profile.pk],
            )
            stored_value = cursor.fetchone()[0]

        self.assertTrue(stored_value.startswith("enc::"))
        self.assertNotIn("plain-secret-value", stored_value)
        profile.refresh_from_db()
        self.assertEqual(profile.mailbox_app_password, "plain-secret-value")


class DurableEmailAuditTests(TestCase):
    def test_outbound_and_reply_audit_survive_client_deletion(self):
        user = User.objects.create_user(username="auditor")
        client = Client.objects.create(
            company_name="Audit Co",
            contact_person="Casey Contact",
            email="CASEY@EXAMPLE.TEST",
            industry="Consulting",
            assigned_to=user,
        )
        self.assertEqual(client.email, "casey@example.test")

        outbound = OutboundEmail.objects.create(
            client=client,
            team_member=user,
            recipient=client.email,
            subject="Audit subject",
            body="Audit body",
            message_id="<audit-1@example.test>",
            generation_mode=OutboundEmail.GenerationMode.AI,
            idempotency_key=f"test-initial:{client.pk}",
        )
        action = ActionLog.objects.create(
            team_member=user,
            client=client,
            outbound_email=outbound,
            notes="Sent",
        )
        reply = EmailReply.objects.create(
            client=client,
            outbound_email=outbound,
            subject="Re: Audit subject",
            body="Interested",
            received_at=timezone.now(),
            message_id="<audit-reply-1@example.test>",
        )

        client.delete()
        outbound.refresh_from_db()
        action.refresh_from_db()
        reply.refresh_from_db()

        self.assertIsNone(outbound.client)
        self.assertIsNone(action.client)
        self.assertIsNone(reply.client)
        self.assertEqual(outbound.client_company, "Audit Co")
        self.assertEqual(action.client_email, "casey@example.test")
        self.assertEqual(reply.client_company, "Audit Co")
