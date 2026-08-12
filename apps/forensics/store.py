import base64
import hashlib
import hmac
import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from django.conf import settings

RETENTION_DAYS = 90
MAX_DAILY_BYTES = 20 * 1024 * 1024
GLOBAL_WRITES_PER_MINUTE = 180
SOURCE_WRITES_PER_MINUTE = 30

_key_cache: dict[str, bytes] | None = None
_last_cleanup_date = ""
_forensic_state: dict[str, Any] = {
    "global": {"started_at": 0, "count": 0},
    "sources": {},
    "last_chains": {},
}


def _private_root() -> Path:
    return Path(settings.BASE_DIR) / ".private"


def _forensic_root() -> Path:
    return _private_root() / "forensics"


def _auth_file() -> Path:
    return _private_root() / "admin-auth.json"


def _keys() -> dict[str, bytes]:
    global _key_cache
    if _key_cache is not None:
        return _key_cache

    master = os.environ.get("FORENSICS_SECRET", "")
    try:
        config = json.loads(_auth_file().read_text(encoding="utf-8"))
        master = config.get("analyticsSecret") or config.get("sessionSecret") or master
    except (OSError, json.JSONDecodeError, TypeError, AttributeError):
        pass
    if not master:
        raise RuntimeError("Forensic encryption is not configured.")

    _key_cache = {
        "encryption": hmac.new(
            master.encode(), b"fries-forensics-encryption-v1", hashlib.sha256
        ).digest(),
        "integrity": hmac.new(
            master.encode(), b"fries-forensics-integrity-v1", hashlib.sha256
        ).digest(),
        "fingerprint": hmac.new(
            master.encode(), b"fries-forensics-fingerprint-v1", hashlib.sha256
        ).digest(),
    }
    return _key_cache


def _clean(value: str | None, max_len: int) -> str:
    return re.sub(r"[\x00-\x1f\x7f]", " ", value or "").strip()[:max_len]


def _header_get(headers: Any, key: str, default: str = "") -> str:
    if hasattr(headers, "get"):
        value = headers.get(key)
        return value if value is not None else default
    return headers.get(key, default)


def _header_has(headers: Any, key: str) -> bool:
    if hasattr(headers, "has"):
        return bool(headers.has(key))
    if isinstance(headers, dict):
        lowered = {str(k).lower(): v for k, v in headers.items()}
        return key.lower() in lowered
    return False


def _safe_ip(value: str | None) -> str:
    ip = _clean(value, 80).replace("::ffff:", "")
    return ip if re.fullmatch(r"[0-9a-f:.]+", ip, flags=re.IGNORECASE) else "unknown"


def _safe_url(value: str | None) -> str:
    if not value:
        return ""
    try:
        parsed = urlparse(value)
        return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"[:300]
    except ValueError:
        return ""


def _redacted_query(value: str | None) -> str:
    if not value:
        return ""
    try:
        raw = value[1:] if value.startswith("?") else value
        safe_pairs: list[tuple[str, str]] = []
        for key, raw_value in parse_qsl(raw, keep_blank_values=True):
            name = _clean(key, 80)
            sensitive = re.search(
                r"pass|pwd|token|secret|auth|key|email|phone|mobile|session|cookie",
                name,
                flags=re.IGNORECASE,
            )
            val = "[redacted]" if sensitive else _clean(raw_value, 160)
            safe_pairs.append((name, val))
            if len(urlencode(safe_pairs)) > 600:
                break
        encoded = urlencode(safe_pairs)
        return f"?{encoded}"[:700] if encoded else ""
    except (ValueError, TypeError):
        return ""


def _structured_value(value: str | None, max_len: int) -> str:
    return _clean(value, max_len).strip('"')


def _device_model(headers: Any, user_agent: str) -> str:
    client_hint = _structured_value(_header_get(headers, "sec-ch-ua-model"), 160)
    if client_hint:
        return client_hint
    android = re.search(
        r"Android\s[^;)]*;\s*([^;)]+?)(?:\s+Build\/|[;)])",
        user_agent,
        flags=re.IGNORECASE,
    )
    if android:
        return _clean(android.group(1), 160)
    if re.search(r"iPhone", user_agent, flags=re.IGNORECASE):
        return "iPhone (exact model not exposed)"
    if re.search(r"iPad", user_agent, flags=re.IGNORECASE):
        return "iPad (exact model not exposed)"
    if re.search(r"Macintosh|Mac OS X", user_agent, flags=re.IGNORECASE):
        return "Mac (hardware model not exposed)"
    if re.search(r"Windows", user_agent, flags=re.IGNORECASE):
        return "Windows PC (hardware model not exposed)"
    if re.search(
        r"bot|crawler|spider|headless|curl|wget|python|scanner",
        user_agent,
        flags=re.IGNORECASE,
    ):
        return "Automated client / model unavailable"
    return "Model not reported"


def _severity(status: int, reason: str) -> str:
    if status == 429 or re.search(
        r"admin login temporarily blocked|honeypot", reason, flags=re.IGNORECASE
    ):
        return "critical"
    if re.search(
        r"sensitive path|invalid admin|cross-site|invalid host", reason, flags=re.IGNORECASE
    ):
        return "high"
    if status in (414, 421, 403):
        return "high"
    if status in (405, 401):
        return "medium"
    return "low"


def _short_hmac(key: bytes, value: str, length: int = 20) -> str:
    return hmac.new(key, value.encode(), hashlib.sha256).hexdigest()[:length]


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    pad = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + pad)


def _write_budget(source: str, now_ms: int) -> bool:
    state = _forensic_state
    if now_ms - state["global"]["started_at"] >= 60_000:
        state["global"] = {"started_at": now_ms, "count": 0}
        stale = [
            key
            for key, counter in state["sources"].items()
            if now_ms - counter["started_at"] >= 120_000
        ]
        for key in stale:
            del state["sources"][key]

    if state["global"]["count"] >= GLOBAL_WRITES_PER_MINUTE:
        return False

    counter = state["sources"].get(source)
    if not counter or now_ms - counter["started_at"] >= 60_000:
        counter = {"started_at": now_ms, "count": 0}
        state["sources"][source] = counter
    if counter["count"] >= SOURCE_WRITES_PER_MINUTE:
        return False

    state["global"]["count"] += 1
    counter["count"] += 1
    return True


def _cleanup(day: str) -> None:
    global _last_cleanup_date
    root = _forensic_root()
    if _last_cleanup_date == day or not root.exists():
        return
    _last_cleanup_date = day
    files = sorted(
        [
            name
            for name in os.listdir(root)
            if re.fullmatch(r"evidence-\d{4}-\d{2}-\d{2}\.ndjson", name)
        ],
        reverse=True,
    )
    for name in files[RETENTION_DAYS + 1 :]:
        try:
            (root / name).unlink(missing_ok=True)
        except OSError:
            pass


def _last_chain(file_path: Path) -> str:
    cached = _forensic_state["last_chains"].get(str(file_path))
    if cached:
        return cached
    if not file_path.exists():
        return "GENESIS"
    try:
        lines = file_path.read_text(encoding="utf-8").strip().split("\n")
        envelope = json.loads(lines[-1])
        chain = envelope.get("chain")
        if chain:
            _forensic_state["last_chains"][str(file_path)] = chain
            return chain
    except (OSError, json.JSONDecodeError, IndexError, TypeError):
        pass
    return "GENESIS"


def _chain_value(envelope: dict[str, Any], integrity_key: bytes) -> str:
    msg = "|".join(
        [
            str(envelope["v"]),
            envelope["at"],
            envelope["prev"],
            envelope["iv"],
            envelope["tag"],
            envelope["data"],
        ]
    )
    return _b64url(hmac.new(integrity_key, msg.encode(), hashlib.sha256).digest())


def _equal(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode(), right.encode())


def record_forensic_event(
    *,
    headers: Any,
    path: str,
    query: str | None = None,
    method: str,
    status: int,
    reason: str | None = None,
    category: str | None = None,
) -> None:
    try:
        at = datetime.now(timezone.utc)
        key_set = _keys()
        source_ip = _safe_ip(
            _header_get(headers, "cf-connecting-ip") or _header_get(headers, "x-real-ip")
        )
        user_agent = _clean(_header_get(headers, "user-agent"), 700)
        source_key = _short_hmac(key_set["fingerprint"], source_ip)
        now_ms = int(at.timestamp() * 1000)
        if not _write_budget(source_key, now_ms):
            return

        day = at.date().isoformat()
        root = _forensic_root()
        root.mkdir(parents=True, exist_ok=True)
        _cleanup(day)
        file_path = root / f"evidence-{day}.ndjson"
        if file_path.exists() and file_path.stat().st_size >= MAX_DAILY_BYTES:
            return

        ray_id = _clean(_header_get(headers, "cf-ray"), 80)
        event_reason = _clean(reason, 140) or "Security policy"
        safe_path = _clean(path, 300) or "/"
        safe_query = _redacted_query(query)
        edge_colo = ""
        if "-" in ray_id:
            edge_colo = _clean(ray_id.rsplit("-", 1)[-1], 8).upper()

        event = {
            "id": str(uuid.uuid4()),
            "at": at.isoformat().replace("+00:00", "Z"),
            "category": category or "security",
            "sourceIp": source_ip,
            "sourceType": "cloudflare" if _header_has(headers, "cf-connecting-ip") else "local",
            "fingerprint": _short_hmac(
                key_set["fingerprint"], f"{source_ip}|{user_agent}", 24
            ),
            "country": (_clean(_header_get(headers, "cf-ipcountry"), 2).upper() or "XX"),
            "city": _clean(_header_get(headers, "cf-ipcity"), 100),
            "region": _clean(_header_get(headers, "cf-region"), 100),
            "timezone": _clean(_header_get(headers, "cf-timezone"), 80),
            "rayId": ray_id,
            "edgeColo": edge_colo,
            "method": _clean(method, 12).upper(),
            "path": safe_path,
            "query": safe_query,
            "status": status,
            "reason": event_reason,
            "severity": _severity(status, event_reason),
            "signature": _short_hmac(
                key_set["fingerprint"],
                f"{method}|{safe_path}|{safe_query}|{user_agent}",
                16,
            ),
            "host": _clean(_header_get(headers, "host"), 180),
            "protocol": _clean(_header_get(headers, "x-forwarded-proto"), 16) or "http",
            "deviceModel": _device_model(headers, user_agent),
            "uaPlatform": _structured_value(_header_get(headers, "sec-ch-ua-platform"), 80),
            "platformVersion": _structured_value(
                _header_get(headers, "sec-ch-ua-platform-version"), 80
            ),
            "architecture": _structured_value(_header_get(headers, "sec-ch-ua-arch"), 40),
            "bitness": _structured_value(_header_get(headers, "sec-ch-ua-bitness"), 20),
            "mobileHint": _clean(_header_get(headers, "sec-ch-ua-mobile"), 20),
            "browserBrands": _clean(_header_get(headers, "sec-ch-ua"), 300),
            "fullVersionList": _clean(_header_get(headers, "sec-ch-ua-full-version-list"), 500),
            "userAgent": user_agent,
            "acceptLanguage": _clean(_header_get(headers, "accept-language"), 180),
            "referrer": _safe_url(_header_get(headers, "referer")),
            "origin": _safe_url(_header_get(headers, "origin")),
        }

        previous = _last_chain(file_path)
        iv = os.urandom(12)
        envelope_base = {
            "v": 1,
            "at": event["at"],
            "prev": previous,
            "iv": _b64url(iv),
            "tag": "",
            "data": "",
        }
        aad = f"{envelope_base['v']}|{envelope_base['at']}|{envelope_base['prev']}".encode()
        aesgcm = AESGCM(key_set["encryption"])
        encrypted = aesgcm.encrypt(iv, json.dumps(event, separators=(",", ":")).encode(), aad)
        envelope_base["tag"] = _b64url(encrypted[-16:])
        envelope_base["data"] = _b64url(encrypted[:-16])
        envelope = {**envelope_base, "chain": _chain_value(envelope_base, key_set["integrity"])}

        with file_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(envelope, separators=(",", ":")) + "\n")
        try:
            os.chmod(file_path, 0o600)
        except OSError:
            pass
        _forensic_state["last_chains"][str(file_path)] = envelope["chain"]
    except Exception:
        return


def _decrypt(envelope: dict[str, Any]) -> dict[str, Any]:
    key_set = _keys()
    iv = _b64url_decode(envelope["iv"])
    tag = _b64url_decode(envelope["tag"])
    data = _b64url_decode(envelope["data"])
    aad = f"{envelope['v']}|{envelope['at']}|{envelope['prev']}".encode()
    aesgcm = AESGCM(key_set["encryption"])
    plaintext = aesgcm.decrypt(iv, data + tag, aad)
    return json.loads(plaintext.decode("utf-8"))


def _ranked(
    events: list[dict[str, Any]], selector, limit: int = 8
) -> list[dict[str, int | str]]:
    counts: dict[str, int] = {}
    for event in events:
        label = selector(event)
        counts[label] = counts.get(label, 0) + 1
    return [
        {"label": label, "value": value}
        for label, value in sorted(counts.items(), key=lambda item: item[1], reverse=True)[
            :limit
        ]
    ]


def get_forensic_report(
    days: int = RETENTION_DAYS,
    event_limit: int = 200,
    deduplicate_connections: bool = True,
) -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    checked_files = 0
    checked_records = 0
    errors = 0
    window_days = min(days, RETENTION_DAYS)
    cutoff_ms = int(datetime.now(timezone.utc).timestamp() * 1000) - window_days * 86_400_000
    root = _forensic_root()

    if root.exists():
        files = sorted(
            [
                name
                for name in os.listdir(root)
                if re.fullmatch(r"evidence-\d{4}-\d{2}-\d{2}\.ndjson", name)
            ]
        )[-(window_days + 1) :]
        for name in files:
            checked_files += 1
            previous = "GENESIS"
            try:
                contents = (root / name).read_text(encoding="utf-8")
            except OSError:
                errors += 1
                continue
            for line in contents.split("\n"):
                if not line:
                    continue
                checked_records += 1
                try:
                    envelope = json.loads(line)
                    expected = _chain_value(
                        {
                            "v": envelope["v"],
                            "at": envelope["at"],
                            "prev": envelope["prev"],
                            "iv": envelope["iv"],
                            "tag": envelope["tag"],
                            "data": envelope["data"],
                        },
                        _keys()["integrity"],
                    )
                    if envelope.get("prev") != previous or not _equal(envelope.get("chain", ""), expected):
                        errors += 1
                    previous = envelope.get("chain", previous)
                    event = _decrypt(envelope)
                    event_ms = _parse_iso_ms(event.get("at", ""))
                    if event_ms >= cutoff_ms:
                        events.append(event)
                except (json.JSONDecodeError, KeyError, ValueError, TypeError, OSError):
                    errors += 1
                except Exception:
                    # 密钥轮换/损坏行：计入错误并跳过，避免拖垮 summary
                    errors += 1

    events.sort(key=lambda item: _parse_iso_ms(item.get("at", "")), reverse=True)
    security_events = [event for event in events if event.get("category") != "visit"]
    connection_events = [event for event in events if event.get("category") == "visit"]
    unique_connections: dict[str, dict[str, Any]] = {}
    for event in connection_events:
        key = event["fingerprint"] if deduplicate_connections else event["id"]
        if key not in unique_connections:
            unique_connections[key] = event

    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    last_24h = [
        event
        for event in security_events
        if _parse_iso_ms(event.get("at", "")) >= now_ms - 86_400_000
    ]

    return {
        "generatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "retentionDays": RETENTION_DAYS,
        "integrity": {
            "verified": errors == 0,
            "checkedFiles": checked_files,
            "checkedRecords": checked_records,
            "errors": errors,
        },
        "events": security_events[:event_limit],
        "connections": list(unique_connections.values())[:event_limit],
        "uniqueSources24h": len({event.get("sourceIp") for event in last_24h}),
        "events24h": len(last_24h),
        "critical24h": sum(1 for event in last_24h if event.get("severity") == "critical"),
        "topSources": _ranked(last_24h, lambda event: f"{event.get('sourceIp')} · {event.get('country')}"),
        "topSignatures": _ranked(
            last_24h, lambda event: f"{event.get('signature')} · {event.get('reason')}"
        ),
    }


def _parse_iso_ms(value: str) -> int:
    try:
        normalized = value.replace("Z", "+00:00")
        return int(datetime.fromisoformat(normalized).timestamp() * 1000)
    except (ValueError, TypeError):
        return 0


def forensic_evidence_export() -> dict[str, Any]:
    report = get_forensic_report(RETENTION_DAYS, 10_000, False)
    all_events = sorted(
        [*report["events"], *report["connections"]],
        key=lambda item: _parse_iso_ms(item.get("at", "")),
        reverse=True,
    )
    return {
        "format": "Fries Global encrypted forensic evidence export v1",
        "exportedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "notice": (
            "Source IP identifies a network endpoint, not necessarily a person. "
            "Preserve original timestamps and Ray IDs when escalating to a provider or law enforcement."
        ),
        "integrity": report["integrity"],
        "retentionDays": report["retentionDays"],
        "eventCount": len(all_events),
        "events": all_events,
    }
