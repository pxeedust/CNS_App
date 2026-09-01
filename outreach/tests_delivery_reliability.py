from datetime import timedelta
from email.message import EmailMessage
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import CampaignRun, Client, OutboundEmail, OutreachCampaign, TeamMember
from .tasks import (
    EmailGenerationResult,
    _process_imap_message,
    send_automated_pings,
)


@override_settings(
    MAILBOX_ENCRYPTION_KEY="delivery-test-key",
    CELERY_TASK_ALWAYS_EAGER=True,
)
class InitialCampaignReliabilityTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="sender", password="strong-test-pass", email="sender@example.test"
        )
        TeamMember.objects.create(
            user=self.user,
            sender_name="Test Sender",
            sender_role="Outreach Lead",
            mailbox_email="sender@example.test",
            mailbox_app_password="mailbox-secret",
        )
        self.client_record = Client.objects.create(
            company_name="Personalized Co",
            contact_person="Alex Contact",
            email="alex@personalized.test",
            industry="Consulting",
            assigned_to=self.user,
        )

    @override_settings(GEMINI_API_KEY="")
    def test_campaign_launch_page_renders_bound_form_and_safety_state(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("outreach:run_campaign"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="confirm_send"')
        self.assertContains(response, "Gemini is not configured")

    @patch("outreach.tasks._send_single_email")
    @patch("outreach.tasks._generate_initial_email_result")
    def test_personalized_content_and_provenance_are_persisted(
        self, mock_generate, mock_send
    ):
        mock_generate.return_value = EmailGenerationResult(
            "180DC IIT Kharagpur X Personalized Co - operations",
            "Hello Alex,\n\nA company-specific observation.",
            OutboundEmail.GenerationMode.AI,
        )
        mock_send.return_value = (True, "Sent")
        run = CampaignRun.objects.create(triggered_by=self.user)

        result = send_automated_pings(
            triggered_by_user_id=self.user.pk, run_id=run.pk
        )

        self.assertEqual(result["sent"], 1)
        outbound = OutboundEmail.objects.get(client=self.client_record)
        self.assertEqual(outbound.generation_mode, OutboundEmail.GenerationMode.AI)
        self.assertIn("Hello Alex", outbound.body)
        self.assertEqual(outbound.status, OutboundEmail.DeliveryStatus.SENT)
        self.assertEqual(
            mock_send.call_args.kwargs["message_id"], outbound.message_id
        )
        self.client_record.refresh_from_db()
        self.assertEqual(self.client_record.status, Client.Status.PINGED)

    @patch("outreach.tasks._send_single_email")
    @patch("outreach.tasks._generate_initial_email_result")
    def test_generation_failure_is_audited_and_never_sent(
        self, mock_generate, mock_send
    ):
        mock_generate.return_value = EmailGenerationResult(
            "", "", "failed", "Gemini quota exhausted"
        )

        result = send_automated_pings(triggered_by_user_id=self.user.pk)

        self.assertEqual(result["failed"], 1)
        mock_send.assert_not_called()
        outbound = OutboundEmail.objects.get(client=self.client_record)
        self.assertEqual(outbound.generation_mode, OutboundEmail.GenerationMode.FAILED)
        self.assertIn("quota", outbound.generation_error)
        self.client_record.refresh_from_db()
        self.assertEqual(self.client_record.status, Client.Status.NOT_CONTACTED)

    @patch("outreach.tasks._send_single_email", return_value=(True, "Sent"))
    @patch("outreach.tasks._generate_initial_email_result")
    def test_initial_outreach_is_idempotent(self, mock_generate, mock_send):
        mock_generate.return_value = EmailGenerationResult(
            "Subject", "Hello Alex, personalized body", OutboundEmail.GenerationMode.AI
        )
        send_automated_pings(triggered_by_user_id=self.user.pk)
        self.client_record.refresh_from_db()
        self.client_record.status = Client.Status.NOT_CONTACTED
        self.client_record.save(update_fields=["status", "updated_at"])

        second = send_automated_pings(triggered_by_user_id=self.user.pk)

        self.assertEqual(mock_send.call_count, 1)
        self.assertEqual(OutboundEmail.objects.count(), 1)
        self.assertEqual(second["sent"], 0)
        self.client_record.refresh_from_db()
        self.assertEqual(self.client_record.status, Client.Status.PINGED)

    @patch("outreach.tasks._send_single_email", return_value=(True, "Sent"))
    @patch("outreach.tasks._generate_initial_email_result")
    def test_campaign_target_industry_is_enforced(self, mock_generate, mock_send):
        mock_generate.return_value = EmailGenerationResult(
            "Subject", "Body", OutboundEmail.GenerationMode.AI
        )
        campaign = OutreachCampaign.objects.create(
            name="Only SaaS",
            target_industry="SaaS",
            subject_template="Hello {{company}}",
            email_template="Hello {{first_name}}, {{email_body}}",
        )

        result = send_automated_pings(
            campaign_id=campaign.pk, triggered_by_user_id=self.user.pk
        )

        self.assertEqual(result["sent"], 0)
        mock_generate.assert_not_called()
        mock_send.assert_not_called()


@override_settings(MAILBOX_ENCRYPTION_KEY="delivery-test-key")
class CredentialAndAuthorizationTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username="owner", password="strong-test-pass"
        )
        self.other = User.objects.create_user(
            username="other", password="strong-test-pass"
        )
        self.profile = TeamMember.objects.create(
            user=self.owner,
            mailbox_email="owner@example.test",
            mailbox_app_password="keep-this-secret",
        )
        TeamMember.objects.create(user=self.other)

    def test_blank_mailbox_password_keeps_existing_secret(self):
        self.client.force_login(self.owner)
        response = self.client.post(
            reverse("outreach:mailbox_settings"),
            {
                "sender_name": "Owner",
                "sender_role": "Lead",
                "mailbox_email": "owner@example.test",
                "mailbox_app_password": "",
            },
        )
        self.assertRedirects(response, reverse("outreach:mailbox_settings"))
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.mailbox_app_password, "keep-this-secret")

    def test_member_cannot_read_another_members_campaign_progress(self):
        run = CampaignRun.objects.create(triggered_by=self.owner)
        self.client.force_login(self.other)
        response = self.client.get(
            reverse("outreach:campaign_progress_api", args=[run.pk])
        )
        self.assertEqual(response.status_code, 404)

    def test_application_admin_can_create_campaign_template(self):
        self.profile.role = TeamMember.Role.ADMIN
        self.profile.save(update_fields=["role"])
        self.client.force_login(self.owner)
        response = self.client.post(
            reverse("outreach:campaign_add"),
            {
                "name": "Personalized consulting",
                "target_industry": "All",
                "subject_template": "180DC X {{company}}",
                "email_template": "Hello {{first_name}},\n\n{{email_body}}",
            },
        )
        self.assertRedirects(response, reverse("outreach:campaign_list"))
        campaign = OutreachCampaign.objects.get(name="Personalized consulting")
        self.assertIn("{{email_body}}", campaign.email_template)


@override_settings(MAILBOX_ENCRYPTION_KEY="delivery-test-key")
class ReplyThreadMatchingTests(TestCase):
    def test_in_reply_to_links_reply_to_exact_outbound_message(self):
        user = User.objects.create_user(username="reply-owner")
        client = Client.objects.create(
            company_name="Thread Co",
            contact_person="Riley Contact",
            email="riley@thread.test",
            industry="Consulting",
            assigned_to=user,
            status=Client.Status.PINGED,
            last_contacted_at=timezone.now() - timedelta(days=1),
        )
        outbound = OutboundEmail.objects.create(
            client=client,
            team_member=user,
            recipient=client.email,
            subject="Original",
            body="Body",
            message_id="<outbound-1@thread.test>",
            generation_mode=OutboundEmail.GenerationMode.AI,
            status=OutboundEmail.DeliveryStatus.SENT,
            sent_at=timezone.now() - timedelta(hours=2),
            idempotency_key="thread-test",
        )
        message = EmailMessage()
        message["Message-ID"] = "<reply-1@thread.test>"
        message["In-Reply-To"] = outbound.message_id
        message["Date"] = timezone.now().strftime("%a, %d %b %Y %H:%M:%S %z")
        message["Subject"] = "Re: Original"
        message.set_content("Interested in a call.")

        parsed = _process_imap_message(message, client, [])

        self.assertEqual(parsed["client"], client)
        self.assertEqual(parsed["outbound_email"], outbound)
