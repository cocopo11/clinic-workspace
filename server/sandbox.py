"""Expiring visitor workspaces, isolated from the private owner's data.

A random HttpOnly bearer cookie selects a validated PostgreSQL schema and Blob
prefix. A shared catalog bounds creation/requests and serializes lifecycle work.
"""
import asyncio
import re
import secrets
import time
import uuid
from collections import OrderedDict
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from security import client_key, consume_limits
from storage import token_hash

COOKIE = 'cw_space'
TTL = 24*3600
MAX_ACTIVE = 10


class WorkspaceRouter:
    def __init__(self, owner, factory, settings):
        self.owner, self.factory, self.settings = owner, factory, settings
        self.store = owner.state.store
        self.apps = OrderedDict()
        self.lock = asyncio.Lock()
        with self.store.connect() as c:
            c.execute('CREATE TABLE IF NOT EXISTS demo_workspaces (id TEXT PRIMARY KEY, token_hash TEXT UNIQUE NOT NULL, expires REAL NOT NULL, state TEXT NOT NULL)')

    def lookup(self, token):
        if not isinstance(token,str) or not token or not re.fullmatch(r'[A-Za-z0-9_-]{43}', token):
            return None
        with self.store.connect(shared=True) as c:
            row = c.execute("SELECT id,expires FROM demo_workspaces WHERE token_hash=? AND expires>? AND state='active'",
                            (token_hash(token), time.time())).fetchone()
        return dict(row) if row else None

    async def workspace(self, record):
        name = record['id']
        async with self.lock:
            if name not in self.apps:
                cfg = {**self.settings, 'schema': name, 'sandbox': True,
                       'data_dir': str(Path(self.settings['data_dir'])/name)}
                cfg.pop('owner_password', None)
                self.apps[name] = await run_in_threadpool(self.factory, cfg)
                while len(self.apps) > MAX_ACTIVE:
                    self.apps.popitem(last=False)
            self.apps.move_to_end(name)
            return self.apps[name]

    async def cleanup(self):
        # Expiration never drops data while a previously accepted request can run.
        with self.store.connect() as c:
            rows = c.execute('SELECT id FROM demo_workspaces WHERE expires<? LIMIT 10', (time.time()-600,)).fetchall()
        for row in rows:
            name = row['id']
            if not re.fullmatch(r'demo_[0-9a-f]{32}', name):
                continue
            from vercel.blob import query_blobs, delete_blob
            try:
                async for blob in query_blobs(prefix=f'clinicworkspace/workspaces/{name}/', page_size=100):
                    await delete_blob(blob.pathname)
                with self.store.connect() as c:
                    c.execute('DROP SCHEMA IF EXISTS '+name+' CASCADE')
                    c.execute('DELETE FROM demo_workspaces WHERE id=?', (name,))
                self.apps.pop(name, None)
                import shutil
                shutil.rmtree(Path(self.settings['data_dir'])/name, ignore_errors=True)
            except Exception:
                # Keep catalog and namespace for a later retry if deletion fails.
                continue

    async def create(self, request):
        consume_limits(self.store, [('space-ip:'+client_key(request), 3, 3600), ('space-global', 20, 86400)])
        await self.cleanup()
        token = secrets.token_urlsafe(32)
        name = 'demo_'+uuid.uuid4().hex
        expires = time.time()+TTL
        with self.store.connect() as c:
            if c.execute('SELECT COUNT(*) FROM demo_workspaces').fetchone()[0] >= MAX_ACTIVE:
                raise HTTPException(429, '체험 공간이 모두 사용 중입니다. 나중에 다시 방문해 주세요.', headers={'Retry-After':'3600'})
            c.execute('CREATE SCHEMA '+name)
            c.execute('INSERT INTO demo_workspaces VALUES(?,?,?,?)', (name, token_hash(token), expires, 'active'))
        try:
            await self.workspace({'id':name, 'expires':expires})
        except Exception:
            with self.store.connect() as c:
                c.execute('UPDATE demo_workspaces SET expires=?,state=? WHERE id=?', (0, 'failed', name))
            raise
        response = JSONResponse({'ok':True, 'expires':expires})
        self.set_cookie(response, token)
        return response

    @staticmethod
    def set_cookie(response, token):
        response.set_cookie(COOKIE, token, httponly=True, secure=True, samesite='strict', max_age=TTL, path='/')
        response.delete_cookie('gm_session', path='/', secure=True, httponly=True, samesite='strict')

    async def dispatch(self, request):
        path = request.url.path
        if path == '/__maintenance' and request.method == 'GET':
            import os
            secret = os.environ.get('CRON_SECRET', '')
            if not secret or not secrets.compare_digest(request.headers.get('authorization',''), 'Bearer '+secret):
                raise HTTPException(403, '인증이 필요합니다.')
            await self.cleanup()
            await self.owner.state.cleanup_uploads()
            count = await run_in_threadpool(self.store.cloud_files.cleanup)
            return JSONResponse({'ok':True, 'removed_orphans':count})
        token = request.cookies.get(COOKIE)
        record = await run_in_threadpool(self.lookup, token)
        if path.startswith('/api/demo/'):
            if request.method == 'POST':
                origin = request.headers.get('origin', '')
                if urlsplit(origin).netloc != request.headers.get('host', ''):
                    raise HTTPException(403, '이 웹사이트에서 요청하세요.')
            if path == '/api/demo/info' and request.method == 'GET':
                return JSONResponse({'enabled':True, 'active':bool(record), 'expires':record['expires'] if record else None,
                                     'storage_mb':100, 'hours':24})
            if path == '/api/demo/start' and request.method == 'POST':
                if record:
                    return JSONResponse({'ok':True, 'expires':record['expires']})
                return await self.create(request)
            if path == '/api/demo/join' and request.method == 'POST':
                consume_limits(self.store, [('join:'+client_key(request), 20, 60)])
                data = await request.json()
                candidate = data.get('code', '')
                target = await run_in_threadpool(self.lookup, candidate)
                if not target:
                    raise HTTPException(404, '체험 공간 연결이 만료되었거나 올바르지 않습니다.')
                response = JSONResponse({'ok':True, 'expires':target['expires']})
                self.set_cookie(response, candidate)
                return response
            if path == '/api/demo/leave' and request.method == 'POST':
                response = JSONResponse({'ok':True})
                response.delete_cookie(COOKIE, path='/', secure=True, httponly=True, samesite='strict')
                response.delete_cookie('gm_session', path='/', secure=True, httponly=True, samesite='strict')
                return response
            if path == '/api/demo/link' and request.method == 'POST' and record:
                app = await self.workspace(record)
                user = await run_in_threadpool(app.state.store.session, request.cookies.get('gm_session'))
                if not user or not secrets.compare_digest(request.headers.get('x-csrf-token',''), user['csrf']):
                    raise HTTPException(403, '로그인한 체험 공간에서 연결하세요.')
                return JSONResponse({'code':token, 'expires':record['expires']})
            raise HTTPException(404, '기능을 찾지 못했습니다.')
        if token and not record and (path.startswith('/api/') or path.startswith('/__upload')):
            raise HTTPException(401, '체험 공간이 만료되었습니다. 새 체험 공간을 시작하세요.')
        if record:
            if path.startswith('/api/cloud/'):
                raise HTTPException(403, '체험 공간에서는 운영 자료 이전 기능을 사용할 수 없습니다.')
            consume_limits(self.store, [('requests:'+record['id'], 300, 60)])
            if request.method not in {'GET', 'HEAD', 'OPTIONS'}:
                consume_limits(self.store, [('writes:'+record['id'], 300, 86400)])
                if path in {'/api/wiki/ask', '/api/wiki/search', '/api/documents/reindex', '/api/transcribe'} or any(x in path for x in ('/geo/generate','/geo/articles','/youtube','/recording/')):
                    consume_limits(self.store, [('ai:'+record['id'], 20, 3600), ('ai-global', 60, 3600)])
            return await self.workspace(record)
        return self.owner

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.owner(scope, receive, send)
        request = Request(scope, receive)
        try:
            target = await self.dispatch(request)
        except HTTPException as error:
            target = JSONResponse({'detail':error.detail}, error.status_code, headers=error.headers)
        if isinstance(target, JSONResponse):
            target.headers.update({'Cache-Control':'no-store', 'X-Content-Type-Options':'nosniff', 'Referrer-Policy':'no-referrer'})
        await target(scope, receive, send)
