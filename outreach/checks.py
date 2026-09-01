"""Deployment checks for outreach-specific security settings."""

from django.conf import settings
from django.core.checks import Error, Tags, Warning, register


@register(Tags.security, deploy=True)
def outreach_security_checks(app_configs, **kwargs):
    issues = []
    encryption_key = getattr(settings, "MAILBOX_ENCRYPTION_KEY", "")
    if not encryption_key:
        issues.append(
            Error(
                "MAILBOX_ENCRYPTION_KEY is not configured.",
                hint="Set a long, random value in the deployment secret store.",
                id="outreach.E001",
            )
        )
    elif encryption_key == settings.SECRET_KEY:
        issues.append(
            Warning(
                "Mailbox encryption currently reuses SECRET_KEY.",
                hint="Set a distinct MAILBOX_ENCRYPTION_KEY before production use.",
                id="outreach.W001",
            )
        )
    return issues
