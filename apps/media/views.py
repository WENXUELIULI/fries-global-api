from __future__ import annotations

import json
from pathlib import Path

from django.conf import settings
from django.http import FileResponse, HttpRequest, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from apps.authx.services import ADMIN_COOKIE, valid_same_origin_request, verify_admin_session
from apps.media.models import SiteImageSlot
from apps.media.slots import SLOT_KEYS, default_public_path
from apps.media.validators import IMAGE_MAX_BYTES, MediaImageError, detect_site_image


def _json(payload: dict, *, status: int = 200) -> HttpResponse:
    response = HttpResponse(
        json.dumps(payload),
        content_type="application/json",
        status=status,
    )
    response["Cache-Control"] = "private, no-store, max-age=0"
    return response


def _require_admin(request: HttpRequest) -> HttpResponse | None:
    if not verify_admin_session(request.COOKIES.get(ADMIN_COOKIE)):
        return HttpResponse("Unauthorized", status=401, headers={"Cache-Control": "no-store"})
    return None


def _site_dir() -> Path:
    path = Path(settings.MEDIA_ROOT) / "site"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _slot_filename(slot_key: str, ext: str) -> str:
    return f"{slot_key.replace('.', '_')}.{ext}"


def _public_url(slot_key: str) -> str:
    return f"/api/site-images/{slot_key}"


def _resolve_stored_path(file_path: str) -> Path:
    path = Path(file_path)
    if not path.is_absolute():
        path = Path(settings.MEDIA_ROOT) / path
    return path


@require_GET
def public_site_images_list(request: HttpRequest):
    rows = {row.slot_key: row for row in SiteImageSlot.objects.all()}
    slots: dict[str, dict] = {}
    for slot_key in sorted(SLOT_KEYS):
        row = rows.get(slot_key)
        if row:
            slots[slot_key] = {
                "url": _public_url(slot_key),
                "updatedAt": row.updated_at.isoformat().replace("+00:00", "Z"),
            }
        else:
            slots[slot_key] = {"url": None, "updatedAt": None}
    return _json({"slots": slots})


@require_GET
def public_site_image(request: HttpRequest, slot_key: str):
    if slot_key not in SLOT_KEYS:
        return HttpResponse("Not found", status=404)
    try:
        row = SiteImageSlot.objects.get(slot_key=slot_key)
    except SiteImageSlot.DoesNotExist:
        return HttpResponse("Not found", status=404)
    path = _resolve_stored_path(row.file_path)
    if not path.is_file():
        return HttpResponse("Not found", status=404)
    response = FileResponse(path.open("rb"), content_type=row.content_type or "application/octet-stream")
    response["Cache-Control"] = "public, max-age=300"
    return response


@require_GET
def admin_site_images_list(request: HttpRequest):
    denied = _require_admin(request)
    if denied:
        return denied
    rows = {row.slot_key: row for row in SiteImageSlot.objects.all()}
    slots = []
    for slot_key in sorted(SLOT_KEYS):
        row = rows.get(slot_key)
        item = {
            "slotKey": slot_key,
            "defaultPath": default_public_path(slot_key),
            "customized": row is not None,
        }
        if row:
            item["url"] = _public_url(slot_key)
            item["updatedAt"] = row.updated_at.isoformat().replace("+00:00", "Z")
            item["contentType"] = row.content_type
        slots.append(item)
    return _json({"slots": slots})


@csrf_exempt
@require_POST
def admin_site_image_upload(request: HttpRequest, slot_key: str):
    denied = _require_admin(request)
    if denied:
        return denied
    if not valid_same_origin_request(request):
        return HttpResponse("Forbidden", status=403)
    if slot_key not in SLOT_KEYS:
        return HttpResponse("Not found", status=404)

    uploaded = request.FILES.get("image") or request.FILES.get("file")
    if not uploaded:
        return _json({"ok": False, "error": "invalid"}, status=400)
    if getattr(uploaded, "size", 0) > IMAGE_MAX_BYTES:
        return _json({"ok": False, "error": "too-large"}, status=400)

    data = uploaded.read(IMAGE_MAX_BYTES + 1)
    if len(data) > IMAGE_MAX_BYTES:
        return _json({"ok": False, "error": "too-large"}, status=400)
    try:
        ext, content_type = detect_site_image(data)
    except MediaImageError as exc:
        status = 413 if exc.code == "too-large" else 400
        return _json({"ok": False, "error": exc.code}, status=status)

    filename = _slot_filename(slot_key, ext)
    abs_path = _site_dir() / filename
    # Remove previous file if extension changed
    try:
        existing = SiteImageSlot.objects.get(slot_key=slot_key)
        old = _resolve_stored_path(existing.file_path)
        if old != abs_path and old.is_file():
            old.unlink(missing_ok=True)
    except SiteImageSlot.DoesNotExist:
        pass

    abs_path.write_bytes(data)
    relative = f"site/{filename}"
    SiteImageSlot.objects.update_or_create(
        slot_key=slot_key,
        defaults={
            "file_path": relative,
            "content_type": content_type,
            "updated_by": "admin",
        },
    )
    return _json({"ok": True, "slotKey": slot_key, "url": _public_url(slot_key)})


@csrf_exempt
@require_POST
def admin_site_image_reset(request: HttpRequest, slot_key: str):
    denied = _require_admin(request)
    if denied:
        return denied
    if not valid_same_origin_request(request):
        return HttpResponse("Forbidden", status=403)
    if slot_key not in SLOT_KEYS:
        return HttpResponse("Not found", status=404)

    try:
        row = SiteImageSlot.objects.get(slot_key=slot_key)
    except SiteImageSlot.DoesNotExist:
        return HttpResponse(status=204)

    path = _resolve_stored_path(row.file_path)
    if path.is_file():
        path.unlink(missing_ok=True)
    row.delete()
    return HttpResponse(status=204)
