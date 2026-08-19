from django.urls import path

from . import views

app_name = "outreach"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("contacts/add/", views.add_contact, name="add_contact"),
    path("contacts/import/", views.import_contacts, name="import_contacts"),
    path("client/<int:pk>/edit/", views.edit_contact, name="edit_contact"),
    path("client/<int:pk>/delete/", views.delete_contact, name="delete_contact"),
    path(
        "client/<int:pk>/update-status/",
        views.update_client_status,
        name="update_client_status",
    ),
    path("campaigns/run/", views.run_campaign, name="run_campaign"),
    path(
        "campaigns/progress/<int:run_id>/",
        views.campaign_progress_page,
        name="campaign_progress_page",
    ),
    path(
        "campaigns/progress/<int:run_id>/api/",
        views.campaign_progress_api,
        name="campaign_progress_api",
    ),
    path("replies/scan/", views.scan_replies, name="scan_replies"),
    path(
        "replies/scan/progress/<int:run_id>/",
        views.scan_progress_page,
        name="scan_progress_page",
    ),
    path(
        "replies/scan/progress/<int:run_id>/api/",
        views.scan_progress_api,
        name="scan_progress_api",
    ),
    path("client/<int:pk>/thread/", views.client_thread, name="client_thread"),
    path("mailbox/", views.mailbox_settings, name="mailbox_settings"),
    path(
        "client/<int:pk>/linkedin/",
        views.log_linkedin_reachout,
        name="log_linkedin_reachout",
    ),
    path("followups/", views.run_followups, name="run_followups"),
    path(
        "followups/progress/<int:run_id>/",
        views.followup_progress_page,
        name="followup_progress_page",
    ),
    path(
        "followups/progress/<int:run_id>/api/",
        views.followup_progress_api,
        name="followup_progress_api",
    ),
    path("team/", views.team_list, name="team_list"),
    path("team/add/", views.team_add, name="team_add"),
    path("team/<int:pk>/edit/", views.team_edit, name="team_edit"),
    path("team/<int:pk>/delete/", views.team_delete, name="team_delete"),
    path("leaderboard/", views.leaderboard, name="leaderboard"),
    path("reliability/", views.reliability_dashboard, name="reliability_dashboard"),
]
