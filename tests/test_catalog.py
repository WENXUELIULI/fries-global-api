from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client

from apps.authx.services import ADMIN_COOKIE, create_admin_session
from apps.catalog.models import ProductCategory
from apps.media.validators import IMAGE_MAX_BYTES

ORIGIN = {"HTTP_ORIGIN": "http://127.0.0.1:3000", "HTTP_HOST": "127.0.0.1:3000"}


def _admin_client():
    client = Client()
    client.cookies[ADMIN_COOKIE] = create_admin_session()
    return client


def test_public_catalog_empty_returns_no_categories(isolated_private, db):
    ProductCategory.objects.all().delete()
    response = Client().get("/api/catalog")
    assert response.status_code == 200
    assert response.json() == {"categories": []}


def test_public_catalog_includes_configured_href(isolated_private, db):
    ProductCategory.objects.create(title="Home", href="https://example.com/ask", sort_order=1)
    body = Client().get("/api/catalog").json()["categories"]
    assert body[0]["title"] == "Home"
    assert body[0]["href"] == "https://example.com/ask"
    assert body[0]["slug"]


def test_public_catalog_localizes_title_and_falls_back_to_english(isolated_private, db):
    ProductCategory.objects.create(
        title="Valves & Pipes",
        slug="valves-pipes",
        titles={"en": "Valves & Pipes", "ru": "Задвижки и трубы"},
        descriptions={"en": "Steel and PE pipes", "ru": "Стальные и ПЭ трубы"},
        href="#contact",
        sort_order=1,
    )
    en = Client().get("/api/catalog", {"locale": "en"}).json()["categories"][0]
    ru = Client().get("/api/catalog", {"locale": "ru"}).json()["categories"][0]
    zh = Client().get("/api/catalog", {"locale": "zh"}).json()["categories"][0]
    assert en["slug"] == "valves-pipes"
    assert en["title"] == "Valves & Pipes"
    assert ru["title"] == "Задвижки и трубы"
    assert ru["description"] == "Стальные и ПЭ трубы"
    assert zh["title"] == "Valves & Pipes"


def test_admin_create_requires_russian_title(isolated_private, db):
    client = _admin_client()
    response = client.post("/admin/api/catalog", data={"title_en": "Parts"}, **ORIGIN)
    assert response.status_code == 303
    assert "error=invalid" in response["Location"]
    assert ProductCategory.objects.filter(title="Parts").count() == 0


def test_admin_create_defaults_href_to_contact(isolated_private, db):
    client = _admin_client()
    response = client.post(
        "/admin/api/catalog",
        data={"title_en": "Parts", "title_ru": "Запчасти"},
        **ORIGIN,
    )
    assert response.status_code == 303
    category = ProductCategory.objects.get(title="Parts")
    assert category.href == "#contact"
    assert category.slug == "parts"
    assert category.titles["en"] == "Parts"
    assert category.titles["ru"] == "Запчасти"
    listed = client.get("/admin/api/catalog").json()["categories"]
    assert listed[0]["href"] == "#contact"
    assert listed[0]["slug"] == "parts"


def test_admin_create_saves_custom_href(isolated_private, db):
    client = _admin_client()
    response = client.post(
        "/admin/api/catalog",
        data={"title_en": "Beauty", "title_ru": "Красота", "href": "https://wa.me/8613800138000"},
        **ORIGIN,
    )
    assert response.status_code == 303
    assert ProductCategory.objects.get(title="Beauty").href == "https://wa.me/8613800138000"


def test_admin_rejects_javascript_href(isolated_private, db):
    client = _admin_client()
    response = client.post(
        "/admin/api/catalog",
        data={"title_en": "Bad", "title_ru": "Плохо", "href": "javascript:alert(1)"},
        **ORIGIN,
    )
    assert response.status_code == 303
    assert "error=invalid" in response["Location"]
    assert ProductCategory.objects.filter(title="Bad").count() == 0


def test_admin_update_href(isolated_private, db):
    category = ProductCategory.objects.create(title="Outdoor", href="#contact", sort_order=1)
    client = _admin_client()
    response = client.post(
        f"/admin/api/catalog/{category.id}",
        data={"title": "Outdoor", "href": "mailto:friesgoglobal@gmail.com"},
        **ORIGIN,
    )
    assert response.status_code == 303
    category.refresh_from_db()
    assert category.href == "mailto:friesgoglobal@gmail.com"


def test_admin_create_keeps_category_when_image_too_large(isolated_private, db):
    client = _admin_client()
    huge = SimpleUploadedFile(
        "big.jpg",
        b"x" * (IMAGE_MAX_BYTES + 1),
        content_type="image/jpeg",
    )
    response = client.post(
        "/admin/api/catalog",
        data={"title_en": "Kept", "title_ru": "Сохранено", "href": "#contact", "image": huge},
        **ORIGIN,
    )
    assert response.status_code == 303
    assert "created=1" in response["Location"]
    assert "error=too-large" in response["Location"]
    category = ProductCategory.objects.get(title="Kept")
    assert category.image_path == ""


def test_admin_create_ignores_empty_image(isolated_private, db):
    client = _admin_client()
    empty = SimpleUploadedFile("empty.png", b"", content_type="image/png")
    response = client.post(
        "/admin/api/catalog",
        data={"title_en": "NoPic", "title_ru": "Без фото", "href": "#contact", "image": empty},
        **ORIGIN,
    )
    assert response.status_code == 303
    assert "created=1" in response["Location"]
    assert "error=" not in response["Location"]
    assert ProductCategory.objects.get(title="NoPic").image_path == ""
