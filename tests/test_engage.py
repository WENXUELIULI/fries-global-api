import json
from datetime import timedelta
from pathlib import Path

import pytest
from django.test import Client
from django.utils import timezone as dj_timezone

from apps.authx.services import ADMIN_COOKIE, create_admin_session
from apps.authx.users import USER_COOKIE, create_site_user
from apps.engage.models import EngageEvent
from apps.engage.sections import SECTION_IDS


def test_section_ids():
    assert "hero" in SECTION_IDS
    assert "cta" in SECTION_IDS
    assert "categories" in SECTION_IDS
    assert "services" in SECTION_IDS
    assert "process" in SECTION_IDS
    assert "why-us" in SECTION_IDS
    assert "global-network" in SECTION_IDS
    assert "footer" in SECTION_IDS
    assert "nav" in SECTION_IDS


@pytest.mark.django_db
def test_engage_accepts_batch(client):
    r = client.post(
        "/api/engage",
        data=json.dumps(
            {
                "events": [
                    {"kind": "section_leave", "sectionId": "hero", "durationMs": 1200},
                    {"kind": "click", "sectionId": "cta", "targetId": "primary-cta"},
                ]
            }
        ),
        content_type="application/json",
    )
    assert r.status_code == 204
    assert EngageEvent.objects.count() == 2


@pytest.mark.django_db
def test_engage_rejects_bad_section(client):
    r = client.post(
        "/api/engage",
        data=json.dumps({"events": [{"kind": "click", "sectionId": "nope", "targetId": "x"}]}),
        content_type="application/json",
    )
    assert r.status_code == 400
    assert EngageEvent.objects.count() == 0


@pytest.mark.django_db
def test_engage_rejects_oversized_body(client):
    payload = json.dumps(
        {
            "events": [
                {
                    "kind": "section_view",
                    "sectionId": "hero",
                    "path": "x" * 70_000,
                }
            ]
        }
    )
    assert len(payload.encode("utf-8")) > 64 * 1024
    r = client.post("/api/engage", data=payload, content_type="application/json")
    assert r.status_code == 413
    assert EngageEvent.objects.count() == 0


@pytest.mark.django_db
def test_engage_caps_duration_ms(client):
    r = client.post(
        "/api/engage",
        data=json.dumps(
            {
                "events": [
                    {
                        "kind": "section_leave",
                        "sectionId": "hero",
                        "durationMs": 30 * 60 * 1000 + 5000,
                    }
                ]
            }
        ),
        content_type="application/json",
    )
    assert r.status_code == 204
    event = EngageEvent.objects.get()
    assert event.duration_ms == 30 * 60 * 1000


def _origin_headers() -> dict[str, str]:
    return {"HTTP_ORIGIN": "http://localhost:3000", "HTTP_HOST": "localhost:3000"}


def _admin_client(isolated_private: Path) -> Client:
    client = Client()
    client.cookies[ADMIN_COOKIE] = create_admin_session()
    return client


@pytest.mark.django_db
def test_engagement_summary_requires_admin(isolated_private):
    client = Client()
    assert client.get("/admin/api/engagement").status_code == 401


@pytest.mark.django_db
def test_engagement_summary_rejects_site_user(isolated_private):
    create_site_user("buyer01", "Buyer-Pass-123")
    client = Client()
    login = client.post(
        "/api/auth/login",
        data={"username": "buyer01", "password": "Buyer-Pass-123", "website": ""},
        **_origin_headers(),
    )
    client.cookies[USER_COOKIE] = login.cookies[USER_COOKIE].value
    assert client.get("/admin/api/engagement").status_code == 401


@pytest.mark.django_db
def test_engagement_summary_aggregates(isolated_private):
    now = dj_timezone.now()
    EngageEvent.objects.bulk_create(
        [
            EngageEvent(
                at=now,
                kind="section_view",
                section_id="hero",
                client_key="a",
            ),
            EngageEvent(
                at=now,
                kind="section_view",
                section_id="hero",
                client_key="b",
            ),
            EngageEvent(
                at=now,
                kind="section_leave",
                section_id="hero",
                duration_ms=1000,
                client_key="a",
            ),
            EngageEvent(
                at=now,
                kind="section_leave",
                section_id="hero",
                duration_ms=3000,
                client_key="b",
            ),
            EngageEvent(
                at=now,
                kind="click",
                section_id="cta",
                target_id="primary-cta",
                client_key="a",
            ),
            EngageEvent(
                at=now,
                kind="click",
                section_id="cta",
                target_id="primary-cta",
                client_key="b",
            ),
            EngageEvent(
                at=now,
                kind="click",
                section_id="nav",
                target_id="logo",
                client_key="a",
            ),
            EngageEvent(
                at=now - timedelta(hours=30),
                kind="section_leave",
                section_id="hero",
                duration_ms=99999,
                client_key="old",
            ),
        ]
    )

    client = _admin_client(isolated_private)
    response = client.get("/admin/api/engagement")
    assert response.status_code == 200
    body = response.json()
    assert body["window"] == "24h"
    assert "generatedAt" in body

    hero = next(row for row in body["sections"] if row["sectionId"] == "hero")
    assert hero["views"] == 2
    assert hero["totalDwellMs"] == 4000
    assert hero["avgDwellMs"] == 2000

    assert body["topClicks"][0] == {
        "sectionId": "cta",
        "targetId": "primary-cta",
        "count": 2,
    }
