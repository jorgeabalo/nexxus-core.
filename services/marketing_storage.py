"""
AITA Marketing (Fase 2) — cuota de almacenamiento de la Biblioteca.

Cuenta TODO lo que ocupa espacio del tenant: originales, derivados (incluidas anonimizaciones y
vistas previas), resultados/temporales de trabajos, subidas en curso y los bytes estimados de los
trabajos pendientes (queued/processing). Las reservas se hacen de forma atómica en la base de datos
(public.marketing_reserve_storage bloquea la configuración del tenant), así dos subidas o trabajos
simultáneos nunca superan el límite. 0 = Biblioteca no habilitada; null = sin límite.
"""
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from services.member_portal import PortalError

# Estimación prudente del tamaño de los resultados de un Reel (render MP4 9:16 + portada + subtítulos).
RENDER_BYTES_PER_SECOND = 1_500_000
FIXED_OUTPUT_BYTES = 2_000_000


def estimated_output_bytes(duration_seconds: int) -> int:
    return int(duration_seconds) * RENDER_BYTES_PER_SECOND + FIXED_OUTPUT_BYTES


def used_bytes(db, tenant_id: str) -> int:
    return int(db.rpc("marketing_storage_used", {"p_tenant": tenant_id}) or 0)


def reserve(db, tenant_id: str, kind: str, size: int, key: Optional[str] = None) -> str:
    """Reserva atómica. Devuelve la clave; lanza PortalError si no cabe o la Biblioteca está cerrada."""
    key = key or f"{kind}:{uuid.uuid4()}"
    res = db.rpc("marketing_reserve_storage", {"p_tenant": tenant_id, "p_key": key, "p_kind": kind,
                                               "p_bytes": int(size)}) or {}
    if res.get("status") == "rejected":
        reason = res.get("reason") or "limit_library_storage"
        raise PortalError(reason, 403 if reason == "library_disabled" else 409)
    return key


def release(db, tenant_id: str, key: str, consumed: bool) -> None:
    db.rpc("marketing_release_storage", {"p_tenant": tenant_id, "p_key": key, "p_consumed": bool(consumed)})


def expire_abandoned(db) -> int:
    """Reservas abandonadas (subida interrumpida, trabajo atascado): pasan a 'expired' una sola vez.
    Una reserva cerrada nunca vuelve a liberar bytes (lo impide también un disparador en la base)."""
    return int(db.rpc("marketing_expire_storage_reservations", {}) or 0)


def confirm_output(db, tenant_id: str, job_id: str, size: int) -> Optional[str]:
    """Antes de registrar un resultado: ¿cabe su tamaño REAL? None si cabe; si no, el motivo."""
    res = db.rpc("marketing_confirm_output_storage", {"p_tenant": tenant_id, "p_job": job_id, "p_bytes": int(size)}) or {}
    return None if res.get("status") == "ok" else (res.get("reason") or "storage_quota_exceeded")


def state(limit: Optional[int], used: int) -> str:
    """disabled (0) · unlimited (null) · full · enabled."""
    if limit is None:
        return "unlimited"
    if int(limit) <= 0:
        return "disabled"
    return "full" if used >= int(limit) else "enabled"


def _expired(v: Any, now: datetime) -> bool:
    if not v:
        return True                                           # sin fecha: se trata como vencido (cerrado)
    d = v if isinstance(v, datetime) else datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)) <= now


def breakdown(db, tenant_id: str, used: int, now: datetime) -> Dict[str, int]:
    """Desglose del uso total (que calcula la base): vencido pendiente de eliminación (original + sus
    derivados; invisible e irrecuperable, pero ocupa espacio hasta la purga física), reservado (subidas y
    trabajos en curso) y activo (el resto)."""
    t = f"eq.{tenant_id}"
    media = db.select("marketing_media", {"tenant_id": t, "processing_status": "neq.deleted",
                                          "select": "id,byte_size,expires_at", "limit": "10000"}) or []
    gone = {m["id"] for m in media if _expired(m.get("expires_at"), now)}
    expired = sum(int(m.get("byte_size") or 0) for m in media if m["id"] in gone)
    if gone:
        expired += sum(int(d.get("byte_size") or 0) for d in (db.select("marketing_media_derivatives", {
            "tenant_id": t, "status": "neq.deleted", "select": "media_id,byte_size", "limit": "10000"}) or [])
            if d.get("media_id") in gone)
    ids = {m["id"] for m in media}
    reserved = sum(int(r.get("bytes") or 0) for r in (db.select("marketing_storage_reservations", {
        "tenant_id": t, "status": "eq.reserved", "expires_at": f"gt.{now.isoformat()}",
        "select": "reservation_key,bytes", "limit": "10000"}) or [])
        if not (str(r.get("reservation_key", "")).startswith("upload:") and r["reservation_key"][7:] in ids))
    expired, reserved = min(expired, used), min(reserved, max(used - expired, 0))
    return {"active_bytes": max(used - expired - reserved, 0), "expired_pending_bytes": expired,
            "reserved_bytes": reserved}


def summary(db, tenant_id: str, settings: Dict[str, Any], now: Optional[datetime] = None) -> Dict[str, Any]:
    used = used_bytes(db, tenant_id)
    limit = settings.get("library_storage_limit_bytes")
    parts = breakdown(db, tenant_id, used, now or datetime.now(timezone.utc))
    return {"used_bytes": used, "limit_bytes": limit, "max_upload_bytes": settings.get("max_upload_bytes"),
            **parts, "available_bytes": None if limit is None else max(int(limit) - used, 0),
            "state": state(limit, used), "counts": "originals+derivatives+outputs+pending"}
