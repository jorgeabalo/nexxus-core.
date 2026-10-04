"""Nexxus household dispatcher. Device execution requires a registered adapter."""
import os
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlparse
import httpx


@dataclass(frozen=True)
class Command:
    home_id: str
    text: str
    request_id: str


class Adapter(Protocol):
    async def execute(self, command: Command) -> str: ...


class JarvisAdapter:
    """Read-only V1 contract; never reuse Claudia or Golden Age conversations."""
    async def execute(self, command: Command) -> str:
        url = os.getenv('DOMUS_JARVIS_URL', '')
        token = os.getenv('DOMUS_JARVIS_TOKEN', '')
        if not url or not token:
            if not url and not token:
                from .intelligence import NexxusIntelligence
                return await NexxusIntelligence().execute(command)
            return 'Nexxus todavía no está conectado. La integración de voz de Domus está lista.'
        parsed = urlparse(url)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
            return 'La conexión de Nexxus necesita una configuración segura.'
        try:
            async with httpx.AsyncClient(timeout=3.0, follow_redirects=False, trust_env=False) as client:
                response = await client.post(url, headers={'Authorization': f'Bearer {token}'}, json={
                    'home_id': command.home_id, 'text': command.text,
                    'request_id': command.request_id, 'mode': 'read_only',
                    'source': 'alexa', 'session_id': f'domus:{command.home_id}',
                })
                response.raise_for_status()
                result = response.json()
                speech = result.get('speech')
                if not isinstance(speech, str) or not speech.strip():
                    raise ValueError('Invalid speech')
                return speech[:2000]
        except (httpx.HTTPError, ValueError, TypeError, AttributeError):
            return 'Nexxus no está disponible ahora. Inténtalo de nuevo en un momento.'


class DomusService:
    # Fixed keys, never derived from a model's proposed tool call.
    DEVICE_KEYS = ('tv', 'wiim', 'plug', 'thermostat', 'roomba')
    FUTURE_MODULES = ('security', 'energy', 'pantry', 'panel')

    def __init__(self, jarvis=None):
        self.jarvis = jarvis or JarvisAdapter()
        self.devices: dict[str, Adapter] = {}

    async def dispatch(self, intent: str, command: Command, device: str = '') -> str:
        if intent == 'DomesticCommandIntent':
            if device not in self.DEVICE_KEYS:
                return 'Puedes pedir televisión, música, enchufes, temperatura o limpieza.'
            adapter = self.devices.get(device)
            if not adapter:
                return 'Ese dispositivo todavía no está conectado a Domus. No he ejecutado la orden.'
            return await adapter.execute(command)
        if intent == 'JarvisCommandIntent':
            return await self.jarvis.execute(command)
        return 'No entendí la orden. Puedes decir: consulta a Nexxus qué tengo pendiente.'
