import importlib.util
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

spec = importlib.util.spec_from_file_location('mini_client', Path(__file__).parents[2] / 'clients/nexxus-mini/client.py')
client = importlib.util.module_from_spec(spec)
spec.loader.exec_module(client)


def test_transport_uses_stdin_tls_and_no_shell(monkeypatch):
    monkeypatch.setattr(client.os.path, 'isfile', lambda p: True)
    proc = Mock(returncode=0)
    proc.communicate.return_value = (b'{"reply":"Hola"}\n200', b'')
    popen = Mock(return_value=proc)
    monkeypatch.setattr(client.subprocess, 'Popen', popen)
    assert client.request({'server': 'https://example.test'}, 'private-token', []) == 'Hola'
    args = popen.call_args.args[0]
    assert 'private-token' not in ' '.join(args)
    assert '-q' == args[1]
    assert '--tlsv1.2' in args and '--cacert' in args
    assert '--insecure' not in args and '--location' not in args
    assert not popen.call_args.kwargs.get('shell')
    config = proc.communicate.call_args.args[0].decode()
    assert 'Authorization: Bearer private-token' in config
    assert '/api/mini-ai/chat' in config


@pytest.mark.parametrize('token', ['', 'a\r\nheader: inject', 'space token'])
def test_invalid_token_is_not_sent(token):
    with pytest.raises(ValueError):
        client.request({'server': 'https://example.test'}, token, [])


def test_missing_tls_bundle_fails_closed(monkeypatch):
    monkeypatch.setattr(client.os.path, 'isfile', lambda p: False)
    with pytest.raises(ValueError, match='TLS'):
        client.request({'server': 'https://example.test'}, 'token', [])


@pytest.mark.parametrize('url', ['http://example.test', 'https://user:pass@example.test',
                                 'https://example.test/path', 'https://example.test?token=x'])
def test_config_rejects_unsafe_origin(monkeypatch, tmp_path, url):
    (tmp_path / 'config.json').write_text(json.dumps({'server': url}))
    monkeypatch.setattr(client, 'ROOT', str(tmp_path))
    with pytest.raises(ValueError):
        client.config()
