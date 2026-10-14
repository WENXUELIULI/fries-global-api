from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.db.models import Max, Prefetch
from django.http import FileResponse, HttpRequest, HttpResponse, HttpResponseRedirect
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from apps.authx.services import valid_same_origin_request
from apps.catalog.models import (
    LOCALES,
    SLUG_PATTERN,
    Product,
    ProductCategory,
    ProductImage,
    localized_text,
    pick_locale,
)
from apps.catalog.views import (
    _descriptions_from_post,
    _json,
    _public_image_error,
    _require_admin,
    _titles_for_create,
    _uploaded_image,
)
from apps.media.validators import IMAGE_MAX_BYTES, MediaImageError, detect_site_image


def _clip(raw: str | None, limit: int) -> str:
    return (raw or "").strip()[:limit]


def _product_image_url(product_id: int, image_id: int) -> str:
    return f"/api/products/{product_id}/images/{image_id}"


def _serialize_image(image: ProductImage) -> dict:
    return {
        "id": image.id,
        "url": _product_image_url(image.product_id, image.id),
        "alt": image.alt,
        "sortOrder": image.sort_order,
    }


def _serialize_product(product: Product, *, include_admin: bool = False, locale: str = "en") -> dict:
    display_locale = "en" if include_admin else locale
    item = {
        "id": product.id,
        "sku": product.sku,
        "slug": product.slug,
        "status": product.status,
        "title": localized_text(product.titles, display_locale),
        "description": localized_text(product.descriptions, display_locale),
        "origin": product.origin,
        "size": product.size,
        "moq": product.moq,
        "packing": product.packing,
        "leadTime": product.lead_time,
        "specs": product.specs,
        "sortOrder": product.sort_order,
        "category": {
            "id": product.category_id,
            "slug": product.category.slug,
            "title": localized_text(product.category.titles, display_locale, fallback=product.category.title),
        },
        "images": [_serialize_image(image) for image in product.images.all()],
    }
    if include_admin:
        item["titles"] = product.titles or {}
        item["descriptions"] = product.descriptions or {}
        item["createdAt"] = product.created_at.isoformat().replace("+00:00", "Z")
        item["updatedAt"] = product.updated_at.isoformat().replace("+00:00", "Z")
    return item


def _images_prefetch():
    return Prefetch("images", queryset=ProductImage.objects.order_by("sort_order", "id"))


def _published_qs():
    return (
        Product.objects.filter(status=Product.STATUS_PUBLISHED)
        .select_related("category")
        .prefetch_related(_images_prefetch())
        .order_by("sort_order", "id")
    )


def _admin_qs():
    return Product.objects.select_related("category").prefetch_related(_images_prefetch()).order_by("sort_order", "id")


def _product_slug_from_post(post, category: ProductCategory, *, exclude_id: int | None = None) -> tuple[str | None, bool]:
    raw = (post.get("slug") or "").strip().lower()
    if not raw:
        return None, True
    if not SLUG_PATTERN.fullmatch(raw) or len(raw) > 140:
        return None, False
    qs = Product.objects.filter(category=category, slug=raw)
    if exclude_id:
        qs = qs.exclude(pk=exclude_id)
    if qs.exists():
        return None, False
    return raw, True


def _sku_taken(category: ProductCategory, sku: str, *, exclude_id: int | None = None) -> bool:
    if not sku:
        return False
    qs = Product.objects.filter(category=category, sku=sku)
    if exclude_id:
        qs = qs.exclude(pk=exclude_id)
    return qs.exists()


def _merge_titles(post, existing) -> dict | None:
    keys = ["title", *[f"title_{loc}" for loc in LOCALES]]
    if not any(key in post for key in keys):
        return dict(existing or {})
    merged = dict(existing or {})
    if "title_en" in post or "title" in post:
        merged["en"] = (post.get("title_en") or post.get("title") or "").strip()
    if "title_ru" in post:
        merged["ru"] = (post.get("title_ru") or "").strip()
    for loc in LOCALES:
        if loc in {"en", "ru"}:
            continue
        key = f"title_{loc}"
        if key in post:
            value = (post.get(key) or "").strip()
            if value:
                merged[loc] = value
            else:
                merged.pop(loc, None)
    if not merged.get("en") or len(merged.get("en") or "") > 120:
        return None
    if not merged.get("ru"):
        merged["ru"] = merged["en"]
    if len(merged["ru"]) > 120:
        return None
    return merged


def _apply_specs(product: Product, post) -> None:
    if "origin" in post:
        product.origin = _clip(post.get("origin"), 160)
    if "size" in post:
        product.size = _clip(post.get("size"), 160)
    if "moq" in post:
        product.moq = _clip(post.get("moq"), 160)
    if "packing" in post:
        product.packing = _clip(post.get("packing"), 160)
    if "lead_time" in post:
        product.lead_time = _clip(post.get("lead_time"), 160)
    if "specs" in post:
        product.specs = _clip(post.get("specs"), 8000)


@require_GET
def public_products(request: HttpRequest):
    locale = pick_locale(request.GET.get("locale"))
    qs = _published_qs()
    category_slug = (request.GET.get("category") or "").strip()
    if category_slug:
        qs = qs.filter(category__slug=category_slug)
    payload = {"products": [_serialize_product(item, locale=locale) for item in qs]}
    response = _json(payload)
    response["Cache-Control"] = "public, max-age=60"
    return response


@require_GET
def public_product_detail(request: HttpRequest, category_slug: str, product_slug: str):
    locale = pick_locale(request.GET.get("locale"))
    try:
        product = _published_qs().get(category__slug=category_slug, slug=product_slug)
    except Product.DoesNotExist:
        return HttpResponse("Not found", status=404)
    response = _json(_serialize_product(product, locale=locale))
    response["Cache-Control"] = "public, max-age=60"
    return response


@require_GET
def public_product_image(request: HttpRequest, product_id: int, image_id: int):
    try:
        image = ProductImage.objects.select_related("product").get(id=image_id, product_id=product_id)
    except ProductImage.DoesNotExist:
        return HttpResponse("Not found", status=404)
    if image.product.status != Product.STATUS_PUBLISHED:
        return HttpResponse("Not found", status=404)
    path = Path(settings.MEDIA_ROOT) / image.image_path
    if not path.is_file():
        return HttpResponse("Not found", status=404)
    response = FileResponse(path.open("rb"), content_type="image/png")
    response["Cache-Control"] = "public, max-age=300"
    return response


@csrf_exempt
def admin_products_collection(request: HttpRequest):
    denied = _require_admin(request)
    if denied:
        return denied

    if request.method == "GET":
        return _json({"products": [_serialize_product(item, include_admin=True) for item in _admin_qs()]})

    if request.method != "POST":
        return HttpResponse("Method not allowed", status=405, headers={"Allow": "GET, POST"})
    if not valid_same_origin_request(request):
        return HttpResponse("Forbidden", status=403)

    try:
        category = ProductCategory.objects.get(id=int(request.POST.get("category_id") or "0"))
    except (ProductCategory.DoesNotExist, ValueError, TypeError):
        return HttpResponseRedirect("/admin/products?error=invalid", status=303)

    titles = _titles_for_create(request.POST)
    descriptions = _descriptions_from_post(request.POST)
    slug, slug_ok = _product_slug_from_post(request.POST, category)
    sku = _clip(request.POST.get("sku"), 80)
    if titles is None or descriptions is None or not slug_ok:
        return HttpResponseRedirect("/admin/products?error=invalid", status=303)
    if _sku_taken(category, sku):
        return HttpResponseRedirect("/admin/products?error=duplicate-sku", status=303)

    max_order = Product.objects.filter(category=category).aggregate(m=Max("sort_order"))["m"] or 0
    product = Product(
        category=category,
        sku=sku,
        titles=titles,
        descriptions=descriptions,
        status=Product.STATUS_DRAFT,
        sort_order=max_order + 1,
        origin=_clip(request.POST.get("origin"), 160),
        size=_clip(request.POST.get("size"), 160),
        moq=_clip(request.POST.get("moq"), 160),
        packing=_clip(request.POST.get("packing"), 160),
        lead_time=_clip(request.POST.get("lead_time"), 160),
        specs=_clip(request.POST.get("specs"), 8000),
    )
    if slug:
        product.slug = slug
    product.save()
    return HttpResponseRedirect("/admin/products?created=1", status=303)


@csrf_exempt
@require_POST
def admin_product_update(request: HttpRequest, product_id: int):
    denied = _require_admin(request)
    if denied:
        return denied
    if not valid_same_origin_request(request):
        return HttpResponse("Forbidden", status=403)
    try:
        product = Product.objects.select_related("category").get(id=product_id)
    except Product.DoesNotExist:
        return HttpResponse("Not found", status=404)

    titles = _merge_titles(request.POST, product.titles)
    if titles is None:
        return HttpResponseRedirect("/admin/products?error=invalid", status=303)
    product.titles = titles

    if any(f"description_{loc}" in request.POST for loc in LOCALES):
        descriptions = _descriptions_from_post(
            request.POST,
            existing=product.descriptions if isinstance(product.descriptions, dict) else {},
            partial=True,
        )
        if descriptions is None:
            return HttpResponseRedirect("/admin/products?error=invalid", status=303)
        product.descriptions = descriptions

    if "category_id" in request.POST:
        try:
            product.category = ProductCategory.objects.get(id=int(request.POST.get("category_id") or "0"))
        except (ProductCategory.DoesNotExist, ValueError, TypeError):
            return HttpResponseRedirect("/admin/products?error=invalid", status=303)

    if "sku" in request.POST:
        sku = _clip(request.POST.get("sku"), 80)
        if _sku_taken(product.category, sku, exclude_id=product.id):
            return HttpResponseRedirect("/admin/products?error=duplicate-sku", status=303)
        product.sku = sku

    if "slug" in request.POST:
        slug, slug_ok = _product_slug_from_post(request.POST, product.category, exclude_id=product.id)
        if not slug_ok:
            return HttpResponseRedirect("/admin/products?error=invalid", status=303)
        if slug:
            product.slug = slug

    if "status" in request.POST:
        status = (request.POST.get("status") or "").strip()
        if status not in {Product.STATUS_DRAFT, Product.STATUS_PUBLISHED}:
            return HttpResponseRedirect("/admin/products?error=invalid", status=303)
        product.status = status

    _apply_specs(product, request.POST)
    product.save()
    return HttpResponseRedirect("/admin/products?updated=1", status=303)


@csrf_exempt
@require_POST
def admin_product_publish(request: HttpRequest, product_id: int):
    denied = _require_admin(request)
    if denied:
        return denied
    if not valid_same_origin_request(request):
        return HttpResponse("Forbidden", status=403)
    try:
        product = Product.objects.get(id=product_id)
    except Product.DoesNotExist:
        return HttpResponse("Not found", status=404)
    product.status = Product.STATUS_PUBLISHED
    product.save(update_fields=["status", "updated_at"])
    return HttpResponseRedirect("/admin/products?updated=1", status=303)


@csrf_exempt
@require_POST
def admin_product_delete(request: HttpRequest, product_id: int):
    denied = _require_admin(request)
    if denied:
        return denied
    if not valid_same_origin_request(request):
        return HttpResponse("Forbidden", status=403)
    try:
        product = Product.objects.prefetch_related("images").get(id=product_id)
    except Product.DoesNotExist:
        return HttpResponse(status=204)
    for image in product.images.all():
        path = Path(settings.MEDIA_ROOT) / image.image_path
        if path.is_file():
            path.unlink(missing_ok=True)
    product.delete()
    return HttpResponseRedirect("/admin/products?deleted=1", status=303)


@csrf_exempt
@require_POST
def admin_product_images(request: HttpRequest, product_id: int):
    denied = _require_admin(request)
    if denied:
        return denied
    if not valid_same_origin_request(request):
        return HttpResponse("Forbidden", status=403)
    try:
        product = Product.objects.get(id=product_id)
    except Product.DoesNotExist:
        return HttpResponse("Not found", status=404)

    uploaded = _uploaded_image(request)
    if not uploaded:
        return HttpResponseRedirect("/admin/products?error=invalid", status=303)
    if getattr(uploaded, "size", 0) > IMAGE_MAX_BYTES:
        return HttpResponseRedirect("/admin/products?error=too-large", status=303)
    data = uploaded.read(IMAGE_MAX_BYTES + 1)
    if len(data) > IMAGE_MAX_BYTES:
        return HttpResponseRedirect("/admin/products?error=too-large", status=303)
    try:
        ext, _content_type = detect_site_image(data)
    except MediaImageError as exc:
        return HttpResponseRedirect(f"/admin/products?error={_public_image_error(exc.code)}", status=303)

    max_order = product.images.aggregate(m=Max("sort_order"))["m"] or 0
    image = ProductImage.objects.create(
        product=product,
        image_path="",
        alt=_clip(request.POST.get("alt"), 200),
        sort_order=max_order + 1,
    )
    folder = Path(settings.MEDIA_ROOT) / "products"
    folder.mkdir(parents=True, exist_ok=True)
    filename = f"{product.id}-{image.id}.{ext}"
    (folder / filename).write_bytes(data)
    image.image_path = f"products/{filename}"
    image.save(update_fields=["image_path"])
    return HttpResponseRedirect("/admin/products?updated=1", status=303)
