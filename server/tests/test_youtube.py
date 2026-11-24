"""URL ingestion, queue lifecycle and authorization; network sources are injected."""
import json
import tempfile
import threading
import time
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
from app import create_app, settings_from_env
from test_app import FakeAI
from youtube_import import (Cancelled, ImportProblem, NoCaptions, YouTubeImporter,
                            YouTubeJobs, canonical_video_url, normalize_segments)

URL='https://www.youtube.com/watch?v=BaW_jenozKc'


def source():
    return dict(segments=[dict(start=1,end=3,text='가상 병원 교육 대본입니다.')],
                language='ko',generated=False,method='manual_caption',duration=3,
                title='가상 교육 영상',author='가상 채널',date='')


class ControlledImporter:
    def __init__(self):
        self.entered=threading.Event();self.release=threading.Event();self.failure=None
    def fetch(self,url,mode,language,report,cancel):
        self.entered.set();report('테스트 수집 중',20)
        while not self.release.wait(.01):
            if cancel():raise Cancelled()
        if cancel():raise Cancelled()
        if self.failure:raise self.failure
        return source()


class ImporterTests(unittest.TestCase):
    def test_canonicalization_and_external_host_rejection(self):
        for url in (URL,URL+'&list=ignore&t=20','https://youtu.be/BaW_jenozKc?si=abc',
                    'https://m.youtube.com/shorts/BaW_jenozKc','https://youtube.com/live/BaW_jenozKc'):
            self.assertEqual(canonical_video_url(url),('BaW_jenozKc',URL))
        for url in ('file:///etc/passwd','https://localhost/watch?v=BaW_jenozKc',
                    'https://youtube.com.evil.example/watch?v=BaW_jenozKc',
                    'https://youtube.com@evil.example/watch?v=BaW_jenozKc',
                    'https://user:pass@youtube.com/watch?v=BaW_jenozKc',
                    'https://youtube.com:9000/watch?v=BaW_jenozKc',
                    'https://youtube.com/playlist?list=abc','https://youtube.com/@hospital',
                    URL+'&v=aaaaaaaaaaa','https://youtu.be/not-an-id','javascript:alert(1)'):
            with self.subTest(url=url),self.assertRaises(ImportProblem):canonical_video_url(url)

    def test_timestamp_normalization_and_repeated_speech_preserved(self):
        rows=normalize_segments([dict(start=1,duration=1,text='A &amp; B'),
                                 dict(start=2,end=3,duration=None,text='A &amp; B')])
        self.assertEqual(len(rows),2);self.assertEqual(rows[0]['text'],'A & B')
        for row in (dict(start=float('nan'),end=1,text='x'),dict(start=-1,end=1,text='x'),
                    dict(start=2,end=1,text='x'),dict(start=0,end=1900,text='x')):
            with self.assertRaises(ImportProblem):normalize_segments([row])
        with self.assertRaises(ImportProblem):normalize_segments([dict(text=' ')])

    def test_captions_prevent_audio_download(self):
        importer=YouTubeImporter(None);importer.captions=Mock(return_value=source())
        importer.metadata=Mock(side_effect=lambda url,s:s);importer.audio=Mock()
        self.assertEqual(importer.fetch(URL,'auto','ko',Mock(),lambda:False)['method'],'manual_caption')
        importer.audio.assert_not_called()

    def test_only_missing_captions_fall_back_to_audio(self):
        importer=YouTubeImporter(None);importer.captions=Mock(side_effect=NoCaptions())
        importer.audio=Mock(return_value=source())
        importer.fetch(URL,'auto','ko',Mock(),lambda:False);importer.audio.assert_called_once()
        importer.audio.reset_mock()
        with self.assertRaises(NoCaptions):importer.fetch(URL,'captions','ko',Mock(),lambda:False)
        importer.audio.assert_not_called()
        importer.captions=Mock(side_effect=ImportProblem('YOUTUBE_BLOCKED','blocked'))
        with self.assertRaises(ImportProblem):importer.fetch(URL,'auto','ko',Mock(),lambda:False)
        importer.audio.assert_not_called()

    def test_audio_mode_skips_captions(self):
        importer=YouTubeImporter(None);importer.captions=Mock();importer.audio=Mock(return_value=source())
        importer.fetch(URL,'audio','en',Mock(),lambda:False);importer.captions.assert_not_called()
        importer.audio.assert_called_once()

    def test_audio_download_limits_cleanup_and_transcription_handoff(self):
        # Only the downloader and model are injected; PyAV opens an actual short WAV.
        paths=[];captured={}
        class Download:
            def __init__(self,opts):captured.update(opts)
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def extract_info(self,url,download):
                captured['match_filter']({'duration':1,'availability':'public'},incomplete=False)
                for info,code in (({'duration':1900},'TOO_LONG'),({'duration':1,'is_live':True},'LIVE_UNSUPPORTED'),({'duration':1,'availability':'private'},'VIDEO_UNAVAILABLE')):
                    with test.assertRaises(ImportProblem) as error:captured['match_filter'](info,incomplete=False)
                    test.assertEqual(error.exception.code,code)
                with test.assertRaises(ImportProblem):captured['progress_hooks'][0]({'downloaded_bytes':130*1024*1024})
                self.path=Path(captured['outtmpl'].replace('%(ext)s','wav'));paths.append(self.path)
                with wave.open(str(self.path),'wb') as f:
                    f.setnchannels(1);f.setsampwidth(2);f.setframerate(16000);f.writeframes(b'\x00\x00'*16000)
                return dict(duration=1,title='테스트 음성',channel='가상 채널',upload_date='20260101')
            def prepare_filename(self,info):return str(self.path)
        test=self;ai=Mock();ai.transcribe.return_value=dict(language='ko',segments=[dict(start=0,end=1,text='가상 대본')])
        with patch.dict('sys.modules',yt_dlp=SimpleNamespace(YoutubeDL=Download)):
            result=YouTubeImporter(ai).audio(URL,'ko',Mock(),lambda:False)
        self.assertEqual(result['method'],'whisper');self.assertEqual(result['date'],'2026-01-01')
        ai.transcribe.assert_called_once();self.assertFalse(paths[0].exists())
        self.assertTrue(captured['noplaylist']);self.assertFalse(captured['remote_components'])
        self.assertNotIn('cookiefile',captured);self.assertNotIn('proxy',captured)


class YouTubeAPITests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();cfg=settings_from_env();cfg['data_dir']=self.temp.name
        self.importer=ControlledImporter();self.app=create_app(cfg,FakeAI(),self.importer)
        self.admin=TestClient(self.app);self.admin.__enter__();self.clients=[self.admin]
        self.login(self.admin,'admin');self.doctor=self.client('doctor');self.staff=self.client('staff')
    def login(self,client,uid):
        r=client.post('/api/login',json={'username':uid,'password':'ClinicDemo42!'})
        self.assertEqual(r.status_code,200,r.text);client.headers['X-CSRF-Token']=r.json()['csrf']
    def client(self,uid):
        client=TestClient(self.app);self.clients.append(client);self.login(client,uid);return client
    def tearDown(self):
        self.importer.release.set();self.app.state.youtube_jobs.close()
        for c in self.clients[1:]:c.close()
        self.admin.__exit__(None,None,None);self.temp.cleanup()
    def submit(self,client=None,**kw):
        body={'url':URL};body.update(kw)
        r=(client or self.admin).post('/api/youtube/jobs',json=body)
        self.assertEqual(r.status_code,202,r.text);return r.json()
    def wait_job(self,jid,client=None):
        deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            job=next(j for j in (client or self.admin).get('/api/youtube/jobs').json()['jobs'] if j['id']==jid)
            if job['status'] not in {'queued','running'}:return job
            time.sleep(.01)
        self.fail('job did not finish')
    def count(self,table):
        with self.app.state.store.connect() as c:return c.execute('SELECT COUNT(*) FROM '+table).fetchone()[0]

    def test_login_csrf_editor_and_url_validation(self):
        anonymous=TestClient(self.app)
        self.assertEqual(anonymous.get('/api/youtube/jobs').status_code,401);anonymous.close()
        for path in ('/api/youtube/jobs','/api/youtube/status'):
            self.assertEqual(self.staff.get(path).status_code,403)
        self.assertEqual(self.staff.post('/api/youtube/jobs',json={'url':URL}).status_code,403)
        token=self.doctor.headers.pop('X-CSRF-Token')
        self.assertEqual(self.doctor.post('/api/youtube/jobs',json={'url':URL}).status_code,403)
        self.doctor.headers['X-CSRF-Token']=token
        for body in ({'url':'https://127.0.0.1/internal'},{'url':URL,'roles':['other']},
                     {'url':URL,'roles':[]},{'url':URL,'roles':['admin']}):
            self.assertEqual(self.doctor.post('/api/youtube/jobs',json=body).status_code,400)
        self.assertEqual(self.count('youtube_jobs'),0)

    def test_dedupe_persistence_unapproved_provenance_and_index(self):
        before=self.count('documents');first=self.submit(self.doctor)
        self.assertTrue(self.importer.entered.wait(1))
        duplicate=self.submit(self.doctor,url='https://youtu.be/BaW_jenozKc?t=1')
        self.assertTrue(duplicate['reused']);self.assertEqual(first['job']['id'],duplicate['job']['id'])
        self.importer.release.set();job=self.wait_job(first['job']['id'])
        self.assertEqual(job['status'],'succeeded',job);self.assertEqual(self.count('documents'),before+1)
        self.assertTrue(self.submit(self.doctor)['reused'])
        did=job['doc_id'];doc=self.doctor.get('/api/documents/'+did).json()
        self.assertFalse(doc['approved']);self.assertEqual(doc['version'],1)
        self.assertEqual(doc['youtube_source']['method'],'manual_caption');self.assertEqual(doc['source_url'],URL)
        self.assertIn('[00:00:01]',doc['content']);self.assertEqual(doc['indexed_chunks'],0)
        self.assertEqual(self.staff.get('/api/documents/'+did).status_code,404)
        self.admin.post('/api/documents/reindex')
        self.assertEqual(self.doctor.get('/api/documents/'+did).json()['indexed_chunks'],0)
        self.assertEqual(self.admin.post(f'/api/documents/{did}/approval',json={'expected_version':1,'approved':True}).status_code,200)
        self.admin.post('/api/documents/reindex')
        self.assertGreater(self.staff.get('/api/documents/'+did).json()['indexed_chunks'],0)
        with self.app.state.store.connect() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM document_versions WHERE doc_id=?',(did,)).fetchone()[0],2)

    def test_import_approve_then_staff_question_uses_video_without_manual_reindex(self):
        from ai_services import validate_generation
        with self.app.state.store.connect() as c:
            c.execute('UPDATE documents SET approved=0')
            c.execute('DELETE FROM chunks')
        quote='예약 변경은 방문 하루 전까지 연락하면 됩니다.'
        video=source();video['segments']=[{'start':65,'end':70,'text':quote}];video['duration']=70
        self.importer.fetch=lambda *args:video
        submitted=self.submit();job=self.wait_job(submitted['job']['id']);did=job['doc_id']
        self.assertEqual(job['status'],'succeeded',job)
        prompt={'question':'예약 변경은 언제까지 연락하나요?'}
        self.assertFalse(self.staff.post('/api/wiki/ask',json=prompt).json()['llm_used'])
        self.admin.post('/api/documents/'+did+'/approval',json={'expected_version':1,'approved':True}).raise_for_status()
        calls=[]
        async def generate(question,sources):
            calls.append(sources)
            return validate_generation({'sufficient':True,'answer':quote,'citations':[{'source_id':sources[0]['source_id'],'quote':quote}]},sources)
        self.app.state.ai.generate=generate
        reply=self.staff.post('/api/wiki/ask',json=prompt)
        self.assertEqual(reply.status_code,200,reply.text)
        answer=reply.json();self.assertTrue(answer['llm_used']);self.assertEqual(answer['answer'],quote)
        self.assertEqual(answer['sources'][0]['doc_id'],did)
        self.assertEqual(answer['sources'][0]['kind'],'youtube_transcript')
        self.assertEqual(answer['sources'][0]['source_url'],URL)
        self.assertIn('[00:01:05]',calls[0][0]['text'])
        self.assertEqual(answer['citations'][0]['quote'],quote)
        self.assertGreater(self.staff.get('/api/documents/'+did).json()['indexed_chunks'],0)
        self.staff.post('/api/wiki/ask',json=prompt).raise_for_status()
        with self.app.state.store.connect() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM audit WHERE action='wiki_auto_index' AND object_id=?",(did,)).fetchone()[0],1)
        version=self.admin.get('/api/documents/'+did).json()['version']
        self.admin.post('/api/documents/'+did+'/approval',json={'expected_version':version,'approved':False}).raise_for_status()
        self.assertFalse(self.staff.post('/api/wiki/ask',json=prompt).json()['llm_used'])
        self.assertEqual(len(calls),2)

    def test_failure_never_creates_empty_document_and_can_retry(self):
        before=self.count('documents');self.importer.failure=ImportProblem('YOUTUBE_BLOCKED','수집 중단')
        first=self.submit();self.importer.release.set();job=self.wait_job(first['job']['id'])
        self.assertEqual(job['status'],'failed');self.assertEqual(job['error_code'],'YOUTUBE_BLOCKED')
        self.assertEqual(self.count('documents'),before);self.assertEqual(self.count('youtube_sources'),0)
        self.importer.failure=None;retry=self.submit();self.assertFalse(retry['reused'])
        self.assertEqual(self.wait_job(retry['job']['id'])['status'],'succeeded')

    def test_cancel_queued_running_and_other_owner_denied(self):
        before=self.count('documents');first=self.submit();self.assertTrue(self.importer.entered.wait(1))
        queued=self.submit(url='https://youtu.be/aaaaaaaaaaa')['job']['id']
        self.assertEqual(self.doctor.post('/api/youtube/jobs/'+first['job']['id']+'/cancel',json={}).status_code,404)
        for jid in (queued,first['job']['id']):
            self.assertEqual(self.admin.post('/api/youtube/jobs/'+jid+'/cancel',json={}).status_code,200)
            self.assertEqual(self.wait_job(jid)['status'],'cancelled')
            self.assertEqual(self.admin.post('/api/youtube/jobs/'+jid+'/cancel',json={}).status_code,409)
        self.assertEqual(self.count('documents'),before)

    def test_account_change_cancels_without_saving(self):
        before=self.count('documents');jid=self.submit(self.doctor)['job']['id']
        self.assertTrue(self.importer.entered.wait(1))
        r=self.admin.patch('/api/admin/users/doctor',json={'name':'역할 변경','role':'staff','active':True,'expected_revision':1})
        self.assertEqual(r.status_code,200,r.text);self.importer.release.set()
        self.assertEqual(self.wait_job(jid)['status'],'cancelled');self.assertEqual(self.count('documents'),before)

    def test_job_scope_and_changed_document_permissions(self):
        first=self.submit(self.doctor);self.importer.release.set();job=self.wait_job(first['job']['id'])
        self.assertEqual(len(self.doctor.get('/api/youtube/jobs').json()['jobs']),1)
        self.submit();self.assertEqual(len(self.doctor.get('/api/youtube/jobs').json()['jobs']),1)
        did=job['doc_id'];doc=self.admin.get('/api/documents/'+did).json()
        r=self.admin.put('/api/documents/'+did,json={**doc,'title':'권한 변경 후 비공개 제목','roles':['admin'],'expected_version':doc['version']})
        self.assertEqual(r.status_code,200,r.text)
        for body in (self.doctor.get('/api/youtube/jobs').json()['jobs'][0],self.submit(self.doctor)['job']):
            self.assertIsNone(body['doc_id']);self.assertNotIn('비공개 제목',body['title'])

    def test_pending_limit_and_restart_recovery(self):
        self.submit();self.assertTrue(self.importer.entered.wait(1))
        for vid in ('aaaaaaaaaaa','bbbbbbbbbbb'):self.submit(url='https://youtu.be/'+vid)
        r=self.admin.post('/api/youtube/jobs',json={'url':'https://youtu.be/ccccccccccc'})
        self.assertEqual(r.status_code,429)
        self.importer.release.set()
        self.app.state.youtube_jobs.close()
        with self.app.state.store.connect() as c:
            c.execute("UPDATE youtube_jobs SET status='running' WHERE video_id='bbbbbbbbbbb'")
        recovered=YouTubeJobs(self.app.state.store,FakeAI(),self.importer)
        recovered.start();recovered.close()
        row=next(j for j in self.admin.get('/api/youtube/jobs').json()['jobs'] if j['video_id']=='bbbbbbbbbbb')
        self.assertEqual(row['status'],'failed');self.assertEqual(row['error_code'],'SERVER_INTERRUPTED')


if __name__=='__main__':unittest.main()
