"""Durable multipart assembly and one-time, lossless local demo-data import."""
import base64
import hashlib
import json
import os
import time
import uuid

import httpx
from fastapi import Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response


def install_cloud_routes(app, store, current, manager, cfg):
    if not getattr(store, "cloud_schema_ready", False):
        with store.connect() as c:
            c.executescript('''
            CREATE TABLE IF NOT EXISTS cloud_uploads(id TEXT PRIMARY KEY,owner_id TEXT NOT NULL,path TEXT NOT NULL,
              content_type TEXT NOT NULL,size INTEGER NOT NULL,received INTEGER NOT NULL DEFAULT 0,
              expires DOUBLE PRECISION NOT NULL,status TEXT NOT NULL DEFAULT 'uploading',result TEXT);
            CREATE TABLE IF NOT EXISTS cloud_parts(upload_id TEXT REFERENCES cloud_uploads(id) ON DELETE CASCADE,
              offset_bytes INTEGER NOT NULL,blob_path TEXT NOT NULL,size INTEGER NOT NULL,PRIMARY KEY(upload_id,offset_bytes));
            CREATE TABLE IF NOT EXISTS cloud_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            ''')
            c.execute('''CREATE OR REPLACE FUNCTION clinicworkspace_mark_changed() RETURNS trigger AS $$
              BEGIN
                INSERT INTO cloud_meta(key,value) VALUES('business_dirty','1')
                ON CONFLICT(key) DO UPDATE SET value='1';
                RETURN NULL;
              END;
              $$ LANGUAGE plpgsql''')
            for table in ('users','patients','photos','notes','leaves','documents','notices','onboarding_courses',
                          'onboarding_steps','onboarding_progress','document_reads','notice_reads','crm_modes','geo_profile','geo_sources','geo_drafts','geo_observations'):
                c.execute('DROP TRIGGER IF EXISTS cloud_changed ON '+table)
                c.execute('CREATE TRIGGER cloud_changed AFTER INSERT OR UPDATE OR DELETE ON '+table+
                          ' FOR EACH ROW EXECUTE FUNCTION clinicworkspace_mark_changed()')
            from storage import CLOUD_SCHEMA_VERSION
            c.execute("INSERT INTO clinicworkspace_schema VALUES(1,?) ON CONFLICT(id) DO UPDATE SET version=excluded.version",(CLOUD_SCHEMA_VERSION,))

    @app.post('/__upload/init')
    async def create_upload(request: Request, user=Depends(current)):
        with store.connect() as c:
            expired=[r[0] for r in c.execute('SELECT id FROM cloud_uploads WHERE expires<? LIMIT 4',(time.time()-600,))]
        for expired_id in expired:
            if await clean_parts(expired_id):
                with store.connect() as c:c.execute('DELETE FROM cloud_uploads WHERE id=? AND expires<?',(expired_id,time.time()-600))
        spec=await request.json();path=spec.get('path','');size=spec.get('size',0)
        import re
        if spec.get('method')!='POST' or not re.fullmatch(r'/api/(patients/[A-Za-z0-9-]+/photos|recording/[A-Za-z0-9_-]+|capture/[A-Za-z0-9_-]+)',path):
            raise HTTPException(400,'사진 또는 녹음 전송 경로를 확인하세요.')
        if user['role'] not in {'admin','doctor'}:raise HTTPException(403,'진료 자료 전송 권한이 필요합니다.')
        # Recording multipart overhead is allowed; photo limits remain unchanged.
        recording=path.startswith('/api/recording/')
        limit=(152 if recording else 27)*1024*1024
        if not isinstance(size,int) or isinstance(size,bool) or not 0<size<=limit:raise HTTPException(413,'파일 크기를 확인하세요.')
        uid=uuid.uuid4().hex
        with store.connect() as c:
            if c.execute("SELECT COUNT(*) FROM cloud_uploads WHERE owner_id=? AND expires>? AND status='uploading'",(user['id'],time.time())).fetchone()[0]>=4:
                raise HTTPException(429,'진행 중인 전송을 먼저 완료하세요.')
            if cfg.get('sandbox'):
                stored=c.execute("SELECT COALESCE(SUM(size),0) FROM asset_ledger WHERE area!='parts'").fetchone()[0]
                reserved=c.execute("SELECT COALESCE(SUM(size),0) FROM cloud_uploads WHERE status IN ('uploading','committing') AND expires>?",(time.time(),)).fetchone()[0]
                if stored+reserved+size>100*1024*1024:raise HTTPException(413,'체험 공간의 파일 용량은 100MB까지입니다.')
            c.execute('INSERT INTO cloud_uploads(id,owner_id,path,content_type,size,expires) VALUES(?,?,?,?,?,?)',
                      (uid,user['id'],path,str(spec.get('content_type',''))[:200],size,time.time()+(3600 if recording else 900)))
        return {'id':uid}

    def own(c,uid,user):
        row=c.execute('SELECT * FROM cloud_uploads WHERE id=? AND owner_id=? AND expires>?',(uid,user['id'],time.time())).fetchone()
        if not row:raise HTTPException(404,'전송 연결이 만료되었습니다.')
        return row

    @app.put('/__upload/{uid}')
    async def upload_part(uid:str,request:Request,offset:int=0,user=Depends(current)):
        from vercel.blob import put_blob
        raw=await request.body()
        if not raw or len(raw)>1024*1024:raise HTTPException(413,'전송 조각 크기를 확인하세요.')
        with store.connect() as c:
            row=own(c,uid,user)
            if row['status']!='uploading' or offset!=row['received'] or offset+len(raw)>row['size']:
                raise HTTPException(409,'전송 순서를 확인하세요.')
        prefix=f'clinicworkspace/workspaces/{store.schema}/upload-parts/' if store.schema else 'clinicworkspace/upload-parts/'
        name=f'{prefix}{uid}/{offset}-{uuid.uuid4().hex}'
        with store.connect() as c:
            c.execute('INSERT INTO asset_ledger VALUES(?,?,?,?,?)',('parts',name,len(raw),'pending',time.time()))
        try:
            await put_blob(name,raw,access='private')
            with store.connect() as c:
                row=own(c,uid,user)
                if row['status']!='uploading' or offset!=row['received']:
                    raise HTTPException(409,'다른 창에서 전송이 진행됐습니다.')
                c.execute('INSERT INTO cloud_parts VALUES(?,?,?,?)',(uid,offset,name,len(raw)))
                c.execute('UPDATE cloud_uploads SET received=received+? WHERE id=?',(len(raw),uid))
                c.execute("UPDATE asset_ledger SET state='stored' WHERE area='parts' AND name=?",(name,))
        except Exception:
            await delete_part(name)
            raise
        return {'received':offset+len(raw)}

    async def delete_part(path):
        from vercel.blob import delete_blob
        try:
            await delete_blob(path)
        except Exception:
            return False
        with store.connect() as c:
            c.execute('DELETE FROM cloud_parts WHERE blob_path=?',(path,))
            c.execute("DELETE FROM asset_ledger WHERE area='parts' AND name=?",(path,))
        return True

    async def clean_parts(uid):
        prefix=f'clinicworkspace/workspaces/{store.schema}/upload-parts/' if store.schema else 'clinicworkspace/upload-parts/'
        with store.connect() as c:
            paths={r[0] for r in c.execute('SELECT blob_path FROM cloud_parts WHERE upload_id=?',(uid,))}
            paths.update(r[0] for r in c.execute("SELECT name FROM asset_ledger WHERE area='parts' AND name LIKE ?",(prefix+uid+'/%',)))
        results=[await delete_part(path) for path in paths]
        return all(results)

    async def cleanup_uploads():
        with store.connect(shared=True) as c:
            ids=[r[0] for r in c.execute('SELECT id FROM cloud_uploads WHERE expires<?',(time.time()-600,))]
        for uid in ids:
            if await clean_parts(uid):
                with store.connect() as c:c.execute('DELETE FROM cloud_uploads WHERE id=?',(uid,))
        # Reconcile pre-fix parts that never acquired a database row.
        from vercel.blob import query_blobs
        prefix=f'clinicworkspace/workspaces/{store.schema}/upload-parts/' if store.schema else 'clinicworkspace/upload-parts/'
        async for blob in query_blobs(prefix=prefix,page_size=100):
            if blob.uploaded_at.timestamp()<time.time()-86400:
                uid=blob.pathname[len(prefix):].split('/')[0]
                with store.connect(shared=True) as c:
                    active=c.execute('SELECT 1 FROM cloud_uploads WHERE id=? AND expires>?',(uid,time.time()-600)).fetchone()
                if not active:await delete_part(blob.pathname)
    app.state.cleanup_uploads=cleanup_uploads

    @app.post('/__upload/{uid}/commit')
    async def commit_upload(uid:str,request:Request,user=Depends(current)):
        from vercel.blob import get_blob
        with store.connect() as c:
            row=dict(own(c,uid,user))
            if row['status']=='done':
                result=json.loads(row['result']);return Response(base64.b64decode(result['body']),status_code=result['status'],headers=result['headers'])
            if row['status']!='uploading' or row['received']!=row['size']:raise HTTPException(409,'전송 완료를 기다려주세요.')
            c.execute("UPDATE cloud_uploads SET status='committing' WHERE id=?",(uid,))
            parts=[dict(r) for r in c.execute('SELECT * FROM cloud_parts WHERE upload_id=? ORDER BY offset_bytes',(uid,))]
        data=bytearray()
        for part in parts:
            data.extend((await get_blob(part['blob_path'],access='private')).body)
        if len(data)!=row['size']:raise HTTPException(409,'전송 내용이 일치하지 않습니다. 다시 전송하세요.')
        headers={'cookie':request.headers.get('cookie',''),'x-csrf-token':request.headers.get('x-csrf-token',''),
                 'content-type':row['content_type'],'host':request.headers.get('host','localhost'),
                 'x-clinicworkspace-backend':os.environ.get('CLOUD_BACKEND_KEY',''),
                 'x-clinicworkspace-route':row['path']}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url=str(request.base_url)) as client:
            result=await client.post(row['path'],content=bytes(data),headers=headers)
        saved={'body':base64.b64encode(result.content).decode(),'status':result.status_code,
               'headers':{'content-type':result.headers.get('content-type','application/json')}}
        with store.connect() as c:c.execute("UPDATE cloud_uploads SET status='done',result=? WHERE id=?",(json.dumps(saved),uid))
        await clean_parts(uid)
        return Response(result.content,status_code=result.status_code,headers=saved['headers'])

    @app.delete('/__upload/{uid}')
    async def abort(uid:str,user=Depends(current)):
        with store.connect() as c:
            row=own(c,uid,user)
            if row['status']=='committing':raise HTTPException(409,'저장이 진행 중입니다.')
            c.execute("UPDATE cloud_uploads SET status='aborted' WHERE id=?",(uid,))
        await clean_parts(uid)
        return {'ok':True}

    @app.get('/api/cloud/storage')
    async def storage_status(user=Depends(manager)):
        with store.connect() as c:
            row=c.execute("SELECT value FROM cloud_meta WHERE key='local_import'").fetchone()
        return {'cloud':True,'database':'postgres','files':'private_blob','local_import':json.loads(row[0]) if row else None}

    @app.post('/api/cloud/migrate-local')
    async def migrate_local(user=Depends(manager)):
        from cloud_ai import CloudAI
        ai=app.state.ai
        if not isinstance(ai,CloudAI):raise HTTPException(409,'클라우드 서버에서 실행하세요.')
        # Do not silently replace any work performed after cloud activation.
        def pristine(c):
            return (c.execute("SELECT COUNT(*) FROM cloud_meta WHERE key IN ('local_import','business_dirty')").fetchone()[0]==0
                    and c.execute("SELECT COUNT(*) FROM documents WHERE id NOT IN ('D01','D02','D03','D04','D05','D06') OR version>1").fetchone()[0]==0
                    and all(c.execute('SELECT COUNT(*) FROM '+t).fetchone()[0]==0 for t in ('notices','notes','leaves','onboarding_progress'))
                    and c.execute("SELECT COUNT(*) FROM photos WHERE filename NOT LIKE 'baseline-%'").fetchone()[0]==0)
        with store.connect() as c:
            if not pristine(c):raise HTTPException(409,'클라우드에 새 업무 기록이 있거나 이미 이전했습니다. 기존 자료를 덮어쓰지 않았습니다.')
        print('Local import: creating snapshot',flush=True)
        manifest=await ai.request('export_manifest')
        tables={}
        for table in manifest['tables']:
            print('Local import table:',table,flush=True)
            # The RPC returns only the fixed allowlist from the PC installation.
            if not table.replace('_','').isalnum():raise HTTPException(400,'자료 형식을 확인하세요.')
            rows=[]
            while True:
                batch=(await ai.request('export_table',{'table':table,'offset':len(rows),'snapshot':manifest['snapshot']}))['rows']
                rows.extend(batch)
                if len(batch)<50:break
            tables[table]=rows
        source_cleanup_deferred=False
        try:
            closed=await ai.request('export_close',{'snapshot':manifest['snapshot']})
            source_cleanup_deferred=bool(closed.get('deferred'))
        except Exception:
            # The snapshot is already fully read. Windows may still hold its handle.
            # Temporary-file cleanup must not prevent an otherwise complete import.
            source_cleanup_deferred=True
        restored_baselines=[]
        for photo in tables.get('photos',[]):
            name=photo['filename'];data=bytearray()
            print('Local import photo:',photo['id'],'synthetic:',name in {'baseline-DEMO-101.png','baseline-DEMO-103.png'},flush=True)
            try:
                while True:
                    part=await ai.request('export_file',{'area':'photos','name':name,'offset':len(data)})
                    raw=base64.b64decode(part['data']);data.extend(raw)
                    if len(raw)<1024*1024:break
            except Exception as error:
                # Only our deterministic, fictional diagram has a known replacement.
                # All other source photos must remain readable before any DB import.
                if name in {'baseline-DEMO-101.png','baseline-DEMO-103.png'}:
                    from fastapi.concurrency import run_in_threadpool
                    baseline=await run_in_threadpool(store.load_file,'photos',name)
                    data=bytearray(await run_in_threadpool(baseline.read_bytes))
                    restored_baselines.append(photo['id'])
                else:
                    raise HTTPException(422,f'PC의 사진 기록 #{photo["id"]}에 연결된 파일을 읽지 못해 이전을 중단했습니다. 원본 자료 폴더를 확인하세요.') from error
            from fastapi.concurrency import run_in_threadpool
            await run_in_threadpool(store.save_file,'photos',name,bytes(data))
        # Parent-before-child import; all database changes commit together or roll back.
        order=['users','patients','documents','onboarding_courses','photos','transcripts','notes','leaves','chunks','audit',
               'document_versions','document_reads','notices','notice_reads','youtube_jobs','youtube_sources',
               'recording_links','recording_jobs','onboarding_steps','onboarding_progress','crm_modes','crm_chart_entries',
               'geo_profile','geo_sources','geo_drafts','geo_observations']
        with store.connect() as c:
            if not pristine(c):raise HTTPException(409,'이전 중에 클라우드 자료가 변경되었습니다. 덮어쓰지 않았습니다.')
            c.execute('TRUNCATE '+','.join(order)+' RESTART IDENTITY CASCADE')
            for table in order:
                columns={r['name'] for r in c.execute('PRAGMA table_info('+table+')')}
                for row in tables.get(table,[]):
                    if table in {'recording_jobs','youtube_jobs'} and row.get('status') in {'running','queued'}:
                        row={**row,'status':'failed','stage':'서버 이전으로 중단 · 다시 접수하세요.'}
                    names=[name for name in row if name in columns]
                    if names:
                        c.execute('INSERT INTO '+table+'('+','.join(names)+') VALUES('+','.join('?' for _ in names)+')',tuple(row[n] for n in names))
            for table in ('photos','transcripts','notes','leaves','chunks','audit'):
                c.execute("SELECT setval(pg_get_serial_sequence(?, 'id'),COALESCE((SELECT MAX(id) FROM "+table+"),0)+1,false)",(table,))
            summary={'at':time.time(),'counts':{table:len(rows) for table,rows in tables.items()},
                     'restored_synthetic_baselines':restored_baselines,
                     'source_cleanup_deferred':source_cleanup_deferred}
            c.execute("INSERT INTO cloud_meta VALUES('local_import',?)",(json.dumps(summary),))
        return {'ok':True,**summary,'login_again':True}
