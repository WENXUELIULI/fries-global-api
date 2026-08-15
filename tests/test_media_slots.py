import struct
import zlib

import pytest
from django.conf import settings

from apps.media.slots import SLOT_KEYS, default_public_path
from apps.media.validators import IMAGE_MAX_BYTES, MediaImageError, detect_site_image


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


def _png_with_size(width: int, height: int) -> bytes:
    ihdr_data = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    ihdr = struct.pack(">I", 13) + b"IHDR" + ihdr_data + struct.pack(">I", zlib.crc32(b"IHDR" + ihdr_data) & 0xFFFFFFFF)
    iend = struct.pack(">I", 0) + b"IEND" + struct.pack(">I", zlib.crc32(b"IEND") & 0xFFFFFFFF)
    return b"\x89PNG\r\n\x1a\n" + ihdr + iend


def test_detect_site_image_accepts_small_png():
    assert detect_site_image(_png_with_size(32, 24)) == ("png", "image/png")


def test_detect_site_image_rejects_over_2mb():
    data = b"\x89PNG\r\n\x1a\n" + b"\x00" * IMAGE_MAX_BYTES
    with pytest.raises(MediaImageError) as exc:
        detect_site_image(data)
    assert exc.value.code == "too-large"


def test_detect_site_image_rejects_non_image():
    with pytest.raises(MediaImageError) as exc:
        detect_site_image(b"not-an-image-at-all")
    assert exc.value.code == "invalid"


def test_detect_site_image_rejects_declared_dimensions_over_4096():
    with pytest.raises(MediaImageError) as exc:
        detect_site_image(_png_with_size(4097, 100))
    assert exc.value.code == "too-large"
