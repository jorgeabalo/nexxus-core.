"""
AITA Marketing (Fase 2) — renovación continua de un lease mientras dura un trabajo largo.

`LeaseKeeper` renueva en segundo plano (como mucho cada lease/3 segundos) llamando a `renew(timeout)`, que
debe preguntar a PostgreSQL (la hora y la validez del lease las decide la base, nunca el reloj local) y
respetar `timeout` (HTTP y statement_timeout en la base): una renovación nunca queda colgada.
* Si una renovación falla, tarda más que su timeout o lanza una excepción → `lost` (`check()` lanza LeaseLost).
* `cancel` (p. ej. el evento de SIGTERM) se observa en todo momento: al activarse deja de renovar de
  inmediato, aunque la subtarea siga bloqueada; el lease vence y otro worker puede retomarlo.
* Al salir del `with` el hilo se une con un tiempo máximo (`join_timeout`); nunca bloquea indefinidamente
  y no abre conexiones propias.
Solo usa la biblioteca estándar.
"""
import threading
import time
from typing import Callable, Optional

MIN_LEASE_SECONDS = 30
MAX_LEASE_SECONDS = 900
TICK = 0.02                                   # cada cuánto se miran la cancelación y la parada
MAX_RENEW_TIMEOUT = 10.0


class LeaseLost(Exception):
    pass


def validate(lease_seconds: float, heartbeat_seconds: Optional[float] = None) -> float:
    """Devuelve el intervalo de heartbeat. Rechaza un lease demasiado corto o largo y un intervalo que no
    sea como mucho un tercio del lease."""
    if not (MIN_LEASE_SECONDS <= float(lease_seconds) <= MAX_LEASE_SECONDS):
        raise ValueError(f"lease_seconds debe estar entre {MIN_LEASE_SECONDS} y {MAX_LEASE_SECONDS}")
    hb = float(lease_seconds) / 3 if heartbeat_seconds is None else float(heartbeat_seconds)
    if not (0 < hb <= float(lease_seconds) / 3):
        raise ValueError("heartbeat_seconds debe ser > 0 y como mucho lease_seconds / 3")
    return hb


class LeaseKeeper:
    def __init__(self, renew: Callable[[float], bool], interval: float, name: str = "lease",
                 cancel: Optional[threading.Event] = None, renew_timeout: Optional[float] = None):
        if interval <= 0:
            raise ValueError("interval")
        self._renew, self._interval, self._name = renew, float(interval), name
        self.renew_timeout = min(float(renew_timeout or interval), MAX_RENEW_TIMEOUT)
        self._cancel = cancel or threading.Event()
        self._stop = threading.Event()
        self._lost = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.renewals = 0
        self.cancelled = False
        self.joined = True

    @property
    def lost(self) -> bool:
        return self._lost.is_set()

    def check(self) -> None:
        if self._lost.is_set():
            raise LeaseLost(self._name)

    def stop(self) -> None:
        self._stop.set()

    def _sleep(self) -> bool:
        """Espera un intervalo vigilando parada y cancelación. True = seguir renovando."""
        end = time.monotonic() + self._interval
        while time.monotonic() < end:
            if self._stop.is_set():
                return False
            if self._cancel.is_set():
                self.cancelled = True                          # SIGTERM: ni una renovación más
                return False
            time.sleep(min(TICK, max(end - time.monotonic(), 0)))
        return not (self._stop.is_set() or self._cancel.is_set())

    def _run(self) -> None:
        while self._sleep():
            started = time.monotonic()
            try:
                ok = bool(self._renew(self.renew_timeout))
            except Exception:                                   # noqa: BLE001 — red/timeout = lease no garantizado
                ok = False
            if time.monotonic() - started > self.renew_timeout:  # llegó tarde: no se puede confiar en ella
                ok = False
            if not ok:
                self._lost.set()
                return
            self.renewals += 1
        if self._cancel.is_set():
            self.cancelled = True

    def __enter__(self) -> "LeaseKeeper":
        # daemon=True solo como red de seguridad al salir el intérprete: el hilo se une siempre en __exit__.
        self._thread = threading.Thread(target=self._run, name=f"lease-keeper-{self._name}", daemon=True)
        self._thread.start()
        self.joined = False
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self.renew_timeout + 1.0)   # acotado: la renovación tiene timeout
            self.joined = not self._thread.is_alive()
            self._thread = None
