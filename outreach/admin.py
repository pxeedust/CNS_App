from django.contrib import admin
from django.utils.html import format_html, format_html_join

from .models import (
    ActionLog,
    CampaignRun,
    Client,
    CompanyResearch,
    EmailReply,
    FollowupRun,
    LinkedInReachout,
    OutreachCampaign,
    ScanRun,
    TeamMember,
)


@admin.register(Client)
class ClientAdmin(admin.ModelAdmin):
    list_display = (
        "company_name",
        "contact_person",
        "title",
        "email",
        "industry",
        "status",
        "sentiment",
        "assigned_to",
        "has_replied",
        "followup_count",
        "last_contacted_at",
    )
    list_filter = (
        "status",
        "sentiment",
        "has_replied",
        "industry",
        "country",
        "assigned_to",
    )
    search_fields = ("company_name", "contact_person", "email", "keywords")
    list_editable = ("status",)
    fieldsets = (
        ("Contact", {"fields": ("contact_person", "title", "email", "phone")}),
        ("Company", {"fields": ("company_name", "industry", "website", "keywords")}),
        ("Location", {"fields": ("city", "state", "country")}),
        (
            "Outreach",
            {
                "fields": (
                    "assigned_to",
                    "status",
                    "sentiment",
                    "last_contacted_at",
                    "linkedin_url",
                )
            },
        ),
        (
            "Reply Tracking",
            {
                "fields": (
                    "has_replied",
                    "reply_snippet",
                    "last_reply_at",
                    "followup_count",
                    "next_followup_at",
                )
            },
        ),
    )


@admin.register(OutreachCampaign)
class OutreachCampaignAdmin(admin.ModelAdmin):
    list_display = ("name", "target_industry", "created_at")
    search_fields = ("name", "target_industry")


@admin.register(ActionLog)
class ActionLogAdmin(admin.ModelAdmin):
    list_display = (
        "client",
        "team_member",
        "campaign",
        "ai_used",
        "quality_score",
        "was_repaired",
        "research_grounded",
        "emailed_at",
    )
    list_filter = ("campaign", "ai_used", "was_repaired", "research_grounded")
    search_fields = ("client__company_name", "team_member__username")
    readonly_fields = ("emailed_at",)


@admin.register(CompanyResearch)
class CompanyResearchAdmin(admin.ModelAdmin):
    """
    Lets an admin inspect (and, when a summary has gone stale or wrong,
    delete) the cached grounded-research summaries that personalise emails.
    Deleting a row simply forces a fresh search on the next send.
    """

    list_display = ("company_name", "grounded", "hit_count", "refreshed_at")
    list_filter = ("grounded",)
    search_fields = ("company_name", "company_key")
    readonly_fields = ("company_key", "hit_count", "created_at")


class _RunLogAdminMixin:
    """Shared admin niceties for CampaignRun/FollowupRun/ScanRun."""

    list_filter = ("status",)
    readonly_fields = ("started_at", "formatted_log")
    exclude = ("log",)

    @admin.display(description="AI Fallback Rate")
    def ai_fallback_rate_display(self, obj):
        rate = obj.ai_fallback_rate
        return f"{rate}%" if rate is not None else "—"

    @admin.display(description="SMTP Failure Rate")
    def smtp_failure_rate_display(self, obj):
        rate = obj.smtp_failure_rate
        return f"{rate}%" if rate is not None else "—"

    @admin.display(description="Log")
    def formatted_log(self, obj):
        if not obj.log:
            return "No entries yet."
        rows = format_html_join(
            "",
            "<tr><td style='padding-right:1em'>{}</td>"
            "<td style='padding-right:1em'>{}</td><td>{}</td></tr>",
            (
                (
                    entry.get("company", ""),
                    entry.get("status", ""),
                    entry.get("detail") or entry.get("ai_failure_reason") or "",
                )
                for entry in obj.log
            ),
        )
        return format_html(
            "<table style='border-collapse:collapse'>"
            "<tr><th style='text-align:left;padding-right:1em'>Company</th>"
            "<th style='text-align:left;padding-right:1em'>Status</th>"
            "<th style='text-align:left'>Detail</th></tr>{}</table>",
            rows,
        )


@admin.register(CampaignRun)
class CampaignRunAdmin(_RunLogAdminMixin, admin.ModelAdmin):
    list_display = (
        "pk",
        "status",
        "total",
        "processed",
        "sent",
        "failed",
        "ai_fallback_rate_display",
        "smtp_failure_rate_display",
        "started_at",
        "finished_at",
    )


@admin.register(FollowupRun)
class FollowupRunAdmin(_RunLogAdminMixin, admin.ModelAdmin):
    list_display = (
        "pk",
        "status",
        "total",
        "processed",
        "sent",
        "failed",
        "ai_fallback_rate_display",
        "smtp_failure_rate_display",
        "started_at",
        "finished_at",
    )


@admin.register(ScanRun)
class ScanRunAdmin(_RunLogAdminMixin, admin.ModelAdmin):
    list_display = (
        "pk",
        "status",
        "total",
        "processed",
        "sent",
        "failed",
        "smtp_failure_rate_display",
        "started_at",
        "finished_at",
    )


@admin.register(EmailReply)
class EmailReplyAdmin(admin.ModelAdmin):
    list_display = ("client", "subject", "sentiment", "received_at", "created_at")
    list_filter = ("sentiment",)
    search_fields = ("client__company_name", "subject", "body")
    readonly_fields = ("message_id", "created_at")


@admin.register(TeamMember)
class TeamMemberAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "role",
        "get_email",
        "get_mailbox_email",
        "get_sender_name",
        "get_active",
        "created_at",
    )
    list_filter = ("role",)
    search_fields = (
        "user__username",
        "user__first_name",
        "user__last_name",
        "user__email",
    )
    # Mailbox app password is never viewable/editable here even though it's
    # encrypted at rest — password changes must go through the app's own
    # mailbox_settings/team_edit views, not the Django admin change form.
    exclude = ("mailbox_app_password",)

    @admin.display(description="Email")
    def get_email(self, obj):
        return obj.user.email

    @admin.display(description="Mailbox")
    def get_mailbox_email(self, obj):
        return obj.mailbox_email or obj.user.email or "-"

    @admin.display(description="Sender Name")
    def get_sender_name(self, obj):
        return obj.sender_name or obj.user.get_full_name() or obj.user.username

    @admin.display(description="Active", boolean=True)
    def get_active(self, obj):
        return obj.user.is_active


@admin.register(LinkedInReachout)
class LinkedInReachoutAdmin(admin.ModelAdmin):
    list_display = ("client", "team_member", "reachout_type", "happened_at")
    list_filter = ("reachout_type", "team_member")
    search_fields = (
        "client__company_name",
        "client__contact_person",
        "team_member__username",
        "notes",
    )
    readonly_fields = ("created_at",)
