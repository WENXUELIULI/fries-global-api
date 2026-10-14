from django.test import Client

from apps.authx.services import ADMIN_COOKIE, create_admin_session
from apps.catalog.models import Product, ProductCategory

ORIGIN = {"HTTP_ORIGIN": "http://127.0.0.1:3000", "HTTP_HOST": "127.0.0.1:3000"}


def _admin_client():
    client = Client()
    client.cookies[ADMIN_COOKIE] = create_admin_session()
    return client


def _category(**kwargs):
    defaults = {
        "title": "Valves & Pipes",
        "titles": {"en": "Valves & Pipes", "ru": "Задвижки и трубы"},
        "href": "#contact",
        "sort_order": 1,
    }
    defaults.update(kwargs)
    return ProductCategory.objects.create(**defaults)


def test_public_products_hides_drafts(isolated_private, db):
    category = _category()
    Product.objects.create(
        category=category,
        titles={"en": "Gate valve", "ru": "Задвижка"},
        status="draft",
    )
    Product.objects.create(
        category=category,
        titles={"en": "Ball valve", "ru": "Шаровой кран"},
        status="published",
    )
    body = Client().get("/api/products", {"category": category.slug, "locale": "en"}).json()
    titles = [item["title"] for item in body["products"]]
    assert titles == ["Ball valve"]


def test_public_products_localizes_and_falls_back_to_english(isolated_private, db):
    category = _category()
    Product.objects.create(
        category=category,
        slug="gate-valve",
        titles={"en": "Gate valve", "ru": "Задвижка"},
        descriptions={"en": "DN50–DN300", "ru": "Ду50–Ду300"},
        status="published",
        sku="VP-001",
    )
    ru = Client().get("/api/products", {"category": "valves-pipes", "locale": "ru"}).json()["products"][0]
    zh = Client().get("/api/products", {"category": "valves-pipes", "locale": "zh"}).json()["products"][0]
    assert ru["title"] == "Задвижка"
    assert ru["description"] == "Ду50–Ду300"
    assert zh["title"] == "Gate valve"
    assert zh["sku"] == "VP-001"


def test_public_product_detail_by_slugs(isolated_private, db):
    category = _category()
    Product.objects.create(
        category=category,
        slug="gate-valve",
        titles={"en": "Gate valve", "ru": "Задвижка"},
        status="published",
        sku="VP-001",
        origin="Hebei, China",
        moq="1 container",
        lead_time="20–35 days",
    )
    response = Client().get("/api/products/valves-pipes/gate-valve", {"locale": "en"})
    assert response.status_code == 200
    body = response.json()
    assert body["sku"] == "VP-001"
    assert body["title"] == "Gate valve"
    assert body["origin"] == "Hebei, China"
    assert body["leadTime"] == "20–35 days"
    assert body["category"]["slug"] == "valves-pipes"


def test_public_product_draft_is_404(isolated_private, db):
    category = _category()
    Product.objects.create(
        category=category,
        slug="gate-valve",
        titles={"en": "Gate valve", "ru": "Задвижка"},
        status="draft",
    )
    assert Client().get("/api/products/valves-pipes/gate-valve").status_code == 404


def test_admin_create_requires_russian_title(isolated_private, db):
    category = _category()
    client = _admin_client()
    response = client.post(
        "/admin/api/products",
        data={"category_id": str(category.id), "title_en": "Gate valve"},
        **ORIGIN,
    )
    assert response.status_code == 303
    assert "error=invalid" in response["Location"]
    assert Product.objects.count() == 0


def test_admin_create_saves_draft_with_sku_and_slug(isolated_private, db):
    category = _category()
    client = _admin_client()
    response = client.post(
        "/admin/api/products",
        data={
            "category_id": str(category.id),
            "title_en": "Gate valve",
            "title_ru": "Задвижка",
            "sku": "VP-001",
            "origin": "Hebei, China",
        },
        **ORIGIN,
    )
    assert response.status_code == 303
    product = Product.objects.get()
    assert product.status == "draft"
    assert product.sku == "VP-001"
    assert product.slug == "gate-valve"
    listed = client.get("/admin/api/products").json()["products"]
    assert listed[0]["title"] == "Gate valve"
    assert listed[0]["status"] == "draft"


def test_admin_rejects_duplicate_sku_in_same_category(isolated_private, db):
    category = _category()
    client = _admin_client()
    payload = {
        "category_id": str(category.id),
        "title_en": "Gate valve",
        "title_ru": "Задвижка",
        "sku": "VP-001",
    }
    assert "created=1" in client.post("/admin/api/products", data=payload, **ORIGIN)["Location"]
    payload["title_en"] = "Globe valve"
    payload["title_ru"] = "Вентиль"
    response = client.post("/admin/api/products", data=payload, **ORIGIN)
    assert response.status_code == 303
    assert "error=duplicate-sku" in response["Location"]
    assert Product.objects.count() == 1


def test_admin_allows_empty_sku_twice(isolated_private, db):
    category = _category()
    client = _admin_client()
    first = {
        "category_id": str(category.id),
        "title_en": "Gate valve",
        "title_ru": "Задвижка",
    }
    second = {
        "category_id": str(category.id),
        "title_en": "Globe valve",
        "title_ru": "Вентиль",
    }
    assert "created=1" in client.post("/admin/api/products", data=first, **ORIGIN)["Location"]
    assert "created=1" in client.post("/admin/api/products", data=second, **ORIGIN)["Location"]
    assert Product.objects.count() == 2


def test_admin_publish_makes_product_public(isolated_private, db):
    category = _category()
    product = Product.objects.create(
        category=category,
        titles={"en": "Gate valve", "ru": "Задвижка"},
        status="draft",
    )
    client = _admin_client()
    response = client.post(f"/admin/api/products/{product.id}/publish", **ORIGIN)
    assert response.status_code == 303
    product.refresh_from_db()
    assert product.status == "published"
    public = Client().get("/api/products", {"category": category.slug}).json()["products"]
    assert public[0]["id"] == product.id
