from __future__ import annotations

import json
from pathlib import Path

from urllib.parse import urlparse

from django.conf import settings
from django.db.models import Max
from django.http import FileResponse, HttpRequest, HttpResponse, HttpResponseRedirect
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from apps.authx.services import ADMIN_COOKIE, valid_same_origin_request, verify_admin_session
from apps.catalog.models import ProductCategory
from apps.media.validators import IMAGE_MAX_BYTES, MediaImageError, detect_site_image


def _normalize_href(raw: str) -> str | None:
    value = (raw or "").strip()
    if not value:
        return "#contact"
    if len(value) > 500:
        return None
    if value.startswith("#"):
        if any(ch in value for ch in " \n\r\t<>\"'"):
            return None
        return value
    if value.startswith("/") and not value.startswith("//"):
        return value
    parsed = urlparse(value)
    scheme = (parsed.scheme or "").lower()
    if scheme == "mailto":
        return value
    if scheme in {"http", "https"} and parsed.netloc:
        return value
    return None


def _json(payload: dict, *, status: int = 200) -> HttpResponse:
    response = HttpResponse(json.dumps(payload), content_type="application/json", status=status)
    response["Cache-Control"] = "private, no-store, max-age=0"
    return response


def _require_admin(request: HttpRequest) -> HttpResponse | None:
    if not verify_admin_session(request.COOKIES.get(ADMIN_COOKIE)):
        return HttpResponse("Unauthorized", status=401, headers={"Cache-Control": "no-store"})
    return None


def _catalog_dir() -> Path:
    path = Path(settings.MEDIA_ROOT) / "catalog"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _category_image_url(category_id: int) -> str:
    return f"/api/catalog/{category_id}/image"


def _serialize(category: ProductCategory, *, include_admin: bool = False) -> dict:
    item = {
        "id": category.id,
        "title": category.title,
        "href": category.href or "#contact",
        "sortOrder": category.sort_order,
    }
    if category.image_path:
        item["imageUrl"] = _category_image_url(category.id)
    else:
        item["imageUrl"] = None
    if include_admin:
        item["createdAt"] = category.created_at.isoformat().replace("+00:00", "Z")
        item["updatedAt"] = category.updated_at.isoformat().replace("+00:00", "Z")
    return item


@require_GET
def public_catalog(request: HttpRequest):
    categories = ProductCategory.objects.order_by("sort_order", "id")
    payload = {"categories": [_serialize(c) for c in categories]}
    response = _json(payload)
    response["Cache-Control"] = "public, max-age=60"
    return response


@require_GET
def public_category_image(request: HttpRequest, category_id: int):
    try:
        category = ProductCategory.objects.get(id=category_id)
    except ProductCategory.DoesNotExist:
        return HttpResponse("Not found", status=404)
    if not category.image_path:
        return HttpResponse("Not found", status=404)
    path = Path(settings.MEDIA_ROOT) / category.image_path
    if not path.is_file():
        return HttpResponse("Not found", status=404)
    response = FileResponse(path.open("rb"), content_type="image/png")
    response["Cache-Control"] = "public, max-age=300"
    return response


@csrf_exempt
def admin_catalog_collection(request: HttpRequest):
    denied = _require_admin(request)
    if denied:
        return denied

    if request.method == "GET":
        categories = ProductCategory.objects.order_by("sort_order", "id")
        return _json({"categories": [_serialize(c, include_admin=True) for c in categories]})

    if request.method != "POST":
        return HttpResponse("Method not allowed", status=405, headers={"Allow": "GET, POST"})
    if not valid_same_origin_request(request):
        return HttpResponse("Forbidden", status=403)

    title = (request.POST.get("title") or "").strip()
    href = _normalize_href(request.POST.get("href") or "")
    if not title or len(title) > 120 or href is None:
        return HttpResponseRedirect("/admin/catalog?error=invalid", status=303)

    max_order = ProductCategory.objects.aggregate(m=Max("sort_order"))["m"] or 0
    category = ProductCategory.objects.create(title=title, href=href, sort_order=max_order + 1)

    image_error = _save_uploaded_category_image(category, request)
    if image_error:
        return HttpResponseRedirect(f"/admin/catalog?created=1&error={image_error}", status=303)

    return HttpResponseRedirect("/admin/catalog?created=1", status=303)


@csrf_exempt
@require_POST
def admin_catalog_update(request: HttpRequest, category_id: int):
    denied = _require_admin(request)
    if denied:
        return denied
    if not valid_same_origin_request(request):
        return HttpResponse("Forbidden", status=403)

    try:
        category = ProductCategory.objects.get(id=category_id)
    except ProductCategory.DoesNotExist:
        return HttpResponse("Not found", status=404)

    if "title" in request.POST:
        title = (request.POST.get("title") or "").strip()
        if not title or len(title) > 120:
            return HttpResponseRedirect("/admin/catalog?error=invalid", status=303)
        category.title = title

    if "href" in request.POST:
        href = _normalize_href(request.POST.get("href") or "")
        if href is None:
            return HttpResponseRedirect("/admin/catalog?error=invalid", status=303)
        category.href = href

    image_error = _save_uploaded_category_image(category, request)

    category.save()
    if image_error:
        return HttpResponseRedirect(f"/admin/catalog?updated=1&error={image_error}", status=303)
    return HttpResponseRedirect("/admin/catalog?updated=1", status=303)


@csrf_exempt
@require_POST
def admin_catalog_move(request: HttpRequest, category_id: int):
    denied = _require_admin(request)
    if denied:
        return denied
    if not valid_same_origin_request(request):
        return HttpResponse("Forbidden", status=403)

    direction = (request.POST.get("direction") or "").strip()
    if direction not in {"up", "down"}:
        return HttpResponseRedirect("/admin/catalog?error=invalid", status=303)

    try:
        category = ProductCategory.objects.get(id=category_id)
    except ProductCategory.DoesNotExist:
        return HttpResponse("Not found", status=404)

    ordered = list(ProductCategory.objects.order_by("sort_order", "id"))
    index = next((i for i, c in enumerate(ordered) if c.id == category.id), -1)
    if index < 0:
        return HttpResponse("Not found", status=404)
    target = index - 1 if direction == "up" else index + 1
    if target < 0 or target >= len(ordered):
        return HttpResponseRedirect("/admin/catalog", status=303)

    neighbor = ordered[target]
    category.sort_order, neighbor.sort_order = neighbor.sort_order, category.sort_order
    ProductCategory.objects.bulk_update([category, neighbor], ["sort_order"])
    return HttpResponseRedirect("/admin/catalog", status=303)


@csrf_exempt
@require_POST
def admin_catalog_delete(request: HttpRequest, category_id: int):
    denied = _require_admin(request)
    if denied:
        return denied
    if not valid_same_origin_request(request):
        return HttpResponse("Forbidden", status=403)

    try:
        category = ProductCategory.objects.get(id=category_id)
    except ProductCategory.DoesNotExist:
        return HttpResponse(status=204)

    if category.image_path:
        path = Path(settings.MEDIA_ROOT) / category.image_path
        if path.is_file():
            path.unlink(missing_ok=True)
    category.delete()
    return HttpResponseRedirect("/admin/catalog?deleted=1", status=303)


def _public_image_error(code: str) -> str:
    return "invalid_image" if code == "invalid" else code


def _uploaded_image(request: HttpRequest):
    uploaded = request.FILES.get("image") or request.FILES.get("file")
    if not uploaded or getattr(uploaded, "size", 0) <= 0:
        return None
    return uploaded


def _save_uploaded_category_image(category: ProductCategory, request: HttpRequest) -> str | None:
    uploaded = _uploaded_image(request)
    if not uploaded:
        return None
    err = _save_category_image(category, uploaded)
    return _public_image_error(err) if err else None


def _save_category_image(category: ProductCategory, uploaded) -> str | None:
    """Persist an uploaded image for a category. Returns an error code or None on success."""
    if getattr(uploaded, "size", 0) > IMAGE_MAX_BYTES:
        return "too-large"
    data = uploaded.read(IMAGE_MAX_BYTES + 1)
    if len(data) > IMAGE_MAX_BYTES:
        return "too-large"
    try:
        ext, _content_type = detect_site_image(data)
    except MediaImageError as exc:
        return exc.code

    filename = f"{category.id}.{ext}"
    abs_path = _catalog_dir() / filename
    # Remove previous file if extension changed
    if category.image_path:
        old = Path(settings.MEDIA_ROOT) / category.image_path
        if old != abs_path and old.is_file():
            old.unlink(missing_ok=True)
    abs_path.write_bytes(data)
    category.image_path = f"catalog/{filename}"
    category.save(update_fields=["image_path", "updated_at"])
    return None
