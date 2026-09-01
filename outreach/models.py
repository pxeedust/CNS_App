from django.db import models
from django.contrib.auth.models import User
from django.core.validators import URLValidator
from django.utils import timezone

from .fields import EncryptedTextField


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
    website = models.URLField(
        blank=True, validators=[URLValidator(schemes=["http", "https"])]
    )
    linkedin_url = models.URLField(
        blank=True, validators=[URLValidator(schemes=["http", "https"])]
    )
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
    send_claimed_at = models.DateTimeField(null=True, blank=True)
    send_claimed_by = models.ForeignKey(
        "CampaignRun",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="claimed_clients",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["company_name"]

    def __str__(self):
        return f"{self.company_name} — {self.contact_person} ({self.status})"

    def save(self, *args, **kwargs):
        self.email = (self.email or "").strip().lower()
        super().save(*args, **kwargs)


class OutreachCampaign(models.Model):
    name = models.CharField(max_length=255, unique=True)
    email_template = models.TextField(
        help_text="Use {{first_name}}, {{company}}, etc. as placeholders."
    )
    subject_template = models.CharField(
        max_length=500,
        default="180DC IIT Kharagpur X {{company}}",
        help_text="Use the same placeholders as the body template.",
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
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="action_logs",
    )
    client_company = models.CharField(max_length=255, blank=True)
    client_email = models.EmailField(blank=True)
    campaign = models.ForeignKey(
        OutreachCampaign,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="action_logs",
    )
    outbound_email = models.ForeignKey(
        "OutboundEmail",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="action_logs",
    )
    notes = models.TextField(blank=True)
    emailed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-emailed_at"]

    def __str__(self):
        team_member_name = (
            self.team_member.get_full_name() or self.team_member.username
            if self.team_member
            else "Unknown"
        )
        company = self.client.company_name if self.client else self.client_company or "Unknown"
        return f"{team_member_name} -> {company} on {self.emailed_at:%Y-%m-%d %H:%M}"

    def save(self, *args, **kwargs):
        if self.client_id:
            self.client_company = self.client.company_name
            self.client_email = self.client.email
        super().save(*args, **kwargs)


class CampaignRun(models.Model):
    """Tracks the progress of a single outreach campaign run."""

    class RunStatus(models.TextChoices):
        RUNNING = "running", "Running"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    class Operation(models.TextChoices):
        INITIAL = "initial", "Initial outreach"
        FOLLOWUP = "followup", "Follow-up outreach"
        REPLY_SCAN = "reply_scan", "Reply scan"

    status = models.CharField(
        max_length=20, choices=RunStatus.choices, default=RunStatus.RUNNING
    )
    operation = models.CharField(
        max_length=20, choices=Operation.choices, default=Operation.INITIAL
    )
    triggered_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="campaign_runs",
    )
    campaign = models.ForeignKey(
        OutreachCampaign,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="runs",
    )
    total = models.PositiveIntegerField(default=0)
    processed = models.PositiveIntegerField(default=0)
    sent = models.PositiveIntegerField(default=0)
    failed = models.PositiveIntegerField(default=0)
    fallback_count = models.PositiveIntegerField(default=0)
    current_company = models.CharField(max_length=255, blank=True)
    current_step = models.CharField(
        max_length=50, blank=True, help_text="researching / generating / sending"
    )
    log = models.JSONField(default=list, blank=True)
    error_message = models.TextField(blank=True)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-started_at"]

    def __str__(self):
        return f"Run #{self.pk} — {self.status} ({self.processed}/{self.total})"


class EmailReply(models.Model):
    """Stores individual reply emails discovered via IMAP scanning."""

    client = models.ForeignKey(
        Client,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="replies",
    )
    client_company = models.CharField(max_length=255, blank=True)
    client_email = models.EmailField(blank=True)
    outbound_email = models.ForeignKey(
        "OutboundEmail",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="replies",
        help_text="Outgoing message this reply belongs to, when known.",
    )
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
        company = self.client.company_name if self.client else self.client_company or "Unknown"
        return f"Reply from {company} on {self.received_at:%Y-%m-%d %H:%M}"

    def save(self, *args, **kwargs):
        if self.client_id:
            self.client_company = self.client.company_name
            self.client_email = self.client.email
        super().save(*args, **kwargs)


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
    mailbox_app_password = EncryptedTextField(
        blank=True,
        help_text="App password or mailbox password used for SMTP/IMAP.",
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


class OutboundEmail(models.Model):
    """Durable audit and idempotency record for an outbound email attempt."""

    class GenerationMode(models.TextChoices):
        AI = "ai", "Gemini AI"
        CAMPAIGN_TEMPLATE = "campaign_template", "Campaign template"
        STATIC_TEMPLATE = "static_template", "Built-in template"
        FAILED = "failed", "Generation failed"

    class DeliveryStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"
        UNKNOWN = "unknown", "Delivery unknown"

    client = models.ForeignKey(
        Client,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="outbound_emails",
    )
    client_company = models.CharField(max_length=255, blank=True)
    client_email = models.EmailField(blank=True)
    campaign = models.ForeignKey(
        OutreachCampaign,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="outbound_emails",
    )
    campaign_run = models.ForeignKey(
        CampaignRun,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="outbound_emails",
    )
    team_member = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="outbound_emails",
    )
    recipient = models.EmailField()
    subject = models.CharField(max_length=998)
    body = models.TextField()
    message_id = models.CharField(max_length=500, unique=True)
    generation_mode = models.CharField(
        max_length=30, choices=GenerationMode.choices
    )
    generation_error = models.TextField(blank=True)
    status = models.CharField(
        max_length=20,
        choices=DeliveryStatus.choices,
        default=DeliveryStatus.PENDING,
    )
    followup_number = models.PositiveSmallIntegerField(null=True, blank=True)
    idempotency_key = models.CharField(max_length=255, unique=True)
    attempt_count = models.PositiveIntegerField(default=0)
    last_error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["recipient", "-created_at"]),
            models.Index(fields=["status", "created_at"]),
        ]

    def __str__(self):
        return f"{self.recipient} - {self.subject} ({self.status})"

    def save(self, *args, **kwargs):
        if self.client_id:
            self.client_company = self.client.company_name
            self.client_email = self.client.email
        super().save(*args, **kwargs)


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
