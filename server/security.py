"""Shared, transactional security controls for SQLite and PostgreSQL."""
import hashlib
import json
import secrets
import time
from contextlib import nullcontext

from fastapi import HTTPException


def initialize_security(store):
    with store.connect() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS security_buckets (key TEXT PRIMARY KEY, count INTEGER NOT NULL, expires REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS security_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS asset_ledger (area TEXT NOT NULL, name TEXT NOT NULL, size INTEGER NOT NULL,
          state TEXT NOT NULL, updated REAL NOT NULL, PRIMARY KEY(area,name));
        CREATE INDEX IF NOT EXISTS security_bucket_expiry ON security_buckets(expires);
        ''')


def client_key(request):
    # Only the authenticated private gateway is allowed to set this header.
    # Local deployments ignore all forwarded headers supplied by browsers.
    import os
    expected = os.environ.get('CLOUD_BACKEND_KEY', '')
    supplied = request.headers.get('x-clinicworkspace-backend', '')
    if expected and secrets.compare_digest(expected, supplied):
        return request.headers.get('x-clinicworkspace-client', 'unknown')[:128]
    return request.client.host if request.client else 'local'


def consume_limits(store, limits, connection=None, now=None):
    """Atomically check all buckets, then reserve requests before expensive work."""
    now = time.time() if now is None else now
    with nullcontext(connection) if connection is not None else store.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        pending = []
        for subject, limit, seconds in limits:
            key = hashlib.sha256(subject.encode()).hexdigest()
            row = c.execute('SELECT count,expires FROM security_buckets WHERE key=?', (key,)).fetchone()
            count, expires = (row['count'], row['expires']) if row and row['expires'] > now else (0, now+seconds)
            if count >= limit:
                raise HTTPException(429, '요청이 많습니다. 잠시 후 다시 시도하세요.',
                                    headers={'Retry-After': str(max(1, int(expires-now)+1))})
            pending.append((key, count+1, expires))
        for values in pending:
            c.execute('INSERT INTO security_buckets VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET count=excluded.count,expires=excluded.expires', values)


def audit_event(store, actor, action, object_id='', result='success', detail=None, connection=None):
    now = time.time()
    with nullcontext(connection) if connection is not None else store.connect() as c:
        # Aggregate repeated denials: never log bearer tokens, request bodies or IPs.
        if action in {'access_denied', 'login_failed'}:
            route = (detail or {}).get('route', '')
            key = 'audit:'+hashlib.sha256(f'{actor}|{action}|{route}|{(detail or {}).get("status", "")}'.encode()).hexdigest()
            row = c.execute('SELECT count,expires FROM security_buckets WHERE key=?', (key,)).fetchone()
            if row and row['expires'] > now:
                c.execute('UPDATE security_buckets SET count=count+1 WHERE key=?', (key,))
                previous = c.execute('SELECT id FROM audit WHERE actor=? AND action=? AND object_id=? AND created>? ORDER BY id DESC LIMIT 1',
                                     (actor, action, route, now-300)).fetchone()
                if previous:
                    c.execute('UPDATE audit SET detail=? WHERE id=?',
                              (json.dumps({**(detail or {}), 'repeated': row['count']+1,
                                           'aggregation_window_seconds': 300}), previous[0]))
                return
            c.execute('INSERT INTO security_buckets VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET count=excluded.count,expires=excluded.expires', (key, 1, now+300))
            object_id = route
            detail = {**(detail or {}), 'repeated': 1, 'aggregation_window_seconds': 300}
        c.execute('INSERT INTO audit(actor,action,object_id,created,result,detail) VALUES(?,?,?,?,?,?)',
                  (actor, action, str(object_id)[:200], now, result, json.dumps(detail or {}, ensure_ascii=False)))
        last = c.execute("SELECT value FROM security_meta WHERE key='audit-maintenance'").fetchone()
        if not last or float(last[0]) < now-3600:
            c.execute('DELETE FROM audit WHERE created<?', (now-90*86400,))
            c.execute('DELETE FROM audit WHERE id IN (SELECT id FROM audit ORDER BY id DESC LIMIT 100000000 OFFSET 50000)')
            c.execute('DELETE FROM security_buckets WHERE expires<?', (now,))
            c.execute("INSERT INTO security_meta VALUES('audit-maintenance',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(now),))


def harden_owner(store, password):
    """One-time revocation/rotation; repeated cold starts never reset user data."""
    from storage import password_hash
    if len(password) < 24:
        raise RuntimeError('The private administrator password must be at least 24 characters')
    fingerprint = hashlib.sha256(password.encode()).hexdigest()
    with store.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        marker = c.execute("SELECT value FROM security_meta WHERE key='owner-credentials-v1'").fetchone()
        if marker and marker[0] == fingerprint:
            return
        for row in c.execute('SELECT id,salt,password_hash FROM users').fetchall():
            exposed = any(secrets.compare_digest(row['password_hash'], password_hash(p, row['salt']))
                          for p in ('ClinicDemo42!', 'Demo1234!'))
            if row['id'] == 'admin' or exposed:
                salt = secrets.token_hex(16)
                replacement = password if row['id'] == 'admin' else secrets.token_urlsafe(32)
                c.execute('UPDATE users SET salt=?,password_hash=?,revision=revision+1 WHERE id=?',
                          (salt, password_hash(replacement, salt), row['id']))
        c.execute('DELETE FROM sessions')
        c.execute('DELETE FROM capture_tokens')
        c.execute('UPDATE recording_links SET revoked=1')
        c.execute("INSERT INTO security_meta VALUES('owner-credentials-v1',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (fingerprint,))
        store.audit('system', 'security_credentials_rotated', connection=c)
