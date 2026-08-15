from django.conf import settings

from apps.media.slots import SLOT_KEYS, default_public_path


def test_media_root_and_apps_registered():
    assert settings.MEDIA_ROOT == settings.BASE_DIR / "media"
    assert settings.MEDIA_URL == "/media/"
    assert "apps.media" in settings.INSTALLED_APPS
    assert "apps.engage" in settings.INSTALLED_APPS


EXPECTED_SLOT_DEFAULTS = {
    "hero.backdrop": "/hero-port-global.png",
    "brand.symbol": "/brand-symbol.png",
    "categories.mascot_sheet": "/category-mascots.png",
    "services.case_a": "/case-industrial-tech.png",
    "services.case_b": "/case-manufacturing.png",
    "cta.visual": None,
}


def test_all_design_slots_and_defaults():
    for slot_key, expected_path in EXPECTED_SLOT_DEFAULTS.items():
        assert slot_key in SLOT_KEYS
        assert default_public_path(slot_key) == expected_path
