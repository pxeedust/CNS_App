from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from .models import Client, LinkedInReachout, TeamMember


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
