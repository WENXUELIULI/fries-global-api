import struct
import zlib
from pathlib import Path

import pytest
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client

from apps.authx.services import ADMIN_COOKIE, create_admin_session
from apps.authx.users import USER_COOKIE, create_site_user
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
    "services.case_1": "/case-industrial-tech.png",
    "services.case_2": "/case-manufacturing.png",
    "services.case_3": "/case-industrial-tech.png",
    "services.case_4": "/case-manufacturing.png",
    "quality.backdrop": None,
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


def test_detect_site_image_rejects_over_20mb():
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


def test_detect_site_image_allows_large_edge_when_unlimited():
    assert detect_site_image(_png_with_size(6000, 4000), max_edge=None) == ("png", "image/png")


def _origin_headers() -> dict[str, str]:
    return {"HTTP_ORIGIN": "http://127.0.0.1:3000", "HTTP_HOST": "127.0.0.1:3000"}


def _prepare_media(isolated_private: Path, settings) -> Path:
    root = Path(settings.BASE_DIR) / "media"
    settings.MEDIA_ROOT = root
    root.mkdir(parents=True, exist_ok=True)
    return root


def _admin_client(isolated_private: Path) -> Client:
    client = Client()
    client.cookies[ADMIN_COOKIE] = create_admin_session()
    return client


@pytest.mark.django_db
def test_public_site_images_list_empty(client):
    response = client.get("/api/site-images")
    assert response.status_code == 200
    body = response.json()
    assert "slots" in body
    assert isinstance(body["slots"], dict)
    # Missing custom may omit or use null url — either is fine
    for key, value in body["slots"].items():
        assert key in SLOT_KEYS
        assert value.get("url") in (None, "")


@pytest.mark.django_db
def test_admin_upload_requires_session(isolated_private, settings):
    _prepare_media(isolated_private, settings)
    client = Client()
    png = SimpleUploadedFile("hero.png", _png_with_size(32, 24), content_type="image/png")
    response = client.post(
        "/admin/api/site-images/hero.backdrop",
        data={"image": png},
        **_origin_headers(),
    )
    assert response.status_code == 401


@pytest.mark.django_db
def test_site_user_cannot_access_admin_site_images(isolated_private, settings):
    _prepare_media(isolated_private, settings)
    create_site_user("buyer01", "Buyer-Pass-123")
    client = Client()
    login = client.post(
        "/api/auth/login",
        data={"username": "buyer01", "password": "Buyer-Pass-123", "website": ""},
        **_origin_headers(),
    )
    client.cookies[USER_COOKIE] = login.cookies[USER_COOKIE].value

    listed = client.get("/admin/api/site-images")
    assert listed.status_code == 401

    png = SimpleUploadedFile("hero.png", _png_with_size(32, 24), content_type="image/png")
    uploaded = client.post(
        "/admin/api/site-images/hero.backdrop",
        data={"image": png},
        **_origin_headers(),
    )
    assert uploaded.status_code == 401


@pytest.mark.django_db
def test_admin_upload_then_public_get_bytes(isolated_private, settings):
    _prepare_media(isolated_private, settings)
    client = _admin_client(isolated_private)
    png_bytes = _png_with_size(32, 24)
    png = SimpleUploadedFile("hero.png", png_bytes, content_type="image/png")
    uploaded = client.post(
        "/admin/api/site-images/hero.backdrop",
        data={"image": png},
        **_origin_headers(),
    )
    assert uploaded.status_code in (200, 204)

    public = Client()
    listed = public.get("/api/site-images")
    assert listed.status_code == 200
    slot = listed.json()["slots"]["hero.backdrop"]
    assert slot["url"] == "/api/site-images/hero.backdrop"
    assert slot.get("updatedAt")

    image = public.get("/api/site-images/hero.backdrop")
    assert image.status_code == 200
    assert image["Content-Type"].startswith("image/png")
    assert b"".join(image.streaming_content) == png_bytes


@pytest.mark.django_db
def test_admin_list_shows_customized_flag(isolated_private, settings):
    _prepare_media(isolated_private, settings)
    client = _admin_client(isolated_private)
    listed = client.get("/admin/api/site-images")
    assert listed.status_code == 200
    body = listed.json()
    assert "slots" in body
    slots = body["slots"]
    # Accept list or dict keyed by slotKey
    if isinstance(slots, dict):
        hero = slots["hero.backdrop"]
        assert hero["customized"] is False
    else:
        hero = next(s for s in slots if s["slotKey"] == "hero.backdrop")
        assert hero["customized"] is False

    png = SimpleUploadedFile("hero.png", _png_with_size(32, 24), content_type="image/png")
    client.post(
        "/admin/api/site-images/hero.backdrop",
        data={"image": png},
        **_origin_headers(),
    )
    listed2 = client.get("/admin/api/site-images")
    slots2 = listed2.json()["slots"]
    if isinstance(slots2, dict):
        assert slots2["hero.backdrop"]["customized"] is True
    else:
        hero2 = next(s for s in slots2 if s["slotKey"] == "hero.backdrop")
        assert hero2["customized"] is True


@pytest.mark.django_db
def test_admin_reset_deletes_custom_image(isolated_private, settings):
    _prepare_media(isolated_private, settings)
    client = _admin_client(isolated_private)
    png = SimpleUploadedFile("hero.png", _png_with_size(32, 24), content_type="image/png")
    client.post(
        "/admin/api/site-images/hero.backdrop",
        data={"image": png},
        **_origin_headers(),
    )
    reset = client.post(
        "/admin/api/site-images/hero.backdrop/reset",
        **_origin_headers(),
    )
    assert reset.status_code in (200, 204)

    image = Client().get("/api/site-images/hero.backdrop")
    assert image.status_code == 404


@pytest.mark.django_db
def test_upload_unknown_slot_returns_404(isolated_private, settings):
    _prepare_media(isolated_private, settings)
    client = _admin_client(isolated_private)
    png = SimpleUploadedFile("x.png", _png_with_size(16, 16), content_type="image/png")
    response = client.post(
        "/admin/api/site-images/not.a.real.slot",
        data={"image": png},
        **_origin_headers(),
    )
    assert response.status_code == 404


@pytest.mark.django_db
def test_public_missing_slot_image_is_404(client):
    response = client.get("/api/site-images/hero.backdrop")
    assert response.status_code == 404
