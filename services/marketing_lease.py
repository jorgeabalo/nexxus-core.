"""
AITA Marketing (Fase 2) — renovación continua de un lease mientras dura un trabajo largo.

`LeaseKeeper` renueva en segundo plano (como mucho cada lease/3 segundos) llamando a `renew()`, que
debe preguntar a PostgreSQL (la hora y la validez del lease las decide la base, nunca el reloj local).
Si una renovación falla o lanza una excepción, el lease se da por perdido: `lost` queda activo y `check()`
lanza `LeaseLost`. El hilo se detiene y se une (join) al salir del bloque `with`, al pedir `stop()` (p. ej.
SIGTERM) o al perder el lease: nunca quedan hilos huérfanos. No abre conexiones propias.
Solo usa la biblioteca estándar.
"""
import threading
from typing import Callable, Optional

MIN_LEASE_SECONDS = 30
MAX_LEASE_SECONDS = 900


class LeaseLost(Exception):
    pass


def validate(lease_seconds: float, heartbeat_seconds: Optional[float] = None) -> float:
    """Devuelve el intervalo de heartbeat. Rechaza un lease demasiado corto o largo y un intervalo que no
    sea estrictamente menor que un tercio del lease."""
    if not (MIN_LEASE_SECONDS <= float(lease_seconds) <= MAX_LEASE_SECONDS):
        raise ValueError(f"lease_seconds debe estar entre {MIN_LEASE_SECONDS} y {MAX_LEASE_SECONDS}")
    hb = float(lease_seconds) / 3 if heartbeat_seconds is None else float(heartbeat_seconds)
    if not (0 < hb <= float(lease_seconds) / 3):
        raise ValueError("heartbeat_seconds debe ser > 0 y como mucho lease_seconds / 3")
    return hb


class LeaseKeeper:
    def __init__(self, renew: Callable[[], bool], interval: float, name: str = "lease"):
        if interval <= 0:
            raise ValueError("interval")
        self._renew, self._interval, self._name = renew, float(interval), name
        self._stop = threading.Event()
        self._lost = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.renewals = 0

    @property
    def lost(self) -> bool:
        return self._lost.is_set()

    def check(self) -> None:
        if self._lost.is_set():
            raise LeaseLost(self._name)

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                ok = bool(self._renew())
            except Exception:                                   # noqa: BLE001 — error de red = lease no garantizado
                ok = False
            if not ok:
                self._lost.set()
                return
            self.renewals += 1

    def __enter__(self) -> "LeaseKeeper":
        self._thread = threading.Thread(target=self._run, name=f"lease-keeper-{self._name}", daemon=False)
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join()
            self._thread = None
