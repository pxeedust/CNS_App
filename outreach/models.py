from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone

from .fields import EncryptedCharField


class Client(models.Model):
    class Status(models.TextChoices):
        NOT_CONTACTED = "Not Contacted", "Not Contacted"
        SENDING = "Sending", "Sending"
        PINGED = "Pinged", "Pinged"
        REPLIED = "Replied", "Replied"
        FOLLOW_UP = "Follow Up", "Follow Up"
        MEETING_SET = "Meeting Set", "Meeting Set"
        REJECTED = "Rejected", "Rejected"

    class Sentiment(models.TextChoices):
        UNKNOWN = "Unknown", "Unknown"
        POSITIVE = "Positive", "Positive"
        INTERESTED = "Interested", "Interested"
        NEUTRAL = "Neutral", "Neutral"
        NEGATIVE = "Negative", "Negative"
        NOT_INTERESTED = "Not Interested", "Not Interested"

    company_name = models.CharField(max_length=255)
    contact_person = models.CharField(max_length=255)
    email = models.EmailField(unique=True)
    title = models.CharField(
        max_length=150, blank=True, help_text="Job title, e.g. CEO, Founder"
    )
    industry = models.CharField(max_length=100)
    phone = models.CharField(max_length=50, blank=True)
    city = models.CharField(max_length=100, blank=True)
    state = models.CharField(max_length=100, blank=True)
    country = models.CharField(max_length=100, blank=True)
    website = models.URLField(blank=True)
    linkedin_url = models.URLField(blank=True)
    keywords = models.TextField(
        blank=True, help_text="Comma-separated tags from Apollo"
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.NOT_CONTACTED,
    )
    sentiment = models.CharField(
        max_length=20,
        choices=Sentiment.choices,
        default=Sentiment.UNKNOWN,
    )
    has_replied = models.BooleanField(default=False)
    reply_snippet = models.TextField(blank=True, help_text="Preview of latest reply")
    last_reply_at = models.DateTimeField(null=True, blank=True)
    followup_count = models.PositiveIntegerField(default=0)
    next_followup_at = models.DateTimeField(null=True, blank=True)
    last_contacted_at = models.DateTimeField(null=True, blank=True)
    assigned_to = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_clients",
        help_text="Team member responsible for this client. Null = shared pool.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["company_name"]

    def __str__(self):
        return f"{self.company_name} — {self.contact_person} ({self.status})"


class OutreachCampaign(models.Model):
    name = models.CharField(max_length=255, unique=True)
    email_template = models.TextField(
        help_text="Use {{first_name}}, {{company}}, etc. as placeholders."
    )
    target_industry = models.CharField(max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} (targeting: {self.target_industry})"


class ActionLog(models.Model):
    team_member = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name="action_logs",
    )
    client = models.ForeignKey(
        Client,
        on_delete=models.CASCADE,
        related_name="action_logs",
    )
    campaign = models.ForeignKey(
        OutreachCampaign,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="action_logs",
    )
    notes = models.TextField(blank=True)
    ai_used = models.BooleanField(
        default=False, help_text="Whether Gemini generated this email (False = static fallback template)."
    )
    ai_failure_reason = models.CharField(
        max_length=255, blank=True, help_text="Why Gemini generation fell back, if it did."
    )
    email_body = models.TextField(
        blank=True, help_text="Full body of the email actually sent, for audit/debugging."
    )
    quality_score = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text="0-100 content-quality score of the email that was sent (see quality.py).",
    )
    quality_issues = models.JSONField(
        default=list,
        blank=True,
        help_text="Content-quality defects detected in the sent email.",
    )
    was_repaired = models.BooleanField(
        default=False,
        help_text="The first AI draft failed the quality gate and a repair pass fixed it.",
    )
    research_grounded = models.BooleanField(
        default=False,
        help_text="A live Google-Search research summary was available when this email was written.",
    )
    emailed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-emailed_at"]

    def __str__(self):
        team_member_name = (
            self.team_member.get_full_name() or self.team_member.username
            if self.team_member
            else "Unknown"
        )
        return f"{team_member_name} → {self.client.company_name} on {self.emailed_at:%Y-%m-%d %H:%M}"


class CompanyResearch(models.Model):
    """
    Cached Google-Search-grounded research summary for one company.

    The grounding pass was previously re-run for every *contact*, so a batch
    of 50 contacts spread across 20 companies made 50 grounded search calls
    instead of 20, paid for again on every re-run, and added its full latency
    to each send. Research about a company is stable over days, so it is
    cached here keyed on the normalised company name and reused until it
    ages past RESEARCH_CACHE_TTL_DAYS.

    `grounded` records whether the search actually returned content. That
    distinction was previously invisible: a failed grounding pass is swallowed
    and rendered into the prompt as "No results available", after which Gemini
    writes a generic email that is still logged as ai_used=True.
    """

    company_key = models.CharField(
        max_length=255,
        unique=True,
        db_index=True,
        help_text="Normalised company name used as the cache key.",
    )
    company_name = models.CharField(max_length=255)
    summary = models.TextField(blank=True)
    grounded = models.BooleanField(
        default=False,
        help_text="False when the search returned nothing — the email will be generic.",
    )
    hit_count = models.PositiveIntegerField(
        default=0, help_text="Times this cached entry was reused instead of re-searching."
    )
    created_at = models.DateTimeField(auto_now_add=True)
    refreshed_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["company_name"]
        verbose_name_plural = "Company research"

    def __str__(self):
        state = "grounded" if self.grounded else "ungrounded"
        return f"{self.company_name} ({state}, {self.hit_count} reuse(s))"

    @staticmethod
    def make_key(company_name: str) -> str:
        """Normalise a company name into a stable cache key."""
        import re as _re

        return _re.sub(r"[^a-z0-9]+", " ", (company_name or "").lower()).strip()


class RunStatus(models.TextChoices):
    RUNNING = "running", "Running"
    COMPLETED = "completed", "Completed"
    FAILED = "failed", "Failed"


class RunLogStatsMixin:
    """
    Shared reliability-stat computations for any run model with a `log`
    JSONField of per-recipient dicts (see send_automated_pings/send_followups
    in tasks.py for the entry shape: status/ai_used/etc).

    Deliberately a plain mixin (no model fields) so it can be added to the
    pre-existing CampaignRun without an extra migration, while still being
    shared with the new FollowupRun/ScanRun models below.
    """

    @property
    def ai_fallback_rate(self):
        sent_entries = [e for e in (self.log or []) if e.get("status") == "sent"]
        if not sent_entries:
            return None
        fallback = sum(1 for e in sent_entries if not e.get("ai_used", True))
        return round(fallback / len(sent_entries) * 100, 1)

    @property
    def smtp_failure_rate(self):
        entries = self.log or []
        if not entries:
            return None
        failed = sum(1 for e in entries if e.get("status") == "failed")
        return round(failed / len(entries) * 100, 1)

    @property
    def avg_quality_score(self):
        """Mean content-quality score across the emails this run sent."""
        scores = [
            e["quality_score"]
            for e in (self.log or [])
            if e.get("status") == "sent" and isinstance(e.get("quality_score"), int)
        ]
        if not scores:
            return None
        return round(sum(scores) / len(scores), 1)

    @property
    def repaired_count(self):
        """Drafts that failed the quality gate and were fixed by a repair pass."""
        return sum(1 for e in (self.log or []) if e.get("was_repaired"))

    @property
    def quality_blocked_count(self):
        """
        Emails that would previously have gone out with unfilled placeholders
        or other unsendable defects, and were replaced by the safe template.
        """
        return sum(1 for e in (self.log or []) if e.get("quality_blocked"))

    @property
    def research_grounded_rate(self):
        """Share of sent emails written with a live research summary available."""
        sent_entries = [e for e in (self.log or []) if e.get("status") == "sent"]
        if not sent_entries:
            return None
        grounded = sum(1 for e in sent_entries if e.get("research_grounded"))
        return round(grounded / len(sent_entries) * 100, 1)


class CampaignRun(RunLogStatsMixin, models.Model):
    """Tracks the progress of a single outreach campaign run."""

    RunStatus = RunStatus  # backwards-compatible alias (CampaignRun.RunStatus.X)

    status = models.CharField(
        max_length=20, choices=RunStatus.choices, default=RunStatus.RUNNING
    )
    total = models.PositiveIntegerField(default=0)
    processed = models.PositiveIntegerField(default=0)
    sent = models.PositiveIntegerField(default=0)
    failed = models.PositiveIntegerField(default=0)
    current_company = models.CharField(max_length=255, blank=True)
    current_step = models.CharField(
        max_length=50, blank=True, help_text="researching / generating / sending"
    )
    log = models.JSONField(default=list, blank=True)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-started_at"]

    def __str__(self):
        return f"Run #{self.pk} — {self.status} ({self.processed}/{self.total})"


class BaseRun(RunLogStatsMixin, models.Model):
    """
    Shared progress-tracking fields for background outreach tasks, used by
    FollowupRun/ScanRun so they get the same live-progress-page treatment
    CampaignRun already has, instead of running synchronously in a request.
    """

    RunStatus = RunStatus

    status = models.CharField(
        max_length=20, choices=RunStatus.choices, default=RunStatus.RUNNING
    )
    total = models.PositiveIntegerField(default=0)
    processed = models.PositiveIntegerField(default=0)
    sent = models.PositiveIntegerField(default=0)
    failed = models.PositiveIntegerField(default=0)
    current_company = models.CharField(max_length=255, blank=True)
    current_step = models.CharField(max_length=50, blank=True)
    log = models.JSONField(default=list, blank=True)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        abstract = True
        ordering = ["-started_at"]

    def __str__(self):
        return f"{type(self).__name__} #{self.pk} — {self.status} ({self.processed}/{self.total})"


class FollowupRun(BaseRun):
    """Tracks the progress of a single send_followups run."""


class ScanRun(BaseRun):
    """Tracks the progress of a single scan_inbox_for_replies run."""


class EmailReply(models.Model):
    """Stores individual reply emails discovered via IMAP scanning."""

    client = models.ForeignKey(Client, on_delete=models.CASCADE, related_name="replies")
    subject = models.CharField(max_length=500, blank=True)
    body = models.TextField()
    received_at = models.DateTimeField()
    sentiment = models.CharField(
        max_length=20,
        choices=Client.Sentiment.choices,
        default=Client.Sentiment.UNKNOWN,
    )
    sentiment_summary = models.TextField(
        blank=True, help_text="AI-generated sentiment explanation"
    )
    message_id = models.CharField(
        max_length=500, unique=True, help_text="Email Message-ID for deduplication"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-received_at"]

    def __str__(self):
        return f"Reply from {self.client.company_name} on {self.received_at:%Y-%m-%d %H:%M}"


class TeamMember(models.Model):
    """Profile extending the built-in User with a team role."""

    class Role(models.TextChoices):
        ADMIN = "Admin", "Admin"
        MEMBER = "Member", "Member"

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile")
    role = models.CharField(max_length=10, choices=Role.choices, default=Role.MEMBER)
    sender_name = models.CharField(
        max_length=150,
        blank=True,
        help_text="Name shown in outbound outreach emails.",
    )
    sender_role = models.CharField(
        max_length=150,
        blank=True,
        help_text="Role/title shown in the email signature.",
    )
    mailbox_email = models.EmailField(
        blank=True,
        help_text="Mailbox used for outbound email and IMAP reply scanning.",
    )
    mailbox_app_password = EncryptedCharField(
        max_length=512,
        blank=True,
        help_text="App password or mailbox password used for SMTP/IMAP (encrypted at rest).",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["user__first_name", "user__last_name"]

    def __str__(self):
        return f"{self.user.get_full_name() or self.user.username} ({self.role})"

    @property
    def effective_sender_name(self):
        return self.sender_name or self.user.get_full_name() or self.user.username

    @property
    def effective_sender_role(self):
        return self.sender_role or "Team Member"

    @property
    def effective_mailbox_email(self):
        return self.mailbox_email or self.user.email


class LinkedInReachout(models.Model):
    """Manual log of LinkedIn outreach activity for a client."""

    class ReachoutType(models.TextChoices):
        CONNECTION_REQUEST = "Connection Request", "Connection Request"
        DIRECT_MESSAGE = "Direct Message", "Direct Message"
        INMAIL = "InMail", "InMail"
        FOLLOW_UP = "Follow-up", "Follow-up"
        OTHER = "Other", "Other"

    client = models.ForeignKey(
        Client,
        on_delete=models.CASCADE,
        related_name="linkedin_reachouts",
    )
    team_member = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name="linkedin_reachouts",
    )
    reachout_type = models.CharField(
        max_length=30,
        choices=ReachoutType.choices,
        default=ReachoutType.DIRECT_MESSAGE,
    )
    notes = models.TextField(blank=True)
    happened_at = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-happened_at", "-created_at"]

    def __str__(self):
        member_name = (
            self.team_member.get_full_name() or self.team_member.username
            if self.team_member
            else "Unknown"
        )
        return (
            f"{member_name} -> {self.client.company_name} "
            f"[{self.reachout_type}] on {self.happened_at:%Y-%m-%d %H:%M}"
        )
