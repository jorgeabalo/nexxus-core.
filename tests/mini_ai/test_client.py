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


@pytest.fixture
def local_tls(tmp_path):
    import http.server
    import shutil
    import ssl
    import subprocess
    import threading
    transport = tmp_path / 'transport'
    transport.mkdir()
    (transport / 'curl').symlink_to('/usr/bin/curl')
    for name in ('trusted', 'other'):
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
                        '-keyout', str(tmp_path / (name + '.key')),
                        '-out', str(tmp_path / (name + '.pem')), '-days', '1',
                        '-subj', '/CN=127.0.0.1', '-addext', 'subjectAltName=IP:127.0.0.1'],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    requests = []
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append(self.headers.get('Authorization'))
            body = b'{"reply":"TLS verificado"}'
            self.rfile.read(int(self.headers['Content-Length']))
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *args):
            pass
    server = http.server.HTTPServer(('127.0.0.1', 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.maximum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(str(tmp_path / 'trusted.pem'), str(tmp_path / 'trusted.key'))
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield tmp_path, server.server_address[1], requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


@pytest.mark.parametrize('mode', ['trusted', 'wrong-ca', 'wrong-hostname'])
def test_real_tls_verifies_ca_and_hostname(local_tls, monkeypatch, mode):
    import shutil
    root, port, requests = local_tls
    monkeypatch.setattr(client, 'ROOT', str(root))
    shutil.copy(root / ('other.pem' if mode == 'wrong-ca' else 'trusted.pem'),
                root / 'transport/cacert.pem')
    hostname = 'localhost' if mode == 'wrong-hostname' else '127.0.0.1'
    settings = {'server': 'https://%s:%d' % (hostname, port)}
    if mode == 'trusted':
        assert client.request(settings, 'test-session', []) == 'TLS verificado'
        assert requests == ['Bearer test-session']
    else:
        with pytest.raises(ValueError, match='TLS'):
            client.request(settings, 'test-session', [])
        assert not requests, 'Authorization must not cross an invalid TLS connection'


def test_package_manifest_detects_changed_files(tmp_path):
    import hashlib
    spec = importlib.util.spec_from_file_location('mini_diagnose',
        Path(__file__).parents[2] / 'clients/nexxus-mini/diagnose.py')
    diagnose = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(diagnose)
    (tmp_path / 'client.py').write_bytes(b'original')
    manifest = {'files': [{'path': 'client.py', 'sha256': hashlib.sha256(b'original').hexdigest()}]}
    (tmp_path / 'MANIFEST.json').write_text(json.dumps(manifest))
    assert diagnose.verify_manifest(str(tmp_path)) == 1
    (tmp_path / 'client.py').write_bytes(b'changed')
    with pytest.raises(ValueError, match='modificado'):
        diagnose.verify_manifest(str(tmp_path))
    manifest['files'][0]['path'] = '../outside'
    (tmp_path / 'MANIFEST.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='Ruta'):
        diagnose.verify_manifest(str(tmp_path))


def test_password_transport_never_uses_command_line(monkeypatch):
    monkeypatch.setattr(client.os.path, 'isfile', lambda p: True)
    proc = Mock(returncode=0)
    proc.communicate.return_value = (b'{"access_token":"session","expires_in":3600}\n200', b'')
    popen = Mock(return_value=proc)
    monkeypatch.setattr(client.subprocess, 'Popen', popen)
    assert client.login({'server': 'https://example.test'}, 'owner@example.test', 'private-password') == 'session'
    args = popen.call_args.args[0]
    assert 'private-password' not in ' '.join(args)
    payload = proc.communicate.call_args.args[0].decode()
    assert 'private-password' in payload and '/api/mini-ai/session' in payload
    assert 'Authorization:' not in payload


def test_metrics_transport_is_authenticated_get(monkeypatch):
    monkeypatch.setattr(client.os.path, 'isfile', lambda p: True)
    proc = Mock(returncode=0)
    proc.communicate.return_value = (b'{"domus":{}}\n200', b'')
    monkeypatch.setattr(client.subprocess, 'Popen', Mock(return_value=proc))
    assert client.transport({'server': 'https://example.test'}, '/api/mini-ai/metrics', token='session') == {'domus': {}}
    config = proc.communicate.call_args.args[0].decode()
    assert 'data-binary' not in config
    assert 'Authorization: Bearer session' in config
    assert '/api/mini-ai/metrics' in config
