import csv
import io
from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView
from django.db.models import Count, Max, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from django.contrib.auth.models import User

from .forms import (
    ClientForm,
    ClientStatusForm,
    ContactImportForm,
    CreateUserForm,
    EditUserForm,
    LinkedInReachoutForm,
    MailboxSettingsForm,
)
from .models import (
    ActionLog,
    CampaignRun,
    Client,
    EmailReply,
    FollowupRun,
    LinkedInReachout,
    OutreachCampaign,
    ScanRun,
    TeamMember,
)
from .tasks import (
    _DEFAULT_BODY_TPL,
    _DEFAULT_SUBJECT_TPL,
    scan_inbox_for_replies,
    send_automated_pings,
    send_followups,
)


def _parse_apollo_csv(file_obj, skip_unverified):
    """
    Parse an Apollo-format CSV uploaded as an InMemoryUploadedFile.
    Returns (rows: list[dict], errors: list[str]).
    Each dict in rows has keys: company_name, contact_person, email, industry.
    """
    rows, errors = [], []

    try:
        text = io.TextIOWrapper(file_obj, encoding="utf-8-sig", errors="replace")
        reader = csv.DictReader(text)
    except Exception as exc:
        return [], [f"Could not read file: {exc}"]

    required = {"First Name", "Last Name", "Company Name", "Email", "Industry"}
    if not required.issubset(set(reader.fieldnames or [])):
        missing = required - set(reader.fieldnames or [])
        return [], [
            f"Missing required columns: {', '.join(sorted(missing))}. "
            "Make sure you exported the file in Apollo's standard contact format."
        ]

    for i, row in enumerate(reader, start=2):  # row 1 is the header
        email = row.get("Email", "").strip()
        if not email:
            errors.append(f"Row {i}: skipped — no email address.")
            continue

        if (
            skip_unverified
            and row.get("Email Status", "").strip().lower() != "verified"
        ):
            errors.append(f"Row {i} ({email}): skipped — Email Status is not Verified.")
            continue

        first = row.get("First Name", "").strip()
        last = row.get("Last Name", "").strip()
        contact_person = f"{first} {last}".strip() or email

        # Prefer "Work Direct Phone" over generic "Phone" if both present
        phone = (
            row.get("Work Direct Phone", "").strip()
            or row.get("Phone", "").strip()
            or row.get("Mobile Phone", "").strip()
        )

        rows.append(
            {
                "company_name": row.get("Company Name", "").strip() or "Unknown",
                "contact_person": contact_person,
                "email": email,
                "title": row.get("Title", "").strip(),
                "industry": row.get("Industry", "").strip() or "Other",
                "phone": phone,
                "city": row.get("City", "").strip(),
                "state": row.get("State", "").strip(),
                "country": row.get("Country", "").strip(),
                "website": row.get("Website", "").strip(),
                "linkedin_url": row.get("LinkedIn URL", "").strip(),
                "keywords": row.get("Keywords", "").strip(),
            }
        )

    return rows, errors


@login_required
def import_contacts(request):
    """
    GET  — render the upload form.
    POST — parse the Apollo CSV, bulk-create new Clients (skip duplicates),
           and redirect to the dashboard with a summary flash message.
    """
    if request.method == "POST":
        form = ContactImportForm(request.POST, request.FILES)
        if form.is_valid():
            skip_unverified = form.cleaned_data["skip_unverified"]
            uploaded = request.FILES["csv_file"]

            parsed_rows, parse_errors = _parse_apollo_csv(uploaded, skip_unverified)

            created_count = 0
            duplicate_count = 0
            for data in parsed_rows:
                obj, created = Client.objects.get_or_create(
                    email=data["email"],
                    defaults={
                        "company_name": data["company_name"],
                        "contact_person": data["contact_person"],
                        "title": data.get("title", ""),
                        "industry": data["industry"],
                        "phone": data.get("phone", ""),
                        "city": data.get("city", ""),
                        "state": data.get("state", ""),
                        "country": data.get("country", ""),
                        "website": data.get("website", ""),
                        "linkedin_url": data.get("linkedin_url", ""),
                        "keywords": data.get("keywords", ""),
                        "status": Client.Status.NOT_CONTACTED,
                        "assigned_to": request.user,
                    },
                )
                if created:
                    created_count += 1
                else:
                    duplicate_count += 1

            # Build summary message
            parts = [f"<strong>{created_count}</strong> contact(s) imported."]
            if duplicate_count:
                parts.append(
                    f"<strong>{duplicate_count}</strong> duplicate(s) skipped."
                )
            if parse_errors:
                parts.append(
                    f"<strong>{len(parse_errors)}</strong> row(s) skipped "
                    f"(see details below)."
                )
            messages.success(request, " ".join(parts), extra_tags="safe")

            if parse_errors:
                for err in parse_errors:
                    messages.warning(request, err)

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
    client = get_object_or_404(Client, pk=pk)
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
    client = get_object_or_404(Client, pk=pk)
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
    view_mode = request.GET.get("view", "").strip()  # "mine" or "all" or ""
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
        # Member: show own + shared unless "all" requested
        if view_mode == "all":
            clients = Client.objects.filter(
                Q(assigned_to=request.user) | Q(assigned_to__isnull=True)
            )
        else:
            clients = Client.objects.filter(
                Q(assigned_to=request.user) | Q(assigned_to__isnull=True)
            )

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

    context = {
        "clients": clients.select_related("assigned_to").annotate(
            linkedin_reachouts_count=Count("linkedin_reachouts"),
            last_linkedin_at=Max("linkedin_reachouts__happened_at"),
        ),
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
    client = get_object_or_404(Client, pk=pk)
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
def run_campaign(request):
    """
    GET  – shows a form to select a campaign and preview target clients.
    POST – creates a CampaignRun, dispatches the task to Celery, and
           redirects to the live progress page.
    """
    from django.conf import settings as dj_settings

    campaigns = OutreachCampaign.objects.all()
    not_contacted_count = Client.objects.filter(
        status=Client.Status.NOT_CONTACTED
    ).filter(Q(assigned_to=request.user) | Q(assigned_to__isnull=True)).count()

    if request.method == "POST":
        mailbox_ready, mailbox_error = _validate_user_mailbox(request.user)
        if not mailbox_ready:
            messages.error(request, mailbox_error)
            return redirect("outreach:mailbox_settings")

        campaign_id_raw = request.POST.get("campaign_id") or None
        campaign_id = int(campaign_id_raw) if campaign_id_raw else None

        # Create a CampaignRun row for progress tracking
        run = CampaignRun.objects.create(total=not_contacted_count)

        # Dispatch to Celery. Previously this spawned a raw daemon thread,
        # which was silently killed (with no resumption and no failure record)
        # by any web-process restart mid-batch. .delay() queues a real,
        # durable task instead — see CELERY_TASK_ACKS_LATE in settings.py for
        # what happens if the worker itself dies mid-task.
        send_automated_pings.delay(
            campaign_id=campaign_id,
            triggered_by_user_id=request.user.pk,
            run_id=run.pk,
        )

        return redirect("outreach:campaign_progress_page", run_id=run.pk)

    context = {
        "campaigns": campaigns,
        "not_contacted_count": not_contacted_count,
        "default_subject": _DEFAULT_SUBJECT_TPL,
        "default_body": _DEFAULT_BODY_TPL,
        "ai_enabled": bool(getattr(dj_settings, "GOOGLE_API_KEY", "")),
    }
    return render(request, "outreach/run_campaign.html", context)


@login_required
def campaign_progress_api(request, run_id):
    """JSON endpoint polled by the progress page."""
    from django.http import JsonResponse

    run = get_object_or_404(CampaignRun, pk=run_id)
    return JsonResponse(_run_progress_json(run))


@login_required
def campaign_progress_page(request, run_id):
    """Renders the live progress bar page for a campaign run."""
    run = get_object_or_404(CampaignRun, pk=run_id)
    api_url = reverse("outreach:campaign_progress_api", kwargs={"run_id": run.pk})
    return render(
        request,
        "outreach/campaign_progress.html",
        {"run": run, "run_label": "Campaign", "api_url": api_url},
    )


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

        # Dispatched to Celery instead of running inline in this request —
        # a large inbox scan (IMAP fetch + a Gemini sentiment call per
        # message) previously risked hitting Gunicorn's default 30s worker
        # timeout when run synchronously here.
        run = ScanRun.objects.create()
        scan_inbox_for_replies.delay(triggered_by_user_id=request.user.pk, run_id=run.pk)
        return redirect("outreach:scan_progress_page", run_id=run.pk)

    # Stats for the GET page
    total_pinged = Client.objects.filter(
        status__in=[Client.Status.PINGED, Client.Status.FOLLOW_UP]
    ).filter(Q(assigned_to=request.user) | Q(assigned_to__isnull=True)).count()
    total_replied = Client.objects.filter(has_replied=True).filter(
        Q(assigned_to=request.user) | Q(assigned_to__isnull=True)
    ).count()
    recent_replies = EmailReply.objects.select_related("client").filter(
        Q(client__assigned_to=request.user) | Q(client__assigned_to__isnull=True)
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


def _run_progress_json(run):
    return {
        "status": run.status,
        "total": run.total,
        "processed": run.processed,
        "sent": run.sent,
        "failed": run.failed,
        "current_company": run.current_company,
        "current_step": run.current_step,
        "log": run.log,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
    }


@login_required
def scan_progress_api(request, run_id):
    """JSON endpoint polled by the reply-scan progress page."""
    from django.http import JsonResponse

    run = get_object_or_404(ScanRun, pk=run_id)
    return JsonResponse(_run_progress_json(run))


@login_required
def scan_progress_page(request, run_id):
    """Renders the live progress bar page for a reply-scan run."""
    run = get_object_or_404(ScanRun, pk=run_id)
    api_url = reverse("outreach:scan_progress_api", kwargs={"run_id": run.pk})
    return render(
        request,
        "outreach/campaign_progress.html",
        {"run": run, "run_label": "Reply Scan", "api_url": api_url},
    )


@login_required
def client_thread(request, pk):
    """Show the full email thread and sentiment for a single client."""
    client = get_object_or_404(Client, pk=pk)
    replies = client.replies.all()
    action_logs = client.action_logs.select_related("team_member", "campaign")[:20]
    linkedin_reachouts = client.linkedin_reachouts.select_related("team_member")[:20]
    return render(
        request,
        "outreach/client_thread.html",
        {
            "client": client,
            "replies": replies,
            "action_logs": action_logs,
            "linkedin_reachouts": linkedin_reachouts,
        },
    )


@login_required
def log_linkedin_reachout(request, pk):
    """Manually record a LinkedIn outreach touchpoint for a client."""
    client = get_object_or_404(Client, pk=pk)
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
    if request.method == "POST":
        mailbox_ready, mailbox_error = _validate_user_mailbox(request.user)
        if not mailbox_ready:
            messages.error(request, mailbox_error)
            return redirect("outreach:mailbox_settings")

        # Dispatched to Celery instead of running inline in this request —
        # each follow-up involves a Gemini call plus an SMTP send, so a
        # batch of any size risked hitting Gunicorn's default 30s timeout
        # when this ran synchronously here.
        run = FollowupRun.objects.create()
        send_followups.delay(triggered_by_user_id=request.user.pk, run_id=run.pk)
        return redirect("outreach:followup_progress_page", run_id=run.pk)

    # Eligible candidates
    from .tasks import _MAX_FOLLOWUPS

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


@login_required
def followup_progress_api(request, run_id):
    """JSON endpoint polled by the follow-up progress page."""
    from django.http import JsonResponse

    run = get_object_or_404(FollowupRun, pk=run_id)
    return JsonResponse(_run_progress_json(run))


@login_required
def followup_progress_page(request, run_id):
    """Renders the live progress bar page for a follow-up run."""
    run = get_object_or_404(FollowupRun, pk=run_id)
    api_url = reverse("outreach:followup_progress_api", kwargs={"run_id": run.pk})
    return render(
        request,
        "outreach/campaign_progress.html",
        {"run": run, "run_label": "Follow-up", "api_url": api_url},
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
        form = EditUserForm(request.POST)
        if form.is_valid():
            target_user.first_name = form.cleaned_data["first_name"]
            target_user.last_name = form.cleaned_data["last_name"]
            target_user.email = form.cleaned_data["email"]
            target_user.is_active = form.cleaned_data["is_active"]
            target_user.save()
            profile.role = form.cleaned_data["role"]
            profile.sender_name = form.cleaned_data["sender_name"]
            profile.sender_role = form.cleaned_data["sender_role"]
            profile.mailbox_email = form.cleaned_data["mailbox_email"]
            # Only overwrite when a new password was actually submitted —
            # previously a blank submission always overwrote, silently
            # wiping the stored password whenever an admin edited a member
            # without intending to touch their mailbox credentials.
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
                # Deliberately not prefilling mailbox_app_password — echoing
                # the stored secret back into page HTML on every edit-form
                # load is exactly the exposure this field's encryption is
                # meant to reduce. The placeholder tells the admin blank
                # means "leave unchanged".
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
                # Not prefilling mailbox_app_password — see team_edit above.
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
        target_user.is_active = False
        target_user.save()
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


@login_required
def reliability_dashboard(request):
    """
    Admin-only view surfacing how often Gemini generation silently falls back
    to the static template, and how often SMTP sends fail — data that used
    to be invisible outside raw log lines / CampaignRun.log JSON.
    """
    if not _is_admin(request.user):
        messages.error(request, "Only admins can view the reliability dashboard.")
        return redirect("outreach:dashboard")

    window_start = timezone.now() - timedelta(days=30)

    action_logs = ActionLog.objects.filter(emailed_at__gte=window_start)
    total_sent = action_logs.count()
    ai_used_count = action_logs.filter(ai_used=True).count()
    fallback_count = total_sent - ai_used_count
    overall_fallback_rate = (
        round(fallback_count / total_sent * 100, 1) if total_sent else None
    )

    recent_fallbacks = (
        action_logs.filter(ai_used=False)
        .select_related("client", "team_member")
        .order_by("-emailed_at")[:25]
    )

    def _run_rows(queryset, label):
        rows = []
        for run in queryset.order_by("-started_at")[:10]:
            rows.append(
                {
                    "kind": label,
                    "pk": run.pk,
                    "status": run.status,
                    "started_at": run.started_at,
                    "processed": run.processed,
                    "total": run.total,
                    "sent": run.sent,
                    "failed": run.failed,
                    "ai_fallback_rate": run.ai_fallback_rate,
                    "smtp_failure_rate": run.smtp_failure_rate,
                }
            )
        return rows

    run_rows = (
        _run_rows(CampaignRun.objects.all(), "Campaign")
        + _run_rows(FollowupRun.objects.all(), "Follow-up")
        + _run_rows(ScanRun.objects.all(), "Reply Scan")
    )
    run_rows.sort(key=lambda r: r["started_at"] or timezone.now(), reverse=True)

    stuck_clients = Client.objects.filter(
        status=Client.Status.SENDING,
        updated_at__lt=timezone.now() - timedelta(minutes=15),
    )

    return render(
        request,
        "outreach/reliability.html",
        {
            "window_days": 30,
            "total_sent": total_sent,
            "ai_used_count": ai_used_count,
            "fallback_count": fallback_count,
            "overall_fallback_rate": overall_fallback_rate,
            "recent_fallbacks": recent_fallbacks,
            "run_rows": run_rows[:20],
            "stuck_clients": stuck_clients,
        },
    )
