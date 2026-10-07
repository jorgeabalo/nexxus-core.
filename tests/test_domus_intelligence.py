import asyncio
from types import SimpleNamespace
import pytest
from domus.intelligence import NexxusIntelligence
from domus.service import Command, JarvisAdapter, DomusService


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv('DOMUS_AI_ENABLED', 'true')
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'synthetic-key')
    monkeypatch.setenv('DOMUS_AI_MODEL', 'test-model')
    monkeypatch.delenv('DOMUS_JARVIS_URL', raising=False)
    monkeypatch.delenv('DOMUS_JARVIS_TOKEN', raising=False)


def test_intelligence_disabled_and_missing_key(monkeypatch):
    monkeypatch.delenv('DOMUS_AI_ENABLED', raising=False)
    command = Command('home', 'hola', 'request')
    assert 'todavía no está conectado' in asyncio.run(NexxusIntelligence().execute(command))
    monkeypatch.setenv('DOMUS_AI_ENABLED', 'true')
    monkeypatch.delenv('ANTHROPIC_API_KEY', raising=False)
    assert 'necesita configurar' in asyncio.run(NexxusIntelligence().execute(command))


def fake_client(monkeypatch, handler):
    import anthropic
    class Client:
        def __init__(self, **kwargs):
            assert kwargs['base_url'] == 'https://api.anthropic.com'
            assert kwargs['max_retries'] == 0 and kwargs['timeout'] == 4.0
            self.messages = self
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def create(self, **kwargs): return await handler(kwargs)
    monkeypatch.setattr(anthropic, 'AsyncAnthropic', Client)


def test_provider_receives_only_current_query(configured, monkeypatch):
    async def handler(kwargs):
        assert kwargs['model'] == 'test-model'
        assert kwargs['max_tokens'] == 180
        assert 'tools' not in kwargs
        assert kwargs['messages'] == [{'role': 'user', 'content': 'cómo organizar mi día'}]
        assert 'Golden Age' in kwargs['system'] and 'No tienes herramientas' in kwargs['system']
        assert 'private-home' not in str(kwargs) and 'private-request' not in str(kwargs)
        return SimpleNamespace(content=[SimpleNamespace(type='text', text='Empieza por tus prioridades.')],
                               stop_reason='end_turn')
    fake_client(monkeypatch, handler)
    answer = asyncio.run(JarvisAdapter().execute(Command('private-home', 'cómo organizar mi día', 'private-request')))
    assert answer == 'Empieza por tus prioridades.'


def test_provider_failure_has_no_error_details(configured, monkeypatch):
    async def handler(kwargs): raise RuntimeError('synthetic-key private-provider-error')
    fake_client(monkeypatch, handler)
    answer = asyncio.run(NexxusIntelligence().execute(Command('home', 'hola', 'req')))
    assert 'Inténtalo' in answer and 'synthetic-key' not in answer and 'private-provider' not in answer


def test_domestic_never_calls_provider(configured, monkeypatch):
    async def handler(kwargs): raise AssertionError('provider must not be invoked')
    fake_client(monkeypatch, handler)
    result = asyncio.run(DomusService().dispatch('DomesticCommandIntent', Command('home', 'apaga', 'r'), 'tv'))
    assert 'No he ejecutado' in result


def test_cancellation_propagates(configured, monkeypatch):
    async def handler(kwargs): raise asyncio.CancelledError()
    fake_client(monkeypatch, handler)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(NexxusIntelligence().execute(Command('home', 'hola', 'r')))


def test_partial_external_config_does_not_fall_back(configured, monkeypatch):
    monkeypatch.setenv('DOMUS_JARVIS_URL', 'https://external.example/query')
    async def handler(kwargs): raise AssertionError('partial config must fail closed')
    fake_client(monkeypatch, handler)
    assert 'todavía no está conectado' in asyncio.run(JarvisAdapter().execute(Command('home', 'hola', 'r')))
