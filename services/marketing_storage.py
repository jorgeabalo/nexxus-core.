"""
AITA Marketing (Fase 2) — cuota de almacenamiento de la Biblioteca.

Cuenta TODO lo que ocupa espacio del tenant: originales, derivados (incluidas anonimizaciones y
vistas previas), resultados/temporales de trabajos, subidas en curso y los bytes estimados de los
trabajos pendientes (queued/processing). Las reservas se hacen de forma atómica en la base de datos
(public.marketing_reserve_storage bloquea la configuración del tenant), así dos subidas o trabajos
simultáneos nunca superan el límite. 0 = Biblioteca no habilitada; null = sin límite.
"""
import uuid
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


def state(limit: Optional[int], used: int) -> str:
    """disabled (0) · unlimited (null) · full · enabled."""
    if limit is None:
        return "unlimited"
    if int(limit) <= 0:
        return "disabled"
    return "full" if used >= int(limit) else "enabled"


def summary(db, tenant_id: str, settings: Dict[str, Any]) -> Dict[str, Any]:
    used = used_bytes(db, tenant_id)
    limit = settings.get("library_storage_limit_bytes")
    return {"used_bytes": used, "limit_bytes": limit, "max_upload_bytes": settings.get("max_upload_bytes"),
            "state": state(limit, used), "counts": "originals+derivatives+outputs+pending"}
