from __future__ import annotations

from datetime import timedelta, timezone
from typing import Any

from django.db.models import Avg, Count, Sum
from django.utils import timezone as dj_timezone

from apps.engage.models import EngageEvent
from apps.engage.sections import SECTION_IDS

WINDOW_LABEL = "24h"
WINDOW_HOURS = 24
TOP_CLICKS_LIMIT = 20


def engagement_summary(*, window: str = WINDOW_LABEL) -> dict[str, Any]:
    now = dj_timezone.now()
    since = now - timedelta(hours=WINDOW_HOURS)
    qs = EngageEvent.objects.filter(at__gte=since)

    views_by_section = {
        row["section_id"]: row["views"]
        for row in qs.filter(kind="section_view")
        .values("section_id")
        .annotate(views=Count("id"))
    }
    dwell_by_section = {
        row["section_id"]: {
            "total": int(row["total"] or 0),
            "avg": int(row["avg"] or 0),
        }
        for row in qs.filter(kind="section_leave", duration_ms__isnull=False)
        .values("section_id")
        .annotate(total=Sum("duration_ms"), avg=Avg("duration_ms"))
    }

    sections: list[dict[str, Any]] = []
    for section_id in sorted(SECTION_IDS):
        dwell = dwell_by_section.get(section_id, {"total": 0, "avg": 0})
        sections.append(
            {
                "sectionId": section_id,
                "views": int(views_by_section.get(section_id, 0)),
                "avgDwellMs": dwell["avg"],
                "totalDwellMs": dwell["total"],
            }
        )

    top_clicks = [
        {
            "sectionId": row["section_id"],
            "targetId": row["target_id"],
            "count": row["count"],
        }
        for row in qs.filter(kind="click")
        .exclude(target_id="")
        .values("section_id", "target_id")
        .annotate(count=Count("id"))
        .order_by("-count", "section_id", "target_id")[:TOP_CLICKS_LIMIT]
    ]

    generated_at = now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return {
        "generatedAt": generated_at,
        "window": window if window else WINDOW_LABEL,
        "sections": sections,
        "topClicks": top_clicks,
    }
