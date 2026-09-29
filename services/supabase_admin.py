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
import re
import logging
from typing import Any, Dict, List, Optional, Tuple

import httpx

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(float(os.getenv("SUPABASE_TIMEOUT_SECONDS", "4")), connect=3.0)


class SupabaseAdmin:
    def __init__(self, url: Optional[str] = None, service_key: Optional[str] = None):
        # Se eliminan espacios/saltos de línea: al pegar la clave en un panel
        # es fácil que se cuele un salto de línea y rompa la cabecera HTTP.
        self.url = re.sub(r"\s+", "", url or os.getenv("SUPABASE_URL") or "").rstrip("/")
        self._key = re.sub(r"\s+", "", service_key or os.getenv("SUPABASE_SERVICE_ROLE_KEY") or "")
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
        try:
            r = self._http().request(method, f"/{table}", params=params, json=json, headers=headers)
        except Exception as e:
            # Nunca propagar el texto original: algunas excepciones de httpx
            # incluyen los valores de las cabeceras (la clave secreta).
            raise RuntimeError(f"Supabase {method} {table}: {type(e).__name__}: {self._scrub(str(e))}") from None
        if r.status_code >= 400:
            # Nunca incluir cabeceras (contienen la clave) en el log
            raise RuntimeError(f"Supabase {method} {table} -> {r.status_code}: {self._scrub(r.text)}")
        if not r.content:
            return []
        data = r.json()
        return data if isinstance(data, list) else [data]

    def _scrub(self, text: str) -> str:
        """Elimina cualquier rastro de la clave de un texto de error."""
        if self._key:
            text = text.replace(self._key, "***")
            for trozo in re.split(r"\s+", os.getenv("SUPABASE_SERVICE_ROLE_KEY") or ""):
                if len(trozo) >= 6:
                    text = text.replace(trozo, "***")
        text = re.sub(r"sb_secret_[A-Za-z0-9_\-\\n]+", "sb_secret_***", text)
        text = re.sub(r"eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+", "***", text)
        return text[:300]

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

    # -- Supabase Auth (GoTrue) -----------------------------------------
    def auth(self, method: str, path: str, *, json=None, params=None, anon: bool = False,
             bearer: Optional[str] = None) -> Tuple[int, Any]:
        """Llamada a /auth/v1. Con service_role por defecto (admin);
        anon=True usa la clave pública (p.ej. enviar enlace mágico);
        bearer=<jwt> consulta como ese usuario (validar su sesión).
        Devuelve (status, json). Nunca incluye claves en los errores."""
        if not self.url:
            return 0, None
        apikey = self.anon_key if (anon or bearer) else self._key
        if not apikey:
            return 0, None
        headers = {"apikey": apikey, "Authorization": f"Bearer {bearer or apikey}",
                   "Content-Type": "application/json"}
        try:
            r = httpx.request(method, f"{self.url}/auth/v1{path}", json=json, params=params,
                              headers=headers, timeout=_TIMEOUT)
        except Exception as e:
            raise RuntimeError(f"Supabase auth {method} {path}: {type(e).__name__}: {self._scrub(str(e))}") from None
        try:
            data = r.json() if r.content else None
        except ValueError:
            data = None
        return r.status_code, data

    @property
    def anon_key(self) -> str:
        return re.sub(r"\s+", "", os.getenv("SUPABASE_ANON_KEY") or "")

    def user_from_jwt(self, jwt: str) -> Optional[Dict[str, Any]]:
        """Valida un access token de Supabase y devuelve el usuario, o None."""
        if not jwt or not self.url:
            return None
        status, data = self.auth("GET", "/user", bearer=jwt)
        return data if status == 200 and isinstance(data, dict) and data.get("id") else None
