from __future__ import annotations

import time
from typing import Any

from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
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
from apps.forensics.store import forensic_evidence_export

WINDOW_MS = 15 * 60_000
BLOCK_MS = 30 * 60_000
MAX_FAILURES = 5
_login_attempts: dict[str, dict[str, Any]] = {}


def _client_key(request: HttpRequest) -> str:
    return (request.META.get("HTTP_CF_CONNECTING_IP") or "").strip() or "local-origin"


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
