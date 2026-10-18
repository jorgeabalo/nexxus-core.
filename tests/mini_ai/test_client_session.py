"""Session rotation and local speech, without real credentials or network."""
import base64
import importlib.util
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

spec = importlib.util.spec_from_file_location(
    'mini_session_client', Path(__file__).parents[2] / 'clients/nexxus-mini/client.py')
client = importlib.util.module_from_spec(spec)
spec.loader.exec_module(client)

SETTINGS = {'server': 'https://app.example', 'supabase_url': 'https://auth.example',
            'supabase_public_key': 'sb_publishable_test'}


def session_response(access='access-one', refresh='refresh-one'):
    return b'200', {'access_token': access, 'refresh_token': refresh, 'expires_in': 3600}


def test_login_refresh_rotates_and_logout_erases_session(monkeypatch):
    http = Mock(side_effect=[session_response(), session_response('access-two', 'refresh-two')])
    monkeypatch.setattr(client, 'http_json', http)
    clock = Mock(return_value=1000)
    monkeypatch.setattr(client.time, 'time', clock)
    session = client.Session(SETTINGS)
    assert session.login(' jorge@example.test ', 'private-password') == 'access-one'
    assert http.call_args.args[2] == {'email': 'jorge@example.test', 'password': 'private-password'}
    assert session.token() == 'access-one'
    assert http.call_count == 1
    clock.return_value = 4550
    assert session.token() == 'access-two'
    assert http.call_args.args[0].endswith('?grant_type=refresh_token')
    assert http.call_args.args[2] == {'refresh_token': 'refresh-one'}
    assert session.refresh == 'refresh-two'
    session.clear()
    assert not session.access and not session.refresh and session.until == 0
    with pytest.raises(ValueError, match='Inicia sesion'):
        session.token()


def test_refresh_rejection_clears_credentials_and_hides_server_error(monkeypatch):
    http = Mock(side_effect=[session_response(), (b'400', {'error': 'PRIVATE_SERVER_ERROR'})])
    monkeypatch.setattr(client, 'http_json', http)
    session = client.Session(SETTINGS)
    session.login('jorge@example.test', 'password')
    session.until = 0
    with pytest.raises(ValueError) as error:
        session.token()
    assert 'PRIVATE' not in str(error.value)
    assert not session.access and not session.refresh


@pytest.mark.parametrize('data', [{}, {'access_token': 'injected\nheader'},
    {'access_token': 'token', 'refresh_token': 'refresh', 'expires_in': -10}])
def test_invalid_session_response_is_rejected(monkeypatch, data):
    monkeypatch.setattr(client, 'http_json', Mock(return_value=(b'200', data)))
    session = client.Session(SETTINGS)
    with pytest.raises(ValueError):
        session.login('a@example.test', 'password')
    assert not session.access


@pytest.mark.parametrize('body', [b'[]', b'not-json', b'"string"'])
def test_transport_rejects_nonobject_response(monkeypatch, body):
    monkeypatch.setattr(client.os.path, 'isfile', lambda _: True)
    proc = Mock(returncode=0)
    proc.communicate.return_value = (body + b'\n200', b'')
    monkeypatch.setattr(client.subprocess, 'Popen', Mock(return_value=proc))
    with pytest.raises(ValueError, match='inesperada'):
        client.http_json('https://auth.example/auth/v1/token', [], {})


def test_secret_key_cannot_be_distributed():
    segment = base64.urlsafe_b64encode(json.dumps({'role': 'service_role'}).encode()).decode().rstrip('=')
    with pytest.raises(ValueError, match='PUBLICA'):
        client.public_key('header.' + segment + '.signature')
    with pytest.raises(ValueError):
        client.public_key('sb_secret_PRIVATE')
    segment = base64.urlsafe_b64encode(json.dumps({'role': 'anon'}).encode()).decode().rstrip('=')
    assert client.public_key('header.' + segment + '.signature')


def test_auth_secrets_travel_in_stdin_not_commandline(monkeypatch):
    monkeypatch.setattr(client.os.path, 'isfile', lambda _: True)
    proc = Mock(returncode=0)
    proc.communicate.return_value = (json.dumps(session_response()[1]).encode() + b'\n200', b'')
    popen = Mock(return_value=proc)
    monkeypatch.setattr(client.subprocess, 'Popen', popen)
    client.Session(SETTINGS).login('jorge@example.test', 'PRIVATE_PASSWORD')
    args = popen.call_args.args[0]
    assert 'PRIVATE_PASSWORD' not in ' '.join(args)
    assert 'jorge@example.test' not in ' '.join(args)
    assert 'PRIVATE_PASSWORD' in proc.communicate.call_args.args[0].decode()
    assert not popen.call_args.kwargs.get('shell')


def test_speech_is_local_stdin_and_interrupts_previous_answer(monkeypatch):
    first, second = Mock(), Mock()
    first.poll.return_value = None
    popen = Mock(side_effect=[first, second])
    monkeypatch.setattr(client.subprocess, 'Popen', popen)
    voice = client.Speech()
    voice.speak('Respuesta [[rate 999]] $() `whoami`')
    assert popen.call_args.args[0] == ['/usr/bin/say']
    assert b'[[' not in first.stdin.write.call_args.args[0]
    voice.speak('Segunda respuesta')
    first.terminate.assert_called_once()
    first.wait.assert_called_once()
    voice.stop()
    assert voice.process is None
