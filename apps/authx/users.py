from __future__ import annotations

import json
import re
import secrets
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from django.conf import settings

from apps.authx.services import hash_secret, secrets_equal, sign_payload

USER_COOKIE = "fries_user_session"
USER_SESSION_SECONDS = 4 * 60 * 60
REGISTER_COOLDOWN_SECONDS = 10 * 60
USERNAME_RE = re.compile(r"^[A-Za-z0-9._-]{3,80}$")
PHONE_RE = re.compile(r"^[0-9+()\-\s]{0,20}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
AVATAR_MAX_BYTES = 256 * 1024
AVATAR_MAX_EDGE = 1024

_PUBLIC_FIELDS = (
    "id",
    "username",
    "enabled",
    "note",
    "displayName",
    "phone",
    "email",
    "address",
    "createdAt",
    "updatedAt",
    "lastLoginAt",
)
_store_lock = threading.RLock()


class UserStoreError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _store_path() -> Path:
    return Path(settings.BASE_DIR) / ".private" / "site-users.json"


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _empty_store() -> dict[str, Any]:
    return {
        "sessionSecret": secrets.token_urlsafe(48),
        "registrationEnabled": True,
        "users": [],
        "registerGate": {},
    }


def _load_store() -> dict[str, Any]:
    path = _store_path()
    try:
        store = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(store, dict) or not store.get("sessionSecret"):
            raise ValueError("invalid store")
        store.setdefault("users", [])
        store.setdefault("registrationEnabled", True)
        store.setdefault("registerGate", {})
        return store
    except (OSError, json.JSONDecodeError, TypeError, ValueError, AttributeError):
        store = _empty_store()
        _save_store(store)
        return store


def _save_store(store: dict[str, Any]) -> None:
    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(store, indent=2) + "\n", encoding="utf-8")


def registration_enabled() -> bool:
    return _load_store().get("registrationEnabled", True) is not False


def set_registration_enabled(enabled: bool) -> bool:
    with _store_lock:
        store = _load_store()
        store["registrationEnabled"] = bool(enabled)
        _save_store(store)
        return store["registrationEnabled"]


def _assert_register_allowed(client_key: str) -> None:
    store = _load_store()
    if store.get("registrationEnabled", True) is False:
        raise UserStoreError("closed")
    last = float((store.get("registerGate") or {}).get(client_key, {}).get("lastSuccessAt") or 0)
    if last and time.time() - last < REGISTER_COOLDOWN_SECONDS:
        raise UserStoreError("rate")


def _mark_register_success(client_key: str) -> None:
    store = _load_store()
    gate = store.setdefault("registerGate", {})
    now = time.time()
    gate[client_key] = {"lastSuccessAt": now}
    if len(gate) > 5_000:
        store["registerGate"] = {
            key: value
            for key, value in gate.items()
            if now - float(value.get("lastSuccessAt") or 0) < REGISTER_COOLDOWN_SECONDS
        }
    _save_store(store)


def register_public_user(username: str, password: str, confirm: str, client_key: str) -> dict[str, Any]:
    if password != confirm:
        raise UserStoreError("mismatch")
    with _store_lock:
        _assert_register_allowed(client_key)
        user = create_site_user(username, password, "公开注册")
        _mark_register_success(client_key)
        return user


def _public_user(user: dict[str, Any]) -> dict[str, Any]:
    data = {key: user.get(key) for key in _PUBLIC_FIELDS}
    data["displayName"] = user.get("displayName") or user.get("username") or ""
    data["phone"] = user.get("phone") or ""
    data["email"] = user.get("email") or ""
    data["address"] = user.get("address") or ""
    data["avatarUrl"] = f"/api/auth/avatar/{user.get('id')}" if user.get("avatarExt") else None
    return data


def list_site_users() -> list[dict[str, Any]]:
    return [_public_user(user) for user in _load_store()["users"]]


def get_site_user(user_id: str) -> dict[str, Any] | None:
    for user in _load_store()["users"]:
        if user.get("id") == user_id:
            return user
    return None


def _username_taken(users: list[dict[str, Any]], username: str, exclude_id: str | None = None) -> bool:
    needle = username.lower()
    for user in users:
        if exclude_id and user.get("id") == exclude_id:
            continue
        if str(user.get("username") or "").lower() == needle:
            return True
    return False


def create_site_user(username: str, password: str, note: str = "") -> dict[str, Any]:
    username = username.strip()
    note = note.strip()[:200]
    if not USERNAME_RE.fullmatch(username) or not 8 <= len(password) <= 256:
        raise UserStoreError("invalid")
    store = _load_store()
    if _username_taken(store["users"], username):
        raise UserStoreError("duplicate")
    salt = secrets.token_hex(24)
    now = _now()
    user = {
        "id": f"usr_{secrets.token_hex(8)}",
        "username": username,
        "passwordSalt": salt,
        "passwordHash": hash_secret(password, salt),
        "enabled": True,
        "note": note,
        "displayName": username,
        "phone": "",
        "email": "",
        "address": "",
        "createdAt": now,
        "updatedAt": now,
        "lastLoginAt": None,
    }
    store["users"].append(user)
    _save_store(store)
    return _public_user(user)


def update_site_user(user_id: str, *, enabled: bool | None = None, password: str | None = None, note: str | None = None) -> dict[str, Any]:
    store = _load_store()
    user = next((item for item in store["users"] if item.get("id") == user_id), None)
    if not user:
        raise UserStoreError("missing")
    if password is not None and password != "":
        if not 8 <= len(password) <= 256:
            raise UserStoreError("invalid")
        salt = secrets.token_hex(24)
        user["passwordSalt"] = salt
        user["passwordHash"] = hash_secret(password, salt)
    if enabled is not None:
        user["enabled"] = bool(enabled)
    if note is not None:
        user["note"] = note.strip()[:200]
    user["updatedAt"] = _now()
    _save_store(store)
    return _public_user(user)


def delete_site_user(user_id: str) -> None:
    store = _load_store()
    remaining = [user for user in store["users"] if user.get("id") != user_id]
    if len(remaining) == len(store["users"]):
        raise UserStoreError("missing")
    store["users"] = remaining
    _save_store(store)


def _avatar_dir() -> Path:
    return Path(settings.BASE_DIR) / ".private" / "site-avatars"


def _png_size(data: bytes) -> tuple[int, int] | None:
    if len(data) < 24:
        return None
    return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")


def _jpeg_size(data: bytes) -> tuple[int, int] | None:
    index = 2
    while index + 9 < len(data) and data[index] == 0xFF:
        marker = data[index + 1]
        if marker in {0xC0, 0xC1, 0xC2, 0xC3}:
            height = int.from_bytes(data[index + 5 : index + 7], "big")
            width = int.from_bytes(data[index + 7 : index + 9], "big")
            return width, height
        if marker in {0xD8, 0xD9}:
            index += 2
            continue
        length = int.from_bytes(data[index + 2 : index + 4], "big")
        if length < 2:
            return None
        index += 2 + length
    return None


def _webp_size(data: bytes) -> tuple[int, int] | None:
    if len(data) < 30:
        return None
    kind = data[12:16]
    if kind == b"VP8X":
        width = 1 + int.from_bytes(data[24:27], "little")
        height = 1 + int.from_bytes(data[27:30], "little")
        return width, height
    if kind == b"VP8 ":
        width = int.from_bytes(data[26:28], "little") & 0x3FFF
        height = int.from_bytes(data[28:30], "little") & 0x3FFF
        return width, height
    if kind == b"VP8L" and len(data) >= 25:
        bits = int.from_bytes(data[21:25], "little")
        return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    return None


def detect_avatar(data: bytes) -> tuple[str, str] | None:
    if not data or len(data) < 24:
        return None
    if len(data) > AVATAR_MAX_BYTES:
        raise UserStoreError("too-large")
    kind: tuple[str, str] | None = None
    size: tuple[int, int] | None = None
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        kind = ("png", "image/png")
        size = _png_size(data)
    elif data.startswith(b"\xff\xd8\xff"):
        kind = ("jpg", "image/jpeg")
        size = _jpeg_size(data)
    elif data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        kind = ("webp", "image/webp")
        size = _webp_size(data)
    if not kind or not size:
        return None
    width, height = size
    if width < 1 or height < 1 or width > AVATAR_MAX_EDGE or height > AVATAR_MAX_EDGE:
        raise UserStoreError("too-large")
    return kind


def avatar_file(user_id: str, ext: str) -> Path:
    return _avatar_dir() / f"{user_id}.{ext}"


def get_avatar_file(user_id: str) -> tuple[Path, str] | None:
    user = get_site_user(user_id)
    ext = str((user or {}).get("avatarExt") or "")
    types = {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp"}
    if not user or ext not in types:
        return None
    path = avatar_file(user_id, ext)
    if not path.is_file():
        return None
    return path, types[ext]


def update_own_profile(
    user_id: str,
    *,
    display_name: str,
    phone: str,
    email: str,
    address: str,
    password: str = "",
    confirm: str = "",
    avatar: bytes | None = None,
) -> dict[str, Any]:
    display_name = display_name.strip()
    phone = phone.strip()
    email = email.strip()
    address = address.strip()[:200]
    if not display_name or len(display_name) > 80:
        raise UserStoreError("invalid")
    if phone and not PHONE_RE.fullmatch(phone):
        raise UserStoreError("invalid")
    if email and (len(email) > 120 or not EMAIL_RE.fullmatch(email)):
        raise UserStoreError("invalid")
    if password or confirm:
        if password != confirm:
            raise UserStoreError("mismatch")
        if not 8 <= len(password) <= 256:
            raise UserStoreError("invalid")
    kind = detect_avatar(avatar) if avatar else None
    if avatar and not kind:
        raise UserStoreError("invalid")
    with _store_lock:
        store = _load_store()
        user = next((item for item in store["users"] if item.get("id") == user_id), None)
        if not user:
            raise UserStoreError("missing")
        user["displayName"] = display_name
        user["phone"] = phone
        user["email"] = email
        user["address"] = address
        if password:
            salt = secrets.token_hex(24)
            user["passwordSalt"] = salt
            user["passwordHash"] = hash_secret(password, salt)
        if kind and avatar:
            _avatar_dir().mkdir(parents=True, exist_ok=True)
            old_ext = user.get("avatarExt")
            if old_ext and old_ext != kind[0]:
                avatar_file(user_id, str(old_ext)).unlink(missing_ok=True)
            avatar_file(user_id, kind[0]).write_bytes(avatar)
            user["avatarExt"] = kind[0]
        user["updatedAt"] = _now()
        _save_store(store)
        return _public_user(user)


def verify_site_credentials(username: str, password: str) -> dict[str, Any] | None:
    if len(username) > 80 or len(password) > 256:
        return None
    store = _load_store()
    user = next(
        (item for item in store["users"] if str(item.get("username") or "").lower() == username.lower()),
        None,
    )
    if not user or not user.get("enabled"):
        return None
    supplied = hash_secret(password, user["passwordSalt"])
    if not secrets_equal(supplied, user["passwordHash"]):
        return None
    user["lastLoginAt"] = _now()
    user["updatedAt"] = user["lastLoginAt"]
    _save_store(store)
    return _public_user(user)


def create_site_session(user_id: str) -> str:
    store = _load_store()
    payload = f"{int(time.time()) + USER_SESSION_SECONDS}.{user_id}.{uuid.uuid4()}"
    signature = sign_payload(store["sessionSecret"], payload)
    return f"{payload}.{signature}"


def verify_site_user_session(token: str | None) -> dict[str, Any] | None:
    if not token or len(token) > 400:
        return None
    store = _load_store()
    last_dot = token.rfind(".")
    if last_dot < 1:
        return None
    payload = token[:last_dot]
    signature = token[last_dot + 1 :]
    parts = payload.split(".", 2)
    if len(parts) < 3:
        return None
    try:
        expires_at = int(parts[0])
    except ValueError:
        return None
    if not expires_at or expires_at <= int(time.time()):
        return None
    expected = sign_payload(store["sessionSecret"], payload)
    if not secrets_equal(signature, expected):
        return None
    user = get_site_user(parts[1])
    if not user or not user.get("enabled"):
        return None
    return _public_user(user)


def safe_next_path(value: str | None) -> str:
    if not value:
        return "/en"
    if not value.startswith("/") or value.startswith("//") or value.startswith("/\\"):
        return "/en"
    if value.startswith("/admin") or "://" in value or "\\" in value:
        return "/en"
    return value[:200]


def login_page_path(
    next_path: str,
    *,
    error: bool | str = False,
    signed_out: bool = False,
    register: bool = False,
) -> str:
    match = re.match(r"^/([a-z]{2})(?:/|$)", next_path)
    locale = match.group(1) if match else "en"
    params: dict[str, str] = {}
    if next_path:
        params["next"] = next_path
    if error:
        params["error"] = "1" if error is True else str(error)
    if signed_out:
        params["signedout"] = "1"
    if register:
        params["register"] = "1"
    query = urlencode(params)
    return f"/{locale}/login?{query}" if query else f"/{locale}/login"
