from __future__ import annotations

import csv
import json
import time
from io import StringIO
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.mail import send_mail
from django.db.models import Q
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from apps.authx.services import ADMIN_COOKIE, verify_admin_session
from apps.inquiries.models import Inquiry

MAX_BODY_BYTES = 64 * 1024
ATTACHMENT_MAX_BYTES = 10 * 1024 * 1024
ALLOWED_ATTACHMENT_TYPES = {
    "application/pdf",
    "image/png",
    "image/jpeg",
    "image/webp",
    "application/zip",
}
RATE_WINDOW_MS = 60_000
RATE_MAX = 8
_rate_buckets: dict[str, list[int]] = {}


def _json(payload: dict, *, status: int = 200) -> JsonResponse:
    response = JsonResponse(payload, status=status)
    response["Cache-Control"] = "private, no-store, max-age=0"
    return response


def _client_key(request: HttpRequest) -> str:
    return (request.META.get("HTTP_CF_CONNECTING_IP") or request.META.get("REMOTE_ADDR") or "").strip() or "local-origin"


def _rate_limited(client_key: str) -> bool:
    now = int(time.time() * 1000)
    bucket = [ts for ts in _rate_buckets.get(client_key, []) if now - ts < RATE_WINDOW_MS]
    if len(bucket) >= RATE_MAX:
        _rate_buckets[client_key] = bucket
        return True
    bucket.append(now)
    _rate_buckets[client_key] = bucket
    return False


def _allowed_origin(request: HttpRequest) -> bool:
    origin = (request.META.get("HTTP_ORIGIN") or "").rstrip("/")
    if not origin:
        return False
    trusted = {item.rstrip("/") for item in getattr(settings, "CSRF_TRUSTED_ORIGINS", [])}
    trusted.update({"http://127.0.0.1:3000", "http://localhost:3000"})
    return origin in trusted


def _read_payload(request: HttpRequest) -> dict[str, Any]:
    if request.content_type and "application/json" in request.content_type:
        if len(request.body) > MAX_BODY_BYTES:
            return {}
        try:
            data = json.loads(request.body.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}
    return {
        "name": request.POST.get("name") or "",
        "company": request.POST.get("company") or "",
        "country": request.POST.get("country") or "",
        "email": request.POST.get("email") or "",
        "whatsapp": request.POST.get("whatsapp") or "",
        "category": request.POST.get("category") or "",
        "specs": request.POST.get("specs") or "",
        "locale": request.POST.get("locale") or "en",
        "website": request.POST.get("website") or "",
        "turnstileToken": request.POST.get("turnstileToken") or request.POST.get("cf-turnstile-response") or "",
    }


def _text(value: Any, *, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()[:limit]


def _turnstile_ok(payload: dict[str, Any]) -> bool:
    secret = getattr(settings, "TURNSTILE_SECRET_KEY", "") or ""
    if not secret:
        return True
    token = _text(payload.get("turnstileToken"), limit=2048)
    return bool(token)


def _save_attachment(inquiry_id: int, uploaded) -> str:
    inquiries_dir = Path(settings.MEDIA_ROOT) / "inquiries"
    inquiries_dir.mkdir(parents=True, exist_ok=True)
    suffix = Path(getattr(uploaded, "name", "file") or "file").suffix[:8] or ".bin"
    relative = f"inquiries/{inquiry_id}{suffix}"
    dest = Path(settings.MEDIA_ROOT) / relative
    with dest.open("wb") as handle:
        for chunk in uploaded.chunks():
            handle.write(chunk)
    return relative


def _mail_body(inquiry: Inquiry) -> str:
    return "\n".join([
        f"Name: {inquiry.name}",
        f"Company: {inquiry.company or '-'}",
        f"Country: {inquiry.country}",
        f"Email: {inquiry.email or '-'}",
        f"WhatsApp: {inquiry.whatsapp or '-'}",
        f"Category: {inquiry.category}",
        f"Locale: {inquiry.locale}",
        "",
        inquiry.specs,
    ])


def _serialize(inquiry: Inquiry) -> dict[str, Any]:
    return {
        "id": inquiry.id,
        "name": inquiry.name,
        "company": inquiry.company,
        "country": inquiry.country,
        "email": inquiry.email,
        "whatsapp": inquiry.whatsapp,
        "category": inquiry.category,
        "specs": inquiry.specs,
        "locale": inquiry.locale,
        "emailSent": inquiry.email_sent,
        "hasAttachment": bool(inquiry.attachment_path),
        "createdAt": inquiry.created_at.isoformat().replace("+00:00", "Z"),
    }


@csrf_exempt
@require_POST
def create_inquiry(request: HttpRequest):
    if not _allowed_origin(request):
        return HttpResponse("Forbidden", status=403)
    if _rate_limited(_client_key(request)):
        return _json({"error": "rate"}, status=429)

    payload = _read_payload(request)
    if _text(payload.get("website"), limit=200):
        return _json({"ok": True, "emailSent": False}, status=201)

    if not _turnstile_ok(payload):
        return HttpResponse("Forbidden", status=403)

    name = _text(payload.get("name"), limit=120)
    company = _text(payload.get("company"), limit=160)
    country = _text(payload.get("country"), limit=120)
    email = _text(payload.get("email"), limit=120)
    whatsapp = _text(payload.get("whatsapp"), limit=40)
    category = _text(payload.get("category"), limit=160)
    specs = _text(payload.get("specs"), limit=4000)
    locale = _text(payload.get("locale"), limit=8) or "en"

    if not name:
        return _json({"error": "name"}, status=400)
    if not country:
        return _json({"error": "country"}, status=400)
    if not category:
        return _json({"error": "category"}, status=400)
    if not specs:
        return _json({"error": "specs"}, status=400)
    if not email and not whatsapp:
        return _json({"error": "contact"}, status=400)

    uploaded = request.FILES.get("attachment")
    if uploaded:
        if uploaded.size > ATTACHMENT_MAX_BYTES:
            return _json({"error": "attachment"}, status=400)
        content_type = (uploaded.content_type or "").lower()
        if content_type not in ALLOWED_ATTACHMENT_TYPES:
            return _json({"error": "attachment"}, status=400)

    inquiry = Inquiry.objects.create(
        name=name,
        company=company,
        country=country,
        email=email,
        whatsapp=whatsapp,
        category=category,
        specs=specs,
        locale=locale,
    )
    if uploaded:
        inquiry.attachment_path = _save_attachment(inquiry.id, uploaded)
        inquiry.save(update_fields=["attachment_path"])

    subject = f"[Website Inquiry] {category} – {country} – {company or '—'}"
    recipients = [getattr(settings, "EMAIL_TO", "") or "friesgoglobal@gmail.com"]
    sender = getattr(settings, "DEFAULT_FROM_EMAIL", "") or recipients[0]
    try:
        send_mail(subject, _mail_body(inquiry), sender, recipients, fail_silently=False)
        inquiry.email_sent = True
        inquiry.save(update_fields=["email_sent"])
    except Exception:
        inquiry.email_sent = False
        inquiry.save(update_fields=["email_sent"])

    return _json({"ok": True, "id": inquiry.id, "emailSent": inquiry.email_sent}, status=201)


@require_GET
def admin_inquiries(request: HttpRequest):
    if not verify_admin_session(request.COOKIES.get(ADMIN_COOKIE)):
        return HttpResponse("Unauthorized", status=401, headers={"Cache-Control": "no-store"})

    qs = Inquiry.objects.order_by("-created_at", "-id")
    query = (request.GET.get("q") or "").strip()
    if query:
        qs = qs.filter(
            Q(name__icontains=query)
            | Q(company__icontains=query)
            | Q(email__icontains=query)
            | Q(category__icontains=query)
        )
    rows = list(qs[:500])
    if request.GET.get("format") == "csv":
        buffer = StringIO()
        writer = csv.writer(buffer)
        writer.writerow(["id", "created_at", "name", "company", "country", "email", "whatsapp", "category", "locale", "email_sent", "specs"])
        for inquiry in rows:
            writer.writerow([
                inquiry.id,
                inquiry.created_at.isoformat(),
                inquiry.name,
                inquiry.company,
                inquiry.country,
                inquiry.email,
                inquiry.whatsapp,
                inquiry.category,
                inquiry.locale,
                inquiry.email_sent,
                inquiry.specs,
            ])
        response = HttpResponse(buffer.getvalue(), content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = 'attachment; filename="inquiries.csv"'
        response["Cache-Control"] = "private, no-store, max-age=0"
        return response
    return _json({"inquiries": [_serialize(inquiry) for inquiry in rows]})
