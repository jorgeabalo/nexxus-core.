"""One-off authorized deployment check. No endpoint, passwords, email or token logs."""
import asyncio
import os
import sys
import httpx
from mini_ai.api import database
from mini_ai.app import app


async def main():
    db = database()
    token = None
    stage = 'configuration'
    try:
        email = os.environ['MINI_AI_EMAIL']
        stage = 'test_session'
        # Generates an account link without sending email. Consume it immediately.
        status, link = await asyncio.to_thread(db.auth, 'POST', '/admin/generate_link',
                                               json={'type': 'magiclink', 'email': email})
        if status != 200 or not isinstance(link, dict) or not link.get('hashed_token'):
            raise ValueError()
        status, session = await asyncio.to_thread(db.auth, 'POST', '/verify', anon=True,
            json={'type': 'magiclink', 'token_hash': link['hashed_token']})
        if status != 200 or not session.get('access_token'):
            raise ValueError()
        token = session['access_token']
        stage = 'chat_quota_provider'
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            result = await client.post('/api/mini-ai/chat', headers={'Authorization': 'Bearer ' + token},
                json={'messages': [{'role': 'user', 'content': 'Responde solo: Nexxus listo.'}]})
        if result.status_code != 200 or not result.json().get('reply'):
            raise ValueError()
        print('MINI_AI_SMOKE_PASS: Supabase session, authorization, SQL quota and Anthropic response verified.')
        return 0
    except Exception:
        print('MINI_AI_SMOKE_FAIL: ' + stage)
        return 1
    finally:
        if token:
            # End only the temporary test session. Preserve Manager and other devices.
            try:
                await asyncio.to_thread(db.auth, 'POST', '/logout', bearer=token, params={'scope': 'local'})
            except Exception:
                pass


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
