"""
AITA Marketing — validación de archivos de la Biblioteca (funciones puras, sin red).

* Solo PNG, JPEG, WebP, GIF, MP4, MOV y WebM. Se comprueban a la vez la extensión, el MIME
  declarado y la FIRMA REAL de los bytes; si no coinciden los tres, se rechaza.
* Nunca SVG, HTML, scripts ni ejecutables (también si vienen disfrazados).
* El nombre que manda el navegador no se usa como ruta: se sanea y la ruta se construye en el
  servidor, aislada por tenant: {tenant_id}/originals/{asset_id}/{safe_filename}.
* Dimensiones y duración se leen de las cabeceras sin decodificar la imagen ni el vídeo.
"""
import hashlib
import os
import re
import struct
import uuid
from typing import Dict, Optional, Tuple

# extensión -> (MIME, tipo de medio)
ALLOWED = {
    "png": ("image/png", "image"), "jpg": ("image/jpeg", "image"), "jpeg": ("image/jpeg", "image"),
    "webp": ("image/webp", "image"), "gif": ("image/gif", "image"),
    "mp4": ("video/mp4", "video"), "mov": ("video/quicktime", "video"), "webm": ("video/webm", "video"),
}
MIME_TO_EXT = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif",
               "video/mp4": "mp4", "video/quicktime": "mov", "video/webm": "webm"}
# Tope absoluto del servidor (memoria); el límite por tenant lo fija el operador en marketing_settings.
HARD_MAX_BYTES = int(os.getenv("MARKETING_HARD_MAX_UPLOAD_BYTES", str(100 * 1024 * 1024)))
_DANGEROUS = (b"<svg", b"<?xml", b"<html", b"<!doctype", b"<script", b"#!/", b"MZ", b"\x7fELF", b"PK\x03\x04")


class MediaFileError(Exception):
    def __init__(self, code: str, status: int = 400):
        super().__init__(code)
        self.code = code
        self.status = status


def sniff(data: bytes) -> Optional[str]:
    """MIME real por los primeros bytes, o None si no es un formato permitido."""
    head = data[:64]
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if head[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    if head[:4] == b"\x1a\x45\xdf\xa3" and b"webm" in data[:64]:
        return "video/webm"
    if head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand == b"qt  ":
            return "video/quicktime"
        if brand in (b"isom", b"iso2", b"iso4", b"iso5", b"iso6", b"mp41", b"mp42", b"avc1", b"M4V ", b"dash"):
            return "video/mp4"
    return None


def looks_dangerous(data: bytes) -> bool:
    """SVG/HTML/scripts/ejecutables/ZIP, aunque la extensión diga otra cosa."""
    head = data[:512].lstrip().lower()
    return any(head.startswith(sig.lower()) for sig in _DANGEROUS) or b"<script" in head or b"<svg" in head


def safe_filename(name: str, ext: str) -> str:
    """Nombre seguro para la ruta: sin directorios, sin '..', solo [A-Za-z0-9._-], extensión del tipo REAL."""
    base = os.path.basename(str(name or "").replace("\\", "/"))
    stem = base.rsplit(".", 1)[0] if "." in base else base
    stem = re.sub(r"[^A-Za-z0-9_-]+", "-", stem).strip("-._")[:80] or "archivo"
    return f"{stem}.{ext}"


def original_path(tenant_id: str, asset_id: str, filename: str) -> str:
    return f"{_uuid(tenant_id)}/originals/{_uuid(asset_id)}/{filename}"


def derivative_path(tenant_id: str, asset_id: str, derivative_id: str, ext: str) -> str:
    return f"{_uuid(tenant_id)}/derivatives/{_uuid(asset_id)}/{_uuid(derivative_id)}.{ext}"


def path_belongs_to(path: str, tenant_id: str) -> bool:
    parts = str(path or "").split("/")
    return (len(parts) == 4 and parts[0] == _uuid(tenant_id) and parts[1] in ("originals", "derivatives")
            and ".." not in parts and all(re.fullmatch(r"[A-Za-z0-9._-]{1,120}", p) for p in parts))


def _uuid(v: str) -> str:
    try:
        return str(uuid.UUID(str(v)))
    except (ValueError, TypeError, AttributeError):
        raise MediaFileError("invalid_path", 400)


def validate(data: bytes, filename: str, declared_mime: str, max_bytes: int) -> Dict:
    """Valida y describe un archivo subido. Lanza MediaFileError con un código seguro."""
    if not data:
        raise MediaFileError("empty_file")
    limit = min(int(max_bytes), HARD_MAX_BYTES)
    if limit <= 0 or len(data) > limit:
        raise MediaFileError("file_too_large", 413)
    if looks_dangerous(data):
        raise MediaFileError("forbidden_file_type", 415)
    ext = (str(filename or "").rsplit(".", 1)[-1].lower() if "." in str(filename or "") else "")
    if ext not in ALLOWED:
        raise MediaFileError("unsupported_extension", 415)
    real = sniff(data)
    if real is None:
        raise MediaFileError("unrecognized_content", 415)
    expected_mime, media_type = ALLOWED[ext]
    declared = str(declared_mime or "").split(";")[0].strip().lower()
    if real != expected_mime:
        raise MediaFileError("extension_mismatch", 415)       # p. ej. un .png que en realidad es GIF
    if declared != real:
        raise MediaFileError("mime_mismatch", 415)            # MIME declarado falso
    width, height, duration = dimensions(data, real)
    return {"mime_type": real, "media_type": media_type, "byte_size": len(data),
            "checksum": hashlib.sha256(data).hexdigest(), "width": width, "height": height,
            "duration_ms": duration, "safe_filename": safe_filename(filename, MIME_TO_EXT[real]),
            "original_filename": os.path.basename(str(filename).replace("\\", "/"))[:200]}


# ---------------------------------------------------------------- cabeceras
def dimensions(data: bytes, mime: str) -> Tuple[Optional[int], Optional[int], Optional[int]]:
    try:
        if mime == "image/png" and len(data) >= 24:
            w, h = struct.unpack(">II", data[16:24])
            return w, h, None
        if mime == "image/gif" and len(data) >= 10:
            w, h = struct.unpack("<HH", data[6:10])
            return w, h, None
        if mime == "image/jpeg":
            return (*_jpeg_size(data), None)
        if mime == "image/webp":
            return (*_webp_size(data), None)
        if mime in ("video/mp4", "video/quicktime"):
            return _mp4_info(data)
    except (struct.error, IndexError, ValueError):
        pass
    return None, None, None


def _jpeg_size(data: bytes) -> Tuple[Optional[int], Optional[int]]:
    i = 2
    while i + 9 < len(data):
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            h, w = struct.unpack(">HH", data[i + 5:i + 9])
            return w, h
        length = struct.unpack(">H", data[i + 2:i + 4])[0]
        i += 2 + length
    return None, None


def _webp_size(data: bytes) -> Tuple[Optional[int], Optional[int]]:
    chunk = data[12:16]
    if chunk == b"VP8X" and len(data) >= 30:
        w = 1 + int.from_bytes(data[24:27], "little")
        h = 1 + int.from_bytes(data[27:30], "little")
        return w, h
    if chunk == b"VP8 " and len(data) >= 30:
        w, h = struct.unpack("<HH", data[26:30])
        return w & 0x3FFF, h & 0x3FFF
    if chunk == b"VP8L" and len(data) >= 25:
        b = data[21:25]
        w = 1 + (((b[1] & 0x3F) << 8) | b[0])
        h = 1 + (((b[3] & 0xF) << 10) | (b[2] << 2) | ((b[1] & 0xC0) >> 6))
        return w, h
    return None, None


def _mp4_info(data: bytes) -> Tuple[Optional[int], Optional[int], Optional[int]]:
    """Duración (mvhd) y tamaño de la primera pista con dimensiones (tkhd), recorriendo las cajas."""
    width = height = duration = None

    def walk(start: int, end: int):
        nonlocal width, height, duration
        i = start
        while i + 8 <= end:
            size, kind = struct.unpack(">I4s", data[i:i + 8])
            header = 8
            if size == 1 and i + 16 <= end:
                size = struct.unpack(">Q", data[i + 8:i + 16])[0]
                header = 16
            if size < header:
                return
            box_end = min(i + size, end)
            if kind in (b"moov", b"trak"):
                walk(i + header, box_end)
            elif kind == b"mvhd" and duration is None:
                v = data[i + 8]
                if v == 1:
                    scale, dur = struct.unpack(">IQ", data[i + 28:i + 40])
                else:
                    scale, dur = struct.unpack(">II", data[i + 20:i + 28])
                duration = int(dur * 1000 / scale) if scale else None
            elif kind == b"tkhd" and width is None:
                w, h = struct.unpack(">II", data[box_end - 8:box_end])
                if w >> 16 and h >> 16:
                    width, height = w >> 16, h >> 16
            i += size
    walk(0, len(data))
    return width, height, duration
