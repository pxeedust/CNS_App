"""
Celery tasks for outreach email automation.

Integrates the SMTP sending logic from the standalone send_emails_smtp.py script,
but pulls recipients from the Django Client database instead of a local JSON file,
and logs every action in ActionLog.
"""

import logging
import smtplib
import time
import imaplib
import email as email_lib
from datetime import timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.header import decode_header
from email.utils import formataddr, parsedate_to_datetime

from google import genai
from celery import shared_task
from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from .models import (
    ActionLog,
    CampaignRun,
    Client,
    EmailReply,
    OutreachCampaign,
    TeamMember,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_email_body(client, campaign):
    """
    Build the plain-text email body by substituting client fields into
    the campaign's template.  Falls back to a generic body on any error.
    """
    template = campaign.email_template
    try:
        return (
            template.replace("{{first_name}}", client.contact_person.split()[0])
            .replace("{{full_name}}", client.contact_person)
            .replace("{{company}}", client.company_name)
            .replace("{{industry}}", client.industry)
        )
    except Exception:
        logger.warning(
            "Template substitution failed for client %s — using raw template.",
            client.pk,
        )
        return template


def _resolve_sender_profile(user=None):
    """Resolve sender identity + mailbox credentials for the triggering user."""
    profile = getattr(user, "profile", None) if user else None
    sender_name = (
        (profile.effective_sender_name if profile else "")
        or (user.get_full_name() if user else "")
        or (user.username if user else "")
        or "180DC Outreach"
    )
    sender_role = (
        (profile.effective_sender_role if profile else "") or "Team Member"
    )
    mailbox_email = (
        (profile.effective_mailbox_email if profile else "")
        or (user.email if user else "")
    )
    mailbox_password = (profile.mailbox_app_password if profile else "") or ""
    smtp_host = getattr(settings, "EMAIL_HOST", "smtp.gmail.com")
    smtp_port = getattr(settings, "EMAIL_PORT", 587)
    imap_host = getattr(settings, "IMAP_HOST", "imap.gmail.com")
    imap_port = int(getattr(settings, "IMAP_PORT", 993))

    return {
        "sender_name": sender_name,
        "sender_role": sender_role,
        "mailbox_email": mailbox_email,
        "mailbox_password": mailbox_password,
        "smtp_host": smtp_host,
        "smtp_port": smtp_port,
        "imap_host": imap_host,
        "imap_port": imap_port,
        "from_header": formataddr((sender_name, mailbox_email)) if mailbox_email else sender_name,
    }


def _validate_sender_profile(sender_profile):
    if not sender_profile["mailbox_email"]:
        return "Mailbox email is not configured for this user."
    if not sender_profile["mailbox_password"]:
        return "Mailbox app password is not configured for this user."
    return None


def _send_single_email(
    *,
    recipient_email: str,
    subject: str,
    body: str,
    cc_emails: list[str],
    sender_profile: dict,
) -> tuple[bool, str]:
    """
    Send one email via SMTP using credentials from Django settings (sourced from
    the .env file).  Returns (success: bool, message: str).

    This function is deliberately isolated so that a failure on one recipient
    never crashes the outer task loop.
    """
    sender = sender_profile["mailbox_email"]
    password = sender_profile["mailbox_password"]
    smtp_host = sender_profile["smtp_host"]
    smtp_port = sender_profile["smtp_port"]

    if not sender or not password:
        return False, "Mailbox email or app password is not configured for this user."

    try:
        msg = MIMEMultipart()
        msg["Subject"] = subject
        msg["From"] = sender_profile["from_header"]
        msg["To"] = recipient_email
        if cc_emails:
            msg["Cc"] = ", ".join(cc_emails)
        msg.attach(MIMEText(body, "plain"))

        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.ehlo()
            server.starttls()
            server.login(sender, password)
            all_recipients = [recipient_email] + cc_emails
            server.send_message(msg, from_addr=sender, to_addrs=all_recipients)

        return True, "Sent"

    except smtplib.SMTPAuthenticationError:
        return False, "SMTP authentication failed — check EMAIL_HOST_PASSWORD in .env"
    except smtplib.SMTPConnectError as exc:
        return False, f"Could not connect to SMTP server: {exc}"
    except smtplib.SMTPRecipientsRefused:
        return False, f"Recipient address refused by server: {recipient_email}"
    except Exception as exc:  # noqa: BLE001
        return False, f"Unexpected SMTP error: {exc}"


# ---------------------------------------------------------------------------
# Fallback static template (used when GOOGLE_API_KEY is not set or API fails)
# ---------------------------------------------------------------------------
_DEFAULT_SUBJECT_TPL = "180DC IIT Kharagpur X {company}"
_DEFAULT_BODY_TPL = """Respected {title} {last_name},

I am {sender_name}, {sender_role} at 180 Degrees Consulting, IIT Kharagpur — a student-run consultancy providing strategic and operational services to organisations aiming for greater impact. We've partnered with the CRY Foundation and Robin Hood Army on operational strategy, user engagement, and program scalability.

We'd love to explore how our data-driven consulting could support {company}'s goals in {industry}. Could we schedule a brief call to discuss this?

Best regards,
{sender_name}
{sender_role}
180 Degrees Consulting, IIT Kharagpur
https://www.180dc.org/branches/IITKGP
"""


def _build_default_body(client, sender_profile):
    """Render the built-in fallback email body for a client."""
    parts = client.contact_person.split()
    last_name = parts[-1] if parts else client.contact_person
    salutation = client.title.strip() if client.title else "Sir/Ma'am"
    return _DEFAULT_BODY_TPL.format(
        sender_name=sender_profile["sender_name"],
        sender_role=sender_profile["sender_role"],
        title=salutation,
        last_name=last_name,
        industry=client.industry,
        company=client.company_name,
    )


# ---------------------------------------------------------------------------
# Gemini AI email generation
# ---------------------------------------------------------------------------


def _google_search_snippets(ai_client, model_name, company, industry, website):
    """
    Use Gemini with Google Search grounding to pull live context about a company.
    Returns a short research summary string.
    """
    try:
        from google.genai import types

        search_tool = types.Tool(google_search=types.GoogleSearch())
        research_prompt = (
            f"Research the company '{company}' in the {industry} industry. "
            f"Their website is {website or 'unknown'}. "
            f"Write a concise 3-4 sentence summary of what they do, their mission, "
            f"key products/services, and any notable achievements or recent news."
        )
        response = ai_client.models.generate_content(
            model=model_name,
            contents=research_prompt,
            config=types.GenerateContentConfig(tools=[search_tool]),
        )
        return response.text.strip()
    except Exception as exc:
        logger.warning("Google Search grounding failed for %s: %s", company, exc)
        return ""


def _generate_ai_email(client, sender_profile, campaign=None) -> tuple[str, str]:
    """
    Generate a fully personalised subject + body for *client* using Gemini.

    Step 1: Research the company via Google Search grounding to get live context.
    Step 2: Feed all Apollo fields + research into a detailed prompt.
    Falls back to the static template when GOOGLE_API_KEY is absent or the
    API call fails.

    Returns (subject, body).
    """
    api_key = getattr(settings, "GOOGLE_API_KEY", "")
    if not api_key:
        logger.warning(
            "GOOGLE_API_KEY not configured — falling back to static template for client %s.",
            client.pk,
        )
        subject = _DEFAULT_SUBJECT_TPL.format(company=client.company_name)
        return subject, _build_default_body(client, sender_profile)

    try:
        ai_client = genai.Client(api_key=api_key)
        model_name = getattr(settings, "GEMINI_MODEL", "gemini-2.5-flash")

        parts = client.contact_person.split()
        first_name = parts[0] if parts else ""
        last_name = parts[-1] if len(parts) > 1 else (parts[0] if parts else "")
        title = client.title or ""
        company = client.company_name or ""
        industry = client.industry or ""
        keywords = client.keywords or ""
        website = client.website or ""
        linkedin = client.linkedin_url or ""
        location_parts = [p for p in [client.city, client.state, client.country] if p]
        location = ", ".join(location_parts)

        # -- Step 1: Research the company via Google Search grounding ----------
        search_snippets = _google_search_snippets(
            ai_client, model_name, company, industry, website
        )

        # -- Step 2: Build the consultant identity ----------------------------
        consultant_name = sender_profile["sender_name"]
        consultant_email = sender_profile["mailbox_email"]
        consultant_role = sender_profile["sender_role"]

        campaign_context = ""
        if campaign:
            campaign_context = (
                f"\nAdditional campaign guidance (do NOT copy verbatim, use as context):\n"
                f"{campaign.email_template}\n"
            )

        prompt = (
            "You are an expert business consultant tasked with writing highly professional, "
            "personalized outreach emails to companies. Each email should include:\n"
            "- A compelling, relevant subject line.\n"
            "- A detailed, friendly, and professional body that references the company's background and mission.\n"
            "- The email should be from the consultant (details below) to the company (details below).\n"
            "- Do not include any labels like 'Subject:' or 'Body:'.\n"
            "- The output must be the subject line, then a newline, then the full email body.\n"
            "- The email should be suitable for a first contact and encourage a reply.\n"
            "- The length limit is two paragraphs, be detailed on what 180DC IITKGP can offer to them and be direct.\n"
            "Here is a sample mail, you must refer to a similar format only for sending the mails:\n\n"
            "---SAMPLE START---\n"
            f"Respected Sir,\n\n"
            f"I am {consultant_name}, {consultant_role} at 180 Degrees Consulting, IIT Kharagpur — "
            "a student-run consultancy providing strategic and operational services to organisations "
            "aiming for greater impact and efficiency. We've been impressed by [Company]'s work in "
            "[specific domain], particularly [specific achievement/product]. [1-2 sentences referencing "
            "concrete recent news, financials, or product milestones from the Google search results].\n\n"
            "At 180DC IITKGP, we've partnered with the CRY Foundation and Robin Hood Army on operational "
            "strategy, user engagement, and program scalability. We believe our data-driven consulting "
            "could support [Company] in [specific area relevant to them — e.g. optimising user acquisition, "
            "refining operational workflows, scaling outreach]. We'd welcome a brief conversation to explore this.\n\n"
            f"Best regards,\n"
            f"{consultant_name}\n"
            f"{consultant_role}\n"
            "180 Degrees Consulting, IIT Kharagpur\n"
            "https://www.180dc.org/branches/IITKGP\n"
            "---SAMPLE END---\n\n"
            "This must be modified according to the purpose and nature of the company. "
            "Use the Google search results below to deeply personalise the email — reference "
            "their specific products, mission, recent news, or achievements.\n\n"
            "IMPORTANT RULES:\n"
            "1. The subject line MUST start with '180DC IIT Kharagpur X {company}' (append a brief descriptor).\n"
            "2. The greeting MUST be 'Respected {salutation} {last_name},' on its own line.\n"
            "3. Two substantial paragraphs in the body — detailed, not generic.\n"
            "4. End with the exact signature block shown in the sample.\n"
            "5. Do NOT use markdown formatting (no **, no ##, no bullet points in the email body).\n"
            "6. Make the email sound warm and human, not AI-generated.\n\n"
            f"Consultant Name: {consultant_name}\n"
            f"Consultant Email: {consultant_email}\n"
            f"Consultant Role: {consultant_role}\n\n"
            f"Company Name: {company}\n"
            f"Company Owner/Contact: {first_name} {last_name}\n"
            f"Contact Title: {title or 'N/A'}\n"
            f"Company Industries: {industry}\n"
            f"Company Keywords: {keywords or 'N/A'}\n"
            f"Company Website: {website or 'N/A'}\n"
            f"Company LinkedIn: {linkedin or 'N/A'}\n"
            f"Company Location: {location or 'N/A'}\n"
            f"Recent Google Search Results about {company}:\n{search_snippets or 'No results available'}\n"
            f"{campaign_context}\n"
            "Now generate the email. Output ONLY the subject line on the first line, "
            "then a blank line, then the full email body (greeting through signature). "
            "Nothing else."
        )

        response = ai_client.models.generate_content(
            model=model_name,
            contents=prompt,
        )
        content = response.text.strip()

        # Parse: first line = subject, rest = body
        lines = content.split("\n", 1)
        if len(lines) == 2:
            subject_raw = lines[0].strip()
            ai_body = lines[1].strip()
        else:
            subject_raw = f"180DC IIT Kharagpur X {company}"
            ai_body = content

        # Clean up subject — strip any accidental "Subject:" prefix
        for prefix in ("SUBJECT:", "Subject:", "subject:"):
            if subject_raw.startswith(prefix):
                subject_raw = subject_raw[len(prefix) :].strip()

        # Ensure subject starts correctly
        if not subject_raw.upper().startswith("180DC"):
            subject_raw = f"180DC IIT Kharagpur X {company}"

        logger.info("Gemini generated email for client %s [%s].", client.pk, company)
        return subject_raw, ai_body

    except Exception as exc:
        logger.warning(
            "Gemini generation failed for client %s (%s): %s — falling back to static template.",
            client.pk,
            client.company_name,
            exc,
        )
        subject = _DEFAULT_SUBJECT_TPL.format(company=client.company_name)
        return subject, _build_default_body(client, sender_profile)


# ---------------------------------------------------------------------------
# Celery task
# ---------------------------------------------------------------------------


@shared_task(bind=True, name="outreach.send_automated_pings")
def send_automated_pings(
    self,
    campaign_id: int | None = None,
    triggered_by_user_id: int | None = None,
    run_id: int | None = None,
):
    """
    Loop through every Client with status 'Not Contacted', send them a
    personalised email, update their status to 'Pinged', and log the action.

    campaign_id is optional. When None the built-in default template is used.
    run_id links to a CampaignRun row for live progress tracking.
    """
    # -- Resolve CampaignRun for progress tracking --------------------------
    run = None
    if run_id:
        try:
            run = CampaignRun.objects.get(pk=run_id)
        except CampaignRun.DoesNotExist:
            pass

    def _update_run(**kwargs):
        """Helper to persist progress updates."""
        if run:
            for k, v in kwargs.items():
                setattr(run, k, v)
            run.save(update_fields=list(kwargs.keys()))

    # -- Resolve campaign (optional) ----------------------------------------
    campaign = None
    if campaign_id is not None:
        try:
            campaign = OutreachCampaign.objects.get(pk=campaign_id)
        except OutreachCampaign.DoesNotExist:
            logger.warning(
                "send_automated_pings: campaign %s not found — using default template.",
                campaign_id,
            )

    # -- Resolve the triggering user (optional, for ActionLog) ---------------
    from django.contrib.auth.models import User  # local import to avoid circular

    triggering_user = None
    if triggered_by_user_id:
        try:
            triggering_user = User.objects.get(pk=triggered_by_user_id)
        except User.DoesNotExist:
            pass

    sender_profile = _resolve_sender_profile(triggering_user)
    sender_error = _validate_sender_profile(sender_profile)
    if sender_error:
        logger.warning("send_automated_pings: %s", sender_error)
        _update_run(status=CampaignRun.RunStatus.FAILED, current_step="mailbox setup")
        return {"status": "error", "sent": 0, "failed": 0, "error": sender_error}

    # -- CC list from settings -----------------------------------------------
    cc_raw = getattr(settings, "OUTREACH_CC_EMAILS", "")
    cc_emails = [e.strip() for e in cc_raw.split(",") if e.strip()]

    # -- Fetch target clients ------------------------------------------------
    clients = list(
        Client.objects.filter(status=Client.Status.NOT_CONTACTED)
        .filter(
            Q(assigned_to=triggering_user) | Q(assigned_to__isnull=True)
            if triggering_user
            else Q()
        )
        .select_related()
    )

    if not clients:
        logger.info("send_automated_pings: no 'Not Contacted' clients to process.")
        _update_run(status=CampaignRun.RunStatus.COMPLETED, total=0, processed=0)
        return {"status": "ok", "sent": 0, "failed": 0, "skipped": 0}

    _update_run(total=len(clients))

    logger.info(
        "send_automated_pings: starting campaign '%s' for %d client(s).",
        campaign.name if campaign else "default template",
        len(clients),
    )

    sent_count = 0
    failed_count = 0
    failed_details = []
    run_log = []

    for index, client in enumerate(clients):
        # Skip records with no email address
        if not client.email:
            logger.warning("Client %s has no email address — skipping.", client.pk)
            run_log.append(
                {
                    "company": client.company_name,
                    "status": "skipped",
                    "detail": "No email",
                }
            )
            _update_run(processed=index + 1, log=run_log)
            continue

        # -- Progress: researching -------------------------------------------
        _update_run(
            current_company=client.company_name,
            current_step="researching",
            processed=index,
        )

        # -- Progress: generating --------------------------------------------
        _update_run(current_step="generating")
        subject, body = _generate_ai_email(client, sender_profile, campaign)

        # -- Progress: sending -----------------------------------------------
        _update_run(current_step="sending")
        success, message = _send_single_email(
            recipient_email=client.email,
            subject=subject,
            body=body,
            cc_emails=cc_emails,
            sender_profile=sender_profile,
        )

        if success:
            # Update client record
            client.status = Client.Status.PINGED
            client.last_contacted_at = timezone.now()
            client.save(update_fields=["status", "last_contacted_at", "updated_at"])

            # Write action log
            ActionLog.objects.create(
                team_member=triggering_user,
                client=client,
                campaign=campaign,
                notes=f"AI-personalised ping. Subject: {subject}",
            )

            sent_count += 1
            run_log.append(
                {
                    "company": client.company_name,
                    "email": client.email,
                    "status": "sent",
                }
            )
            logger.info(
                "  [%d/%d] Sent → %s <%s>",
                index + 1,
                len(clients),
                client.company_name,
                client.email,
            )
        else:
            failed_count += 1
            failed_details.append(
                {"client_id": client.pk, "email": client.email, "error": message}
            )
            run_log.append(
                {
                    "company": client.company_name,
                    "email": client.email,
                    "status": "failed",
                    "detail": message,
                }
            )
            logger.error(
                "  [%d/%d] FAILED → %s <%s>: %s",
                index + 1,
                len(clients),
                client.company_name,
                client.email,
                message,
            )

        _update_run(
            processed=index + 1,
            sent=sent_count,
            failed=failed_count,
            log=run_log,
        )

        # Rate-limit delay — skip when running eagerly (in-process/DEBUG)
        eager = getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False)
        if not eager and index < len(clients) - 1:
            time.sleep(2)

    _update_run(
        status=CampaignRun.RunStatus.COMPLETED,
        current_step="done",
        current_company="",
        finished_at=timezone.now(),
    )

    summary = {
        "status": "ok",
        "campaign": campaign.name if campaign else "default",
        "sent": sent_count,
        "failed": failed_count,
        "failed_details": failed_details,
        "run_id": run.pk if run else None,
    }
    logger.info("send_automated_pings complete: %s", summary)
    return summary


# ---------------------------------------------------------------------------
# IMAP Reply Scanning
# ---------------------------------------------------------------------------


def _decode_mime_header(raw):
    """Decode a MIME-encoded email header into a plain string."""
    if not raw:
        return ""
    parts = decode_header(raw)
    decoded = []
    for data, charset in parts:
        if isinstance(data, bytes):
            decoded.append(data.decode(charset or "utf-8", errors="replace"))
        else:
            decoded.append(data)
    return "".join(decoded)


def _extract_text_body(msg):
    """Extract the plain-text body from an email.message.Message object."""
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            if ct == "text/plain" and part.get("Content-Disposition") != "attachment":
                payload = part.get_payload(decode=True)
                charset = part.get_content_charset() or "utf-8"
                return payload.decode(charset, errors="replace")
        # Fallback: try text/html
        for part in msg.walk():
            ct = part.get_content_type()
            if ct == "text/html" and part.get("Content-Disposition") != "attachment":
                payload = part.get_payload(decode=True)
                charset = part.get_content_charset() or "utf-8"
                return payload.decode(charset, errors="replace")
    else:
        payload = msg.get_payload(decode=True)
        charset = msg.get_content_charset() or "utf-8"
        if payload:
            return payload.decode(charset, errors="replace")
    return ""


def _analyze_reply_sentiment(reply_text, company_name):
    """
    Use Gemini to classify the sentiment of a client reply.
    Returns (sentiment_label, summary_text).
    """
    api_key = getattr(settings, "GOOGLE_API_KEY", "")
    if not api_key or not reply_text.strip():
        return Client.Sentiment.UNKNOWN, ""

    try:
        ai_client = genai.Client(api_key=api_key)
        model_name = getattr(settings, "GEMINI_MODEL", "gemini-2.5-flash")

        prompt = (
            "You are analysing a reply email from a company representative in response to "
            "a consulting outreach email from 180 Degrees Consulting, IIT Kharagpur.\n\n"
            f"Company: {company_name}\n"
            f'Reply text:\n"""\n{reply_text[:3000]}\n"""\n\n'
            "Classify the sentiment into EXACTLY one of these categories:\n"
            "- Positive (warm, appreciative, open to discussion)\n"
            "- Interested (explicitly wants to learn more, schedule a call, or collaborate)\n"
            "- Neutral (acknowledgement without clear interest or disinterest)\n"
            "- Negative (critical, unhappy, complaints)\n"
            "- Not Interested (polite decline, not relevant, asks to stop contacting)\n\n"
            "Output ONLY two lines:\n"
            "Line 1: The sentiment label (exactly one of: Positive, Interested, Neutral, Negative, Not Interested)\n"
            "Line 2: A one-sentence explanation of why you chose this sentiment.\n"
            "Nothing else."
        )

        response = ai_client.models.generate_content(model=model_name, contents=prompt)
        lines = response.text.strip().split("\n", 1)
        label = lines[0].strip()
        summary = lines[1].strip() if len(lines) > 1 else ""

        # Map to valid choice
        valid_map = {
            "Positive": Client.Sentiment.POSITIVE,
            "Interested": Client.Sentiment.INTERESTED,
            "Neutral": Client.Sentiment.NEUTRAL,
            "Negative": Client.Sentiment.NEGATIVE,
            "Not Interested": Client.Sentiment.NOT_INTERESTED,
        }
        sentiment = valid_map.get(label, Client.Sentiment.UNKNOWN)
        return sentiment, summary

    except Exception as exc:
        logger.warning("Sentiment analysis failed for %s: %s", company_name, exc)
        return Client.Sentiment.UNKNOWN, ""


def _find_all_mail_folder(mail):
    """
    List IMAP mailboxes and return the name of Gmail's 'All Mail' folder.
    Returns the folder name (bytes-decoded) or None if not found.
    """
    status, folders = mail.list()
    if status != "OK":
        return None
    for folder_line in folders:
        # Each line looks like: b'(\\HasNoChildren \\All) "/" "[Gmail]/All Mail"'
        decoded = folder_line.decode("utf-8", errors="replace")
        if "\\All" in decoded:
            # Extract folder name from the quoted part at the end
            parts = decoded.rsplit('"', 2)
            if len(parts) >= 2:
                return parts[-2]
    return None


def _process_imap_message(msg, client, debug_log):
    """
    Process a single IMAP message: extract headers/body, build a stable
    message ID, and return a dict ready for EmailReply creation.
    Returns None if the message should be skipped (already stored / empty).
    """
    import hashlib

    message_id = msg.get("Message-ID", "").strip()
    if not message_id:
        raw_key = f"{client.pk}-" f"{msg.get('Date', '')}-" f"{msg.get('Subject', '')}"
        message_id = f"synthetic-{hashlib.sha256(raw_key.encode()).hexdigest()[:16]}"

    # Skip if already stored
    if EmailReply.objects.filter(message_id=message_id).exists():
        return None

    subject = _decode_mime_header(msg.get("Subject", ""))
    body = _extract_text_body(msg)

    # Skip empty bodies (auto-generated / bounce)
    if not body.strip():
        return None

    date_str = msg.get("Date", "")
    try:
        received_at = parsedate_to_datetime(date_str)
    except Exception:
        received_at = timezone.now()

    if timezone.is_naive(received_at):
        received_at = timezone.make_aware(received_at)

    return {
        "message_id": message_id,
        "subject": subject,
        "body": body,
        "received_at": received_at,
    }


def scan_inbox_for_replies(triggered_by_user_id: int | None = None):
    """
    Connect to IMAP, scan for replies from pinged/follow-up clients,
    create EmailReply records, run sentiment analysis, and update client status.

    Uses two search strategies:
      1. FROM search — matches emails from the client's stored email address.
      2. SUBJECT search — matches "Re:" reply subjects containing the company
         name, catching replies from alternate email addresses.

    Searches Gmail's 'All Mail' folder (falls back to INBOX) so that
    archived / read replies are also found.  Only scans the last 90 days.

    Returns a summary dict with debug info.
    """
    from datetime import date

    from django.contrib.auth.models import User

    triggering_user = None
    if triggered_by_user_id:
        try:
            triggering_user = User.objects.get(pk=triggered_by_user_id)
        except User.DoesNotExist:
            pass

    sender_profile = _resolve_sender_profile(triggering_user)
    sender_error = _validate_sender_profile(sender_profile)
    imap_host = sender_profile["imap_host"]
    imap_port = sender_profile["imap_port"]
    username = sender_profile["mailbox_email"]
    password = sender_profile["mailbox_password"]

    if sender_error:
        return {"error": sender_error}

    # Clients we want to check for replies
    scannable = Client.objects.filter(
        status__in=[Client.Status.PINGED, Client.Status.FOLLOW_UP],
    ).filter(
        Q(assigned_to=triggering_user) | Q(assigned_to__isnull=True)
        if triggering_user
        else Q()
    ).exclude(email="")

    if not scannable.exists():
        return {
            "scanned": 0,
            "new_replies": 0,
            "message": "No pinged/follow-up clients to scan",
        }

    # Build lookups
    client_by_email = {c.email.lower().strip(): c for c in scannable}
    # For subject-based matching: map company names to clients
    client_by_company = {}
    for c in scannable:
        key = c.company_name.lower().strip()
        client_by_company[key] = c

    new_replies = 0
    scanned = 0
    errors = []
    debug_log = []
    # Track message IDs we've already processed in this run to avoid duplicates
    processed_msg_ids = set()

    # SINCE date — only look at emails from the last 90 days
    since_date = (date.today() - timedelta(days=90)).strftime("%d-%b-%Y")

    try:
        mail = imaplib.IMAP4_SSL(imap_host, imap_port)
        mail.login(username, password)
        debug_log.append(f"Logged in as {username}")

        # Try to select [Gmail]/All Mail for comprehensive search
        mailbox = "INBOX"
        all_mail = _find_all_mail_folder(mail)
        if all_mail:
            status, _ = mail.select(f'"{all_mail}"', readonly=True)
            if status == "OK":
                mailbox = all_mail
                debug_log.append(f"Selected mailbox: {all_mail}")
            else:
                mail.select("INBOX", readonly=True)
                debug_log.append("All Mail unavailable, using INBOX")
        else:
            mail.select("INBOX", readonly=True)
            debug_log.append("All Mail folder not found, using INBOX")

        # ------------------------------------------------------------------
        # Strategy 1: Search by FROM (exact client email)
        # ------------------------------------------------------------------
        debug_log.append("--- Strategy 1: FROM search ---")
        for client_email, client in client_by_email.items():
            scanned += 1
            try:
                search_criteria = f'FROM "{client_email}" SINCE {since_date}'
                status, msg_nums = mail.search(None, search_criteria)

                if status != "OK" or not msg_nums[0]:
                    debug_log.append(
                        f"  {client.company_name} ({client_email}): 0 via FROM"
                    )
                    continue

                msg_id_list = msg_nums[0].split()
                debug_log.append(
                    f"  {client.company_name} ({client_email}): "
                    f"{len(msg_id_list)} via FROM"
                )

                for num in msg_id_list:
                    status, msg_data = mail.fetch(num, "(RFC822)")
                    if status != "OK" or not msg_data or not msg_data[0]:
                        continue

                    msg = email_lib.message_from_bytes(msg_data[0][1])
                    parsed = _process_imap_message(msg, client, debug_log)
                    if not parsed:
                        continue
                    if parsed["message_id"] in processed_msg_ids:
                        continue
                    processed_msg_ids.add(parsed["message_id"])

                    sentiment, sentiment_summary = _analyze_reply_sentiment(
                        parsed["body"], client.company_name
                    )
                    EmailReply.objects.create(
                        client=client,
                        subject=parsed["subject"],
                        body=parsed["body"],
                        received_at=parsed["received_at"],
                        sentiment=sentiment,
                        sentiment_summary=sentiment_summary,
                        message_id=parsed["message_id"],
                    )
                    new_replies += 1
                    _update_client_reply(client, parsed, sentiment)

            except Exception as exc:
                errors.append(f"FROM search — {client.company_name}: {exc}")
                logger.warning("IMAP FROM scan error for %s: %s", client_email, exc)

        # ------------------------------------------------------------------
        # Strategy 2: Search by SUBJECT for reply-pattern emails
        #   ("Re: ... 180DC ... X CompanyName ...")
        #   This catches replies sent from a different email address.
        # ------------------------------------------------------------------
        debug_log.append("--- Strategy 2: SUBJECT search ---")

        # Search for any email TO us with "Re:" in subject and "180DC" keyword
        try:
            subject_criteria = (
                f'TO "{username}" SUBJECT "Re:" SUBJECT "180DC" SINCE {since_date}'
            )
            status, msg_nums = mail.search(None, subject_criteria)

            candidate_count = 0
            if status == "OK" and msg_nums[0]:
                candidate_ids = msg_nums[0].split()
                candidate_count = len(candidate_ids)
                debug_log.append(
                    f"  Found {candidate_count} reply email(s) matching subject pattern"
                )

                for num in candidate_ids:
                    status, msg_data = mail.fetch(num, "(RFC822)")
                    if status != "OK" or not msg_data or not msg_data[0]:
                        continue

                    msg = email_lib.message_from_bytes(msg_data[0][1])
                    subject_raw = _decode_mime_header(msg.get("Subject", ""))

                    # Check sender — skip if it's from ourselves (our own outgoing)
                    from_header = msg.get("From", "").lower()
                    if username.lower() in from_header:
                        continue

                    # Try to match subject to a client by company name
                    subject_lower = subject_raw.lower()
                    matched_client = None
                    for company_key, client in client_by_company.items():
                        if company_key in subject_lower:
                            matched_client = client
                            break

                    if not matched_client:
                        continue

                    parsed = _process_imap_message(msg, matched_client, debug_log)
                    if not parsed:
                        continue
                    if parsed["message_id"] in processed_msg_ids:
                        continue
                    processed_msg_ids.add(parsed["message_id"])

                    debug_log.append(
                        f"  -> Matched reply to {matched_client.company_name} "
                        f"(from: {from_header.strip()})"
                    )

                    sentiment, sentiment_summary = _analyze_reply_sentiment(
                        parsed["body"], matched_client.company_name
                    )
                    EmailReply.objects.create(
                        client=matched_client,
                        subject=parsed["subject"],
                        body=parsed["body"],
                        received_at=parsed["received_at"],
                        sentiment=sentiment,
                        sentiment_summary=sentiment_summary,
                        message_id=parsed["message_id"],
                    )
                    new_replies += 1
                    _update_client_reply(matched_client, parsed, sentiment)

            else:
                debug_log.append("  No subject-pattern replies found")

        except Exception as exc:
            errors.append(f"SUBJECT search: {exc}")
            logger.warning("IMAP SUBJECT scan error: %s", exc)

        mail.logout()

    except imaplib.IMAP4.error as exc:
        return {"error": f"IMAP connection failed: {exc}", "debug": debug_log}
    except Exception as exc:
        return {"error": f"IMAP error: {exc}", "debug": debug_log}

    return {
        "scanned": scanned,
        "new_replies": new_replies,
        "errors": errors,
        "mailbox": mailbox,
        "debug": debug_log,
    }


def _update_client_reply(client, parsed, sentiment):
    """Update a client record after a reply is found."""
    client.has_replied = True
    client.reply_snippet = parsed["body"][:300]
    client.last_reply_at = parsed["received_at"]
    client.sentiment = sentiment
    if client.status in (Client.Status.PINGED, Client.Status.FOLLOW_UP):
        client.status = Client.Status.REPLIED
    client.save(
        update_fields=[
            "has_replied",
            "reply_snippet",
            "last_reply_at",
            "sentiment",
            "status",
            "updated_at",
        ]
    )


# ---------------------------------------------------------------------------
# Follow-up email system
# ---------------------------------------------------------------------------

# Default intervals (days after last contact) for each follow-up round
_FOLLOWUP_INTERVALS = [3, 7, 14]
_MAX_FOLLOWUPS = 3


def _generate_followup_email(client, followup_number, sender_profile) -> tuple[str, str]:
    """
    Generate a follow-up email using Gemini AI.
    Adapts tone based on which follow-up round this is.
    Falls back to a static template when AI is unavailable.
    """
    api_key = getattr(settings, "GOOGLE_API_KEY", "")
    company = client.company_name

    ordinal = {1: "first", 2: "second", 3: "third"}.get(
        followup_number, f"#{followup_number}"
    )

    if not api_key:
        subject = f"Following up — 180DC IIT Kharagpur X {company}"
        parts = client.contact_person.split()
        last_name = parts[-1] if parts else client.contact_person
        salutation = client.title.strip() if client.title else "Sir/Ma'am"
        body = (
            f"Respected {salutation} {last_name},\n\n"
            f"I hope this message finds you well. I'm following up on my earlier email "
            f"regarding a potential collaboration between 180 Degrees Consulting, IIT Kharagpur "
            f"and {company}.\n\n"
            f"We remain excited about the possibility of supporting your team with "
            f"data-driven strategy and operational consulting. Would you have a few minutes "
            f"for a brief call this week?\n\n"
            f"Best regards,\n"
            f"{sender_profile['sender_name']}\n"
            f"{sender_profile['sender_role']}\n"
            f"180 Degrees Consulting, IIT Kharagpur\n"
            f"https://www.180dc.org/branches/IITKGP\n"
        )
        return subject, body

    try:
        ai_client = genai.Client(api_key=api_key)
        model_name = getattr(settings, "GEMINI_MODEL", "gemini-2.5-flash")

        parts = client.contact_person.split()
        first_name = parts[0] if parts else ""
        last_name = parts[-1] if len(parts) > 1 else (parts[0] if parts else "")
        title = client.title or ""

        tone_guidance = {
            1: "Gentle and friendly reminder. Reference the initial email briefly. Keep it short (1 paragraph + CTA).",
            2: "Slightly more direct. Mention a specific value proposition relevant to their industry. Two short paragraphs.",
            3: "Final attempt. Express genuine interest but acknowledge they may be busy. Offer to reconnect later if timing isn't right. One paragraph.",
        }

        prompt = (
            f"Write a {ordinal} follow-up email for a consulting outreach that received no reply.\n\n"
            f"Tone: {tone_guidance.get(followup_number, tone_guidance[3])}\n\n"
            f"Consultant: {sender_profile['sender_name']}, {sender_profile['sender_role']}, 180 Degrees Consulting, IIT Kharagpur\n"
            f"Recipient: {first_name} {last_name}, {title or 'N/A'} at {company}\n"
            f"Industry: {client.industry}\n"
            f"Company Website: {client.website or 'N/A'}\n\n"
            "RULES:\n"
            f"1. Subject line: 'Following up — 180DC IIT Kharagpur X {company}'\n"
            "2. Greeting: 'Respected {salutation} {last_name},' on its own line.\n"
            f"3. End with the signature: {sender_profile['sender_name']} / {sender_profile['sender_role']} / 180 Degrees Consulting, IIT Kharagpur / https://www.180dc.org/branches/IITKGP\n"
            "4. No markdown formatting.\n"
            "5. Sound warm and human.\n\n"
            "Output ONLY: subject line on first line, blank line, then full email body."
        )

        response = ai_client.models.generate_content(model=model_name, contents=prompt)
        content = response.text.strip()

        lines = content.split("\n", 1)
        if len(lines) == 2:
            subject = lines[0].strip()
            body = lines[1].strip()
        else:
            subject = f"Following up — 180DC IIT Kharagpur X {company}"
            body = content

        for prefix in ("SUBJECT:", "Subject:", "subject:"):
            if subject.startswith(prefix):
                subject = subject[len(prefix) :].strip()

        return subject, body

    except Exception as exc:
        logger.warning("Follow-up generation failed for %s: %s", company, exc)
        return (
            f"Following up — 180DC IIT Kharagpur X {company}",
            _build_default_body(client, sender_profile),
        )


def send_followups(triggered_by_user_id: int | None = None):
    """
    Send follow-up emails to clients who were pinged but haven't replied,
    respecting follow-up intervals and max follow-up count.

    Returns a summary dict.
    """
    intervals = getattr(settings, "FOLLOWUP_INTERVALS_DAYS", _FOLLOWUP_INTERVALS)
    max_followups = getattr(settings, "MAX_FOLLOWUPS", _MAX_FOLLOWUPS)
    now = timezone.now()
    from django.contrib.auth.models import User

    triggering_user = None
    if triggered_by_user_id:
        try:
            triggering_user = User.objects.get(pk=triggered_by_user_id)
        except User.DoesNotExist:
            pass

    sender_profile = _resolve_sender_profile(triggering_user)
    sender_error = _validate_sender_profile(sender_profile)
    if sender_error:
        return {
            "sent": 0,
            "skipped": 0,
            "failed": 0,
            "details": [],
            "error": sender_error,
        }

    # Find clients eligible for follow-up:
    # status = Pinged or Follow Up, haven't exceeded max, and enough time has passed
    candidates = Client.objects.filter(
        status__in=[Client.Status.PINGED, Client.Status.FOLLOW_UP],
        has_replied=False,
        followup_count__lt=max_followups,
    ).filter(
        Q(assigned_to=triggering_user) | Q(assigned_to__isnull=True)
        if triggering_user
        else Q()
    ).exclude(email="")

    cc_raw = getattr(settings, "OUTREACH_CC_EMAILS", "")
    cc_emails = [e.strip() for e in cc_raw.split(",") if e.strip()]

    sent_count = 0
    skipped_count = 0
    failed_count = 0
    results = []

    for client in candidates:
        # Determine how long since last contact
        last_contact = client.last_contacted_at
        if not last_contact:
            continue

        followup_num = client.followup_count + 1
        interval_idx = min(followup_num - 1, len(intervals) - 1)
        required_gap = timedelta(days=intervals[interval_idx])

        if (now - last_contact) < required_gap:
            skipped_count += 1
            continue

        subject, body = _generate_followup_email(client, followup_num, sender_profile)
        success, message = _send_single_email(
            recipient_email=client.email,
            subject=subject,
            body=body,
            cc_emails=cc_emails,
            sender_profile=sender_profile,
        )

        if success:
            client.followup_count = followup_num
            client.last_contacted_at = now
            client.status = Client.Status.FOLLOW_UP

            # Calculate next follow-up date
            next_idx = min(followup_num, len(intervals) - 1)
            if followup_num < max_followups:
                client.next_followup_at = now + timedelta(days=intervals[next_idx])
            else:
                client.next_followup_at = None

            client.save(
                update_fields=[
                    "followup_count",
                    "last_contacted_at",
                    "status",
                    "next_followup_at",
                    "updated_at",
                ]
            )

            ActionLog.objects.create(
                team_member=triggering_user,
                client=client,
                notes=f"Follow-up #{followup_num}. Subject: {subject}",
            )

            sent_count += 1
            results.append(
                {
                    "company": client.company_name,
                    "status": "sent",
                    "followup": followup_num,
                }
            )
        else:
            failed_count += 1
            results.append(
                {"company": client.company_name, "status": "failed", "detail": message}
            )

    return {
        "sent": sent_count,
        "skipped": skipped_count,
        "failed": failed_count,
        "details": results,
    }
