import hashlib
import hmac
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from django.conf import settings

from apps.forensics.store import get_forensic_report, record_forensic_event

MAX_EVENT_FILES = 31
MAX_EVENT_FILE_BYTES = 10 * 1024 * 1024
WRITE_LIMIT_PER_MINUTE = 240
WRITE_LIMIT_PER_NETWORK_MINUTE = 60

_cached_secret = ""
_last_cleanup_date = ""
_analytics_write_state: dict[str, Any] = {
    "global": {"started_at": 0, "count": 0},
    "networks": {},
}


def _private_root() -> Path:
    return Path(settings.BASE_DIR) / ".private"


def _event_root() -> Path:
    return _private_root() / "analytics"


def _auth_file() -> Path:
    return _private_root() / "admin-auth.json"


def analytics_secret() -> str:
    global _cached_secret
    if _cached_secret:
        return _cached_secret
    try:
        config = json.loads(_auth_file().read_text(encoding="utf-8"))
        secret = config.get("analyticsSecret")
        if secret and len(secret) >= 32:
            _cached_secret = secret
            return _cached_secret
    except (OSError, json.JSONDecodeError, TypeError, AttributeError):
        pass
    _cached_secret = os.environ.get(
        "ANALYTICS_SECRET", "fries-global-local-preview-anonymous-analytics"
    )
    return _cached_secret


def _short_hmac(value: str) -> str:
    return hmac.new(
        analytics_secret().encode(), value.encode(), hashlib.sha256
    ).hexdigest()[:16]


def _header_get(headers: Any, key: str, default: str = "") -> str:
    if hasattr(headers, "get"):
        value = headers.get(key)
        return value if value is not None else default
    return headers.get(key, default)


def _normalize_ip(value: str) -> str:
    ip = value.strip().replace("::ffff:", "")[:80]
    return ip or "local"


def _network_hint(ip: str) -> str:
    if ip == "local":
        return "Local preview"
    if ":" in ip:
        parts = [part for part in ip.split(":") if part]
        return f"{':'.join(parts[:2])}::"
    parts = ip.split(".")
    return f"{parts[0]}.{parts[1]}.x.x" if len(parts) == 4 else "Anonymous network"


def _parse_agent(user_agent: str) -> dict[str, str]:
    ua = user_agent[:512]
    bot = bool(
        re.search(
            r"bot|crawler|spider|headless|curl|wget|python|scanner|facebookexternalhit|preview",
            ua,
            flags=re.IGNORECASE,
        )
    )
    tablet = bool(re.search(r"iPad|Tablet|PlayBook|Silk", ua, flags=re.IGNORECASE))
    mobile = bool(re.search(r"Mobi|Android|iPhone|Windows Phone", ua, flags=re.IGNORECASE))
    if bot:
        device = "Bot"
    elif tablet:
        device = "Tablet"
    elif mobile:
        device = "Mobile"
    elif ua:
        device = "Desktop"
    else:
        device = "Unknown"

    if re.search(r"Edg/", ua, flags=re.IGNORECASE):
        browser = "Edge"
    elif re.search(r"OPR/", ua, flags=re.IGNORECASE):
        browser = "Opera"
    elif re.search(r"CriOS|Chrome/", ua, flags=re.IGNORECASE):
        browser = "Chrome"
    elif re.search(r"FxiOS|Firefox/", ua, flags=re.IGNORECASE):
        browser = "Firefox"
    elif re.search(r"Safari/", ua, flags=re.IGNORECASE):
        browser = "Safari"
    elif bot:
        browser = "Automated client"
    else:
        browser = "Other"

    if re.search(r"Windows", ua, flags=re.IGNORECASE):
        os_name = "Windows"
    elif re.search(r"iPhone|iPad|iPod", ua, flags=re.IGNORECASE):
        os_name = "iOS / iPadOS"
    elif re.search(r"Android", ua, flags=re.IGNORECASE):
        os_name = "Android"
    elif re.search(r"Mac OS X|Macintosh", ua, flags=re.IGNORECASE):
        os_name = "macOS"
    elif re.search(r"Linux", ua, flags=re.IGNORECASE):
        os_name = "Linux"
    else:
        os_name = "Other"

    return {"device": device, "browser": browser, "os": os_name}


def _safe_path(pathname: str) -> str:
    without_query = re.split(r"[?#]", pathname, maxsplit=1)[0] or "/"
    return re.sub(r"[\x00-\x1f\x7f]", "", without_query)[:240]


def _referrer_host(value: str) -> str:
    if not value:
        return "Direct / unknown"
    try:
        hostname = urlparse(value).hostname
        return (hostname or "Direct / unknown").lower()[:120]
    except ValueError:
        return "Direct / unknown"


def _cleanup_old_files(today: str) -> None:
    global _last_cleanup_date
    root = _event_root()
    if _last_cleanup_date == today or not root.exists():
        return
    _last_cleanup_date = today
    files = sorted(
        [
            name
            for name in os.listdir(root)
            if re.fullmatch(r"events-\d{4}-\d{2}-\d{2}\.ndjson", name)
        ],
        reverse=True,
    )
    for name in files[MAX_EVENT_FILES:]:
        try:
            (root / name).unlink(missing_ok=True)
        except OSError:
            pass


def _consume_write_budget(key: str, now_ms: int) -> bool:
    state = _analytics_write_state
    if now_ms - state["global"]["started_at"] >= 60_000:
        state["global"] = {"started_at": now_ms, "count": 0}
        stale = [
            network
            for network, counter in state["networks"].items()
            if now_ms - counter["started_at"] >= 120_000
        ]
        for network in stale:
            del state["networks"][network]

    if state["global"]["count"] >= WRITE_LIMIT_PER_MINUTE:
        return False

    counter = state["networks"].get(key)
    if not counter or now_ms - counter["started_at"] >= 60_000:
        counter = {"started_at": now_ms, "count": 0}
        state["networks"][key] = counter
    if counter["count"] >= WRITE_LIMIT_PER_NETWORK_MINUTE:
        return False

    state["global"]["count"] += 1
    counter["count"] += 1
    return True


def record_analytics_event(
    *,
    headers: Any,
    path: str,
    method: str,
    status: int,
    kind: str | None = None,
    reason: str | None = None,
    query: str | None = None,
    capture_forensics: str | None = None,
) -> None:
    try:
        event_kind = kind or "visit"
        forensic_category = capture_forensics or (
            "security"
            if event_kind == "security"
            else "visit"
            if event_kind == "visit" or not kind
            else None
        )
        if forensic_category:
            record_forensic_event(
                headers=headers,
                path=path,
                query=query,
                method=method,
                status=status,
                reason=reason or (
                    "Page visit" if forensic_category == "visit" else "Security policy"
                ),
                category=forensic_category,
            )

        at = datetime.now(timezone.utc)
        day = at.date().isoformat()
        ip = _normalize_ip(
            _header_get(headers, "cf-connecting-ip") or _header_get(headers, "x-real-ip") or "local"
        )
        user_agent = _header_get(headers, "user-agent")
        anonymous_network = _short_hmac(ip)
        now_ms = int(at.timestamp() * 1000)
        if not _consume_write_budget(anonymous_network, now_ms):
            return

        root = _event_root()
        root.mkdir(parents=True, exist_ok=True)
        _cleanup_old_files(day)
        event_file = root / f"events-{day}.ndjson"
        if event_file.exists() and event_file.stat().st_size >= MAX_EVENT_FILE_BYTES:
            return

        agent = _parse_agent(user_agent)
        event = {
            "at": at.isoformat().replace("+00:00", "Z"),
            "kind": event_kind,
            "path": _safe_path(path),
            "method": method[:12],
            "status": status,
            "deviceId": _short_hmac(f"{ip}|{user_agent}"),
            "networkId": anonymous_network,
            "networkHint": _network_hint(ip),
            "country": (_header_get(headers, "cf-ipcountry") or "XX").upper()[:2],
            "device": agent["device"],
            "browser": agent["browser"],
            "os": agent["os"],
            "referrer": _referrer_host(_header_get(headers, "referer")),
        }
        if reason:
            event["reason"] = reason[:100]

        with event_file.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, separators=(",", ":")) + "\n")
        try:
            os.chmod(event_file, 0o600)
        except OSError:
            pass
    except Exception:
        return


def _parse_iso_ms(value: str) -> int:
    try:
        normalized = value.replace("Z", "+00:00")
        return int(datetime.fromisoformat(normalized).timestamp() * 1000)
    except (ValueError, TypeError):
        return 0


def _read_recent_events(days: int = 7) -> list[dict[str, Any]]:
    root = _event_root()
    if not root.exists():
        return []
    cutoff_ms = int(datetime.now(timezone.utc).timestamp() * 1000) - days * 86_400_000
    files = sorted(
        [
            name
            for name in os.listdir(root)
            if re.fullmatch(r"events-\d{4}-\d{2}-\d{2}\.ndjson", name)
        ]
    )[-min(days + 1, MAX_EVENT_FILES) :]
    events: list[dict[str, Any]] = []
    for name in files:
        try:
            contents = (root / name).read_text(encoding="utf-8")
        except OSError:
            continue
        for line in contents.split("\n"):
            if not line or len(events) >= 100_000:
                continue
            try:
                event = json.loads(line)
                if _parse_iso_ms(event.get("at", "")) >= cutoff_ms:
                    events.append(event)
            except json.JSONDecodeError:
                continue
    events.sort(key=lambda item: _parse_iso_ms(item.get("at", "")))
    return events


def _ranked(
    events: list[dict[str, Any]], selector, limit: int = 6
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


def country_name(code: str) -> str:
    if code == "XX":
        return "Unknown"
    return code


def get_analytics_summary() -> dict[str, Any]:
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    events = _read_recent_events(7)
    visits = [event for event in events if event.get("kind") == "visit"]
    activity = [event for event in events if event.get("kind") in ("visit", "presence")]
    security = [event for event in events if event.get("kind") == "security"]
    visits_24h = [
        event for event in visits if _parse_iso_ms(event.get("at", "")) >= now_ms - 86_400_000
    ]
    security_24h = [
        event for event in security if _parse_iso_ms(event.get("at", "")) >= now_ms - 86_400_000
    ]
    online_events = [
        event for event in activity if _parse_iso_ms(event.get("at", "")) >= now_ms - 2 * 60_000
    ]

    hourly = []
    for index in range(24):
        start = now_ms - (23 - index) * 3_600_000
        end = start + 3_600_000
        bucket = [
            event
            for event in visits
            if start <= _parse_iso_ms(event.get("at", "")) < end
        ]
        label_dt = datetime.fromtimestamp(start / 1000, tz=timezone.utc)
        hourly.append(
            {
                "label": label_dt.strftime("%H:%M"),
                "views": len(bucket),
                "visitors": len({event.get("deviceId") for event in bucket}),
            }
        )

    latest_by_device: dict[str, dict[str, Any]] = {}
    for event in activity:
        previous = latest_by_device.get(event.get("deviceId", ""))
        if event.get("kind") == "presence" and previous:
            latest_by_device[event["deviceId"]] = {
                **previous,
                "at": event.get("at"),
                "path": event.get("path"),
            }
        else:
            latest_by_device[event.get("deviceId", "")] = event

    return {
        "generatedAt": datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
        "onlineNow": len({event.get("deviceId") for event in online_events}),
        "views24h": len(visits_24h),
        "unique24h": len({event.get("deviceId") for event in visits_24h}),
        "security24h": len(security_24h),
        "bot24h": sum(1 for event in visits_24h if event.get("device") == "Bot"),
        "views7d": len(visits),
        "hourly": hourly,
        "topPages": _ranked(visits_24h, lambda event: event.get("path", "")),
        "topCountries": _ranked(
            visits_24h,
            lambda event: f"{event.get('country')} · {country_name(event.get('country', 'XX'))}",
        ),
        "topDevices": _ranked(visits_24h, lambda event: event.get("device", "")),
        "topReferrers": _ranked(visits_24h, lambda event: event.get("referrer", "")),
        "recentVisitors": [
            {
                "at": event.get("at"),
                "deviceId": event.get("deviceId"),
                "networkHint": event.get("networkHint"),
                "country": f"{event.get('country')} · {country_name(event.get('country', 'XX'))}",
                "device": event.get("device"),
                "browser": event.get("browser"),
                "os": event.get("os"),
                "path": event.get("path"),
                "referrer": event.get("referrer"),
            }
            for event in sorted(
                latest_by_device.values(),
                key=lambda item: _parse_iso_ms(item.get("at", "")),
                reverse=True,
            )[:40]
        ],
        "recentSecurity": [
            {
                "at": event.get("at"),
                "networkHint": event.get("networkHint"),
                "country": f"{event.get('country')} · {country_name(event.get('country', 'XX'))}",
                "path": event.get("path"),
                "method": event.get("method"),
                "status": event.get("status"),
                "reason": event.get("reason") or "Security policy",
            }
            for event in list(reversed(security))[:40]
        ],
        "forensics": get_forensic_report(),
    }


def analytics_csv() -> str:
    events = _read_recent_events(30)

    def escape(value: str | int | None) -> str:
        return f'"{str(value if value is not None else "").replace(chr(34), chr(34) * 2)}"'

    header = [
        "time",
        "type",
        "path",
        "method",
        "status",
        "anonymous_device",
        "network_hint",
        "country",
        "device",
        "browser",
        "os",
        "referrer",
        "reason",
    ]
    rows = []
    for event in events:
        row = [
            event.get("at"),
            event.get("kind"),
            event.get("path"),
            event.get("method"),
            event.get("status"),
            event.get("deviceId"),
            event.get("networkHint"),
            event.get("country"),
            event.get("device"),
            event.get("browser"),
            event.get("os"),
            event.get("referrer"),
            event.get("reason"),
        ]
        rows.append(",".join(escape(value) for value in row))
    return "\ufeff" + ",".join(header) + "\r\n" + "\r\n".join(rows)
