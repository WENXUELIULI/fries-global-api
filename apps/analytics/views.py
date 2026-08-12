from __future__ import annotations

from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.analytics.store import record_analytics_event


class PresenceView(APIView):
    authentication_classes = []
    permission_classes = []

    def get(self, request: Request):
        requested_page = request.query_params.get("page") or "/"
        reported_path = requested_page if requested_page.startswith("/") else "/"
        is_initial_profile = request.query_params.get("profile") == "1"
        record_analytics_event(
            headers=request.headers,
            path=reported_path,
            method="GET",
            status=204,
            kind="presence",
            capture_forensics="visit" if is_initial_profile else None,
            reason="Active device profile" if is_initial_profile else None,
        )
        return Response(status=204, headers={"Cache-Control": "no-store, max-age=0"})


class IngestView(APIView):
    authentication_classes = []
    permission_classes = []

    def post(self, request: Request):
        body = request.data if isinstance(request.data, dict) else None
        if not body or not isinstance(body.get("path"), str) or not isinstance(body.get("method"), str):
            return Response("Bad Request", status=400)
        record_analytics_event(
            headers=request.headers,
            path=body["path"],
            method=body["method"],
            status=int(body.get("status") or 0),
            kind=body.get("kind"),
            reason=body.get("reason"),
            query=body.get("query"),
            capture_forensics=body.get("captureForensics"),
        )
        return Response(status=204, headers={"Cache-Control": "no-store, max-age=0"})
