"""Permissions, revisions, onboarding and additive migration regressions."""
import json, sqlite3, tempfile, unittest
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit,parse_qs
from fastapi.testclient import TestClient
from app import create_app,settings_from_env
from storage import Store
from test_app import FakeAI

class IntranetTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();cfg=settings_from_env();cfg['data_dir']=self.temp.name
        self.app=create_app(cfg,FakeAI());self.clients=[]
        self.admin,self.doctor,self.staff=[self.login(u) for u in ('admin','doctor','staff')]
    def tearDown(self):
        for c in self.clients:c.close()
        self.temp.cleanup()
    def login(self,uid):
        c=TestClient(self.app);self.clients.append(c)
        r=c.post('/api/login',json={'username':uid,'password':'ClinicDemo42!'})
        self.assertEqual(r.status_code,200,r.text);c.headers['X-CSRF-Token']=r.json()['csrf'];return c
    def add(self,client=None,**kw):
        body=dict(title='가상 자료',content='가상 안내 원문입니다.',approved=True);body.update(kw)
        r=(client or self.admin).post('/api/documents',json=body);self.assertEqual(r.status_code,200,r.text);return r.json()['id']
    def revise(self,did,**kw):
        d=self.admin.get('/api/documents/'+did).json();d['expected_version']=d['version'];d.update(kw)
        r=self.admin.put('/api/documents/'+did,json=d);self.assertEqual(r.status_code,200,r.text);return r.json()
    def test_listing_documents_has_constant_query_count_as_library_grows(self):
        from contextlib import contextmanager
        from unittest.mock import patch
        store=self.app.state.store;connect=store.connect;counts=[]
        class Counted:
            def __init__(self,c):self.c=c
            def execute(self,*args,**kwargs):counts.append(1);return self.c.execute(*args,**kwargs)
        @contextmanager
        def counted(**kwargs):
            with connect(**kwargs) as c:yield Counted(c)
        with patch.object(store,'connect',counted):
            response=self.admin.get('/api/documents');self.assertEqual(response.status_code,200);small=len(counts)
        for i in range(12):self.add(title='성능 확인 자료 '+str(i))
        counts.clear()
        with patch.object(store,'connect',counted):
            response=self.admin.get('/api/documents');self.assertEqual(response.status_code,200);large=len(counts)
        self.assertGreaterEqual(len(response.json()['documents']),12)
        self.assertEqual(small,large)

    def test_document_delete_checks_permissions_csrf_and_current_version(self):
        did=self.add();body={'expected_version':1}
        for client,status in [(self.staff,403),(TestClient(self.app),401)]:
            r=client.request('DELETE','/api/documents/'+did,json=body);self.assertEqual(r.status_code,status,r.text)
        csrf=self.admin.headers.pop('X-CSRF-Token')
        self.assertEqual(self.admin.request('DELETE','/api/documents/'+did,json=body).status_code,403)
        self.admin.headers['X-CSRF-Token']=csrf
        self.assertEqual(self.admin.request('DELETE','/api/documents/'+did,json={'expected_version':9}).status_code,409)
        self.assertEqual(self.admin.get('/api/documents/'+did).status_code,200)
        r=self.admin.request('DELETE','/api/documents/'+did,json=body);self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(self.admin.get('/api/documents/'+did).status_code,404)

    def test_delete_removes_versions_reads_search_and_onboarding_links(self):
        did=self.add();keep=self.add(title='유지할 자료')
        self.staff.get('/api/documents/'+did)
        course=self.admin.post('/api/admin/onboarding',json={'title':'삭제 연결 시험','document_ids':[did,keep]}).json()['id']
        with self.app.state.store.connect() as c:
            c.execute('INSERT INTO chunks(doc_id,version,text,vector,model,digest) VALUES(?,?,?,?,?,?)',(did,1,'fixture','[]','fixture','fixture'))
        r=self.admin.request('DELETE','/api/documents/'+did,json={'expected_version':1});self.assertEqual(r.status_code,200,r.text)
        with self.app.state.store.connect() as c:
            for table in ('documents','chunks','document_versions','document_reads','onboarding_steps','onboarding_progress'):
                key='id' if table=='documents' else 'doc_id'
                self.assertEqual(c.execute(f'SELECT COUNT(*) FROM {table} WHERE {key}=?',(did,)).fetchone()[0],0,table)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM onboarding_steps WHERE course_id=?',(course,)).fetchone()[0],1)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM audit WHERE action='delete_document' AND object_id=?",(did,)).fetchone()[0],1)
        self.assertEqual(self.admin.get('/api/documents/'+keep).status_code,200)

    def test_delete_removes_linked_youtube_job_and_source_metadata(self):
        did=self.add(kind='youtube_transcript',source_url='https://www.youtube.com/watch?v=DEMO1234567')
        with self.app.state.store.connect() as c:
            c.execute("INSERT INTO youtube_jobs(id,owner_id,owner_revision,video_id,url,title,mode,language,roles,status,stage,doc_id,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",('YT-DELETE','admin',1,'DEMO1234567','https://www.youtube.com/watch?v=DEMO1234567','fixture','auto','ko','["admin"]','succeeded','완료',did,0,0))
            c.execute('INSERT INTO youtube_sources VALUES(?,?,?,?,?,?,?,?)',(did,'DEMO1234567','whisper','ko',1,1,0,'[]'))
        r=self.admin.request('DELETE','/api/documents/'+did,json={'expected_version':1});self.assertEqual(r.status_code,200,r.text)
        with self.app.state.store.connect() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM youtube_jobs WHERE doc_id=?',(did,)).fetchone()[0],0)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM youtube_sources WHERE doc_id=?',(did,)).fetchone()[0],0)

    def test_archive_keeps_original_and_delete_is_available_to_admin_in_archive(self):
        did=self.add()
        self.admin.post(f'/api/documents/{did}/archive',json={'expected_version':1,'archived':True})
        self.assertEqual(self.staff.get('/api/documents/'+did).status_code,404)
        self.assertEqual(self.admin.get('/api/documents/'+did).status_code,200)
        self.assertEqual(self.admin.request('DELETE','/api/documents/'+did,json={'expected_version':2}).status_code,200)

    def test_youtube_import_approval_metadata_and_read_audit(self):
        r=self.doctor.post('/api/documents/import',files={'file':('example.srt','1\n00:00:01,000 --> 00:00:03,000\n가상 대본'.encode())})
        self.assertEqual(r.status_code,200,r.text);self.assertFalse(r.json()['persisted'])
        did=self.add(self.doctor,title='유튜브 대본',content=r.json()['content'],approved=False,kind='youtube_transcript',source_url='https://www.youtube.com/watch?v=DEMO1234567',source_author='가상 채널')
        self.assertEqual(self.staff.get('/api/documents/'+did).status_code,404)
        body={'expected_version':1,'approved':True}
        self.assertEqual(self.doctor.post(f'/api/documents/{did}/approval',json=body).status_code,403)
        self.assertEqual(self.admin.post(f'/api/documents/{did}/approval',json=body).status_code,200)
        docs=self.staff.get('/api/documents').json()['documents'];self.assertIn(did,[d['id'] for d in docs]);self.assertTrue(all('content' not in d for d in docs))
        self.assertIn('00:00:01',self.staff.get('/api/documents/'+did).json()['content'])
        log=self.admin.get('/api/admin/audit',params={'actor':'staff','action':'read_document'}).json()['entries'][0]
        self.assertEqual(log['object_id'],did);self.assertEqual(log['detail']['version'],2)
    def test_file_and_source_url_validation(self):
        for name,raw in [('file.exe',b'text'),('bad.txt',b'\xff\xff'),('null.md',b'\x00'),('large.txt',b'x'*1000001)]:
            self.assertEqual(self.admin.post('/api/documents/import',files={'file':(name,raw)}).status_code,400)
        self.assertEqual(self.staff.post('/api/documents/import',files={'file':('ok.txt',b'hello')}).status_code,403)
        for url in ('javascript:alert(1)','http://[','https://user:pass@example.com','https://other.example/video'):
            r=self.admin.post('/api/documents',json={'title':'x','content':'x','kind':'youtube_transcript','source_url':url});self.assertEqual(r.status_code,400,r.text)
    def test_revision_conflict_snapshot_and_restore(self):
        did=self.add(content='첫 원문');old=self.admin.get('/api/documents/'+did).json()
        self.revise(did,content='수정 원문')
        self.assertEqual(self.admin.put('/api/documents/'+did,json={**old,'expected_version':1}).status_code,409)
        self.assertEqual(self.staff.get(f'/api/documents/{did}/versions').status_code,403)
        versions=self.admin.get(f'/api/documents/{did}/versions').json()['versions'];self.assertEqual([v['version'] for v in versions],[2,1])
        r=self.admin.post(f'/api/documents/{did}/restore',json={'expected_version':2,'version':1});self.assertEqual(r.json()['version'],3)
        d=self.admin.get('/api/documents/'+did).json();self.assertEqual(d['content'],'첫 원문');self.assertFalse(d['approved'])
        self.assertEqual(self.staff.get('/api/documents/'+did).status_code,404)
    def test_archive_excludes_retrieval_and_unarchive_requires_approval(self):
        did=self.add();self.admin.post('/api/documents/reindex')
        self.assertGreater(self.admin.get('/api/documents/'+did).json()['indexed_chunks'],0)
        r=self.admin.post(f'/api/documents/{did}/archive',json={'expected_version':1,'archived':True});self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(self.staff.get('/api/documents/'+did).status_code,404)
        self.assertEqual(self.admin.get('/api/documents/'+did).json()['indexed_chunks'],0)
        sources=self.staff.post('/api/wiki/search',json={'question':'자료'}).json()['sources'];self.assertNotIn(did,[s['doc_id'] for s in sources])
        self.assertEqual(self.admin.post(f'/api/documents/{did}/archive',json={'expected_version':2,'archived':False}).status_code,200)
        self.assertFalse(self.admin.get('/api/documents/'+did).json()['approved'])
    def test_permission_is_checked_for_detail_download_and_approval(self):
        did=self.add(roles=['admin'],content='PRIVATE-ORIGINAL')
        for suffix in ('','/download'):
            self.assertEqual(self.staff.get('/api/documents/'+did+suffix).status_code,404)
            self.assertEqual(self.doctor.get('/api/documents/'+did+suffix).status_code,404)
        self.assertEqual(self.admin.get('/api/documents/'+did+'/download').status_code,200)
        self.assertEqual(self.doctor.post('/api/documents',json={'title':'x','content':'x','approved':True}).status_code,403)
    def test_onboarding_read_required_per_user_and_version(self):
        path='/api/onboarding/ONB-INTRO/steps/D02'
        self.assertEqual(self.staff.post(path,json={'version':1,'completed':True}).status_code,409)
        self.staff.get('/api/documents/D02');self.assertEqual(self.staff.post(path,json={'version':1,'completed':True}).status_code,200)
        self.assertEqual(self.staff.get('/api/onboarding').json()['courses'][0]['completed'],1)
        self.assertEqual(self.doctor.get('/api/onboarding').json()['courses'][0]['completed'],0)
        self.revise('D02',content='최신 원문',approved=True)
        d=next(d for d in self.staff.get('/api/onboarding').json()['courses'][0]['steps'] if d['id']=='D02')
        self.assertTrue(d['needs_review']);self.assertFalse(d['read_current']);self.assertFalse(d['completed'])
        self.assertEqual(self.staff.post(path,json={'version':1,'completed':True}).status_code,409)
        self.staff.get('/api/documents/D02');self.assertEqual(self.staff.post(path,json={'version':2,'completed':True}).status_code,200)
    def test_hidden_onboarding_step_has_no_document_leak(self):
        self.revise('D02',roles=['admin'],content='HIDDEN-CONTENT',approved=True)
        c=self.staff.get('/api/onboarding').json()['courses'][0]
        self.assertEqual(c['blocked'],1);self.assertEqual(c['total'],3);self.assertNotIn('D02',[d['id'] for d in c['steps']]);self.assertNotIn('HIDDEN-CONTENT',json.dumps(c))
        self.assertEqual(self.staff.post('/api/onboarding/ONB-INTRO/steps/D02',json={'version':2,'completed':True}).status_code,404)
    def test_course_role_scope_revision_and_deactivation(self):
        body={'title':'CS 교육','roles':['staff'],'document_ids':['D01']}
        self.assertEqual(self.staff.post('/api/admin/onboarding',json=body).status_code,403)
        r=self.admin.post('/api/admin/onboarding',json=body);self.assertEqual(r.status_code,200,r.text);cid=r.json()['id']
        self.assertNotIn(cid,[c['id'] for c in self.doctor.get('/api/onboarding').json()['courses']])
        self.assertIn(cid,[c['id'] for c in self.staff.get('/api/onboarding').json()['courses']])
        self.assertEqual(self.admin.put('/api/admin/onboarding/'+cid,json={**body,'expected_revision':9}).status_code,409)
        self.assertEqual(self.admin.patch('/api/admin/onboarding/'+cid+'/active',json={'active':False,'expected_revision':1}).status_code,200)
        self.assertNotIn(cid,[c['id'] for c in self.staff.get('/api/onboarding').json()['courses']])
        self.assertEqual(self.admin.patch('/api/admin/onboarding/'+cid+'/active',json={'active':True,'expected_revision':2}).status_code,200)
        self.assertEqual(self.admin.post('/api/admin/onboarding',json={**body,'document_ids':['D06']}).status_code,400)
    def test_role_change_and_deactivation_revoke_sessions_and_capture(self):
        r=self.doctor.post('/api/capture-sessions',json={'patient_id':'DEMO-101','base_url':'http://testserver'});token=parse_qs(urlsplit(r.json()['url']).query)['token'][0]
        r=self.admin.patch('/api/admin/users/doctor',json={'name':'가상 직원','role':'staff','active':True,'expected_revision':1});self.assertEqual(r.status_code,200,r.text)
        self.assertTrue(r.json()['sessions_revoked']);self.assertEqual(self.doctor.get('/api/me').status_code,401)
        self.assertEqual(self.admin.get('/api/capture/'+token).status_code,410)
        new=self.login('doctor');self.assertEqual(new.get('/api/patients').status_code,403)
        self.assertEqual(self.admin.patch('/api/admin/users/doctor',json={'name':'가상 직원','role':'staff','active':False,'expected_revision':2}).status_code,200)
        self.assertEqual(new.get('/api/me').status_code,401)
        self.assertEqual(new.post('/api/login',json={'username':'doctor','password':'ClinicDemo42!'}).status_code,401)
    def test_account_create_duplicate_and_admin_self_protection(self):
        body={'id':'new_staff','name':'새 가상 직원','role':'staff'}
        self.assertEqual(self.staff.post('/api/admin/users',json=body).status_code,403)
        self.assertEqual(self.admin.post('/api/admin/users',json=body).status_code,200)
        self.assertEqual(self.admin.post('/api/admin/users',json=body).status_code,409)
        self.assertEqual(self.login('new_staff').get('/api/me').json()['user']['role'],'staff')
        self.assertEqual(self.admin.patch('/api/admin/users/admin',json={'name':'관리자','role':'staff','active':False,'expected_revision':1}).status_code,400)
        users=self.admin.get('/api/users').text;self.assertNotIn('password_hash',users);self.assertNotIn('salt',users)
    def test_audit_filters_permissions_and_no_original_content(self):
        did=self.add(content='PRIVATE-AUDIT-MARKER');self.staff.get('/api/documents/'+did)
        self.assertEqual(self.staff.get('/api/admin/audit').status_code,403)
        self.assertEqual(self.staff.get('/api/admin/onboarding').status_code,403)
        self.assertEqual(self.doctor.get('/api/admin/audit').status_code,403)
        r=self.admin.get('/api/admin/audit',params={'actor':'staff','action':'read_document','limit':1})
        self.assertEqual(len(r.json()['entries']),1);self.assertEqual(r.json()['entries'][0]['object_id'],did)
        self.assertNotIn('PRIVATE-AUDIT-MARKER',r.text)
        denied=self.admin.get('/api/admin/audit',params={'result':'denied'}).json()['entries'];self.assertTrue(any(e['actor']=='staff' and e['action']=='access_denied' for e in denied))
    def test_audit_filters_use_korean_midnight(self):
        a=datetime(2026,11,14,14,59,tzinfo=timezone.utc).timestamp();b=datetime(2026,11,14,15,0,tzinfo=timezone.utc).timestamp()
        with self.app.state.store.connect() as c:c.executemany('INSERT INTO audit(actor,action,object_id,created) VALUES(?,?,?,?)',[('staff','date_check','before',a),('staff','date_check','after',b)])
        r=self.admin.get('/api/admin/audit',params={'action':'date_check','start':'2026-11-15','end':'2026-11-15'})
        self.assertEqual([x['object_id'] for x in r.json()['entries']],['after'])

class MigrationTests(unittest.TestCase):
    def test_existing_documents_balances_and_steps_survive_migration(self):
        with tempfile.TemporaryDirectory() as td:
            c=sqlite3.connect(Path(td)/'clinicworkspace.sqlite3')
            c.execute('CREATE TABLE documents(id TEXT PRIMARY KEY,title TEXT,content TEXT,version INTEGER,approved INTEGER,roles TEXT,kind TEXT,source_url TEXT,updated REAL)')
            c.execute('INSERT INTO documents VALUES(?,?,?,?,?,?,?,?,?)',('D01','기존 제목','기존 원문',7,1,json.dumps(['admin','staff']),'manual','',1));c.commit();c.close()
            store=Store(td)
            with store.connect() as c:c.execute("UPDATE users SET balance=6 WHERE id='staff'")
            store=Store(td)
            with store.connect() as c:
                d=c.execute("SELECT * FROM documents WHERE id='D01'").fetchone();self.assertEqual(d['content'],'기존 원문');self.assertEqual(d['version'],7)
                self.assertEqual(c.execute("SELECT balance FROM users WHERE id='staff'").fetchone()[0],6)
                self.assertEqual(c.execute("SELECT COUNT(*) FROM document_versions WHERE doc_id='D01'").fetchone()[0],1)
                self.assertEqual(c.execute("SELECT COUNT(*) FROM onboarding_steps WHERE course_id='ONB-INTRO'").fetchone()[0],3)

if __name__=='__main__':unittest.main(verbosity=2)
