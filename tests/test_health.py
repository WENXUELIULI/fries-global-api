import pytest
from django.test import Client


@pytest.mark.django_db
def test_health_ok():
    c = Client()
    r = c.get("/health")
    assert r.status_code == 200
    assert r.json() == {"ok": True}
