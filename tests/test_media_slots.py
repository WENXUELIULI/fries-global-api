from django.conf import settings


def test_media_root_and_apps_registered():
    assert settings.MEDIA_ROOT == settings.BASE_DIR / "media"
    assert settings.MEDIA_URL == "/media/"
    assert "apps.media" in settings.INSTALLED_APPS
    assert "apps.engage" in settings.INSTALLED_APPS
