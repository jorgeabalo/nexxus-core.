from fastapi import FastAPI
from fastapi.testclient import TestClient
from domus.onboarding import router

app = FastAPI()
app.include_router(router)
client = TestClient(app)


def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv('DOMUS_ONBOARDING_ENABLED', raising=False)
    for path in ['/domus', '/api/domus/onboarding/config', '/domus/assets/app.js']:
        assert client.get(path).status_code == 404


def test_public_config_never_exposes_service_key(monkeypatch):
    monkeypatch.setenv('DOMUS_ONBOARDING_ENABLED', 'true')
    monkeypatch.setenv('SUPABASE_URL', 'https://example.supabase.co/')
    monkeypatch.setenv('SUPABASE_ANON_KEY', 'public-test-key')
    monkeypatch.setenv('SUPABASE_SERVICE_ROLE_KEY', 'private-test-key')
    response = client.get('/api/domus/onboarding/config')
    assert response.json() == {'supabaseUrl': 'https://example.supabase.co', 'supabaseAnonKey': 'public-test-key'}
    assert 'private-test-key' not in response.text
    assert response.headers['Cache-Control'] == 'no-store'


def test_missing_config_and_asset_allowlist(monkeypatch):
    monkeypatch.setenv('DOMUS_ONBOARDING_ENABLED', 'true')
    monkeypatch.delenv('SUPABASE_ANON_KEY', raising=False)
    assert client.get('/api/domus/onboarding/config').status_code == 503
    assert client.get('/domus/assets/onboarding.py').status_code == 404
    for name in ['app.js', 'style.css', 'supabase.js']:
        assert client.get('/domus/assets/' + name).status_code == 200
    response = client.get('/domus')
    assert response.status_code == 200
    assert "frame-ancestors 'none'" in response.headers['Content-Security-Policy']
    assert 'Autorizo' in response.text
