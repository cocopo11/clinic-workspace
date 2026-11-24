"""Tablet pairing, audio queue and exactly-once mock CRM draft registration."""
import io
import tempfile
import threading
import time
import unittest
import wave
from urllib.parse import urlsplit,parse_qs
from unittest.mock import patch
from fastapi.testclient import TestClient
from app import create_app,settings_from_env
from ai_services import AIUnavailable
from test_app import FakeAI
from recording import RecordingJobs
from storage import token_hash


def wav():
    b=io.BytesIO()
    with wave.open(b,'wb') as w:w.setnchannels(1);w.setsampwidth(2);w.setframerate(16000);w.writeframes(b'\x00\x00'*16000)
    return b.getvalue()


class RecordingAI(FakeAI):
    def __init__(self):super().__init__();self.entered=threading.Event();self.release=threading.Event();self.failure=False;self.empty=False
    def transcribe(self,path,language):
        self.entered.set()
        if not self.release.wait(3):raise RuntimeError('test timed out')
        if self.failure:raise AIUnavailable('test model unavailable')
        return {'text':'' if self.empty else '가상 경과 녹음입니다. 약명과 수치를 확인합니다.',
                'segments':[{'start':0,'end':1,'text':'가상 경과 녹음입니다.'}],'language':language}


class RecordingTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();cfg=settings_from_env();cfg['data_dir']=self.temp.name
        self.ai=RecordingAI();self.app=create_app(cfg,self.ai)
        self.admin=TestClient(self.app);self.admin.__enter__();self.clients=[self.admin]
        self.login(self.admin,'admin');self.doctor=self.client('doctor');self.staff=self.client('staff');self.phone=TestClient(self.app);self.clients.append(self.phone)
    def login(self,c,uid):
        r=c.post('/api/login',json={'username':uid,'password':'ClinicDemo42!'});self.assertEqual(r.status_code,200);c.headers['X-CSRF-Token']=r.json()['csrf']
    def client(self,uid):c=TestClient(self.app);self.clients.append(c);self.login(c,uid);return c
    def tearDown(self):
        self.ai.release.set()
        if self.ai.entered.is_set():
            deadline=time.monotonic()+3
            while time.monotonic()<deadline:
                with self.app.state.store.connect() as c:active=c.execute("SELECT COUNT(*) FROM recording_jobs WHERE status IN ('queued','running')").fetchone()[0]
                if not active:break
                time.sleep(.01)
        for c in self.clients[1:]:c.close()
        self.admin.__exit__(None,None,None);self.temp.cleanup()
    def pair(self,client=None,pid='DEMO-101'):
        r=(client or self.doctor).post('/api/recording-links',json={'patient_id':pid,'base_url':'https://testserver'})
        self.assertEqual(r.status_code,200,r.text);return parse_qs(urlsplit(r.json()['url']).query)['token'][0]
    def upload(self,token,data=None):return self.phone.post('/api/recording/'+token,files={'file':('ipad.m4a',data if data is not None else wav(),'audio/mp4')},data={'language':'ko'})
    def wait_job(self,token):
        deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            r=self.phone.get('/api/recording/'+token);self.assertEqual(r.status_code,200,r.text)
            j=r.json()['job']
            if j and j['status'] not in {'queued','running'}:return j
            time.sleep(.01)
        self.fail('unfinished job')
    def count(self,table):
        with self.app.state.store.connect() as c:return c.execute('SELECT COUNT(*) FROM '+table).fetchone()[0]

    def test_reset_clinical_clears_records_and_files_preserves_other_data(self):
        from app import make_sample
        token=self.pair();self.ai.release.set();self.upload(token);job=self.wait_job(token)
        photo=self.doctor.post('/api/patients/DEMO-101/photos',files={'file':('test.png',make_sample(),'image/png')}).json()['photo_id']
        capture=self.doctor.post('/api/capture-sessions',json={'patient_id':'DEMO-101','base_url':'https://testserver'}).json()['url']
        capture_token=parse_qs(urlsplit(capture).query)['token'][0]
        with self.app.state.store.connect() as c:
            kept={table:c.execute('SELECT COUNT(*) FROM '+table).fetchone()[0] for table in ('patients','users','sessions','documents','leaves')}
            filename=c.execute('SELECT filename FROM photos WHERE id=?',(photo,)).fetchone()[0]
        body={'confirm':'RESET_CLINICAL_DEMO'}
        self.assertEqual(self.doctor.post('/api/admin/reset-clinical',json=body).status_code,403)
        self.assertEqual(self.staff.post('/api/admin/reset-clinical',json=body).status_code,403)
        self.assertEqual(self.phone.post('/api/admin/reset-clinical',json=body).status_code,401)
        self.assertEqual(self.admin.post('/api/admin/reset-clinical',json={}).status_code,400)
        r=self.admin.post('/api/admin/reset-clinical',json=body)
        self.assertEqual(r.status_code,200,r.text)
        with self.app.state.store.connect() as c:
            for table in ('notes','transcripts','recording_jobs','recording_links','capture_tokens','crm_chart_entries','crm_modes'):
                self.assertEqual(c.execute('SELECT COUNT(*) FROM '+table).fetchone()[0],0,table)
            for table,count in kept.items():self.assertEqual(c.execute('SELECT COUNT(*) FROM '+table).fetchone()[0],count,table)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM photos').fetchone()[0],2)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM audit WHERE action='reset_clinical_demo'").fetchone()[0],1)
        self.assertFalse((self.app.state.store.directory/'photos'/filename).exists())
        self.assertEqual(self.phone.get('/api/recording/'+token).status_code,410)
        self.assertEqual(self.phone.get('/api/capture/'+capture_token).status_code,410)
        images=self.doctor.get('/api/patients/DEMO-101/photos').json()
        self.assertIsNone(images['today']);self.assertEqual(images['before']['visit_date'],'2026-10-24')
        self.assertEqual(self.admin.post('/api/admin/reset-clinical',json=body).status_code,200)
        self.assertEqual(self.doctor.post('/api/patients/DEMO-101/photos',files={'file':('again.png',make_sample(),'image/png')}).status_code,200)

    def test_reset_refuses_inflight_recording(self):
        token=self.pair();self.upload(token);self.assertTrue(self.ai.entered.wait(1))
        r=self.admin.post('/api/admin/reset-clinical',json={'confirm':'RESET_CLINICAL_DEMO'})
        self.assertEqual(r.status_code,409,r.text)
        self.ai.release.set();self.assertEqual(self.wait_job(token)['status'],'succeeded')

    def test_photo_history_lists_prior_visits_in_date_order_and_patient_scope(self):
        from app import make_sample
        ids=[]
        for date in ('2026-09-01','2026-10-01','2026-10-01'):
            photo=self.doctor.post('/api/patients/DEMO-101/photos',files={'file':('past.png',make_sample(),'image/png')}).json()['photo_id']
            with self.app.state.store.connect() as c:c.execute('UPDATE photos SET visit_date=? WHERE id=?',(date,photo))
            ids.append(photo)
        self.doctor.post('/api/patients/DEMO-101/photos',files={'file':('today.png',make_sample(),'image/png')}).raise_for_status()
        token=self.pair()
        pc=self.doctor.get('/api/patients/DEMO-101/photos').json()
        tablet=self.phone.get('/api/recording/'+token+'/photos').json()
        for data in (pc,tablet):
            self.assertEqual([r['visit_date'] for r in data['history']],['2026-10-24','2026-10-01','2026-10-01','2026-09-01'])
            self.assertEqual([r['id'] for r in data['history'][1:3]],list(reversed(ids[1:])))
            self.assertEqual(data['before']['id'],data['history'][0]['id'])
            self.assertNotIn(data['today']['id'],[r['id'] for r in data['history']])
        empty=self.doctor.get('/api/patients/DEMO-102/photos').json()
        self.assertEqual(empty['history'],[])
        self.assertIsNone(empty['before'])
        self.assertEqual(self.phone.get('/api/recording/'+token+'/photos/'+str(ids[0])).status_code,200)

    def test_manual_chart_registration(self):
        self.doctor.post('/api/patients/DEMO-101/crm-mode',json={'mode':'manual'}).raise_for_status()
        token=self.pair();self.ai.release.set();self.upload(token);job=self.wait_job(token)
        self.assertFalse(job['auto_reviewed']);self.assertFalse(job['reviewed']);self.assertEqual(job['stage'],'전사 완료 · 수동 등록 대기');self.assertEqual(self.doctor.get('/api/patients/DEMO-101/crm-chart').json()['entries'],[])
        self.doctor.post('/api/recording-jobs/'+job['id']+'/review',json={'text':job['note_text'],'reviewed':True,'expected_version':1}).raise_for_status()
        entries=self.doctor.get('/api/patients/DEMO-101/crm-chart').json()['entries']
        self.assertEqual(len(entries),1)
        self.assertEqual(entries[0]['note_id'],job['note_id'])

    def test_mobile_entry_and_independent_device_sessions(self):
        self.assertEqual(self.phone.get('/mobile').status_code,200)
        tablet=self.client('doctor')
        tablet_token=self.pair(tablet)
        capture=self.doctor.post('/api/capture-sessions',json={'patient_id':'DEMO-101','base_url':'https://testserver'})
        self.assertEqual(capture.status_code,200)
        self.doctor.post('/api/recording-links/revoke',json={})
        self.assertEqual(self.phone.get('/api/recording/'+tablet_token+'/photos').status_code,200)
        tablet.post('/api/logout',json={})
        self.assertEqual(self.phone.get('/api/recording/'+tablet_token+'/photos').status_code,410)

    def test_tablet_report_photo_scope_refresh_and_revocation(self):
        from app import make_sample
        token=self.pair()
        endpoint='/api/recording/'+token+'/photos'
        initial=self.phone.get(endpoint).json()
        self.assertIsNone(initial['today'])
        self.assertIsNotNone(initial['before'])
        self.assertEqual(self.phone.get(endpoint+'/'+str(initial['before']['id'])).status_code,200)
        self.doctor.post('/api/patients/DEMO-101/photos',files={'file':('test.png',make_sample(),'image/png')}).raise_for_status()
        updated=self.phone.get(endpoint).json()
        self.assertIsNotNone(updated['today'])
        self.assertEqual(self.phone.get(endpoint+'/'+str(updated['today']['id'])).status_code,200)
        other=self.doctor.get('/api/patients/DEMO-103/photos').json()['before']['id']
        self.assertEqual(self.phone.get(endpoint+'/'+str(other)).status_code,404)
        self.doctor.post('/api/recording-links/revoke',json={})
        self.assertEqual(self.phone.get(endpoint).status_code,410)
        self.assertEqual(self.phone.get(endpoint+'/'+str(initial['before']['id'])).status_code,410)

    def test_roles_csrf_link_shape_and_no_token_in_database(self):
        body={'patient_id':'DEMO-101','base_url':'https://testserver'}
        self.assertEqual(self.staff.post('/api/recording-links',json=body).status_code,403)
        self.assertEqual(self.phone.post('/api/recording-links',json=body).status_code,401)
        self.assertEqual(self.staff.get('/api/recording-jobs?patient_id=DEMO-101').status_code,403)
        for base in ('javascript:x','https://user:pass@host','http://[','https://host/path','https://host:bad','https://host/?token=x'):
            self.assertEqual(self.doctor.post('/api/recording-links',json={**body,'base_url':base}).status_code,400)
        token=self.pair();info=self.phone.get('/api/recording/'+token).json();self.assertEqual(info['patient_id'],'DEMO-101');self.assertFalse(info['crm_connected'])
        with self.app.state.store.connect() as c:
            row=c.execute('SELECT * FROM recording_links').fetchone();self.assertEqual(row['hash'],token_hash(token));self.assertNotIn(token,str(dict(row)))
        r=self.phone.get('/record');self.assertEqual(r.status_code,200);self.assertIn('no-store',r.headers['cache-control'])
        self.assertNotIn('type="file"',r.text)

    def test_stop_upload_automatically_registers_without_review_or_manual_save(self):
        token=self.pair();r=self.upload(token);self.assertEqual(r.status_code,202,r.text);jid=r.json()['job']['id']
        self.assertTrue(self.ai.entered.wait(1));self.assertEqual(self.count('notes'),0)
        self.ai.release.set();job=self.wait_job(token)
        self.assertEqual(job['status'],'succeeded',job);self.assertFalse(job['reviewed']);self.assertFalse(job['crm_connected'])
        self.assertEqual(job['registration_status'],'전송 성공');self.assertTrue(job['auto_reviewed']);self.assertIn('자동 검토 완료',job['stage']);self.assertIn('의료진 확인 미수행',job['note_text']);self.assertNotIn('검토 대기',job['stage']);self.assertEqual(self.count('crm_chart_entries'),1);self.assertIn('가상 경과',job['text']);self.assertEqual(self.count('notes'),1);self.assertEqual(self.count('transcripts'),1)
        self.assertEqual(list(self.app.state.recording_jobs.directory.iterdir()),[])
        with self.app.state.store.connect() as c:
            note=c.execute('SELECT * FROM notes').fetchone();self.assertEqual(note['patient_id'],'DEMO-101');self.assertEqual(note['source'],'tablet_recording');self.assertEqual(note['reviewed'],0)
            self.assertNotIn(token,str([dict(x) for x in c.execute('SELECT * FROM audit')]))
        notes=self.doctor.get('/api/notes').json()['notes'];self.assertEqual(notes[0]['id'],job['note_id'])
        self.assertEqual(self.doctor.get('/api/recording-jobs?patient_id=DEMO-101').json()['jobs'][0]['id'],jid)

    def test_lost_response_retry_returns_same_job_and_single_note(self):
        token=self.pair();first=self.upload(token).json()['job'];self.assertTrue(self.ai.entered.wait(1))
        retry=self.upload(token);self.assertTrue(retry.json()['duplicate']);self.assertEqual(retry.json()['job']['id'],first['id'])
        self.ai.release.set();self.wait_job(token)
        self.assertTrue(self.upload(token).json()['duplicate']);self.assertEqual(self.count('notes'),1);self.assertEqual(self.count('recording_jobs'),1)

    def test_invalid_audio_keeps_link_usable_and_removes_temporary_file(self):
        token=self.pair();r=self.upload(token,b'not audio');self.assertEqual(r.status_code,400,r.text)
        self.assertEqual(self.count('recording_jobs'),0);self.assertEqual(list(self.app.state.recording_jobs.directory.iterdir()),[])
        self.assertIsNone(self.phone.get('/api/recording/'+token).json()['job'])
        self.ai.release.set();self.assertEqual(self.upload(token).status_code,202);self.assertEqual(self.wait_job(token)['status'],'succeeded')

    def test_expiry_date_change_revoke_and_logout(self):
        token=self.pair()
        with self.app.state.store.connect() as c:c.execute('UPDATE recording_links SET expires=0 WHERE hash=?',(token_hash(token),))
        self.assertEqual(self.upload(token).status_code,410)
        token=self.pair()
        with self.app.state.store.connect() as c:c.execute("UPDATE recording_links SET visit_date='2000-01-01' WHERE hash=?",(token_hash(token),))
        self.assertEqual(self.phone.get('/api/recording/'+token).status_code,410)
        token=self.pair();self.doctor.post('/api/recording-links/revoke',json={});self.assertEqual(self.upload(token).status_code,410)
        token=self.pair();self.doctor.post('/api/logout',json={});self.assertEqual(self.upload(token).status_code,410)

    def test_audio_size_duration_and_pending_limit(self):
        token=self.pair()
        with patch('recording.LIMIT',1024):
            self.assertEqual(self.upload(token,b'x'*1025).status_code,413)
        data=io.BytesIO()
        with wave.open(data,'wb') as w:w.setnchannels(1);w.setsampwidth(2);w.setframerate(8000);w.writeframes(b'\x00\x00'*8000*1801)
        self.assertEqual(self.upload(token,data.getvalue()).status_code,413)
        self.assertEqual(self.count('recording_jobs'),0)
        self.assertEqual(self.upload(token).status_code,202);self.assertTrue(self.ai.entered.wait(1))
        for _ in range(2):self.assertEqual(self.upload(self.pair()).status_code,202)
        self.assertEqual(self.upload(self.pair()).status_code,429);self.assertEqual(self.count('recording_jobs'),3)
        self.ai.release.set()

    def test_other_doctor_cannot_list_or_review_owner_recording(self):
        r=self.admin.post('/api/admin/users',json={'id':'doctor2','name':'다른 가상 의료진','role':'doctor'});self.assertEqual(r.status_code,200)
        other=self.client('doctor2');token=self.pair();self.ai.release.set();self.upload(token);job=self.wait_job(token)
        self.assertEqual(other.get('/api/recording-jobs?patient_id=DEMO-101').json()['jobs'],[])
        self.assertEqual(other.post('/api/recording-jobs/'+job['id']+'/review',json={'text':job['note_text'],'reviewed':True,'expected_version':1}).status_code,404)
        self.assertEqual(self.phone.post('/api/recording/'+token,headers={'Origin':'https://evil.example'},files={'file':('x.wav',wav())}).status_code,403)

    def test_patient_switch_keeps_accepted_job_bound_to_original_visit(self):
        token=self.pair();self.upload(token);self.assertTrue(self.ai.entered.wait(1))
        self.doctor.post('/api/recording-links/revoke',json={});other=self.pair(pid='DEMO-103')
        self.ai.release.set();job=self.wait_job(token)
        self.assertEqual(job['patient_id'],'DEMO-101');self.assertIsNone(self.phone.get('/api/recording/'+other).json()['job'])
        self.assertEqual(self.doctor.get('/api/recording-jobs?patient_id=DEMO-103').json()['jobs'],[])

    def test_account_change_during_transcription_creates_no_record(self):
        token=self.pair();self.upload(token);self.assertTrue(self.ai.entered.wait(1))
        self.admin.patch('/api/admin/users/doctor',json={'name':'변경','role':'staff','active':True,'expected_revision':1});self.ai.release.set()
        deadline=time.monotonic()+2
        while time.monotonic()<deadline:
            with self.app.state.store.connect() as c:row=c.execute('SELECT status FROM recording_jobs').fetchone()
            if row[0]=='cancelled':break
            time.sleep(.01)
        self.assertEqual(row[0],'cancelled');self.assertEqual(self.count('notes'),0);self.assertEqual(self.count('transcripts'),0)
        self.assertEqual(self.phone.get('/api/recording/'+token).status_code,410)

    def test_failure_and_silence_do_not_fake_registration(self):
        self.ai.release.set()
        for empty in (False,True):
            self.ai.failure=not empty;self.ai.empty=empty;token=self.pair();self.upload(token)
            job=self.wait_job(token);self.assertEqual(job['status'],'failed');self.assertIsNone(job['note_id']);self.assertEqual(self.count('notes'),0)
        self.assertEqual(list(self.app.state.recording_jobs.directory.iterdir()),[])

    def test_review_updates_same_record_and_checks_version_and_ownership(self):
        token=self.pair();self.ai.release.set();self.upload(token);job=self.wait_job(token)
        path='/api/recording-jobs/'+job['id']+'/review';body={'text':job['note_text'],'reviewed':False,'expected_version':1}
        self.assertEqual(self.doctor.post(path,json=body).status_code,400)
        other=self.client('admin')
        self.assertEqual(other.post(path,json={**body,'reviewed':True}).status_code,200)
        self.assertEqual(self.doctor.post(path,json={**body,'reviewed':True}).status_code,409)
        updated=self.wait_job(token);self.assertEqual(updated['note_id'],job['note_id']);self.assertTrue(updated['reviewed']);self.assertEqual(updated['note_version'],2);self.assertEqual(self.count('notes'),1)
        self.assertEqual(self.staff.post(path,json={**body,'reviewed':True}).status_code,403)
        self.assertFalse(updated['crm_connected'])
        self.assertIn('검토 완료',updated['stage'])

    def test_restart_marks_running_failed_and_cleans_audio(self):
        self.ai.release.set();token=self.pair();self.upload(token);job=self.wait_job(token)
        self.app.state.recording_jobs.close()
        leftover=self.app.state.recording_jobs.directory/'interrupted.m4a';leftover.write_bytes(wav())
        with self.app.state.store.connect() as c:c.execute("UPDATE recording_jobs SET status='running',audio_path=? WHERE id=?",(leftover.name,job['id']))
        restarted=RecordingJobs(self.app.state.store,self.ai,self.app.state.recording_jobs.today);restarted.start();restarted.close()
        self.assertEqual(self.wait_job(token)['status'],'failed');self.assertFalse(leftover.exists())


if __name__=='__main__':unittest.main()
