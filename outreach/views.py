import csv
import io

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Count, Max, Q, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods

from django.contrib.auth.models import User

from .forms import (
    CampaignLaunchForm,
    ClientForm,
    ClientStatusForm,
    ContactImportForm,
    CreateUserForm,
    EditUserForm,
    LinkedInReachoutForm,
    MailboxSettingsForm,
    OutreachCampaignForm,
)
from .models import (
    CampaignRun,
    Client,
    EmailReply,
    LinkedInReachout,
    OutreachCampaign,
    TeamMember,
)
from .tasks import (
    _DEFAULT_BODY_TPL,
    _DEFAULT_SUBJECT_TPL,
    send_automated_pings,
    scan_inbox_for_replies_task,
    send_followups_task,
)


_MAX_IMPORT_ROWS = 5_000
_MAX_IMPORT_WARNINGS = 20


def _client_scope_for_user(user):
    """Clients a user may view or mutate through the application."""
    clients = Client.objects.all()
    if _is_admin(user):
        return clients
    return clients.filter(Q(assigned_to=user) | Q(assigned_to__isnull=True))


def _get_accessible_client(user, pk):
    return get_object_or_404(_client_scope_for_user(user), pk=pk)


def _campaign_run_scope_for_user(user):
    """Campaign runs are private to their initiator, except for administrators."""
    runs = CampaignRun.objects.all()
    if _is_admin(user):
        return runs
    return runs.filter(triggered_by=user)


def _normalise_email(value):
    return (value or "").strip().lower()


def _csv_cell(row, header_lookup, *candidate_headers):
    """Return the first matching Apollo column using case-insensitive headers."""
    for candidate in candidate_headers:
        actual_header = header_lookup.get(candidate.casefold())
        if actual_header is not None:
            return (row.get(actual_header) or "").strip()
    return ""


def _parse_apollo_csv(file_obj, skip_unverified):
    """
    Parse an Apollo-format CSV uploaded as an InMemoryUploadedFile.
    Returns (rows: list[dict], errors: list[str]).
    Each dict in rows has keys: company_name, contact_person, email, industry.
    """
    rows, errors = [], []

    try:
        file_obj.seek(0)
        text = file_obj.read().decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text))
    except (UnicodeDecodeError, OSError, csv.Error) as exc:
        return [], [f"Could not read file: {exc}"]

    fieldnames = [name for name in (reader.fieldnames or []) if name]
    header_lookup = {name.strip().casefold(): name for name in fieldnames}
    required = {"First Name", "Last Name", "Company Name", "Email", "Industry"}
    missing = {
        name for name in required if name.casefold() not in header_lookup
    }
    if missing:
        return [], [
            f"Missing required columns: {', '.join(sorted(missing))}. "
            "Make sure you exported the file in Apollo's standard contact format."
        ]

    seen_emails = set()
    status_header_present = "email status" in header_lookup

    try:
        for i, row in enumerate(reader, start=2):  # row 1 is the header
            if i > _MAX_IMPORT_ROWS + 1:
                errors.append(
                    f"Import stopped after {_MAX_IMPORT_ROWS:,} data rows. "
                    "Split larger exports into smaller files."
                )
                break

            email = _normalise_email(_csv_cell(row, header_lookup, "Email"))
            if not email:
                errors.append(f"Row {i}: skipped — no email address.")
                continue

            email_status = _csv_cell(row, header_lookup, "Email Status").lower()
            if skip_unverified and status_header_present and email_status != "verified":
                errors.append(
                    f"Row {i} ({email}): skipped — Email Status is not Verified."
                )
                continue

            if email in seen_emails:
                errors.append(f"Row {i} ({email}): skipped — duplicate in this file.")
                continue
            seen_emails.add(email)

            first = _csv_cell(row, header_lookup, "First Name")
            last = _csv_cell(row, header_lookup, "Last Name")
            contact_person = f"{first} {last}".strip() or email

            # Prefer direct numbers over generic/corporate phone fields.
            phone = _csv_cell(
                row,
                header_lookup,
                "Work Direct Phone",
                "Mobile Phone",
                "Phone",
                "Corporate Phone",
            )

            data = {
                "company_name": _csv_cell(row, header_lookup, "Company Name")
                or "Unknown",
                "contact_person": contact_person,
                "email": email,
                "title": _csv_cell(row, header_lookup, "Title"),
                "industry": _csv_cell(row, header_lookup, "Industry") or "Other",
                "phone": phone,
                "city": _csv_cell(row, header_lookup, "City"),
                "state": _csv_cell(row, header_lookup, "State"),
                "country": _csv_cell(row, header_lookup, "Country"),
                "website": _csv_cell(row, header_lookup, "Website"),
                "linkedin_url": _csv_cell(
                    row,
                    header_lookup,
                    "Person Linkedin Url",
                    "Person Linkedin URL",
                    "LinkedIn URL",
                    "Linkedin Url",
                ),
                "keywords": _csv_cell(row, header_lookup, "Keywords"),
            }

            # ORM create/save does not invoke field validators. Validate before any write.
            candidate = Client(**data)
            try:
                candidate.full_clean(validate_unique=False)
            except ValidationError as exc:
                details = "; ".join(
                    f"{field}: {', '.join(messages)}"
                    for field, messages in exc.message_dict.items()
                )
                errors.append(f"Row {i} ({email}): skipped — {details}")
                continue

            rows.append(data)
    except csv.Error as exc:
        errors.append(f"CSV parsing stopped: {exc}")

    return rows, errors


@login_required
@require_http_methods(["GET", "POST"])
def import_contacts(request):
    """
    GET  — render the upload form.
    POST — validate an Apollo CSV, create new Clients, and refresh records that
           already belong to the current user or the shared pool.
    """
    if request.method == "POST":
        form = ContactImportForm(request.POST, request.FILES)
        if form.is_valid():
            skip_unverified = form.cleaned_data["skip_unverified"]
            uploaded = form.cleaned_data["csv_file"]

            parsed_rows, parse_errors = _parse_apollo_csv(uploaded, skip_unverified)

            created_count = 0
            updated_count = 0
            protected_count = 0
            for data in parsed_rows:
                try:
                    with transaction.atomic():
                        existing = (
                            Client.objects.select_for_update()
                            .filter(email__iexact=data["email"])
                            .order_by("pk")
                            .first()
                        )

                        if existing is not None:
                            if existing.assigned_to_id not in (None, request.user.pk):
                                # Do not reveal or reassign another member's contact.
                                protected_count += 1
                                continue

                            for field, value in data.items():
                                setattr(existing, field, value)
                            existing.full_clean()
                            existing.save()
                            updated_count += 1
                        else:
                            client = Client(
                                **data,
                                status=Client.Status.NOT_CONTACTED,
                                assigned_to=request.user,
                            )
                            client.full_clean()
                            client.save()
                            created_count += 1
                except (IntegrityError, ValidationError):
                    parse_errors.append(
                        f"{data['email']}: skipped — the record conflicts with existing data."
                    )

            # Build summary message
            parts = [f"{created_count} contact(s) imported."]
            if updated_count:
                parts.append(f"{updated_count} existing contact(s) updated.")
            if protected_count:
                parts.append(
                    f"{protected_count} contact(s) already belong to another member and were unchanged."
                )
            if parse_errors:
                parts.append(
                    f"{len(parse_errors)} row(s) skipped "
                    f"(see details below)."
                )
            messages.success(request, " ".join(parts))

            if parse_errors:
                for err in parse_errors[:_MAX_IMPORT_WARNINGS]:
                    messages.warning(request, err)
                hidden_error_count = len(parse_errors) - _MAX_IMPORT_WARNINGS
                if hidden_error_count > 0:
                    messages.warning(
                        request,
                        f"{hidden_error_count} additional row warning(s) were omitted.",
                    )

            return redirect("outreach:dashboard")
    else:
        form = ContactImportForm()

    return render(request, "outreach/import_contacts.html", {"form": form})


@login_required
def add_contact(request):
    """Create a single new contact manually — auto-assigns to the current user."""
    if request.method == "POST":
        form = ClientForm(request.POST)
        if form.is_valid():
            client = form.save(commit=False)
            client.assigned_to = request.user
            client.save()
            messages.success(
                request,
                f"Contact <strong>{client.contact_person}</strong> at "
                f"<strong>{client.company_name}</strong> added.",
                extra_tags="safe",
            )
            return redirect("outreach:dashboard")
    else:
        form = ClientForm()
    return render(
        request, "outreach/contact_form.html", {"form": form, "action": "Add"}
    )


@login_required
def edit_contact(request, pk):
    """Edit all fields of an existing contact."""
    client = _get_accessible_client(request.user, pk)
    if request.method == "POST":
        form = ClientForm(request.POST, instance=client)
        if form.is_valid():
            form.save()
            messages.success(
                request,
                f"Contact <strong>{client.company_name}</strong> updated.",
                extra_tags="safe",
            )
            return redirect("outreach:dashboard")
    else:
        form = ClientForm(instance=client)
    return render(
        request,
        "outreach/contact_form.html",
        {"form": form, "action": "Edit", "client": client},
    )


@login_required
def delete_contact(request, pk):
    """Confirm then delete a contact record."""
    client = _get_accessible_client(request.user, pk)
    if request.method == "POST":
        name = f"{client.contact_person} ({client.company_name})"
        client.delete()
        messages.success(
            request, f"Contact <strong>{name}</strong> deleted.", extra_tags="safe"
        )
        return redirect("outreach:dashboard")
    return render(request, "outreach/confirm_delete.html", {"client": client})


class AppLoginView(LoginView):
    """Custom login view using the app's own template."""

    template_name = "outreach/login.html"
    redirect_authenticated_user = True


@login_required
def dashboard(request):
    """
    Main dashboard.
    - Admins: see all clients by default; can filter to a specific member via ?member=<username>
    - Members: see only their own + shared (assigned_to=None) clients by default;
      can also view the full shared pool via ?view=all
    Filterable by status via ?status=
    """
    status_filter = request.GET.get("status", "").strip()
    company_filter = request.GET.get("company", "").strip()
    member_filter = request.GET.get("member", "").strip()  # username, admin only

    is_admin_user = _is_admin(request.user)

    if is_admin_user:
        if member_filter == "__shared__":
            clients = Client.objects.filter(assigned_to__isnull=True)
        elif member_filter:
            try:
                filter_user = User.objects.get(username=member_filter)
                clients = Client.objects.filter(assigned_to=filter_user)
            except User.DoesNotExist:
                clients = Client.objects.all()
                member_filter = ""
        else:
            clients = Client.objects.all()
    else:
        clients = _client_scope_for_user(request.user)

    company_choices = list(
        clients.order_by("company_name").values_list("company_name", flat=True).distinct()
    )

    if status_filter:
        clients = clients.filter(status=status_filter)
    if company_filter:
        clients = clients.filter(company_name__icontains=company_filter)

    dashboard_stats = {
        "total_clients": clients.count(),
        "company_count": clients.values("company_name").distinct().count(),
        "replied_count": clients.filter(has_replied=True).count(),
        "meeting_count": clients.filter(status=Client.Status.MEETING_SET).count(),
        "linkedin_touchpoints": LinkedInReachout.objects.filter(client__in=clients).count(),
    }

    # For admin member-picker dropdown
    all_members = (
        TeamMember.objects.select_related("user").all() if is_admin_user else []
    )

    clients = clients.select_related("assigned_to").annotate(
            linkedin_reachouts_count=Count("linkedin_reachouts"),
            last_linkedin_at=Max("linkedin_reachouts__happened_at"),
        ).order_by("company_name", "pk")
    page_obj = Paginator(clients, 50).get_page(request.GET.get("page"))
    pagination_params = request.GET.copy()
    pagination_params.pop("page", None)

    context = {
        "clients": page_obj,
        "page_obj": page_obj,
        "pagination_query": pagination_params.urlencode(),
        "status_choices": Client.Status.choices,
        "active_filter": status_filter,
        "company_filter": company_filter,
        "company_choices": company_choices,
        "dashboard_stats": dashboard_stats,
        "is_admin_user": is_admin_user,
        "member_filter": member_filter,
        "all_members": all_members,
    }
    return render(request, "outreach/dashboard.html", context)


@login_required
def update_client_status(request, pk):
    """Allows a team member to update a single client's status."""
    client = _get_accessible_client(request.user, pk)
    if request.method == "POST":
        form = ClientStatusForm(request.POST, instance=client)
        if form.is_valid():
            form.save()
            messages.success(
                request,
                f"Status for <strong>{client.company_name}</strong> updated to "
                f"<strong>{client.status}</strong>.",
                extra_tags="safe",
            )
            return redirect("outreach:dashboard")
    else:
        form = ClientStatusForm(instance=client)
    return render(
        request, "outreach/update_status.html", {"form": form, "client": client}
    )


@login_required
@require_http_methods(["GET", "POST"])
def run_campaign(request):
    """
    GET  – shows a form to select a campaign and preview target clients.
    POST – validates the bulk-send confirmation, creates an owned CampaignRun,
           queues the Celery task, and redirects to the live progress page.
    """
    campaigns = list(OutreachCampaign.objects.all())
    eligible_clients = _client_scope_for_user(request.user).filter(
        status=Client.Status.NOT_CONTACTED
    )
    not_contacted_count = eligible_clients.count()
    form = CampaignLaunchForm(request.POST or None)

    if request.method == "POST":
        mailbox_ready, mailbox_error = _validate_user_mailbox(request.user)
        if not mailbox_ready:
            messages.error(request, mailbox_error)
            return redirect("outreach:mailbox_settings")
        if form.is_valid():
            campaign = form.cleaned_data["campaign_id"]
            campaign_clients = eligible_clients
            if (
                campaign
                and campaign.target_industry
                and campaign.target_industry.strip().casefold() not in {"all", "any", "*"}
            ):
                campaign_clients = campaign_clients.filter(
                    industry__iexact=campaign.target_industry.strip()
                )
            recipient_count = campaign_clients.count()

            if recipient_count == 0:
                form.add_error(
                    "campaign_id",
                    "No eligible contacts match this campaign's target industry.",
                )
            else:
                run = CampaignRun.objects.create(
                    total=recipient_count,
                    triggered_by=request.user,
                    campaign=campaign,
                    operation=CampaignRun.Operation.INITIAL,
                )
                try:
                    send_automated_pings.delay(
                        campaign_id=campaign.pk if campaign else None,
                        triggered_by_user_id=request.user.pk,
                        run_id=run.pk,
                    )
                except Exception as exc:  # broker unavailable / enqueue failure
                    run.status = CampaignRun.RunStatus.FAILED
                    run.current_step = "queueing"
                    run.error_message = f"Could not queue campaign: {exc}"
                    run.finished_at = timezone.now()
                    run.save(
                        update_fields=[
                            "status",
                            "current_step",
                            "error_message",
                            "finished_at",
                        ]
                    )
                return redirect("outreach:campaign_progress_page", run_id=run.pk)

    campaign_previews = {
        str(campaign.pk): {
            "subject": campaign.subject_template,
            "body": campaign.email_template,
            "recipient_count": (
                eligible_clients.filter(
                    industry__iexact=campaign.target_industry.strip()
                ).count()
                if campaign.target_industry
                and campaign.target_industry.strip().casefold() not in {"all", "any", "*"}
                else not_contacted_count
            ),
        }
        for campaign in campaigns
    }

    context = {
        "form": form,
        "campaigns": campaigns,
        "campaign_previews": campaign_previews,
        "not_contacted_count": not_contacted_count,
        "default_subject": _DEFAULT_SUBJECT_TPL,
        "default_body": _DEFAULT_BODY_TPL,
        "ai_enabled": bool(getattr(settings, "GEMINI_API_KEY", "")),
        "fallback_enabled": bool(
            getattr(settings, "ALLOW_STATIC_EMAIL_FALLBACK", False)
        ),
    }
    return render(request, "outreach/run_campaign.html", context)


@login_required
@require_GET
def campaign_progress_api(request, run_id):
    """JSON endpoint polled by the progress page."""
    run = get_object_or_404(_campaign_run_scope_for_user(request.user), pk=run_id)
    return JsonResponse(
        {
            "status": run.status,
            "operation": run.operation,
            "total": run.total,
            "processed": run.processed,
            "sent": run.sent,
            "failed": run.failed,
            "fallback_count": run.fallback_count,
            "current_company": run.current_company,
            "current_step": run.current_step,
            "error_message": run.error_message,
            "log": run.log,
            "started_at": run.started_at.isoformat() if run.started_at else None,
            "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        }
    )


@login_required
@require_GET
def campaign_progress_page(request, run_id):
    """Renders the live progress bar page for a campaign run."""
    run = get_object_or_404(_campaign_run_scope_for_user(request.user), pk=run_id)
    return render(request, "outreach/campaign_progress.html", {"run": run})


@login_required
def scan_replies(request):
    """
    GET  — show the scan page with current reply stats.
    POST — run IMAP inbox scan, show results.
    """

    if request.method == "POST":
        mailbox_ready, mailbox_error = _validate_user_mailbox(request.user)
        if not mailbox_ready:
            messages.error(request, mailbox_error)
            return redirect("outreach:mailbox_settings")

        run = CampaignRun.objects.create(
            operation=CampaignRun.Operation.REPLY_SCAN,
            triggered_by=request.user,
        )
        try:
            scan_inbox_for_replies_task.delay(
                triggered_by_user_id=request.user.pk, run_id=run.pk
            )
        except Exception as exc:
            run.status = CampaignRun.RunStatus.FAILED
            run.current_step = "queueing"
            run.error_message = f"Could not queue reply scan: {exc}"
            run.finished_at = timezone.now()
            run.save(update_fields=["status", "current_step", "error_message", "finished_at"])
        return redirect("outreach:campaign_progress_page", run_id=run.pk)

    # Stats for the GET page
    total_pinged = Client.objects.filter(
        status__in=[Client.Status.PINGED, Client.Status.FOLLOW_UP]
    ).filter(Q(assigned_to=request.user) | Q(assigned_to__isnull=True)).count()
    total_replied = Client.objects.filter(has_replied=True).filter(
        Q(assigned_to=request.user) | Q(assigned_to__isnull=True)
    ).count()
    recent_replies = EmailReply.objects.select_related("client").filter(
        client__in=_client_scope_for_user(request.user)
    )[:20]

    return render(
        request,
        "outreach/scan_replies.html",
        {
            "total_pinged": total_pinged,
            "total_replied": total_replied,
            "recent_replies": recent_replies,
        },
    )


@login_required
def client_thread(request, pk):
    """Show the full email thread and sentiment for a single client."""
    client = _get_accessible_client(request.user, pk)
    replies = client.replies.all()
    action_logs = client.action_logs.select_related("team_member", "campaign")[:20]
    outbound_emails = client.outbound_emails.select_related(
        "team_member", "campaign", "campaign_run"
    )[:50]
    linkedin_reachouts = client.linkedin_reachouts.select_related("team_member")[:20]
    return render(
        request,
        "outreach/client_thread.html",
        {
            "client": client,
            "replies": replies,
            "action_logs": action_logs,
            "outbound_emails": outbound_emails,
            "linkedin_reachouts": linkedin_reachouts,
        },
    )


@login_required
def log_linkedin_reachout(request, pk):
    """Manually record a LinkedIn outreach touchpoint for a client."""
    client = _get_accessible_client(request.user, pk)
    recent_reachouts = client.linkedin_reachouts.select_related("team_member")[:10]

    if request.method == "POST":
        form = LinkedInReachoutForm(request.POST)
        if form.is_valid():
            reachout = form.save(commit=False)
            reachout.client = client
            reachout.team_member = request.user
            reachout.save()
            messages.success(
                request,
                f"LinkedIn activity logged for <strong>{client.company_name}</strong>.",
                extra_tags="safe",
            )
            return redirect("outreach:log_linkedin_reachout", pk=client.pk)
    else:
        form = LinkedInReachoutForm()

    return render(
        request,
        "outreach/linkedin_reachout_form.html",
        {
            "client": client,
            "form": form,
            "recent_reachouts": recent_reachouts,
        },
    )


@login_required
def run_followups(request):
    """
    GET  — show follow-up candidates and summary.
    POST — send follow-up emails to eligible clients.
    """
    from datetime import timedelta

    if request.method == "POST":
        mailbox_ready, mailbox_error = _validate_user_mailbox(request.user)
        if not mailbox_ready:
            messages.error(request, mailbox_error)
            return redirect("outreach:mailbox_settings")

        candidate_count = _client_scope_for_user(request.user).filter(
            status__in=[Client.Status.PINGED, Client.Status.FOLLOW_UP],
            has_replied=False,
        ).exclude(email="").count()
        run = CampaignRun.objects.create(
            operation=CampaignRun.Operation.FOLLOWUP,
            triggered_by=request.user,
            total=candidate_count,
        )
        try:
            send_followups_task.delay(
                triggered_by_user_id=request.user.pk, run_id=run.pk
            )
        except Exception as exc:
            run.status = CampaignRun.RunStatus.FAILED
            run.current_step = "queueing"
            run.error_message = f"Could not queue follow-ups: {exc}"
            run.finished_at = timezone.now()
            run.save(update_fields=["status", "current_step", "error_message", "finished_at"])
        return redirect("outreach:campaign_progress_page", run_id=run.pk)

    # Eligible candidates
    from .tasks import _FOLLOWUP_INTERVALS, _MAX_FOLLOWUPS

    max_followups = getattr(settings, "MAX_FOLLOWUPS", _MAX_FOLLOWUPS)
    candidates = (
        Client.objects.filter(
            status__in=[Client.Status.PINGED, Client.Status.FOLLOW_UP],
            has_replied=False,
            followup_count__lt=max_followups,
        )
        .filter(Q(assigned_to=request.user) | Q(assigned_to__isnull=True))
        .exclude(email="")
        .order_by("last_contacted_at")
    )

    return render(
        request,
        "outreach/run_followups.html",
        {
            "candidates": candidates,
            "max_followups": max_followups,
        },
    )


# ---------------------------------------------------------------------------
# Team / User management
# ---------------------------------------------------------------------------


def _is_admin(user):
    """Return True if the user has an Admin-role TeamMember profile or is a superuser."""
    if user.is_superuser:
        return True
    profile = getattr(user, "profile", None)
    return profile and profile.role == TeamMember.Role.ADMIN


def _validate_user_mailbox(user):
    profile = getattr(user, "profile", None)
    if not profile:
        return False, "Create your team profile mailbox settings before sending or scanning mail."
    mailbox_email = profile.mailbox_email or user.email
    if not mailbox_email:
        return False, "Set your mailbox email in My Mailbox before sending or scanning mail."
    if not profile.mailbox_app_password:
        return False, "Set your mailbox app password in My Mailbox before sending or scanning mail."
    return True, ""


@login_required
def campaign_list(request):
    """List editable campaign templates for application administrators."""
    if not _is_admin(request.user):
        messages.error(request, "Only admins can manage campaign templates.")
        return redirect("outreach:dashboard")
    campaigns = OutreachCampaign.objects.all()
    return render(
        request, "outreach/campaign_list.html", {"campaigns": campaigns}
    )


@login_required
def campaign_edit(request, pk=None):
    """Create or update a campaign template."""
    if not _is_admin(request.user):
        messages.error(request, "Only admins can manage campaign templates.")
        return redirect("outreach:dashboard")
    campaign = get_object_or_404(OutreachCampaign, pk=pk) if pk else None
    form = OutreachCampaignForm(request.POST or None, instance=campaign)
    if request.method == "POST" and form.is_valid():
        saved = form.save()
        messages.success(request, f"Campaign template '{saved.name}' saved.")
        return redirect("outreach:campaign_list")
    return render(
        request,
        "outreach/campaign_form.html",
        {"form": form, "campaign": campaign},
    )


@login_required
def team_list(request):
    """List all team members. Only admins can access."""
    if not _is_admin(request.user):
        messages.error(request, "Only admins can manage team members.")
        return redirect("outreach:dashboard")

    members = TeamMember.objects.select_related("user").all()
    return render(request, "outreach/team_list.html", {"members": members})


@login_required
def team_add(request):
    """Create a new user + TeamMember profile. Admin only."""
    if not _is_admin(request.user):
        messages.error(request, "Only admins can add team members.")
        return redirect("outreach:dashboard")

    if request.method == "POST":
        form = CreateUserForm(request.POST)
        if form.is_valid():
            user = User.objects.create_user(
                username=form.cleaned_data["username"],
                email=form.cleaned_data["email"],
                password=form.cleaned_data["password"],
                first_name=form.cleaned_data["first_name"],
                last_name=form.cleaned_data["last_name"],
            )
            TeamMember.objects.create(
                user=user,
                role=form.cleaned_data["role"],
                sender_name=form.cleaned_data["sender_name"],
                sender_role=form.cleaned_data["sender_role"],
                mailbox_email=form.cleaned_data["mailbox_email"],
                mailbox_app_password=form.cleaned_data["mailbox_app_password"],
            )
            messages.success(
                request,
                f"Account created for <strong>{user.get_full_name()}</strong> "
                f'({form.cleaned_data["role"]}).',
                extra_tags="safe",
            )
            return redirect("outreach:team_list")
    else:
        form = CreateUserForm()

    return render(request, "outreach/team_form.html", {"form": form, "editing": False})


@login_required
def team_edit(request, pk):
    """Edit an existing team member. Admin only."""
    if not _is_admin(request.user):
        messages.error(request, "Only admins can edit team members.")
        return redirect("outreach:dashboard")

    profile = get_object_or_404(TeamMember, pk=pk)
    target_user = profile.user

    if request.method == "POST":
        form = EditUserForm(request.POST, user=target_user)
        if form.is_valid():
            target_user.first_name = form.cleaned_data["first_name"]
            target_user.last_name = form.cleaned_data["last_name"]
            target_user.email = form.cleaned_data["email"]
            target_user.is_active = form.cleaned_data["is_active"]
            target_user.save()
            if not target_user.is_active:
                Client.objects.filter(assigned_to=target_user).update(assigned_to=None)
            profile.role = form.cleaned_data["role"]
            profile.sender_name = form.cleaned_data["sender_name"]
            profile.sender_role = form.cleaned_data["sender_role"]
            profile.mailbox_email = form.cleaned_data["mailbox_email"]
            if form.cleaned_data["mailbox_app_password"]:
                profile.mailbox_app_password = form.cleaned_data["mailbox_app_password"]
            profile.save()
            messages.success(
                request,
                f"Updated <strong>{target_user.get_full_name()}</strong>.",
                extra_tags="safe",
            )
            return redirect("outreach:team_list")
    else:
        form = EditUserForm(
            initial={
                "first_name": target_user.first_name,
                "last_name": target_user.last_name,
                "email": target_user.email,
                "sender_name": profile.sender_name,
                "sender_role": profile.sender_role,
                "mailbox_email": profile.mailbox_email,
                "role": profile.role,
                "is_active": target_user.is_active,
            }
        )

    return render(
        request,
        "outreach/team_form.html",
        {"form": form, "editing": True, "target_user": target_user, "profile": profile},
    )


@login_required
def mailbox_settings(request):
    """Allow a user to configure their sender identity and mailbox credentials."""
    profile, _ = TeamMember.objects.get_or_create(user=request.user)

    if request.method == "POST":
        form = MailboxSettingsForm(request.POST)
        if form.is_valid():
            profile.sender_name = form.cleaned_data["sender_name"]
            profile.sender_role = form.cleaned_data["sender_role"]
            profile.mailbox_email = form.cleaned_data["mailbox_email"]
            if form.cleaned_data["mailbox_app_password"]:
                profile.mailbox_app_password = form.cleaned_data["mailbox_app_password"]
            profile.save()
            messages.success(request, "Mailbox settings updated.")
            return redirect("outreach:mailbox_settings")
    else:
        form = MailboxSettingsForm(
            initial={
                "sender_name": profile.sender_name,
                "sender_role": profile.sender_role,
                "mailbox_email": profile.mailbox_email,
            }
        )

    return render(
        request,
        "outreach/mailbox_settings.html",
        {"form": form, "profile": profile},
    )


@login_required
def team_delete(request, pk):
    """Deactivate (not delete) a team member. Admin only."""
    if not _is_admin(request.user):
        messages.error(request, "Only admins can remove team members.")
        return redirect("outreach:dashboard")

    profile = get_object_or_404(TeamMember, pk=pk)
    target_user = profile.user

    if target_user == request.user:
        messages.error(request, "You cannot deactivate your own account.")
        return redirect("outreach:team_list")

    if request.method == "POST":
        with transaction.atomic():
            target_user.is_active = False
            target_user.save(update_fields=["is_active"])
            Client.objects.filter(assigned_to=target_user).update(assigned_to=None)
        name = target_user.get_full_name() or target_user.username
        messages.success(
            request,
            f"Account for <strong>{name}</strong> has been deactivated.",
            extra_tags="safe",
        )
        return redirect("outreach:team_list")

    return render(
        request,
        "outreach/team_confirm_deactivate.html",
        {"target_user": target_user, "profile": profile},
    )


@login_required
def leaderboard(request):
    """
    Admin-only leaderboard: per-member outreach stats.
    """
    if not _is_admin(request.user):
        messages.error(request, "Only admins can view the leaderboard.")
        return redirect("outreach:dashboard")

    members = TeamMember.objects.select_related("user").filter(user__is_active=True)

    stats = []
    for m in members:
        u = m.user
        qs = Client.objects.filter(assigned_to=u)
        linkedin_qs = LinkedInReachout.objects.filter(team_member=u)
        row = {
            "member": m,
            "total": qs.count(),
            "contacted": qs.exclude(status=Client.Status.NOT_CONTACTED).count(),
            "pinged": qs.filter(status=Client.Status.PINGED).count(),
            "replied": qs.filter(
                Q(status=Client.Status.REPLIED) | Q(has_replied=True)
            ).count(),
            "meeting_set": qs.filter(status=Client.Status.MEETING_SET).count(),
            "rejected": qs.filter(status=Client.Status.REJECTED).count(),
            "followups_sent": qs.aggregate(total=Sum("followup_count"))["total"] or 0,
            "linkedin_reachouts": linkedin_qs.count(),
            "linkedin_clients": linkedin_qs.values("client_id").distinct().count(),
        }
        row["outreach_actions"] = (
            row["contacted"] + row["followups_sent"] + row["linkedin_reachouts"]
        )
        # reply rate (avoid div/zero)
        row["reply_rate"] = (
            round(row["replied"] / row["contacted"] * 100)
            if row["contacted"] > 0
            else 0
        )
        stats.append(row)

    # Sort by total outreach activity, then replies and meetings.
    stats.sort(key=lambda r: (-r["outreach_actions"], -r["replied"], -r["meeting_set"]))

    # Shared pool stats (assigned_to=None)
    shared_qs = Client.objects.filter(assigned_to__isnull=True)
    shared_stats = {
        "total": shared_qs.count(),
        "contacted": shared_qs.exclude(status=Client.Status.NOT_CONTACTED).count(),
        "replied": shared_qs.filter(has_replied=True).count(),
        "meeting_set": shared_qs.filter(status=Client.Status.MEETING_SET).count(),
        "linkedin_reachouts": LinkedInReachout.objects.filter(
            client__assigned_to__isnull=True
        ).count(),
    }

    return render(
        request,
        "outreach/leaderboard.html",
        {"member_stats": stats, "shared": shared_stats},
    )
