import smtplib
from unittest.mock import MagicMock, patch
from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase, TransactionTestCase
from django.test.utils import override_settings
from django.urls import reverse
from django.utils import timezone

from . import ai_client
from .models import (
    ActionLog,
    CampaignRun,
    Client,
    FollowupRun,
    LinkedInReachout,
    ScanRun,
    TeamMember,
)
from .tasks import (
    _claim_client,
    _generate_ai_email,
    _release_client,
    _send_single_email,
    scan_inbox_for_replies,
    send_automated_pings,
    send_followups,
)


class LinkedInReachoutFlowTests(TestCase):
    def setUp(self):
        self.member = User.objects.create_user(
            username="member1",
            password="testpass123",
            first_name="Team",
            last_name="Member",
        )
        TeamMember.objects.create(user=self.member, role=TeamMember.Role.MEMBER)
        self.client_record = Client.objects.create(
            company_name="Acme Corp",
            contact_person="Alice Founder",
            email="alice@acme.test",
            industry="Consulting",
            assigned_to=self.member,
        )

    def test_member_can_log_manual_linkedin_reachout(self):
        self.client.force_login(self.member)

        response = self.client.post(
            reverse("outreach:log_linkedin_reachout", args=[self.client_record.pk]),
            {
                "reachout_type": LinkedInReachout.ReachoutType.DIRECT_MESSAGE,
                "happened_at": "2026-03-15T11:30",
                "notes": "Sent initial intro over LinkedIn.",
            },
        )

        self.assertRedirects(
            response,
            reverse("outreach:log_linkedin_reachout", args=[self.client_record.pk]),
        )
        reachout = LinkedInReachout.objects.get()
        self.assertEqual(reachout.client, self.client_record)
        self.assertEqual(reachout.team_member, self.member)
        self.assertEqual(
            reachout.reachout_type, LinkedInReachout.ReachoutType.DIRECT_MESSAGE
        )
        self.assertEqual(reachout.notes, "Sent initial intro over LinkedIn.")


class LeaderboardLinkedInStatsTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="admin1",
            password="testpass123",
            first_name="Admin",
            last_name="User",
        )
        TeamMember.objects.create(user=self.admin, role=TeamMember.Role.ADMIN)

        self.member = User.objects.create_user(
            username="member1",
            password="testpass123",
            first_name="Member",
            last_name="User",
        )
        TeamMember.objects.create(user=self.member, role=TeamMember.Role.MEMBER)

        self.assigned_client = Client.objects.create(
            company_name="Beta Labs",
            contact_person="Bob Founder",
            email="bob@betalabs.test",
            industry="SaaS",
            assigned_to=self.member,
            status=Client.Status.PINGED,
            followup_count=2,
            has_replied=True,
        )
        self.shared_client = Client.objects.create(
            company_name="Shared Co",
            contact_person="Sam Shared",
            email="sam@shared.test",
            industry="NGO",
        )

        LinkedInReachout.objects.create(
            client=self.assigned_client,
            team_member=self.member,
            reachout_type=LinkedInReachout.ReachoutType.CONNECTION_REQUEST,
        )
        LinkedInReachout.objects.create(
            client=self.assigned_client,
            team_member=self.member,
            reachout_type=LinkedInReachout.ReachoutType.FOLLOW_UP,
        )
        LinkedInReachout.objects.create(
            client=self.shared_client,
            team_member=self.member,
            reachout_type=LinkedInReachout.ReachoutType.INMAIL,
        )

    def test_leaderboard_context_includes_linkedin_totals(self):
        self.client.force_login(self.admin)

        response = self.client.get(reverse("outreach:leaderboard"))

        self.assertEqual(response.status_code, 200)
        row = next(
            stat
            for stat in response.context["member_stats"]
            if stat["member"].user == self.member
        )
        self.assertEqual(row["contacted"], 1)
        self.assertEqual(row["followups_sent"], 2)
        self.assertEqual(row["linkedin_reachouts"], 3)
        self.assertEqual(row["linkedin_clients"], 2)
        self.assertEqual(row["outreach_actions"], 6)
        self.assertEqual(response.context["shared"]["linkedin_reachouts"], 1)


class DashboardCompanyFilterTests(TestCase):
    def setUp(self):
        self.member = User.objects.create_user(
            username="member2",
            password="testpass123",
            first_name="Filter",
            last_name="User",
        )
        TeamMember.objects.create(user=self.member, role=TeamMember.Role.MEMBER)
        self.acme_primary = Client.objects.create(
            company_name="Acme Corp",
            contact_person="Alice One",
            email="alice.one@acme.test",
            industry="Consulting",
            assigned_to=self.member,
        )
        self.acme_secondary = Client.objects.create(
            company_name="Acme Corp",
            contact_person="Alice Two",
            email="alice.two@acme.test",
            industry="Consulting",
            assigned_to=self.member,
        )
        self.other_client = Client.objects.create(
            company_name="Beta Labs",
            contact_person="Bob Other",
            email="bob@beta.test",
            industry="SaaS",
            assigned_to=self.member,
        )

    def test_dashboard_can_filter_clients_by_company_name(self):
        self.client.force_login(self.member)

        response = self.client.get(reverse("outreach:dashboard"), {"company": "Acme"})

        self.assertEqual(response.status_code, 200)
        companies = {client.company_name for client in response.context["clients"]}
        contacts = {client.contact_person for client in response.context["clients"]}
        self.assertEqual(companies, {"Acme Corp"})
        self.assertEqual(contacts, {"Alice One", "Alice Two"})
        self.assertEqual(response.context["company_filter"], "Acme")
        self.assertIn("Acme Corp", response.context["company_choices"])
        self.assertIn("Beta Labs", response.context["company_choices"])


class UserMailboxSettingsTests(TestCase):
    def setUp(self):
        self.member = User.objects.create_user(
            username="member_mailbox",
            password="testpass123",
            first_name="Mailbox",
            last_name="Owner",
            email="mailbox.owner@test.com",
        )
        TeamMember.objects.create(user=self.member, role=TeamMember.Role.MEMBER)

    def test_run_campaign_requires_mailbox_setup(self):
        self.client.force_login(self.member)

        response = self.client.post(reverse("outreach:run_campaign"), {})

        self.assertRedirects(response, reverse("outreach:mailbox_settings"))


class PersonalizedFollowupTests(TestCase):
    def setUp(self):
        self.member = User.objects.create_user(
            username="member_sender",
            password="testpass123",
            first_name="Asha",
            last_name="Rao",
            email="asha@test.com",
        )
        self.member_profile = TeamMember.objects.create(
            user=self.member,
            role=TeamMember.Role.MEMBER,
            sender_name="Asha Rao",
            sender_role="Outreach Lead",
            mailbox_email="asha.mail@test.com",
            mailbox_app_password="app-pass-123",
        )

        self.other_member = User.objects.create_user(
            username="member_other",
            password="testpass123",
            first_name="Parth",
            last_name="Other",
            email="parth@test.com",
        )
        TeamMember.objects.create(
            user=self.other_member,
            role=TeamMember.Role.MEMBER,
            sender_name="Parth Other",
            sender_role="Analyst",
            mailbox_email="parth.mail@test.com",
            mailbox_app_password="other-pass",
        )

        self.member_client = Client.objects.create(
            company_name="Own Client",
            contact_person="Client One",
            email="own@test.com",
            industry="Consulting",
            assigned_to=self.member,
            status=Client.Status.PINGED,
            last_contacted_at=timezone.now() - timedelta(days=10),
        )
        self.other_client = Client.objects.create(
            company_name="Other Client",
            contact_person="Client Two",
            email="other@test.com",
            industry="SaaS",
            assigned_to=self.other_member,
            status=Client.Status.PINGED,
            last_contacted_at=timezone.now() - timedelta(days=10),
        )

    @patch("outreach.tasks._open_smtp_connection")
    @patch("outreach.tasks._smtp_preflight")
    @patch("outreach.tasks._send_single_email")
    @override_settings(GOOGLE_API_KEY="")
    def test_followups_use_logged_in_user_mailbox_and_scope(
        self, mock_send, mock_preflight, mock_open_connection
    ):
        captured = {}

        def _fake_send(**kwargs):
            captured["sender_profile"] = kwargs["sender_profile"]
            captured["recipient_email"] = kwargs["recipient_email"]
            return True, "Sent"

        mock_send.side_effect = _fake_send
        mock_preflight.return_value = (True, "ok")
        mock_open_connection.return_value = MagicMock()

        result = send_followups(triggered_by_user_id=self.member.pk)

        self.member_client.refresh_from_db()
        self.other_client.refresh_from_db()

        self.assertEqual(result["sent"], 1)
        self.assertEqual(captured["recipient_email"], "own@test.com")
        self.assertEqual(captured["sender_profile"]["sender_name"], "Asha Rao")
        self.assertEqual(captured["sender_profile"]["mailbox_email"], "asha.mail@test.com")
        self.assertEqual(self.member_client.status, Client.Status.FOLLOW_UP)
        self.assertEqual(self.other_client.status, Client.Status.PINGED)
        action_log = ActionLog.objects.get(client=self.member_client)
        self.assertEqual(action_log.team_member, self.member)


class ClassifyGeminiExceptionTests(TestCase):
    """Pure unit tests for the transient/permanent error classification that
    decides whether a Gemini failure gets retried."""

    def test_rate_limit_is_transient(self):
        from google.genai import errors as genai_errors

        exc = genai_errors.ClientError(429, {"error": {"message": "rate limited"}})
        self.assertEqual(ai_client.classify_gemini_exception(exc), "transient")

    def test_bad_argument_is_not_transient(self):
        from google.genai import errors as genai_errors

        exc = genai_errors.ClientError(400, {"error": {"message": "bad request"}})
        self.assertEqual(ai_client.classify_gemini_exception(exc), "invalid")

    def test_server_error_is_transient(self):
        from google.genai import errors as genai_errors

        exc = genai_errors.ServerError(503, {"error": {"message": "overloaded"}})
        self.assertEqual(ai_client.classify_gemini_exception(exc), "transient")

    def test_safety_block_is_not_transient(self):
        exc = ValueError("Gemini returned no candidates (likely a safety block).")
        self.assertEqual(ai_client.classify_gemini_exception(exc), "safety_block")

    def test_timeout_text_is_transient(self):
        exc = TimeoutError("request timed out")
        self.assertEqual(ai_client.classify_gemini_exception(exc), "transient")


class ValidateAndCleanTests(TestCase):
    """Pure unit tests for the defense-in-depth content validation that
    replaced the old fragile string-split parsing."""

    def test_empty_body_flagged(self):
        _subject, body, problems = ai_client.validate_and_clean("Subject", "")
        self.assertEqual(body, "")
        self.assertIn("empty body", problems)

    def test_markdown_is_stripped(self):
        subject, body, problems = ai_client.validate_and_clean(
            "**Big** News", "Hello **there**, this is a *test* of the system." + "x" * 40
        )
        self.assertNotIn("**", subject)
        self.assertNotIn("**", body)
        self.assertNotIn("*", body)
        self.assertTrue(any("markdown" in p for p in problems))

    def test_subject_prefix_mismatch_is_kept_not_overwritten(self):
        # The old code silently replaced a non-matching subject with a
        # generic one, destroying valid Gemini personalization. The new
        # behavior keeps the subject and just flags it.
        subject, _body, problems = ai_client.validate_and_clean(
            "A completely different subject", "x" * 60, subject_prefix="180DC"
        )
        self.assertEqual(subject, "A completely different subject")
        self.assertTrue(any("did not start with required prefix" in p for p in problems))

    def test_clean_input_has_no_problems(self):
        subject, body, problems = ai_client.validate_and_clean(
            "180DC IIT Kharagpur X Acme", "A perfectly normal email body. " * 5
        )
        self.assertEqual(problems, [])
        self.assertEqual(subject, "180DC IIT Kharagpur X Acme")
        self.assertTrue(body)


class GeminiEmailGenerationTests(TestCase):
    """Exercises _generate_ai_email's success/fallback branches by mocking
    ai_client.generate_json directly — unlike the old test suite, this
    actually exercises Gemini-success and Gemini-failure code paths rather
    than only ever hitting the no-API-key fallback."""

    def setUp(self):
        self.sender_profile = {
            "sender_name": "Asha Rao",
            "sender_role": "Outreach Lead",
            "mailbox_email": "asha.mail@test.com",
        }
        self.client_record = Client.objects.create(
            company_name="Acme Corp",
            contact_person="Jordan Lee",
            email="jordan@acme.test",
            industry="SaaS",
        )

    @override_settings(GOOGLE_API_KEY="test-key")
    @patch("outreach.ai_client.generate_json")
    @patch("outreach.tasks._google_search_snippets", return_value="")
    def test_successful_generation_marks_ai_used(self, _mock_research, mock_generate_json):
        mock_generate_json.return_value = {
            "subject": "180DC IIT Kharagpur X Acme Corp",
            "body": "Respected Sir,\n\nThis is a genuinely personalised email body. " * 3,
        }

        subject, body, meta = _generate_ai_email(self.client_record, self.sender_profile)

        self.assertTrue(meta["ai_used"])
        self.assertIsNone(meta["fallback_reason"])
        self.assertEqual(subject, "180DC IIT Kharagpur X Acme Corp")
        self.assertIn("personalised", body)

    @override_settings(GOOGLE_API_KEY="test-key")
    @patch("outreach.ai_client.generate_json")
    @patch("outreach.tasks._google_search_snippets", return_value="")
    def test_empty_body_falls_back_to_template(self, _mock_research, mock_generate_json):
        mock_generate_json.return_value = {"subject": "Hello", "body": ""}

        subject, body, meta = _generate_ai_email(self.client_record, self.sender_profile)

        self.assertFalse(meta["ai_used"])
        self.assertIn("empty", meta["fallback_reason"].lower())
        self.assertIn("Acme Corp", subject)
        self.assertTrue(body)  # static fallback template body

    @override_settings(GOOGLE_API_KEY="test-key")
    @patch("outreach.ai_client.generate_json")
    @patch("outreach.tasks._google_search_snippets", return_value="")
    def test_api_failure_falls_back_and_records_reason(self, _mock_research, mock_generate_json):
        mock_generate_json.side_effect = RuntimeError("503 overloaded")

        subject, body, meta = _generate_ai_email(self.client_record, self.sender_profile)

        self.assertFalse(meta["ai_used"])
        self.assertIn("overloaded", meta["fallback_reason"])
        self.assertIn("Acme Corp", subject)
        self.assertTrue(body)

    @override_settings(GOOGLE_API_KEY="")
    def test_no_api_key_falls_back_immediately(self):
        subject, body, meta = _generate_ai_email(self.client_record, self.sender_profile)

        self.assertFalse(meta["ai_used"])
        self.assertEqual(meta["fallback_reason"], "GOOGLE_API_KEY not configured")
        self.assertIn("Acme Corp", subject)
        self.assertTrue(body)


class GeminiRetryTests(TestCase):
    """Exercises the real tenacity retry wrapper (with sleeps patched out
    so the test stays fast) to confirm transient errors are retried and
    permanent ones are not."""

    @patch("tenacity.nap.time.sleep", return_value=None)
    @patch("outreach.ai_client.get_client")
    def test_transient_error_is_retried_then_succeeds(self, mock_get_client, _mock_sleep):
        from google.genai import errors as genai_errors

        fake_response = MagicMock()
        fake_response.candidates = [MagicMock(finish_reason="STOP")]
        fake_response.parsed = {"subject": "S", "body": "B"}

        fake_client = MagicMock()
        fake_client.models.generate_content.side_effect = [
            genai_errors.ServerError(503, {"error": {"message": "overloaded"}}),
            genai_errors.ServerError(503, {"error": {"message": "overloaded"}}),
            fake_response,
        ]
        mock_get_client.return_value = fake_client

        result = ai_client.generate_json(
            api_key="k",
            model_name="gemini-2.5-flash",
            prompt="hi",
            response_schema=ai_client.EMAIL_RESPONSE_SCHEMA,
        )

        self.assertEqual(result, {"subject": "S", "body": "B"})
        self.assertEqual(fake_client.models.generate_content.call_count, 3)

    @patch("tenacity.nap.time.sleep", return_value=None)
    @patch("outreach.ai_client.get_client")
    def test_permanent_error_is_not_retried(self, mock_get_client, _mock_sleep):
        from google.genai import errors as genai_errors

        fake_client = MagicMock()
        fake_client.models.generate_content.side_effect = genai_errors.ClientError(
            400, {"error": {"message": "bad request"}}
        )
        mock_get_client.return_value = fake_client

        with self.assertRaises(genai_errors.ClientError):
            ai_client.generate_json(
                api_key="k",
                model_name="gemini-2.5-flash",
                prompt="hi",
                response_schema=ai_client.EMAIL_RESPONSE_SCHEMA,
            )

        self.assertEqual(fake_client.models.generate_content.call_count, 1)


class SmtpReliabilityTests(TestCase):
    """Exercises _send_single_email's transient-vs-permanent SMTP handling
    and confirms a connection timeout is actually configured."""

    def setUp(self):
        self.sender_profile = {
            "mailbox_email": "sender@test.com",
            "mailbox_password": "app-pass",
            "smtp_host": "smtp.example.test",
            "smtp_port": 587,
            "from_header": "Sender <sender@test.com>",
        }

    @patch("tenacity.nap.time.sleep", return_value=None)
    @patch("outreach.tasks.smtplib.SMTP")
    def test_transient_smtp_error_is_retried_then_succeeds(self, mock_smtp_cls, _mock_sleep):
        mock_server = MagicMock()
        mock_server.send_message.side_effect = [
            smtplib.SMTPServerDisconnected("dropped"),
            None,
        ]
        mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_server)
        mock_smtp_cls.return_value = mock_server

        success, message = _send_single_email(
            recipient_email="client@test.com",
            subject="Hi",
            body="Body",
            cc_emails=[],
            sender_profile=self.sender_profile,
        )

        self.assertTrue(success)
        self.assertEqual(mock_server.send_message.call_count, 2)
        # timeout is passed positionally/keyword to smtplib.SMTP(...)
        _args, kwargs = mock_smtp_cls.call_args
        self.assertIn("timeout", kwargs)

    @patch("outreach.tasks.smtplib.SMTP")
    def test_auth_failure_is_not_retried(self, mock_smtp_cls):
        mock_server = MagicMock()
        mock_server.send_message.side_effect = smtplib.SMTPAuthenticationError(535, b"bad creds")
        mock_smtp_cls.return_value = mock_server

        success, message = _send_single_email(
            recipient_email="client@test.com",
            subject="Hi",
            body="Body",
            cc_emails=[],
            sender_profile=self.sender_profile,
        )

        self.assertFalse(success)
        self.assertIn("authentication failed", message.lower())
        self.assertEqual(mock_server.send_message.call_count, 1)


class ClaimClientRaceTests(TransactionTestCase):
    """Confirms the atomic compare-and-swap claim closes the double-send
    race: two attempts to claim the same client can never both succeed."""

    def setUp(self):
        self.client_record = Client.objects.create(
            company_name="Race Co",
            contact_person="Riley Fast",
            email="riley@race.test",
            industry="Logistics",
            status=Client.Status.NOT_CONTACTED,
        )

    def test_only_one_claim_succeeds(self):
        first = _claim_client(self.client_record.pk, Client.Status.NOT_CONTACTED)
        second = _claim_client(self.client_record.pk, Client.Status.NOT_CONTACTED)

        self.assertTrue(first)
        self.assertFalse(second)

        self.client_record.refresh_from_db()
        self.assertEqual(self.client_record.status, Client.Status.SENDING)

    def test_release_makes_client_claimable_again(self):
        _claim_client(self.client_record.pk, Client.Status.NOT_CONTACTED)
        _release_client(self.client_record.pk, Client.Status.NOT_CONTACTED)

        self.client_record.refresh_from_db()
        self.assertEqual(self.client_record.status, Client.Status.NOT_CONTACTED)

        reclaimed = _claim_client(self.client_record.pk, Client.Status.NOT_CONTACTED)
        self.assertTrue(reclaimed)


class CeleryTaskExecutionTests(TestCase):
    """Runs the Celery-task-wrapped entry points directly (equivalent to
    CELERY_TASK_ALWAYS_EAGER=True) and checks the associated progress-run
    row ends up in a sane completed state — the first tests in this repo to
    exercise send_followups/scan_inbox_for_replies as tasks rather than
    plain functions, and the only ones covering the new CampaignRun/
    FollowupRun/ScanRun progress-tracking wiring end to end."""

    def setUp(self):
        self.member = User.objects.create_user(
            username="celery_member",
            password="testpass123",
            first_name="Cam",
            last_name="Paign",
            email="cam@test.com",
        )
        TeamMember.objects.create(
            user=self.member,
            role=TeamMember.Role.MEMBER,
            mailbox_email="cam.mail@test.com",
            mailbox_app_password="app-pass-123",
        )

    @override_settings(GOOGLE_API_KEY="", CELERY_TASK_ALWAYS_EAGER=True)
    @patch("outreach.tasks._open_smtp_connection")
    @patch("outreach.tasks._smtp_preflight", return_value=(True, "ok"))
    @patch("outreach.tasks._send_single_email", return_value=(True, "Sent"))
    def test_send_automated_pings_completes_campaign_run(
        self, _mock_send, _mock_preflight, mock_open_connection
    ):
        mock_open_connection.return_value = MagicMock()
        client_record = Client.objects.create(
            company_name="Task Co",
            contact_person="Taylor Task",
            email="taylor@task.test",
            industry="Consulting",
            assigned_to=self.member,
            status=Client.Status.NOT_CONTACTED,
        )
        run = CampaignRun.objects.create()

        send_automated_pings(
            triggered_by_user_id=self.member.pk, run_id=run.pk
        )

        run.refresh_from_db()
        client_record.refresh_from_db()
        self.assertEqual(run.status, CampaignRun.RunStatus.COMPLETED)
        self.assertEqual(run.sent, 1)
        self.assertEqual(client_record.status, Client.Status.PINGED)
        self.assertEqual(run.ai_fallback_rate, 100.0)  # no API key => template fallback

    @override_settings(GOOGLE_API_KEY="", CELERY_TASK_ALWAYS_EAGER=True)
    @patch("outreach.tasks._open_smtp_connection")
    @patch("outreach.tasks._smtp_preflight", return_value=(True, "ok"))
    @patch("outreach.tasks._send_single_email", return_value=(True, "Sent"))
    def test_send_followups_completes_followup_run(
        self, _mock_send, _mock_preflight, mock_open_connection
    ):
        mock_open_connection.return_value = MagicMock()
        Client.objects.create(
            company_name="Followup Co",
            contact_person="Fern Uppe",
            email="fern@followup.test",
            industry="Consulting",
            assigned_to=self.member,
            status=Client.Status.PINGED,
            last_contacted_at=timezone.now() - timedelta(days=10),
        )
        run = FollowupRun.objects.create()

        send_followups(triggered_by_user_id=self.member.pk, run_id=run.pk)

        run.refresh_from_db()
        self.assertEqual(run.status, FollowupRun.RunStatus.COMPLETED)
        self.assertEqual(run.sent, 1)

    @patch("outreach.tasks._scan_inbox_for_replies_impl")
    def test_scan_inbox_for_replies_completes_scan_run(self, mock_impl):
        mock_impl.return_value = {
            "scanned": 3,
            "new_replies": 1,
            "errors": [],
            "mailbox": "INBOX",
            "debug": [],
        }
        run = ScanRun.objects.create()

        scan_inbox_for_replies(triggered_by_user_id=self.member.pk, run_id=run.pk)

        run.refresh_from_db()
        self.assertEqual(run.status, ScanRun.RunStatus.COMPLETED)
        self.assertEqual(run.total, 3)
        self.assertEqual(run.sent, 1)
