from django.contrib import admin

from .models import (
    ActionLog,
    CampaignRun,
    Client,
    EmailReply,
    LinkedInReachout,
    OutreachCampaign,
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
    list_display = ("client", "team_member", "campaign", "emailed_at")
    list_filter = ("campaign",)
    search_fields = ("client__company_name", "team_member__username")
    readonly_fields = ("emailed_at",)


@admin.register(CampaignRun)
class CampaignRunAdmin(admin.ModelAdmin):
    list_display = (
        "pk",
        "status",
        "total",
        "processed",
        "sent",
        "failed",
        "started_at",
        "finished_at",
    )
    list_filter = ("status",)
    readonly_fields = ("started_at",)


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
