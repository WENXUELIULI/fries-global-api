from django.conf import settings
from django.test import Client


def test_media_root_and_apps_registered():
    assert str(settings.MEDIA_ROOT).endswith("media")
    assert "apps.media" in settings.INSTALLED_APPS
    assert "apps.engage" in settings.INSTALLED_APPS


def test_health_still_ok():
    c = Client()
    r = c.get("/health")
    assert r.status_code == 200
