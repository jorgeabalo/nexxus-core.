import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from domus.onboarding import router
from domus import onboarding

app = FastAPI()
app.include_router(router)
client = TestClient(app)
HOME = '00000000-0000-0000-0000-000000000001'


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv('DOMUS_ONBOARDING_ENABLED', 'true')
    monkeypatch.setenv('SUPABASE_URL', 'https://example.supabase.co')
    monkeypatch.setenv('SUPABASE_ANON_KEY', 'public-test')


def test_missing_session_and_invalid_question(configured):
    assert client.post('/api/domus/panel/ask', json={'home_id': HOME, 'text': 'Hola'}).status_code == 401
    assert client.post('/api/domus/panel/ask', json={'home_id': HOME, 'text': 'x'*1501}).status_code == 422


@pytest.mark.parametrize('identity_status,homes,consent,expected', [(401,[],True,401),(200,[],True,403),(200,[{'id':HOME}],False,400),(200,[{'id':HOME}],True,200)])
def test_user_rls_and_explicit_question_consent(configured,monkeypatch,identity_status,homes,consent,expected):
    sent=[]
    def handler(request):
        sent.append(request)
        if request.url.path.endswith('/user'):
            return httpx.Response(identity_status,json={'id':'user'} if identity_status==200 else {})
        return httpx.Response(200,json=homes)
    original=httpx.AsyncClient
    monkeypatch.setattr(onboarding.httpx,'AsyncClient',lambda **kw: original(transport=httpx.MockTransport(handler),**kw))
    calls=[]
    async def execute(self,command):
        calls.append(command);return 'Respuesta real de prueba'
    monkeypatch.setattr(onboarding.NexxusIntelligence,'execute',execute)
    response=client.post('/api/domus/panel/ask',headers={'Authorization':'Bearer test-user-jwt'},json={'home_id':HOME,'text':'Hola','consent':consent})
    assert response.status_code==expected
    assert len(calls)==(1 if expected==200 else 0)
    for request in sent:
        assert request.headers['Authorization']=='Bearer test-user-jwt'
        assert request.headers['apikey']=='public-test'
    if expected==200:
        assert response.json()=={'answer':'Respuesta real de prueba'}
        assert vars(calls[0])=={'text':'Hola','panel':True}
        assert response.headers['Cache-Control']=='no-store'
