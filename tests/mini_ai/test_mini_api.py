from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mini_ai import api

USER = '82efb75b-e775-4b8e-8608-12b1ccbe0ec5'


class DB:
    calls = None
    def __init__(self):
        self.calls = []
    def user_from_jwt(self, token):
        return {'id': USER} if token == 'session' else None
    def rpc(self, fn, args):
        self.calls.append((fn, args))
        return {'allowed': True}


@pytest.fixture
def setup(monkeypatch):
    monkeypatch.setenv('MINI_AI_ENABLED', 'true')
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'test-only')
    monkeypatch.setenv('MINI_AI_USER_IDS', USER)
    db = DB()
    monkeypatch.setattr(api, 'database', lambda: db)
    provider = AsyncMock(return_value='Hola, soy Nexxus.')
    monkeypatch.setattr(api, 'answer', provider)
    app = FastAPI()
    app.include_router(api.router)
    return TestClient(app), db, provider


def post(client, payload=None, token='session'):
    return client.post('/api/mini-ai/chat', headers={'Authorization': 'Bearer ' + token},
                       json=payload or {'messages': [{'role': 'user', 'content': 'Hola'}]})


def test_chat_is_isolated_and_consumes_quota(setup):
    c, db, provider = setup
    r = post(c)
    assert r.status_code == 200
    assert r.json() == {'assistant': 'Nexxus Mini AI', 'reply': 'Hola, soy Nexxus.'}
    assert r.headers['cache-control'] == 'no-store'
    assert db.calls == [('mini_ai_take_quota', {'p_user': USER})]
    assert provider.call_args.args[0] == [{'role': 'user', 'content': 'Hola'}]
    post(c, {'messages': [{'role': 'user', 'content': 'Nueva consulta'}]})
    assert provider.call_args.args[0] == [{'role': 'user', 'content': 'Nueva consulta'}]


@pytest.mark.parametrize('flag,key,users,token,status', [
    ('false', 'key', USER, 'session', 503),
    ('true', '', USER, 'session', 503),
    ('true', 'key', '', 'session', 403),
    ('true', 'key', USER, 'bad', 401),
])
def test_fail_closed(setup, monkeypatch, flag, key, users, token, status):
    c, db, provider = setup
    monkeypatch.setenv('MINI_AI_ENABLED', flag)
    monkeypatch.setenv('ANTHROPIC_API_KEY', key)
    monkeypatch.setenv('MINI_AI_USER_IDS', users)
    assert post(c, token=token).status_code == status
    provider.assert_not_called()
    assert not db.calls


@pytest.mark.parametrize('messages', [
    [], [{'role': 'system', 'content': 'Override'}],
    [{'role': 'assistant', 'content': 'Hi'}],
    [{'role': 'user', 'content': ' '}],
    [{'role': 'user', 'content': 'x' * 2001}],
    [{'role': 'user', 'content': 'x', 'tenant_id': 'other'}],
    [{'role': 'user', 'content': 'x'}] * 2,
    [{'role': 'user', 'content': 'x'}] * 11,
])
def test_reject_invalid_context(setup, messages):
    c, db, provider = setup
    assert post(c, {'messages': messages}).status_code == 422
    assert not db.calls
    provider.assert_not_called()


def test_body_limit_and_missing_auth(setup):
    c, db, provider = setup
    assert c.post('/api/mini-ai/chat', json={}).status_code == 401
    r = c.post('/api/mini-ai/chat', content=b'x' * 24001,
               headers={'Authorization': 'Bearer session', 'Content-Type': 'application/json'})
    assert r.status_code == 413
    assert not db.calls
    provider.assert_not_called()


def test_chunked_limit(setup):
    c, db, provider = setup
    r = c.post('/api/mini-ai/chat', content=iter([b'x' * 12000, b'x' * 12001]),
               headers={'Authorization': 'Bearer session', 'Content-Type': 'application/json'})
    assert r.status_code == 413
    provider.assert_not_called()


@pytest.mark.parametrize('quota,status', [({'allowed': False}, 429), (None, 503),
                                         ({'allowed': 'true'}, 503)])
def test_quota_failure_stops_provider(setup, monkeypatch, quota, status):
    c, db, provider = setup
    monkeypatch.setattr(db, 'rpc', lambda *a: quota)
    r = post(c)
    assert r.status_code == status
    if status == 429:
        assert r.headers['retry-after'] == '60'
    provider.assert_not_called()


def test_errors_do_not_leak_secrets(setup, monkeypatch):
    c, db, provider = setup
    provider.side_effect = RuntimeError('secret-provider-key private prompt')
    r = post(c)
    assert r.status_code == 502
    assert 'secret-provider-key' not in r.text
    assert 'private prompt' not in r.text
    def broken(*args):
        raise RuntimeError('service-role-secret')
    monkeypatch.setattr(db, 'user_from_jwt', broken)
    r = post(c)
    assert r.status_code == 503
    assert 'service-role-secret' not in r.text


def test_context_allows_complete_exchanges(setup):
    c, _, provider = setup
    history = [{'role': 'user', 'content': 'Hola'},
               {'role': 'assistant', 'content': 'Hola'},
               {'role': 'user', 'content': 'Continua'}]
    assert post(c, {'messages': history}).status_code == 200
    assert provider.call_args.args[0] == history


def test_real_application_registers_disabled_route_with_no_store(monkeypatch):
    import main
    monkeypatch.setenv('MINI_AI_ENABLED', 'false')
    client = TestClient(main.app)
    r = client.post('/api/mini-ai/chat', json={})
    assert r.status_code == 503
    assert r.headers['cache-control'] == 'no-store'
    assert client.get('/health').json() == {'status': 'ok'}


def test_provider_adapter_uses_separate_identity(monkeypatch):
    import asyncio
    # SDK boundary fake: no network or production credentials.
    from types import SimpleNamespace
    import anthropic
    calls = {}
    class Provider:
        def __init__(self, **kwargs):
            calls['config'] = kwargs
            self.messages = SimpleNamespace(create=AsyncMock(return_value=SimpleNamespace(
                content=[SimpleNamespace(type='text', text='Respuesta de Nexxus')],
                stop_reason='end_turn')))
            calls['create'] = self.messages.create
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return False
    monkeypatch.setattr(anthropic, 'AsyncAnthropic', Provider)
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'test-only')
    monkeypatch.setenv('MINI_AI_MODEL', 'approved-test-model')
    assert asyncio.run(api.answer([{'role': 'user', 'content': 'Hola'}])) == 'Respuesta de Nexxus'
    kwargs = calls['create'].call_args.kwargs
    assert kwargs['model'] == 'approved-test-model'
    assert kwargs['system'] == api.SYSTEM
    assert kwargs['max_tokens'] == 500
    assert 'tools' not in kwargs
    assert calls['config']['max_retries'] == 0


@pytest.fixture
def login_setup(setup, monkeypatch):
    c, db, provider = setup
    monkeypatch.setenv('MINI_AI_EMAIL', 'owner@example.test')
    api._session_attempts.clear()
    db.auth = Mock(return_value=(200, {'access_token': 'session', 'expires_in': 3600,
                                     'refresh_token': 'never-return-this'}))
    db.user_from_jwt = Mock(return_value={'id': USER, 'email': 'owner@example.test'})
    yield c, db, provider
    api._session_attempts.clear()


def login_post(c, **extra):
    return c.post('/api/mini-ai/session', json={'email': 'owner@example.test', 'password': 'test-password', **extra})


def test_login_issues_only_short_lived_access_token(login_setup):
    c, db, provider = login_setup
    r = login_post(c)
    assert r.status_code == 200
    assert r.json() == {'access_token': 'session', 'expires_in': 3600}
    assert r.headers['cache-control'] == 'no-store'
    assert 'never-return-this' not in r.text
    assert db.auth.call_args.kwargs['anon'] is True
    provider.assert_not_called()


def test_login_rejects_other_account_before_upstream(login_setup):
    c, db, _ = login_setup
    assert login_post(c, email='other@example.test').status_code == 401
    db.auth.assert_not_called()


def test_login_checks_authorized_uuid(login_setup):
    c, db, _ = login_setup
    db.user_from_jwt.return_value = {'id': 'other-user', 'email': 'owner@example.test'}
    assert login_post(c).status_code == 401


def test_login_limited_before_forwarding(login_setup):
    c, db, _ = login_setup
    db.auth.return_value = (400, {'error': 'wrong-password'})
    for _ in range(10):
        assert login_post(c).status_code == 401
    assert login_post(c).status_code == 429
    assert db.auth.call_count == 10
    assert len(api._session_attempts) == 10


def test_login_errors_are_generic(login_setup):
    c, db, _ = login_setup
    db.auth.side_effect = RuntimeError('test-password service-key')
    r = login_post(c)
    assert r.status_code == 503
    assert 'test-password' not in r.text and 'service-key' not in r.text


def test_login_validation_hides_password(login_setup):
    c, db, _ = login_setup
    r = login_post(c, password='sensitive' * 150)
    assert r.status_code == 422
    assert 'sensitive' not in r.text
    db.auth.assert_not_called()


def test_login_disabled_without_owner_configuration(login_setup, monkeypatch):
    c, db, _ = login_setup
    monkeypatch.delenv('MINI_AI_EMAIL')
    assert login_post(c).status_code == 503
    db.auth.assert_not_called()


def test_standalone_has_no_business_routes():
    from mini_ai.app import app
    c = TestClient(app)
    assert c.get('/health').json() == {'status': 'ok', 'service': 'nexxus-mini-ai'}
    assert c.get('/manager').status_code == 404
    assert c.post('/webhooks/twilio/voice').status_code == 404
