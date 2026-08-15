from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any

from django.http import HttpRequest, HttpResponse
from django.utils.dateparse import parse_datetime
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from apps.engage.models import EngageEvent
from apps.engage.sections import SECTION_IDS

ALLOWED_KINDS = frozenset({"section_view", "section_leave", "click"})
MAX_BODY_BYTES = 64 * 1024
DURATION_MS_CAP = 30 * 60 * 1000
RATE_WINDOW_MS = 60_000
RATE_MAX = 120

_rate_buckets: dict[str, list[int]] = {}


def _client_key(request: HttpRequest) -> str:
    return (request.META.get("HTTP_CF_CONNECTING_IP") or "").strip() or "local-origin"


def _rate_limited(client_key: str) -> bool:
    now = int(time.time() * 1000)
    bucket = [ts for ts in _rate_buckets.get(client_key, []) if now - ts < RATE_WINDOW_MS]
    if len(bucket) >= RATE_MAX:
        _rate_buckets[client_key] = bucket
        return True
    bucket.append(now)
    _rate_buckets[client_key] = bucket
    return False


def _parse_at(raw: Any) -> datetime:
    if isinstance(raw, str) and raw.strip():
        parsed = parse_datetime(raw.strip())
        if parsed is not None:
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
    return datetime.now(timezone.utc)


def _validate_event(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    kind = item.get("kind")
    section_id = item.get("sectionId")
    if kind not in ALLOWED_KINDS or not isinstance(section_id, str) or section_id not in SECTION_IDS:
        return None

    target_id = item.get("targetId") or ""
    if not isinstance(target_id, str):
        return None
    if kind == "click" and not target_id.strip():
        return None

    duration_raw = item.get("durationMs")
    duration_ms: int | None = None
    if duration_raw is not None:
        if isinstance(duration_raw, bool) or not isinstance(duration_raw, (int, float)):
            return None
        if duration_raw < 0:
            return None
        duration_ms = min(int(duration_raw), DURATION_MS_CAP)

    path = item.get("path") or ""
    if not isinstance(path, str):
        return None

    return {
        "at": _parse_at(item.get("at") or item.get("enteredAt")),
        "kind": kind,
        "section_id": section_id,
        "target_id": target_id.strip()[:128],
        "duration_ms": duration_ms,
        "path": path[:512],
    }


@csrf_exempt
@require_POST
def engage_ingest(request: HttpRequest):
    content_length = int(request.META.get("CONTENT_LENGTH") or 0)
    body_bytes = request.body or b""
    if content_length > MAX_BODY_BYTES or len(body_bytes) > MAX_BODY_BYTES:
        return HttpResponse("Payload too large", status=413, headers={"Cache-Control": "no-store"})

    client_key = _client_key(request)
    if _rate_limited(client_key):
        return HttpResponse(
            "Too many requests",
            status=429,
            headers={"Retry-After": "60", "Cache-Control": "no-store"},
        )

    try:
        payload = json.loads(body_bytes.decode("utf-8") or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError):
        return HttpResponse("Bad Request", status=400, headers={"Cache-Control": "no-store"})

    if not isinstance(payload, dict) or not isinstance(payload.get("events"), list):
        return HttpResponse("Bad Request", status=400, headers={"Cache-Control": "no-store"})

    parsed: list[dict[str, Any]] = []
    for item in payload["events"]:
        validated = _validate_event(item)
        if validated is None:
            return HttpResponse("Bad Request", status=400, headers={"Cache-Control": "no-store"})
        validated["client_key"] = client_key[:128]
        parsed.append(validated)

    EngageEvent.objects.bulk_create([EngageEvent(**row) for row in parsed])
    return HttpResponse(status=204, headers={"Cache-Control": "no-store, max-age=0"})
