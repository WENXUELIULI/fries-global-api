from django.urls import path

from apps.analytics.views import IngestView, PresenceView
from apps.authx.views import (
    AdminExportView,
    AdminForensicsView,
    AdminSessionView,
    AdminSummaryView,
    admin_login,
    admin_logout,
)
from config.views import health

urlpatterns = [
    path("health", health),
    path("api/presence", PresenceView.as_view()),
    path("api/ingest", IngestView.as_view()),
    path("admin/api/login", admin_login),
    path("admin/api/logout", admin_logout),
    path("admin/api/session", AdminSessionView.as_view()),
    path("admin/api/summary", AdminSummaryView.as_view()),
    path("admin/api/export", AdminExportView.as_view()),
    path("admin/api/forensics", AdminForensicsView.as_view()),
]
