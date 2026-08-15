from __future__ import annotations

import json
import re
import time
from typing import Any

from django.http import FileResponse, HttpRequest, HttpResponse, HttpResponseRedirect, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.analytics.store import analytics_csv, get_analytics_summary, record_analytics_event
from apps.authx.services import (
    ADMIN_COOKIE,
    ADMIN_SESSION_SECONDS,
    create_admin_session,
    request_is_https,
    valid_same_origin_request,
    verify_admin_session,
    verify_credentials,
)
from apps.authx.users import (
    AVATAR_MAX_BYTES,
    USER_COOKIE,
    USER_SESSION_SECONDS,
    UserStoreError,
    create_site_session,
    create_site_user,
    delete_site_user,
    get_avatar_file,
    list_site_users,
    login_page_path,
    register_public_user,
    registration_enabled,
    safe_next_path,
    set_registration_enabled,
    update_own_profile,
    update_site_user,
    verify_site_credentials,
    verify_site_user_session,
)
from apps.forensics.store import forensic_evidence_export

WINDOW_MS = 15 * 60_000
BLOCK_MS = 30 * 60_000
MAX_FAILURES = 5
PROFILE_MAX_BYTES = 400_000
_login_attempts: dict[str, dict[str, Any]] = {}


def _client_key(request: HttpRequest) -> str:
    return (request.META.get("HTTP_CF_CONNECTING_IP") or "").strip() or "local-origin"


def _wants_json(request: HttpRequest) -> bool:
    return "application/json" in (request.META.get("HTTP_ACCEPT") or "").lower()


def _request_fields(request: HttpRequest) -> dict[str, str]:
    content_type = (request.META.get("CONTENT_TYPE") or "").split(";")[0].strip().lower()
    if content_type == "application/json":
        try:
            payload = json.loads(request.body.decode("utf-8") or "{}")
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {}
        if not isinstance(payload, dict):
            return {}
        return {str(key): "" if value is None else str(value) for key, value in payload.items()}
    return {key: str(request.POST.get(key) or "") for key in request.POST}


def _json(payload: dict[str, Any], status: int = 200) -> JsonResponse:
    response = JsonResponse(payload, status=status)
    response["Cache-Control"] = "no-store, max-age=0"
    return response


def _set_admin_cookie(response: HttpResponse, request: HttpRequest) -> None:
    response.set_cookie(
        ADMIN_COOKIE,
        create_admin_session(),
        max_age=ADMIN_SESSION_SECONDS,
        httponly=True,
        secure=request_is_https(request),
        samesite="Strict",
        path="/admin",
    )


def _set_user_cookie(response: HttpResponse, request: HttpRequest, user_id: str) -> None:
    response.set_cookie(
        USER_COOKIE,
        create_site_session(user_id),
        max_age=USER_SESSION_SECONDS,
        httponly=True,
        secure=request_is_https(request),
        samesite="Strict",
        path="/",
    )


def _session_token(request: HttpRequest | Request) -> str | None:
    return request.COOKIES.get(ADMIN_COOKIE)


@csrf_exempt
@require_POST
def admin_login(request: HttpRequest):
    content_length = int(request.META.get("CONTENT_LENGTH") or 0)
    if not valid_same_origin_request(request) or content_length > 2048:
        record_analytics_event(
            headers=request.headers,
            path="/admin/api/login",
            method="POST",
            status=403,
            kind="security",
            reason="Rejected cross-site or oversized admin login",
        )
        return HttpResponse("Forbidden", status=403, headers={"Cache-Control": "no-store"})

    now = int(time.time() * 1000)
    key = _client_key(request)
    state = _login_attempts.get(key) or {"startedAt": now, "failures": 0, "blockedUntil": 0}
    if now - state["startedAt"] > WINDOW_MS:
        state = {"startedAt": now, "failures": 0, "blockedUntil": 0}
    if state["blockedUntil"] > now:
        record_analytics_event(
            headers=request.headers,
            path="/admin/api/login",
            method="POST",
            status=429,
            kind="security",
            reason="Admin login temporarily blocked",
        )
        retry = max(1, int((state["blockedUntil"] - now) / 1000))
        return HttpResponse(
            "Too many login attempts",
            status=429,
            headers={"Retry-After": str(retry), "Cache-Control": "no-store"},
        )

    username = str(request.POST.get("username") or "")
    password = str(request.POST.get("password") or "")
    honeypot = str(request.POST.get("website") or "")
    if honeypot or not verify_credentials(username, password):
        state["failures"] += 1
        if state["failures"] >= MAX_FAILURES:
            state["blockedUntil"] = now + BLOCK_MS
        _login_attempts[key] = state
        record_analytics_event(
            headers=request.headers,
            path="/admin/api/login",
            method="POST",
            status=401,
            kind="security",
            reason="Admin login honeypot triggered" if honeypot else "Invalid admin credentials",
        )
        response = HttpResponseRedirect("/admin/login?error=1", status=303)
        response["Cache-Control"] = "no-store, max-age=0"
        return response

    _login_attempts.pop(key, None)
    response = HttpResponseRedirect("/admin", status=303)
    response.set_cookie(
        ADMIN_COOKIE,
        create_admin_session(),
        max_age=ADMIN_SESSION_SECONDS,
        httponly=True,
        secure=request_is_https(request),
        samesite="Strict",
        path="/admin",
    )
    response["Cache-Control"] = "no-store, max-age=0"
    return response


@csrf_exempt
@require_POST
def admin_logout(request: HttpRequest):
    if not valid_same_origin_request(request):
        record_analytics_event(
            headers=request.headers,
            path="/admin/api/logout",
            method="POST",
            status=403,
            kind="security",
            reason="Rejected cross-site admin logout",
        )
        return HttpResponse("Forbidden", status=403)
    response = HttpResponseRedirect("/admin/login?signedout=1", status=303)
    response.set_cookie(
        ADMIN_COOKIE,
        "",
        max_age=0,
        httponly=True,
        samesite="Strict",
        path="/admin",
    )
    response["Cache-Control"] = "no-store, max-age=0"
    return response


class AdminSessionView(APIView):
    authentication_classes = []
    permission_classes = []

    def get(self, request: Request):
        if not verify_admin_session(_session_token(request)):
            return Response("Unauthorized", status=401)
        return Response(status=204, headers={"Cache-Control": "private, no-store, max-age=0"})


class AdminSummaryView(APIView):
    authentication_classes = []
    permission_classes = []

    def get(self, request: Request):
        if not verify_admin_session(_session_token(request)):
            return Response("Unauthorized", status=401)
        return Response(
            get_analytics_summary(),
            headers={"Cache-Control": "private, no-store, max-age=0"},
        )


class AdminExportView(APIView):
    authentication_classes = []
    permission_classes = []

    def get(self, request: Request):
        if not verify_admin_session(_session_token(request)):
            return Response("Unauthorized", status=401)
        day = time.strftime("%Y-%m-%d", time.gmtime())
        return HttpResponse(
            analytics_csv(),
            content_type="text/csv; charset=utf-8",
            headers={
                "Cache-Control": "private, no-store, max-age=0",
                "Content-Disposition": f'attachment; filename="fries-global-analytics-{day}.csv"',
            },
        )


class AdminForensicsView(APIView):
    authentication_classes = []
    permission_classes = []

    def get(self, request: Request):
        if not verify_admin_session(_session_token(request)):
            return Response("Unauthorized", status=401)
        day = time.strftime("%Y-%m-%d", time.gmtime())
        return Response(
            forensic_evidence_export(),
            headers={
                "Cache-Control": "private, no-store, max-age=0",
                "Content-Disposition": f'attachment; filename="fries-global-forensics-{day}.json"',
            },
        )


def _as_bool(value: str | None) -> bool:
    return str(value or "").lower() in {"1", "true", "on", "yes"}


def _user_login_attempts_key(request: HttpRequest) -> str:
    return f"user:{_client_key(request)}"


@csrf_exempt
@require_POST
def site_login(request: HttpRequest):
    content_length = int(request.META.get("CONTENT_LENGTH") or 0)
    fields = _request_fields(request)
    next_path = safe_next_path(fields.get("next"))
    if not valid_same_origin_request(request) or content_length > 2048:
        record_analytics_event(
            headers=request.headers,
            path="/api/auth/login",
            method="POST",
            status=403,
            kind="security",
            reason="Rejected cross-site or oversized site login",
        )
        return HttpResponse("Forbidden", status=403, headers={"Cache-Control": "no-store"})

    now = int(time.time() * 1000)
    key = _user_login_attempts_key(request)
    state = _login_attempts.get(key) or {"startedAt": now, "failures": 0, "blockedUntil": 0}
    if now - state["startedAt"] > WINDOW_MS:
        state = {"startedAt": now, "failures": 0, "blockedUntil": 0}
    if state["blockedUntil"] > now:
        retry = max(1, int((state["blockedUntil"] - now) / 1000))
        if _wants_json(request):
            response = _json({"ok": False, "error": "rate"}, status=429)
            response["Retry-After"] = str(retry)
            return response
        return HttpResponse(
            "Too many login attempts",
            status=429,
            headers={"Retry-After": str(retry), "Cache-Control": "no-store"},
        )

    username = fields.get("username") or ""
    password = fields.get("password") or ""
    honeypot = fields.get("website") or ""
    admin_ok = (not honeypot) and verify_credentials(username, password)
    user = None if honeypot or admin_ok else verify_site_credentials(username, password)
    if not admin_ok and not user:
        state["failures"] += 1
        if state["failures"] >= MAX_FAILURES:
            state["blockedUntil"] = now + BLOCK_MS
        _login_attempts[key] = state
        record_analytics_event(
            headers=request.headers,
            path="/api/auth/login",
            method="POST",
            status=401,
            kind="security",
            reason="Site login honeypot triggered" if honeypot else "Invalid site credentials",
        )
        if _wants_json(request):
            return _json({"ok": False, "error": "invalid"}, status=401)
        response = HttpResponseRedirect(login_page_path(next_path, error=True), status=303)
        response["Cache-Control"] = "no-store, max-age=0"
        return response

    _login_attempts.pop(key, None)
    if admin_ok:
        response = _json({"ok": True, "role": "admin", "next": "/admin"}) if _wants_json(request) else HttpResponseRedirect("/admin", status=303)
        _set_admin_cookie(response, request)
        response["Cache-Control"] = "no-store, max-age=0"
        return response

    response = _json({"ok": True, "role": "user", **user}) if _wants_json(request) else HttpResponseRedirect(next_path, status=303)
    _set_user_cookie(response, request, user["id"])
    response["Cache-Control"] = "no-store, max-age=0"
    return response


@csrf_exempt
@require_POST
def site_logout(request: HttpRequest):
    fields = _request_fields(request)
    next_path = safe_next_path(fields.get("next") or "/en")
    if not valid_same_origin_request(request):
        return HttpResponse("Forbidden", status=403)
    response = _json({"ok": True}) if _wants_json(request) else HttpResponseRedirect(next_path, status=303)
    response.set_cookie(
        USER_COOKIE,
        "",
        max_age=0,
        httponly=True,
        samesite="Strict",
        path="/",
    )
    response["Cache-Control"] = "no-store, max-age=0"
    return response


@csrf_exempt
@require_POST
def site_register(request: HttpRequest):
    content_length = int(request.META.get("CONTENT_LENGTH") or 0)
    fields = _request_fields(request)
    next_path = safe_next_path(fields.get("next"))
    if not valid_same_origin_request(request) or content_length > 2048:
        record_analytics_event(
            headers=request.headers,
            path="/api/auth/register",
            method="POST",
            status=403,
            kind="security",
            reason="Rejected cross-site or oversized site register",
        )
        return HttpResponse("Forbidden", status=403, headers={"Cache-Control": "no-store"})

    def fail(code: str) -> HttpResponse:
        if _wants_json(request):
            status = 429 if code == "rate" else 403 if code == "closed" else 400
            return _json({"ok": False, "error": code}, status=status)
        response = HttpResponseRedirect(login_page_path(next_path, error=code, register=True), status=303)
        response["Cache-Control"] = "no-store, max-age=0"
        return response

    if fields.get("website"):
        record_analytics_event(
            headers=request.headers,
            path="/api/auth/register",
            method="POST",
            status=401,
            kind="security",
            reason="Site register honeypot triggered",
        )
        return fail("invalid")
    try:
        user = register_public_user(
            fields.get("username") or "",
            fields.get("password") or "",
            fields.get("confirm") or "",
            _client_key(request),
        )
    except UserStoreError as exc:
        return fail(exc.code)
    response = _json({"ok": True, "role": "user", **user}) if _wants_json(request) else HttpResponseRedirect(next_path, status=303)
    _set_user_cookie(response, request, user["id"])
    response["Cache-Control"] = "no-store, max-age=0"
    return response


class SiteSessionView(APIView):
    authentication_classes = []
    permission_classes = []

    def get(self, request: Request):
        user = verify_site_user_session(request.COOKIES.get(USER_COOKIE))
        if not user:
            return Response("Unauthorized", status=401)
        return Response(
            {"ok": True, **user},
            headers={"Cache-Control": "private, no-store, max-age=0"},
        )


@csrf_exempt
def site_profile(request: HttpRequest):
    user = verify_site_user_session(request.COOKIES.get(USER_COOKIE))
    if not user:
        return HttpResponse("Unauthorized", status=401, headers={"Cache-Control": "no-store"})
    if request.method == "GET":
        return _json(user)
    if request.method != "POST":
        return HttpResponse("Method not allowed", status=405, headers={"Allow": "GET, POST"})
    if not valid_same_origin_request(request):
        return HttpResponse("Forbidden", status=403)
    content_length = int(request.META.get("CONTENT_LENGTH") or 0)
    if content_length > PROFILE_MAX_BYTES:
        return HttpResponse("Payload too large", status=413)
    avatar = request.FILES.get("avatar")
    avatar_bytes = None
    if avatar:
        if getattr(avatar, "size", 0) > AVATAR_MAX_BYTES:
            return _json({"ok": False, "error": "too-large"}, status=400)
        avatar_bytes = avatar.read(AVATAR_MAX_BYTES + 1)
        if len(avatar_bytes) > AVATAR_MAX_BYTES:
            return _json({"ok": False, "error": "too-large"}, status=400)
    try:
        updated = update_own_profile(
            user["id"],
            display_name=str(request.POST.get("displayName") or ""),
            phone=str(request.POST.get("phone") or ""),
            email=str(request.POST.get("email") or ""),
            address=str(request.POST.get("address") or ""),
            password=str(request.POST.get("password") or ""),
            confirm=str(request.POST.get("confirm") or ""),
            avatar=avatar_bytes,
        )
    except UserStoreError as exc:
        return _json({"ok": False, "error": exc.code}, status=400)
    return _json(updated)


def site_avatar(request: HttpRequest, user_id: str):
    if not re.fullmatch(r"usr_[A-Fa-f0-9]+", user_id or ""):
        return HttpResponse("Not found", status=404)
    found = get_avatar_file(user_id)
    if not found:
        return HttpResponse("Not found", status=404)
    path, content_type = found
    response = FileResponse(path.open("rb"), content_type=content_type)
    response["Cache-Control"] = "private, max-age=300"
    return response


class SiteAuthSettingsView(APIView):
    authentication_classes = []
    permission_classes = []

    def get(self, request: Request):
        return Response(
            {"registrationEnabled": registration_enabled()},
            headers={"Cache-Control": "private, no-store, max-age=0"},
        )


def _require_admin(request: HttpRequest) -> HttpResponse | None:
    if not verify_admin_session(request.COOKIES.get(ADMIN_COOKIE)):
        return HttpResponse("Unauthorized", status=401, headers={"Cache-Control": "no-store"})
    return None


@csrf_exempt
def admin_users_collection(request: HttpRequest):
    denied = _require_admin(request)
    if denied:
        return denied
    if request.method == "GET":
        response = HttpResponse(
            json.dumps({"users": list_site_users(), "registrationEnabled": registration_enabled()}),
            content_type="application/json",
        )
        response["Cache-Control"] = "private, no-store, max-age=0"
        return response
    if request.method != "POST":
        return HttpResponse("Method not allowed", status=405, headers={"Allow": "GET, POST"})
    if not valid_same_origin_request(request):
        return HttpResponse("Forbidden", status=403)
    if str(request.POST.get("website") or ""):
        return HttpResponse("Forbidden", status=403)
    try:
        create_site_user(
            str(request.POST.get("username") or ""),
            str(request.POST.get("password") or ""),
            str(request.POST.get("note") or ""),
        )
    except UserStoreError as exc:
        response = HttpResponseRedirect(f"/admin/users?error={exc.code}", status=303)
        response["Cache-Control"] = "no-store, max-age=0"
        return response
    response = HttpResponseRedirect("/admin/users?created=1", status=303)
    response["Cache-Control"] = "no-store, max-age=0"
    return response


@csrf_exempt
@require_POST
def admin_users_settings(request: HttpRequest):
    denied = _require_admin(request)
    if denied:
        return denied
    if not valid_same_origin_request(request):
        return HttpResponse("Forbidden", status=403)
    set_registration_enabled(_as_bool(str(request.POST.get("registrationEnabled"))))
    response = HttpResponseRedirect("/admin/users?settings=1", status=303)
    response["Cache-Control"] = "no-store, max-age=0"
    return response


@csrf_exempt
@require_POST
def admin_user_update(request: HttpRequest, user_id: str):
    denied = _require_admin(request)
    if denied:
        return denied
    if not valid_same_origin_request(request):
        return HttpResponse("Forbidden", status=403)
    enabled = request.POST.get("enabled")
    password = request.POST.get("password")
    note = request.POST.get("note")
    try:
        update_site_user(
            user_id,
            enabled=None if enabled is None else _as_bool(str(enabled)),
            password=None if password is None else str(password),
            note=None if note is None else str(note),
        )
    except UserStoreError as exc:
        response = HttpResponseRedirect(f"/admin/users?error={exc.code}", status=303)
        response["Cache-Control"] = "no-store, max-age=0"
        return response
    response = HttpResponseRedirect("/admin/users?updated=1", status=303)
    response["Cache-Control"] = "no-store, max-age=0"
    return response


@csrf_exempt
@require_POST
def admin_user_delete(request: HttpRequest, user_id: str):
    denied = _require_admin(request)
    if denied:
        return denied
    if not valid_same_origin_request(request):
        return HttpResponse("Forbidden", status=403)
    try:
        delete_site_user(user_id)
    except UserStoreError as exc:
        response = HttpResponseRedirect(f"/admin/users?error={exc.code}", status=303)
        response["Cache-Control"] = "no-store, max-age=0"
        return response
    response = HttpResponseRedirect("/admin/users?deleted=1", status=303)
    response["Cache-Control"] = "no-store, max-age=0"
    return response
