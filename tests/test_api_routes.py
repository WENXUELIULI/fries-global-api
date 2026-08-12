import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from django.test import Client

from apps.authx.services import ADMIN_COOKIE, create_admin_session

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture()
def isolated_private(tmp_path, settings):
    settings.BASE_DIR = tmp_path
    private = tmp_path / ".private"
    access = tmp_path / "ADMIN_ACCESS.txt"
    if private.exists():
        shutil.rmtree(private)
    access.unlink(missing_ok=True)
    env = {**os.environ, "PYTHONPATH": str(PROJECT_ROOT)}
    subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "scripts" / "init_admin.py")],
        cwd=tmp_path,
        check=True,
        env=env,
    )
    yield tmp_path


def test_presence_returns_204(isolated_private):
    client = Client()
    response = client.get("/api/presence", {"page": "/en", "profile": "1"}, HTTP_USER_AGENT="pytest")
    assert response.status_code == 204
    assert "no-store" in response.headers.get("Cache-Control", "")


def test_ingest_accepts_visit(isolated_private):
    client = Client()
    response = client.post(
        "/api/ingest",
        data={"path": "/en", "method": "GET", "status": 200, "kind": "visit"},
        content_type="application/json",
        HTTP_USER_AGENT="pytest",
    )
    assert response.status_code == 204


def test_ingest_rejects_bad_body(isolated_private):
    client = Client()
    response = client.post(
        "/api/ingest",
        data={"foo": "bar"},
        content_type="application/json",
    )
    assert response.status_code == 400


def test_admin_summary_rejects_missing_session(isolated_private):
    client = Client()
    response = client.get("/admin/api/summary")
    assert response.status_code == 401


def test_admin_summary_returns_json_with_session(isolated_private):
    token = create_admin_session()
    client = Client()
    client.cookies[ADMIN_COOKIE] = token
    response = client.get("/admin/api/summary")
    assert response.status_code == 200
    body = response.json()
    assert "generatedAt" in body


def _read_access(isolated_private: Path) -> tuple[str, str]:
    text = (isolated_private / "ADMIN_ACCESS.txt").read_text(encoding="utf-8")
    username = ""
    password = ""
    for line in text.splitlines():
        if line.startswith("Username:"):
            username = line.split(":", 1)[1].strip()
        if line.startswith("Password:"):
            password = line.split(":", 1)[1].strip()
    return username, password


def test_admin_login_rejects_cross_origin(isolated_private):
    client = Client()
    response = client.post(
        "/admin/api/login",
        data={"username": "x", "password": "y", "website": ""},
        HTTP_ORIGIN="http://evil.example",
        HTTP_HOST="127.0.0.1:3000",
    )
    assert response.status_code == 403


def test_admin_login_success_sets_cookie(isolated_private):
    username, password = _read_access(isolated_private)
    client = Client()
    response = client.post(
        "/admin/api/login",
        data={"username": username, "password": password, "website": ""},
        HTTP_ORIGIN="http://127.0.0.1:3000",
        HTTP_HOST="127.0.0.1:3000",
    )
    assert response.status_code == 303
    assert response["Location"] == "/admin"
    assert ADMIN_COOKIE in response.cookies


def test_admin_login_bad_credentials_redirects(isolated_private):
    client = Client()
    response = client.post(
        "/admin/api/login",
        data={"username": "nope", "password": "nope", "website": ""},
        HTTP_ORIGIN="http://127.0.0.1:3000",
        HTTP_HOST="127.0.0.1:3000",
    )
    assert response.status_code == 303
    assert "error=1" in response["Location"]


def test_admin_logout_clears_cookie(isolated_private):
    client = Client()
    response = client.post(
        "/admin/api/logout",
        HTTP_ORIGIN="http://127.0.0.1:3000",
        HTTP_HOST="127.0.0.1:3000",
    )
    assert response.status_code == 303
    assert "signedout=1" in response["Location"]
    assert response.cookies[ADMIN_COOKIE]["max-age"] == 0
