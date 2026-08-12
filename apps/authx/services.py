import base64
import hashlib
import hmac
import json
import time
import uuid
from pathlib import Path

from django.conf import settings

ADMIN_COOKIE = "fries_admin_session"
ADMIN_SESSION_SECONDS = 4 * 60 * 60

_SCRYPT_N = 16384
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 64


def _config_path() -> Path:
    return Path(settings.BASE_DIR) / ".private" / "admin-auth.json"


def _load_config() -> dict | None:
    try:
        config = json.loads(_config_path().read_text(encoding="utf-8"))
        if (
            not config.get("username")
            or not config.get("passwordSalt")
            or not config.get("passwordHash")
            or not config.get("sessionSecret")
        ):
            return None
        return config
    except (OSError, json.JSONDecodeError, TypeError, AttributeError):
        return None


def _safe_equal(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode(), right.encode())


def _scrypt_hex(password: str, salt: str) -> str:
    derived = hashlib.scrypt(
        password.encode(),
        salt=salt.encode(),
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_SCRYPT_DKLEN,
    )
    return derived.hex()


def _hmac_base64url(secret: str, payload: str) -> str:
    digest = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def verify_credentials(username: str, password: str) -> bool:
    config = _load_config()
    if not config or len(username) > 80 or len(password) > 256:
        return False
    supplied_hash = _scrypt_hex(password, config["passwordSalt"])
    return _safe_equal(username, config["username"]) and _safe_equal(
        supplied_hash, config["passwordHash"]
    )


def create_admin_session() -> str:
    config = _load_config()
    if not config:
        raise RuntimeError("Admin authentication is not configured.")
    payload = f"{int(time.time()) + ADMIN_SESSION_SECONDS}.{uuid.uuid4()}"
    signature = _hmac_base64url(config["sessionSecret"], payload)
    return f"{payload}.{signature}"


def verify_admin_session(token: str | None) -> bool:
    if not token or len(token) > 300:
        return False
    config = _load_config()
    if not config:
        return False
    last_dot = token.rfind(".")
    if last_dot < 1:
        return False
    payload = token[:last_dot]
    signature = token[last_dot + 1 :]
    expires_at_str = payload.split(".", 1)[0]
    try:
        expires_at = int(expires_at_str)
    except ValueError:
        return False
    if not expires_at or expires_at <= int(time.time()):
        return False
    expected = _hmac_base64url(config["sessionSecret"], payload)
    return _safe_equal(signature, expected)


def request_is_https(request) -> bool:
    forwarded = (request.META.get("HTTP_X_FORWARDED_PROTO") or "").split(",")[0].strip()
    return forwarded == "https" or request.is_secure()


def valid_same_origin_request(request) -> bool:
    origin = request.META.get("HTTP_ORIGIN") or ""
    host = (
        request.META.get("HTTP_X_FORWARDED_HOST")
        or request.META.get("HTTP_HOST")
        or ""
    ).lower()
    if not origin or not host:
        return False
    if (request.META.get("HTTP_SEC_FETCH_SITE") or "").lower() == "cross-site":
        return False
    try:
        from urllib.parse import urlparse

        return urlparse(origin).netloc.lower() == host
    except Exception:
        return False
