from pathlib import Path
import json
import struct
import zlib

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client

from apps.authx.services import ADMIN_COOKIE, create_admin_session
from apps.authx.users import USER_COOKIE, create_site_user, list_site_users, verify_site_user_session


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


def _admin_client(isolated_private: Path) -> Client:
    client = Client()
    client.cookies[ADMIN_COOKIE] = create_admin_session()
    return client


def _origin_headers() -> dict[str, str]:
    return {"HTTP_ORIGIN": "http://127.0.0.1:3000", "HTTP_HOST": "127.0.0.1:3000"}


def test_site_session_rejects_missing_cookie(isolated_private):
    client = Client()
    response = client.get("/api/auth/session")
    assert response.status_code == 401


def test_admin_users_rejects_missing_session(isolated_private):
    client = Client()
    response = client.get("/admin/api/users")
    assert response.status_code == 401


def test_admin_can_create_and_list_site_users(isolated_private):
    client = _admin_client(isolated_private)
    created = client.post(
        "/admin/api/users",
        data={"username": "buyer01", "password": "Buyer-Pass-123", "note": "EU buyer", "website": ""},
        **_origin_headers(),
    )
    assert created.status_code == 303
    assert "created=1" in created["Location"]

    listed = client.get("/admin/api/users")
    assert listed.status_code == 200
    body = listed.json()
    assert len(body["users"]) == 1
    user = body["users"][0]
    assert user["username"] == "buyer01"
    assert user["enabled"] is True
    assert user["note"] == "EU buyer"
    assert "passwordHash" not in user
    assert "passwordSalt" not in user


def test_duplicate_username_is_rejected(isolated_private):
    client = _admin_client(isolated_private)
    client.post(
        "/admin/api/users",
        data={"username": "buyer01", "password": "Buyer-Pass-123", "website": ""},
        **_origin_headers(),
    )
    duplicate = client.post(
        "/admin/api/users",
        data={"username": "Buyer01", "password": "Buyer-Pass-456", "website": ""},
        **_origin_headers(),
    )
    assert duplicate.status_code == 303
    assert "error=duplicate" in duplicate["Location"]
    assert len(list_site_users()) == 1


def test_site_user_login_sets_cookie_and_session(isolated_private):
    create_site_user("buyer01", "Buyer-Pass-123")
    client = Client()
    response = client.post(
        "/api/auth/login",
        data={"username": "buyer01", "password": "Buyer-Pass-123", "website": "", "next": "/zh"},
        **_origin_headers(),
    )
    assert response.status_code == 303
    assert response["Location"] == "/zh"
    assert USER_COOKIE in response.cookies

    client.cookies[USER_COOKIE] = response.cookies[USER_COOKIE].value
    session = client.get("/api/auth/session")
    assert session.status_code == 200
    assert session.json()["username"] == "buyer01"
    assert verify_site_user_session(response.cookies[USER_COOKIE].value)["username"] == "buyer01"


def test_site_login_rejects_open_redirect(isolated_private):
    create_site_user("buyer01", "Buyer-Pass-123")
    client = Client()
    response = client.post(
        "/api/auth/login",
        data={
            "username": "buyer01",
            "password": "Buyer-Pass-123",
            "website": "",
            "next": "https://evil.example/phish",
        },
        **_origin_headers(),
    )
    assert response.status_code == 303
    assert response["Location"] == "/en"


def test_disabled_user_cannot_login(isolated_private):
    user = create_site_user("buyer01", "Buyer-Pass-123")
    admin = _admin_client(isolated_private)
    disable = admin.post(
        f"/admin/api/users/{user['id']}",
        data={"enabled": "0"},
        **_origin_headers(),
    )
    assert disable.status_code == 303

    client = Client()
    response = client.post(
        "/api/auth/login",
        data={"username": "buyer01", "password": "Buyer-Pass-123", "website": ""},
        **_origin_headers(),
    )
    assert response.status_code == 303
    assert "error=1" in response["Location"]
    assert USER_COOKIE not in response.cookies


def test_deleted_user_session_is_invalid(isolated_private):
    user = create_site_user("buyer01", "Buyer-Pass-123")
    client = Client()
    login = client.post(
        "/api/auth/login",
        data={"username": "buyer01", "password": "Buyer-Pass-123", "website": ""},
        **_origin_headers(),
    )
    token = login.cookies[USER_COOKIE].value
    admin = _admin_client(isolated_private)
    deleted = admin.post(
        f"/admin/api/users/{user['id']}/delete",
        **_origin_headers(),
    )
    assert deleted.status_code == 303
    assert "deleted=1" in deleted["Location"]

    client.cookies[USER_COOKIE] = token
    session = client.get("/api/auth/session")
    assert session.status_code == 401


def test_site_user_cannot_access_admin_summary(isolated_private):
    create_site_user("buyer01", "Buyer-Pass-123")
    client = Client()
    login = client.post(
        "/api/auth/login",
        data={"username": "buyer01", "password": "Buyer-Pass-123", "website": ""},
        **_origin_headers(),
    )
    client.cookies[USER_COOKIE] = login.cookies[USER_COOKIE].value
    response = client.get("/admin/api/summary")
    assert response.status_code == 401


def test_site_logout_clears_cookie(isolated_private):
    client = Client()
    response = client.post("/api/auth/logout", data={"next": "/zh"}, **_origin_headers())
    assert response.status_code == 303
    assert response["Location"] == "/zh"
    assert response.cookies[USER_COOKIE]["max-age"] == 0


def test_auth_settings_default_to_open_registration(isolated_private):
    client = Client()
    response = client.get("/api/auth/settings")
    assert response.status_code == 200
    assert response.json()["registrationEnabled"] is True


def test_public_register_creates_user_and_session(isolated_private):
    client = Client()
    response = client.post(
        "/api/auth/register",
        data={
            "username": "buyer01",
            "password": "Buyer-Pass-123",
            "confirm": "Buyer-Pass-123",
            "website": "",
            "next": "/zh",
        },
        **_origin_headers(),
    )
    assert response.status_code == 303
    assert response["Location"] == "/zh"
    assert USER_COOKIE in response.cookies
    users = list_site_users()
    assert len(users) == 1
    assert users[0]["username"] == "buyer01"


def test_public_register_rejects_password_mismatch(isolated_private):
    client = Client()
    response = client.post(
        "/api/auth/register",
        data={
            "username": "buyer01",
            "password": "Buyer-Pass-123",
            "confirm": "Buyer-Pass-999",
            "website": "",
        },
        **_origin_headers(),
    )
    assert response.status_code == 303
    assert "error=mismatch" in response["Location"]
    assert "register=1" in response["Location"]
    assert list_site_users() == []


def test_public_register_rate_limits_same_client(isolated_private):
    client = Client()
    first = client.post(
        "/api/auth/register",
        data={
            "username": "buyer01",
            "password": "Buyer-Pass-123",
            "confirm": "Buyer-Pass-123",
            "website": "",
        },
        **_origin_headers(),
    )
    assert first.status_code == 303
    second = client.post(
        "/api/auth/register",
        data={
            "username": "buyer02",
            "password": "Buyer-Pass-123",
            "confirm": "Buyer-Pass-123",
            "website": "",
        },
        **_origin_headers(),
    )
    assert second.status_code == 303
    assert "error=rate" in second["Location"]
    assert len(list_site_users()) == 1


def test_admin_can_disable_public_registration(isolated_private):
    admin = _admin_client(isolated_private)
    listed = admin.get("/admin/api/users")
    assert listed.status_code == 200
    assert listed.json()["registrationEnabled"] is True

    closed = admin.post(
        "/admin/api/users/settings",
        data={"registrationEnabled": "0"},
        **_origin_headers(),
    )
    assert closed.status_code == 303
    assert "settings=1" in closed["Location"]
    assert admin.get("/admin/api/users").json()["registrationEnabled"] is False
    assert Client().get("/api/auth/settings").json()["registrationEnabled"] is False

    rejected = Client().post(
        "/api/auth/register",
        data={
            "username": "buyer01",
            "password": "Buyer-Pass-123",
            "confirm": "Buyer-Pass-123",
            "website": "",
        },
        **_origin_headers(),
    )
    assert rejected.status_code == 303
    assert "error=closed" in rejected["Location"]
    assert list_site_users() == []

    created = admin.post(
        "/admin/api/users",
        data={"username": "buyer01", "password": "Buyer-Pass-123", "website": ""},
        **_origin_headers(),
    )
    assert created.status_code == 303
    assert "created=1" in created["Location"]
    assert len(list_site_users()) == 1


def _json_headers() -> dict[str, str]:
    return {**_origin_headers(), "HTTP_ACCEPT": "application/json"}


def test_admin_credentials_on_site_login_go_to_admin(isolated_private):
    username, password = _read_access(isolated_private)
    client = Client()
    response = client.post(
        "/api/auth/login",
        data=json.dumps({"username": username, "password": password, "website": ""}),
        content_type="application/json",
        **_json_headers(),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["role"] == "admin"
    assert body["next"] == "/admin"
    assert ADMIN_COOKIE in response.cookies
    assert USER_COOKIE not in response.cookies


def test_json_site_login_returns_user_profile(isolated_private):
    create_site_user("buyer01", "Buyer-Pass-123")
    client = Client()
    response = client.post(
        "/api/auth/login",
        data=json.dumps({"username": "buyer01", "password": "Buyer-Pass-123", "website": ""}),
        content_type="application/json",
        **_json_headers(),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["role"] == "user"
    assert body["username"] == "buyer01"
    assert USER_COOKIE in response.cookies


def test_user_can_update_profile_fields(isolated_private):
    create_site_user("buyer01", "Buyer-Pass-123")
    client = Client()
    login = client.post(
        "/api/auth/login",
        data=json.dumps({"username": "buyer01", "password": "Buyer-Pass-123", "website": ""}),
        content_type="application/json",
        **_json_headers(),
    )
    client.cookies[USER_COOKIE] = login.cookies[USER_COOKIE].value
    updated = client.post(
        "/api/auth/profile",
        data={
            "displayName": "Wei Li",
            "phone": "+86 13800000000",
            "email": "wei@example.com",
            "address": "Shanghai Bonded Zone",
        },
        **_json_headers(),
    )
    assert updated.status_code == 200
    body = updated.json()
    assert body["displayName"] == "Wei Li"
    assert body["phone"] == "+86 13800000000"
    assert body["email"] == "wei@example.com"
    assert body["address"] == "Shanghai Bonded Zone"

    session = client.get("/api/auth/session")
    assert session.json()["displayName"] == "Wei Li"


def test_profile_rejects_password_mismatch(isolated_private):
    create_site_user("buyer01", "Buyer-Pass-123")
    client = Client()
    login = client.post(
        "/api/auth/login",
        data=json.dumps({"username": "buyer01", "password": "Buyer-Pass-123", "website": ""}),
        content_type="application/json",
        **_json_headers(),
    )
    client.cookies[USER_COOKIE] = login.cookies[USER_COOKIE].value
    updated = client.post(
        "/api/auth/profile",
        data={"displayName": "Wei Li", "password": "new-pass-99", "confirm": "other-pass"},
        **_json_headers(),
    )
    assert updated.status_code == 400
    assert updated.json()["error"] == "mismatch"


def test_profile_accepts_png_avatar(isolated_private):
    create_site_user("buyer01", "Buyer-Pass-123")
    client = Client()
    login = client.post(
        "/api/auth/login",
        data=json.dumps({"username": "buyer01", "password": "Buyer-Pass-123", "website": ""}),
        content_type="application/json",
        **_json_headers(),
    )
    client.cookies[USER_COOKIE] = login.cookies[USER_COOKIE].value
    png = bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
        "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
    )
    updated = client.post(
        "/api/auth/profile",
        data={
            "displayName": "Wei Li",
            "avatar": SimpleUploadedFile("avatar.png", png, content_type="image/png"),
        },
        **_json_headers(),
    )
    assert updated.status_code == 200
    avatar_url = updated.json()["avatarUrl"]
    assert avatar_url.startswith("/api/auth/avatar/")
    image = client.get(avatar_url)
    assert image.status_code == 200
    assert image["Content-Type"] == "image/png"


def _png_with_size(width: int, height: int) -> bytes:
    ihdr_data = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    ihdr = struct.pack(">I", 13) + b"IHDR" + ihdr_data + struct.pack(">I", zlib.crc32(b"IHDR" + ihdr_data) & 0xFFFFFFFF)
    iend = struct.pack(">I", 0) + b"IEND" + struct.pack(">I", zlib.crc32(b"IEND") & 0xFFFFFFFF)
    return b"\x89PNG\r\n\x1a\n" + ihdr + iend


def _profile_client(isolated_private):
    create_site_user("buyer01", "Buyer-Pass-123")
    client = Client()
    login = client.post(
        "/api/auth/login",
        data=json.dumps({"username": "buyer01", "password": "Buyer-Pass-123", "website": ""}),
        content_type="application/json",
        **_json_headers(),
    )
    client.cookies[USER_COOKIE] = login.cookies[USER_COOKIE].value
    return client


def test_profile_rejects_oversized_avatar_bytes(isolated_private):
    client = _profile_client(isolated_private)
    huge = SimpleUploadedFile("avatar.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * (256 * 1024), content_type="image/png")
    updated = client.post(
        "/api/auth/profile",
        data={"displayName": "Wei Li", "avatar": huge},
        **_json_headers(),
    )
    assert updated.status_code == 400
    assert updated.json()["error"] == "too-large"


def test_profile_rejects_decompression_bomb_dimensions(isolated_private):
    client = _profile_client(isolated_private)
    bomb = SimpleUploadedFile("bomb.png", _png_with_size(20000, 20000), content_type="image/png")
    updated = client.post(
        "/api/auth/profile",
        data={"displayName": "Wei Li", "avatar": bomb},
        **_json_headers(),
    )
    assert updated.status_code == 400
    assert updated.json()["error"] == "too-large"
