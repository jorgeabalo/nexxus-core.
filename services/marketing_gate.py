"""
AITA Marketing — puerta única de acceso (usada por todos los endpoints vía MarketingService.ctx).

1. Marketing solo está habilitado si tenants.modules.marketing es exactamente true. Cualquier otro
   valor (ausente, null, "true", 1, modules mal formado) falla cerrado con 403 marketing_disabled,
   y en ese caso no se consulta ninguna tabla marketing_*.
2. Si está habilitado pero una tabla marketing_* no existe (migración sin aplicar), se responde
   503 marketing_unavailable sin detalles. Solo ese error concreto: RLS, permisos o conexión se
   propagan tal cual (500) para no ocultarlos como si faltara la migración.
"""
import logging
import re
from typing import Any

from services.member_portal import PortalError

logger = logging.getLogger(__name__)

def marketing_module_enabled(tenant: Any) -> bool:
    """True solo si tenants.modules es un objeto y modules.marketing es exactamente true."""
    modules = tenant.get("modules") if isinstance(tenant, dict) else None
    return isinstance(modules, dict) and modules.get("marketing") is True


# PostgREST responde 404 con PGRST205 (o 42P01 en versiones anteriores) cuando la tabla no existe.
_MISSING_TABLE = re.compile(r"-> 404:.*(PGRST205|PGRST202|42P01|42883)", re.S)
_GUARDED = ("marketing_", "rpc/marketing_")


class MarketingTablesGuard:
    """Envuelve la base de datos: si una tabla marketing_* no existe (migración sin aplicar),
    responde 503 marketing_unavailable sin detalles. Cualquier otro error (RLS, permisos,
    conexión) se propaga tal cual y acaba como 500, para no ocultarlo como "falta la migración"."""

    def __init__(self, db):
        self._db = db

    def __getattr__(self, name):
        return getattr(self._db, name)

    def _call(self, fn, table, *args):
        try:
            return fn(table, *args)
        except PortalError:
            raise
        except Exception as e:
            if str(table).startswith(_GUARDED) and _MISSING_TABLE.search(str(e)):
                logger.warning(f"MARKETING_UNAVAILABLE tabla={table}")
                raise PortalError("marketing_unavailable", 503)
            raise

    def select(self, table, params):
        return self._call(self._db.select, table, params)

    def insert(self, table, row):
        return self._call(self._db.insert, table, row)

    def upsert(self, table, row, on_conflict):
        return self._call(self._db.upsert, table, row, on_conflict)

    def update(self, table, filters, values):
        return self._call(self._db.update, table, filters, values)

    def rpc(self, fn, args):
        return self._call(lambda _t, a: self._db.rpc(fn, a), f"rpc/{fn}", args)
