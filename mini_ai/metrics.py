"""Authenticated read-only counters; Domus always uses user JWT + anon key/RLS."""
import asyncio
import os
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, HTTPException, Request
from starlette.concurrency import run_in_threadpool
from mini_ai.api import database

router = APIRouter(prefix='/api/mini-ai')
COUNTS = {
    'homes': ('domus_homes', {}),
    'rooms': ('domus_rooms', {}),
    'devices': ('domus_devices', {}),
    'marked_connected': ('domus_devices', {'connection_status': 'eq.connected'}),
    'pending': ('domus_devices', {'connection_status': 'eq.pending'}),
    'attention': ('domus_devices', {'connection_status': 'eq.attention'}),
    'assistants': ('domus_channels', {}),
}


async def counts(url, key, token):
    async with httpx.AsyncClient(timeout=4.0, follow_redirects=False,
            headers={'apikey': key, 'Authorization': 'Bearer ' + token, 'Prefer': 'count=exact'}) as client:
        async def one(table, filters):
            try:
                r = await client.head(url + '/rest/v1/' + table, params={'select': 'id', **filters})
                if r.status_code not in (200, 206):
                    return None
                count = r.headers.get('content-range', '').rsplit('/', 1)[-1]
                return int(count) if count.isdigit() else None
            except Exception:
                return None
        values = await asyncio.gather(*(one(table, filters) for table, filters in COUNTS.values()))
    return dict(zip(COUNTS, values))


def usage(db, user_id):
    try:
        rows = db.select('mini_ai_quota', {'user_id': 'eq.' + user_id,
                        'select': 'day_start,day_count', 'limit': '1'})
        if rows is None:
            return None
        today = datetime.now(timezone.utc).date().isoformat()
        attempts = int(rows[0]['day_count']) if rows and rows[0]['day_start'] == today else 0
        return {'attempts_today': attempts, 'daily_limit': 100, 'remaining': max(0, 100-attempts)}
    except Exception:
        return None


@router.get('/metrics')
async def metrics(request: Request):
    if os.getenv('MINI_AI_ENABLED', '').lower() != 'true':
        raise HTTPException(503, 'mini_ai_disabled')
    auth = request.headers.get('authorization', '')
    if not auth.startswith('Bearer ') or not 1 <= len(auth[7:]) <= 8192:
        raise HTTPException(401, 'invalid_session')
    token = auth[7:]
    db = database()
    try:
        user = await run_in_threadpool(db.user_from_jwt, token)
    except Exception:
        raise HTTPException(503, 'auth_unavailable') from None
    if not user:
        raise HTTPException(401, 'invalid_session')
    allowed = {x.strip() for x in os.getenv('MINI_AI_USER_IDS', '').split(',') if x.strip()}
    if user['id'] not in allowed:
        raise HTTPException(403, 'mini_ai_access_denied')
    if not db.url.startswith('https://') or not db.anon_key:
        raise HTTPException(503, 'domus_unconfigured')
    domus, quota = await asyncio.gather(counts(db.url, db.anon_key, token),
                                       run_in_threadpool(usage, db, user['id']))
    return {'domus': domus, 'nexxus': quota,
            'sampled_at': datetime.now(timezone.utc).isoformat(),
            'note': 'Registered configuration, not live device telemetry.'}
