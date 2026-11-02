"""
AITA Marketing — Biblioteca en un bucket S3 (Railway Buckets) en lugar de Supabase Storage.

* Solo se desvía el bucket de la Biblioteca (marketing-assets). Recibos, documentos y todo lo demás
  siguen en Supabase. La base de datos no cambia: las filas guardan la misma ruta
  {tenant_id}/originals/{asset_id}/{archivo}; solo cambia dónde vive el objeto.
* Se activa con MARKETING_S3_BUCKET, MARKETING_S3_ENDPOINT, MARKETING_S3_ACCESS_KEY_ID y
  MARKETING_S3_SECRET_ACCESS_KEY (más MARKETING_S3_REGION, por defecto "auto", y MARKETING_S3_ADDRESSING
  "virtual" | "path"). Configuración a medias = error al arrancar: nunca se escribe en un sitio por error.
* El bucket es privado y el navegador nunca recibe una URL suya: todo pasa por el backend, como antes.
* Un original nunca se sobrescribe (equivalente a x-upsert=false): se comprueba antes de escribir y la
  ruta lleva un UUID nuevo, así que dos escrituras no pueden apuntar al mismo objeto.
* Los errores nunca incluyen credenciales ni la ruta (puede identificar al tenant).
"""
import os
from typing import Any, Dict, Iterator, List, Optional, Tuple

MARKETING_BUCKET = "marketing-assets"
_VARS = ("MARKETING_S3_BUCKET", "MARKETING_S3_ENDPOINT", "MARKETING_S3_ACCESS_KEY_ID", "MARKETING_S3_SECRET_ACCESS_KEY")


class StoreConfigError(RuntimeError):
    pass


def config(env: Optional[Dict[str, str]] = None) -> Optional[Dict[str, str]]:
    """None si no hay bucket configurado; error si la configuración está incompleta."""
    env = os.environ if env is None else env
    vals = {k: (env.get(k) or "").strip() for k in _VARS}
    present = [k for k, v in vals.items() if v]
    if not present:
        return None
    if len(present) != len(_VARS):
        raise StoreConfigError("MARKETING_S3_* incompleto: faltan " + ", ".join(k for k in _VARS if not vals[k]))
    addressing = (env.get("MARKETING_S3_ADDRESSING") or "virtual").strip().lower()
    if addressing not in ("virtual", "path"):
        raise StoreConfigError("MARKETING_S3_ADDRESSING debe ser 'virtual' o 'path'")
    return {"bucket": vals["MARKETING_S3_BUCKET"], "endpoint": vals["MARKETING_S3_ENDPOINT"],
            "key_id": vals["MARKETING_S3_ACCESS_KEY_ID"], "secret": vals["MARKETING_S3_SECRET_ACCESS_KEY"],
            "region": (env.get("MARKETING_S3_REGION") or "auto").strip(), "addressing": addressing}


class S3Store:
    """Cliente mínimo del bucket. Cada método traduce los errores a RuntimeError sin detalles sensibles."""

    def __init__(self, cfg: Dict[str, str], client=None):
        self.bucket = cfg["bucket"]
        if client is None:
            import boto3
            from botocore.config import Config
            client = boto3.client(
                "s3", endpoint_url=cfg["endpoint"], aws_access_key_id=cfg["key_id"],
                aws_secret_access_key=cfg["secret"], region_name=cfg["region"],
                config=Config(s3={"addressing_style": cfg["addressing"]}, connect_timeout=5, read_timeout=60,
                              retries={"max_attempts": 3, "mode": "standard"}))
        self.c = client

    def _call(self, op: str, fn, **kw):
        try:
            return fn(Bucket=self.bucket, **kw)
        except Exception as e:
            code = getattr(e, "response", {}).get("Error", {}).get("Code", "") if hasattr(e, "response") else ""
            raise RuntimeError(f"Bucket {op}: {type(e).__name__} {code}".strip()) from None

    def size(self, key: str) -> Optional[int]:
        try:
            return int(self.c.head_object(Bucket=self.bucket, Key=key)["ContentLength"])
        except Exception as e:
            status = getattr(e, "response", {}).get("ResponseMetadata", {}).get("HTTPStatusCode") \
                if hasattr(e, "response") else None
            if status == 404 or getattr(e, "response", {}).get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return None
            raise RuntimeError(f"Bucket HEAD: {type(e).__name__}") from None

    def put(self, key: str, data: bytes, mime: str) -> None:
        if self.size(key) is not None:
            raise RuntimeError("Bucket PUT -> 409: Duplicate")          # nunca se sobrescribe un original
        self._call("PUT", self.c.put_object, Key=key, Body=data, ContentType=mime)

    def get(self, key: str) -> bytes:
        return self._call("GET", self.c.get_object, Key=key)["Body"].read()

    def read_range(self, key: str, start: int, end: int) -> bytes:
        """Bytes [start, end] inclusivos (lecturas acotadas para validar sin bajar el archivo entero)."""
        return self._call("GET", self.c.get_object, Key=key, Range=f"bytes={int(start)}-{int(end)}")["Body"].read()

    def stream(self, key: str, chunk_size: int = 65536, byte_range: Optional[Tuple[int, int]] = None) -> Iterator[bytes]:
        kw = {"Key": key}
        if byte_range:
            kw["Range"] = f"bytes={int(byte_range[0])}-{int(byte_range[1])}"
        body = self._call("STREAM", self.c.get_object, **kw)["Body"]

        def chunks():
            try:
                for chunk in body.iter_chunks(chunk_size):
                    if chunk:
                        yield chunk
            finally:
                body.close()
        return chunks()

    def remove(self, key: str) -> None:
        self._call("DELETE", self.c.delete_object, Key=key)

    # -- subida por partes (vídeos grandes) --
    def create_multipart(self, key: str, mime: str) -> str:
        if self.size(key) is not None:
            raise RuntimeError("Bucket MULTIPART -> 409: Duplicate")
        return self._call("MULTIPART", self.c.create_multipart_upload, Key=key, ContentType=mime)["UploadId"]

    def upload_part(self, key: str, upload_id: str, number: int, data: bytes) -> str:
        return self._call("PART", self.c.upload_part, Key=key, UploadId=upload_id, PartNumber=int(number),
                          Body=data)["ETag"]

    def complete_multipart(self, key: str, upload_id: str, etags: List[str]) -> None:
        parts = [{"PartNumber": i + 1, "ETag": e} for i, e in enumerate(etags)]
        self._call("COMPLETE", self.c.complete_multipart_upload, Key=key, UploadId=upload_id,
                   MultipartUpload={"Parts": parts})

    def abort_multipart(self, key: str, upload_id: str) -> None:
        self._call("ABORT", self.c.abort_multipart_upload, Key=key, UploadId=upload_id)


class StoreRoutedDB:
    """El mismo cliente de base de datos, pero los objetos de la Biblioteca van al bucket S3.
    Todo lo que no es Storage de la Biblioteca se delega sin cambios."""

    def __init__(self, db, store: S3Store):
        self._db = db
        self.objects = store

    def __getattr__(self, name: str) -> Any:
        return getattr(self._db, name)

    def storage_upload(self, bucket, key, data, content_type):
        if bucket != MARKETING_BUCKET:
            return self._db.storage_upload(bucket, key, data, content_type)
        self.objects.put(key, data, content_type)

    def storage_download(self, bucket, key):
        return self.objects.get(key) if bucket == MARKETING_BUCKET else self._db.storage_download(bucket, key)

    def storage_stream(self, bucket, key, chunk_size=65536, byte_range=None):
        if bucket != MARKETING_BUCKET:
            return self._db.storage_stream(bucket, key, chunk_size=chunk_size, byte_range=byte_range)
        return self.objects.stream(key, chunk_size=chunk_size, byte_range=byte_range)

    def storage_remove(self, bucket, key):
        if bucket != MARKETING_BUCKET:
            return self._db.storage_remove(bucket, key)
        self.objects.remove(key)


_STORE: Optional[S3Store] = None


def marketing_db(db):
    """Envuelve el cliente si hay bucket configurado; si no, lo devuelve tal cual (Supabase Storage)."""
    global _STORE
    if db is None:
        return None
    cfg = config()
    if cfg is None:
        return db
    if _STORE is None or _STORE.bucket != cfg["bucket"]:
        _STORE = S3Store(cfg)
    return StoreRoutedDB(db, _STORE)


def large_uploads_available(db) -> bool:
    """La subida por partes solo existe con el bucket S3 (Supabase gratis no admite archivos > 50 MB)."""
    return getattr(db, "objects", None) is not None          # también a través de MarketingTablesGuard
