import sqlite3, json, threading, uuid
from contextlib import contextmanager

class Store:
    """Local persistence. Every resource is keyed by tenant and vehicle."""
    def __init__(self, path):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self.db.executescript('''
        PRAGMA foreign_keys=ON;
        CREATE TABLE IF NOT EXISTS profiles(tenant TEXT PRIMARY KEY, data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS vehicles(tenant TEXT, id TEXT, data TEXT NOT NULL, PRIMARY KEY(tenant,id));
        CREATE TABLE IF NOT EXISTS records(tenant TEXT, vehicle TEXT, id TEXT, kind TEXT, data TEXT NOT NULL,
          PRIMARY KEY(tenant,id), FOREIGN KEY(tenant,vehicle) REFERENCES vehicles(tenant,id));
        ''')
        self.db.commit()

    @contextmanager
    def transaction(self):
        with self.lock:
            try:
                self.db.execute('BEGIN IMMEDIATE')
                yield
                self.db.commit()
            except Exception:
                self.db.rollback(); raise

    def profile(self, tenant):
        r = self.db.execute('SELECT data FROM profiles WHERE tenant=?', (tenant,)).fetchone()
        return json.loads(r[0]) if r else None

    def save_profile(self, tenant, data):
        self.db.execute('INSERT OR REPLACE INTO profiles VALUES(?,?)', (tenant,json.dumps(data)))

    def vehicles(self, tenant):
        return [json.loads(r[0]) for r in self.db.execute('SELECT data FROM vehicles WHERE tenant=?', (tenant,))]

    def vehicle(self, tenant, vid):
        r = self.db.execute('SELECT data FROM vehicles WHERE tenant=? AND id=?', (tenant,vid)).fetchone()
        return json.loads(r[0]) if r else None

    def save_vehicle(self, tenant, data):
        self.db.execute('INSERT OR REPLACE INTO vehicles VALUES(?,?,?)', (tenant,data['id'],json.dumps(data)))

    def records(self, tenant, vid, kind):
        return [json.loads(r[0]) for r in self.db.execute('SELECT data FROM records WHERE tenant=? AND vehicle=? AND kind=? ORDER BY rowid DESC', (tenant,vid,kind))]

    def record(self, tenant, vid, rid, kind):
        r = self.db.execute('SELECT data FROM records WHERE tenant=? AND vehicle=? AND id=? AND kind=?', (tenant,vid,rid,kind)).fetchone()
        return json.loads(r[0]) if r else None

    def save_record(self, tenant, vid, kind, data):
        self.db.execute('INSERT OR REPLACE INTO records VALUES(?,?,?,?,?)', (tenant,vid,data['id'],kind,json.dumps(data)))

    def seed(self, tenant):
        with self.transaction():
            if self.profile(tenant): return
            self.save_profile(tenant, {'name': 'Jorge' if tenant=='demo-a' else 'Cliente B', 'plan': 'basic'})
            self.save_vehicle(tenant, {'id': str(uuid.uuid4()), 'make':'Kia','model':'Sportage','year':2021,
                'odometer':48250,'mpg':26,'fuel_price':3.45,'oil_due':50000})
