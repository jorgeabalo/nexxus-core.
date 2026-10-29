import pytest
from car.store import Store
from car.alexa import CarAlexa
from car.alexa_link import AlexaLink

URI='https://pitangui.amazon.com/api/skill/link/test'
def setup():
    store=Store(':memory:')
    link=AlexaLink(store,'client','x'*32,[URI],{'a','b'})
    CarAlexa(store,link.verify)
    return store,link

def test_code_single_use_exact_redirect_client_and_revocation():
    store,link=setup()
    for uri in (URI+'x','http://example.org'):
        with pytest.raises(ValueError):link.validate('client',uri,'car')
    code=link.authorize('a',URI)
    with pytest.raises(ValueError):link.exchange('client','wrong','authorization_code',code,URI)
    with pytest.raises(ValueError):link.exchange('client','x'*32,'authorization_code',code,URI+'x')
    data=link.exchange('client','x'*32,'authorization_code',code,URI)
    assert link.verify(data['access_token'])=='a'
    with pytest.raises(ValueError):link.exchange('client','x'*32,'authorization_code',code,URI)
    assert store.db.execute('SELECT hash FROM alexa_tokens').fetchone()[0] not in data.values()
    link.revoke('b');assert link.verify(data['access_token'])=='a'
    link.revoke('a');assert link.verify(data['access_token']) is None
    with pytest.raises(ValueError):link.exchange('client','x'*32,'refresh_token',data['refresh_token'])

def test_refresh_rotation_expiry_and_allowlist():
    store,link=setup();code=link.authorize('a',URI)
    first=link.exchange('client','x'*32,'authorization_code',code,URI)
    second=link.exchange('client','x'*32,'refresh_token',first['refresh_token'])
    assert link.verify(first['access_token']) is None
    assert link.verify(second['access_token'])=='a'
    with pytest.raises(ValueError):link.exchange('client','x'*32,'refresh_token',first['refresh_token'])
    link.allowed.remove('a');assert link.verify(second['access_token']) is None
    with pytest.raises(ValueError):link.exchange('client','x'*32,'refresh_token',second['refresh_token'])
    link.allowed.add('a')
    with store.transaction():store.db.execute('UPDATE alexa_grants SET expires=0')
    assert link.verify(second['access_token']) is None
