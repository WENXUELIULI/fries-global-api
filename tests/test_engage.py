import json

import pytest
from django.test import Client

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
