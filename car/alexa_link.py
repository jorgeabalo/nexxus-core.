"""Dedicated, revocable Alexa grants. Never export Supabase session tokens."""
import hashlib
import secrets
import time
from urllib.parse import urlsplit


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


class AlexaLink:
    def __init__(self, store, client_id, client_secret, redirects, allowed):
        if not client_id or len(client_secret) < 32 or not redirects:
            raise ValueError('Alexa OAuth configuration incomplete')
        for uri in redirects:
            url = urlsplit(uri)
            if url.scheme != 'https' or not url.netloc or url.fragment:
                raise ValueError('Exact HTTPS redirect URIs required')
        self.store, self.client_id, self.secret = store, client_id, client_secret
        self.redirects, self.allowed = set(redirects), allowed
        with store.transaction():
            store.db.executescript('''
            CREATE TABLE IF NOT EXISTS alexa_codes(hash TEXT PRIMARY KEY,tenant TEXT,redirect TEXT,expires REAL);
            CREATE TABLE IF NOT EXISTS alexa_grants(id TEXT PRIMARY KEY,tenant TEXT,expires REAL);
            CREATE TABLE IF NOT EXISTS alexa_tokens(hash TEXT PRIMARY KEY,grant_id TEXT,kind TEXT,expires REAL);
            ''')

    def validate(self, client_id, redirect, scope, response_type='code'):
        if client_id != self.client_id or redirect not in self.redirects or scope != 'car' or response_type != 'code':
            raise ValueError('Invalid authorization request')

    def authorize(self, tenant, redirect):
        if tenant not in self.allowed or redirect not in self.redirects:
            raise ValueError('Not authorized')
        code = secrets.token_urlsafe(32)
        with self.store.transaction():
            self.store.db.execute('DELETE FROM alexa_codes WHERE expires<?', (time.time(),))
            self.store.db.execute('INSERT INTO alexa_codes VALUES(?,?,?,?)', (digest(code), tenant, redirect, time.time()+120))
        return code

    def exchange(self, client_id, secret, grant_type, value, redirect=None):
        if not secrets.compare_digest(client_id, self.client_id) or not secrets.compare_digest(secret, self.secret):
            raise ValueError('Invalid client')
        now = time.time()
        with self.store.transaction():
            db = self.store.db
            db.execute('DELETE FROM alexa_tokens WHERE expires<?', (now,))
            db.execute('DELETE FROM alexa_grants WHERE expires<?', (now,))
            db.execute('DELETE FROM alexa_codes WHERE expires<?', (now,))
            if grant_type == 'authorization_code':
                row = db.execute('SELECT tenant,redirect FROM alexa_codes WHERE hash=?', (digest(value),)).fetchone()
                if not row or row['redirect'] != redirect or row['tenant'] not in self.allowed:
                    raise ValueError('Invalid grant')
                db.execute('DELETE FROM alexa_codes WHERE hash=?', (digest(value),))
                gid = secrets.token_urlsafe(24)
                expiry = now+30*86400
                db.execute('INSERT INTO alexa_grants VALUES(?,?,?)', (gid,row['tenant'],expiry))
            elif grant_type == 'refresh_token':
                row = db.execute('SELECT g.id,g.tenant,g.expires FROM alexa_tokens t JOIN alexa_grants g ON g.id=t.grant_id WHERE t.hash=? AND t.kind=?', (digest(value),'refresh')).fetchone()
                if not row or row['tenant'] not in self.allowed:
                    raise ValueError('Invalid grant')
                gid, expiry = row['id'], row['expires']
                db.execute('DELETE FROM alexa_tokens WHERE grant_id=?', (gid,))
            else:
                raise ValueError('Unsupported grant')
            access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            db.execute('INSERT INTO alexa_tokens VALUES(?,?,?,?)', (digest(access),gid,'access',min(now+3600,expiry)))
            db.execute('INSERT INTO alexa_tokens VALUES(?,?,?,?)', (digest(refresh),gid,'refresh',expiry))
            return {'access_token':access,'refresh_token':refresh,'token_type':'Bearer','expires_in':min(3600,int(expiry-now)),'scope':'car'}

    def verify(self, token):
        if not isinstance(token,str) or len(token)>512:
            return None
        with self.store.lock:
            row = self.store.db.execute('SELECT g.tenant FROM alexa_tokens t JOIN alexa_grants g ON g.id=t.grant_id WHERE t.hash=? AND t.kind=? AND t.expires>? AND g.expires>?', (digest(token),'access',time.time(),time.time())).fetchone()
            return row['tenant'] if row and row['tenant'] in self.allowed else None

    def revoke(self, tenant):
        with self.store.transaction():
            self.store.db.execute('DELETE FROM alexa_tokens WHERE grant_id IN (SELECT id FROM alexa_grants WHERE tenant=?)', (tenant,))
            self.store.db.execute('DELETE FROM alexa_grants WHERE tenant=?', (tenant,))
            self.store.db.execute('DELETE FROM alexa_codes WHERE tenant=?', (tenant,))
            self.store.db.execute('DELETE FROM alexa_pending WHERE tenant=?', (tenant,))
