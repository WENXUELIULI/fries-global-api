from django.urls import path

from apps.analytics.views import IngestView, PresenceView
from apps.authx.views import (
    AdminExportView,
    AdminForensicsView,
    AdminSessionView,
    AdminSummaryView,
    SiteAuthSettingsView,
    SiteSessionView,
    admin_login,
    admin_logout,
    admin_user_delete,
    admin_user_update,
    admin_users_collection,
    admin_users_settings,
    site_login,
    site_logout,
    site_profile,
    site_register,
    site_avatar,
)
from apps.media.views import (
    admin_site_image_reset,
    admin_site_image_upload,
    admin_site_images_list,
    public_site_image,
    public_site_images_list,
)
from config.views import health

urlpatterns = [
    path("health", health),
    path("api/presence", PresenceView.as_view()),
    path("api/ingest", IngestView.as_view()),
    path("api/site-images", public_site_images_list),
    path("api/site-images/<str:slot_key>", public_site_image),
    path("api/auth/login", site_login),
    path("api/auth/logout", site_logout),
    path("api/auth/register", site_register),
    path("api/auth/session", SiteSessionView.as_view()),
    path("api/auth/settings", SiteAuthSettingsView.as_view()),
    path("api/auth/profile", site_profile),
    path("api/auth/avatar/<str:user_id>", site_avatar),
    path("admin/api/login", admin_login),
    path("admin/api/logout", admin_logout),
    path("admin/api/session", AdminSessionView.as_view()),
    path("admin/api/summary", AdminSummaryView.as_view()),
    path("admin/api/export", AdminExportView.as_view()),
    path("admin/api/forensics", AdminForensicsView.as_view()),
    path("admin/api/site-images", admin_site_images_list),
    path("admin/api/site-images/<str:slot_key>/reset", admin_site_image_reset),
    path("admin/api/site-images/<str:slot_key>", admin_site_image_upload),
    path("admin/api/users/settings", admin_users_settings),
    path("admin/api/users", admin_users_collection),
    path("admin/api/users/<str:user_id>/delete", admin_user_delete),
    path("admin/api/users/<str:user_id>", admin_user_update),
]
