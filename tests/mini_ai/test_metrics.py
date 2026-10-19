from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from fastapi.testclient import TestClient
from mini_ai import metrics
from mini_ai.app import app

USER = '5356ae19-08a0-4f5a-9624-0145061bcacd'


@pytest.fixture
def setup(monkeypatch):
    monkeypatch.setenv('MINI_AI_ENABLED', 'true')
    monkeypatch.setenv('MINI_AI_USER_IDS', USER)
    db = SimpleNamespace(url='https://project.supabase.co', anon_key='public-anon',
        user_from_jwt=Mock(return_value={'id': USER}), select=Mock(return_value=[{
            'day_start': datetime.now(timezone.utc).date().isoformat(), 'day_count': 3}]))
    monkeypatch.setattr(metrics, 'database', lambda: db)
    requests = []
    original = httpx.AsyncClient
    def handler(request):
        requests.append(request)
        return httpx.Response(200, headers={'content-range': '*/4'})
    monkeypatch.setattr(metrics.httpx, 'AsyncClient', lambda **kw: original(
        transport=httpx.MockTransport(handler), **kw))
    return TestClient(app), db, requests


def test_domus_uses_user_rls_and_usage_is_self_only(setup):
    client, db, calls = setup
    r = client.get('/api/mini-ai/metrics?user_id=other', headers={'Authorization': 'Bearer user-session'})
    assert r.status_code == 200
    assert r.headers['cache-control'] == 'no-store'
    assert r.json()['domus']['homes'] == 4
    assert r.json()['nexxus']['remaining'] == 97
    assert len(calls) == 7
    for req in calls:
        assert req.method == 'HEAD'
        assert req.headers['apikey'] == 'public-anon'
        assert req.headers['Authorization'] == 'Bearer user-session'
    assert db.select.call_args.args[1]['user_id'] == 'eq.' + USER


@pytest.mark.parametrize('token,status', [('', 401), ('bad', 401), ('other', 403)])
def test_metrics_rejects_missing_invalid_or_other_user(setup, token, status):
    client, db, calls = setup
    db.user_from_jwt.return_value = None if token == 'bad' else {'id': 'not-allowed'}
    r = client.get('/api/mini-ai/metrics', headers={'Authorization': 'Bearer ' + token})
    assert r.status_code == status
    assert not calls
    db.select.assert_not_called()


def test_outage_is_unknown_not_fake_zero(setup, monkeypatch):
    client, db, _ = setup
    original = httpx.AsyncClient
    # The original has been substituted by the fixture; use its closure-free class.
    from httpx import _client
    def failed(request):
        return httpx.Response(503, text='do not expose server details')
    monkeypatch.setattr(metrics.httpx, 'AsyncClient', lambda **kw: _client.AsyncClient(
        transport=httpx.MockTransport(failed), **kw))
    db.select.side_effect = RuntimeError('secret-key')
    r = client.get('/api/mini-ai/metrics', headers={'Authorization': 'Bearer user-session'})
    assert r.status_code == 200
    assert all(value is None for value in r.json()['domus'].values())
    assert r.json()['nexxus'] is None
    assert 'secret-key' not in r.text and 'server details' not in r.text


def test_usage_day_reset_and_no_row():
    db = SimpleNamespace(select=Mock(return_value=[{'day_start':'2000-01-01','day_count':99}]))
    assert metrics.usage(db, USER)['attempts_today'] == 0
    db.select.return_value = []
    assert metrics.usage(db, USER)['remaining'] == 100
    db.select.return_value = None
    assert metrics.usage(db, USER) is None
