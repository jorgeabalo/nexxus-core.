import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from mini_ai import smoke


@pytest.mark.parametrize('chat_status,expected', [(200, 0), (502, 1)])
def test_deployment_check_uses_temporary_session_without_leaking(monkeypatch, capsys, chat_status, expected):
    monkeypatch.setenv('MINI_AI_EMAIL', 'owner@example.test')
    db = SimpleNamespace(auth=Mock(side_effect=[
        (200, {'hashed_token': 'private-link'}),
        (200, {'access_token': 'private-session'}),
        (204, None),
    ]))
    monkeypatch.setattr(smoke, 'database', lambda: db)
    app = FastAPI()
    @app.post('/api/mini-ai/chat', status_code=chat_status)
    def reply():
        return {'reply': 'Nexxus listo.'}
    monkeypatch.setattr(smoke, 'app', app)
    assert asyncio.run(smoke.main()) == expected
    output = capsys.readouterr().out
    assert 'private-link' not in output and 'private-session' not in output
    assert db.auth.call_args.args == ('POST', '/logout')
    assert db.auth.call_args.kwargs['params'] == {'scope': 'local'}
    assert db.auth.call_args_list[0].args == ('POST', '/admin/generate_link')
