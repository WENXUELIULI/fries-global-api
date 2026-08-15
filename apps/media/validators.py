from __future__ import annotations

IMAGE_MAX_BYTES = 2 * 1024 * 1024
IMAGE_MAX_EDGE = 4096


class MediaImageError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


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


def detect_site_image(data: bytes) -> tuple[str, str]:
    if not data or len(data) < 24:
        raise MediaImageError("invalid")
    if len(data) > IMAGE_MAX_BYTES:
        raise MediaImageError("too-large")
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
        raise MediaImageError("invalid")
    width, height = size
    if width < 1 or height < 1 or width > IMAGE_MAX_EDGE or height > IMAGE_MAX_EDGE:
        raise MediaImageError("too-large")
    return kind
