"""
Cliente mínimo de Supabase (PostgREST) SOLO para el servidor.

Usa SUPABASE_SERVICE_ROLE_KEY, que ignora RLS. Por eso:
  * este módulo nunca se importa desde el frontend ni se expone por HTTP;
  * la clave nunca se registra en logs;
  * si faltan SUPABASE_URL o SUPABASE_SERVICE_ROLE_KEY, queda deshabilitado
    (todas las llamadas devuelven None) y Claudia sigue funcionando igual.

Se usa httpx (ya en requirements) con timeouts cortos para no retener hilos.
"""
import os
import logging
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(float(os.getenv("SUPABASE_TIMEOUT_SECONDS", "4")), connect=3.0)


class SupabaseAdmin:
    def __init__(self, url: Optional[str] = None, service_key: Optional[str] = None):
        self.url = (url or os.getenv("SUPABASE_URL") or "").rstrip("/")
        self._key = service_key or os.getenv("SUPABASE_SERVICE_ROLE_KEY") or ""
        self.enabled = bool(self.url and self._key)
        self._client: Optional[httpx.Client] = None
        if not self.enabled:
            logger.warning("SupabaseAdmin deshabilitado: faltan SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY")

    # -- interno ---------------------------------------------------------
    def _http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                base_url=f"{self.url}/rest/v1",
                timeout=_TIMEOUT,
                headers={
                    "apikey": self._key,
                    "Authorization": f"Bearer {self._key}",
                    "Content-Type": "application/json",
                },
            )
        return self._client

    def _request(self, method: str, table: str, *, params=None, json=None, prefer=None) -> Optional[List[Dict[str, Any]]]:
        if not self.enabled:
            return None
        headers = {"Prefer": prefer} if prefer else None
        r = self._http().request(method, f"/{table}", params=params, json=json, headers=headers)
        if r.status_code >= 400:
            # Nunca incluir cabeceras (contienen la clave) en el log
            raise RuntimeError(f"Supabase {method} {table} -> {r.status_code}: {r.text[:300]}")
        if not r.content:
            return []
        data = r.json()
        return data if isinstance(data, list) else [data]

    # -- API pública -----------------------------------------------------
    def select(self, table: str, params: Dict[str, str]) -> Optional[List[Dict[str, Any]]]:
        return self._request("GET", table, params=params)

    def insert(self, table: str, row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        rows = self._request("POST", table, json=row, prefer="return=representation")
        return rows[0] if rows else None

    def upsert(self, table: str, row: Dict[str, Any], on_conflict: str) -> Optional[Dict[str, Any]]:
        rows = self._request("POST", table, json=row, params={"on_conflict": on_conflict},
                             prefer="resolution=merge-duplicates,return=representation")
        return rows[0] if rows else None

    def update(self, table: str, filters: Dict[str, str], values: Dict[str, Any]) -> Optional[List[Dict[str, Any]]]:
        return self._request("PATCH", table, params=filters, json=values, prefer="return=representation")
