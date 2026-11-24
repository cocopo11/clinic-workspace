"""Scoped tablet recording -> real STT -> unreviewed local mock CRM record."""
import base64
import hashlib
import io
import json
import secrets
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlencode, urlsplit

import qrcode
from fastapi import Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool
from ai_services import AIUnavailable
from storage import token_hash

LIMIT=150*1024*1024
MAX_SECONDS=30*60
LINK_SECONDS=90*60
MAX_TRANSCRIPT_CHARS=60000


def validate_audio(path):
    import av
    try:
        with av.open(str(path)) as media:
            if not media.streams.audio:raise ValueError('no audio')
            if media.duration:duration=media.duration/av.time_base
            else:
                duration=0
                for frame in media.decode(audio=0):
                    duration+=frame.samples/frame.sample_rate
                    if duration>MAX_SECONDS:break
            if duration<=0:raise ValueError('empty audio')
            if duration>MAX_SECONDS:raise HTTPException(413,'녹음은 30분 이하로 제한합니다.')
            return duration
    except HTTPException:raise
    except Exception:raise HTTPException(400,'음성 파일을 읽지 못했습니다. 다시 녹음하세요.')


class RecordingJobs:
    def __init__(self,store,ai,today):
        self.store=store;self.ai=ai;self.today=today
        self.directory=store.directory/'recording_uploads';self.directory.mkdir(exist_ok=True)
        self.stop=threading.Event();self.wake=threading.Event();self.lock=threading.Lock();self.thread=None
    def start(self):
        if getattr(self, "cloud", False):return
        with self.lock:
            if self.thread and self.thread.is_alive():return
            with self.store.connect() as c:
                rows=c.execute("SELECT audio_path FROM recording_jobs WHERE status='running'").fetchall()
                c.execute("UPDATE recording_jobs SET status='failed',stage='서버 재시작으로 중단',error='서버가 재시작되어 전사가 중단됐습니다. 새 녹음 링크에서 다시 녹음하세요.',updated=? WHERE status='running'",(time.time(),))
            for row in rows:(self.directory/row['audio_path']).unlink(missing_ok=True)
            # An interrupted upload that never entered the queue must not retain audio.
            with self.store.connect() as c:keep={r[0] for r in c.execute("SELECT audio_path FROM recording_jobs WHERE status='queued'")}
            for path in self.directory.iterdir():
                if path.is_file() and path.name not in keep:path.unlink(missing_ok=True)
            self.stop.clear();self.thread=threading.Thread(target=self.work,name='tablet-recording',daemon=True);self.thread.start()
    def close(self):
        self.stop.set();self.wake.set()
        if self.thread:self.thread.join(timeout=.2)
    def authorized(self,c,job):
        row=c.execute('''SELECT u.active,u.role,u.revision,s.expires,l.owner_revision
          FROM recording_links l JOIN users u ON u.id=l.owner_id JOIN sessions s ON s.hash=l.session_hash
          WHERE l.hash=?''',(job['link_hash'],)).fetchone()
        return bool(row and row['active'] and row['role'] in {'admin','doctor'} and
                    row['revision']==row['owner_revision'] and row['expires']>time.time() and job['visit_date']==self.today())
    def work(self):
        while not self.stop.is_set():
            with self.store.connect() as c:
                c.execute('BEGIN IMMEDIATE');row=c.execute("SELECT * FROM recording_jobs WHERE status='queued' ORDER BY created LIMIT 1").fetchone()
                if row:c.execute("UPDATE recording_jobs SET status='running',stage='Whisper 음성 전사 중',progress=25,updated=? WHERE id=?",(time.time(),row['id']))
            if row:self.process(dict(row))
            else:self.wake.wait(1);self.wake.clear()
    def process(self,job):
        path=self.directory/job['audio_path']
        try:
            with self.store.connect() as c:
                if self.stop.is_set() or not self.authorized(c,job):raise PermissionError()
            result=self.ai.transcribe(path,job['language'])
            text=result['text'].strip()
            if not text:raise ValueError('NO_SPEECH')
            if len(text)>MAX_TRANSCRIPT_CHARS:raise ValueError('TOO_LONG')
            automatic=job['registration_mode']=='auto'
            draft=f'[환자] {job["patient_id"]}\n[방문일] {job["visit_date"]}\n[입력] 아이패드·모바일 녹음 자동 전사\n\n[전사 원문 · 검토 전]\n{text}\n\n[검토 상태] 미검토 · 약명·수치·부정어 확인 필요\n[CRM] 데모용 로컬 자동 등록 · 실제 병원 CRM 미연결'
            if automatic:
                draft=draft.replace('[전사 원문 · 검토 전]','[전사 원문]').replace('[검토 상태] 미검토 · 약명·수치·부정어 확인 필요','[검토 상태] 자동 검토 완료 (기본 형식·환자/방문 연결) · 의료진 확인 미수행')
            with self.store.connect() as c:
                c.execute('BEGIN IMMEDIATE')
                if getattr(self,'cloud',False):
                    current=c.execute('SELECT status FROM recording_jobs WHERE id=?',(job['id'],)).fetchone()
                    if not current or current['status']!='running':raise PermissionError()
                if self.stop.is_set() or not self.authorized(c,job):raise PermissionError()
                tid=c.execute('INSERT INTO transcripts(patient_id,user_id,text,language,segments,created) VALUES(?,?,?,?,?,?)',
                    (job['patient_id'],job['owner_id'],text,result['language'],json.dumps(result['segments'],ensure_ascii=False),time.time())).lastrowid
                nid=c.execute('INSERT INTO notes(patient_id,text,hash,user_id,attempts,status,created,reviewed,source,visit_date) VALUES(?,?,?,?,?,?,?,?,?,?)',
                    (job['patient_id'],draft,hashlib.sha256(('recording:'+job['id']).encode()).hexdigest(),job['owner_id'],1,'자동 등록 · 검토 대기',time.time(),0,'tablet_recording',job['visit_date'])).lastrowid
                c.execute("UPDATE recording_jobs SET status='succeeded',stage='모의 CRM 자동 등록 완료 · 검토 대기',progress=100,transcript_id=?,note_id=?,updated=? WHERE id=?",(tid,nid,time.time(),job['id']))
                if automatic:
                    c.execute("UPDATE notes SET status='전송 성공' WHERE id=?",(nid,))
                    c.execute("UPDATE recording_jobs SET auto_reviewed=1,stage='자동 검토 완료 · 모의 CRM 진료차트 등록 완료' WHERE id=?",(job['id'],))
                    c.execute('INSERT OR IGNORE INTO crm_chart_entries VALUES(?,?,?,?,?)',(nid,job['patient_id'],job['visit_date'],draft,time.time()))
                else:
                    c.execute("UPDATE notes SET status='수동 등록 대기' WHERE id=?",(nid,))
                    c.execute("UPDATE recording_jobs SET stage='전사 완료 · 수동 등록 대기' WHERE id=?",(job['id'],))
                self.store.audit(job['owner_id'],'recording_registered',job['patient_id'],detail={'job_id':job['id'],'note_id':nid,'reviewed':False,'auto_reviewed':automatic,'crm_connected':False},connection=c)
        except PermissionError:self.fail(job,'cancelled','로그인·계정 권한 또는 방문일이 변경되어 등록을 중단했습니다.')
        except AIUnavailable:self.fail(job,'failed','Whisper 전사에 실패했습니다. PC의 모델 준비 상태를 확인하고 새 링크에서 다시 녹음하세요.')
        except ValueError:self.fail(job,'failed','인식한 음성이 없거나 전사문이 너무 깁니다. 짧게 다시 녹음하세요.')
        except Exception:self.fail(job,'failed','녹음 처리에 실패했습니다. 모델·서버 상태를 확인하고 새 녹음 링크를 만드세요.')
        finally:self.store.delete_file('recording_uploads',path.name)
    def fail(self,job,status,message):
        with self.store.connect() as c:
            c.execute('UPDATE recording_jobs SET status=?,stage=?,error=?,updated=? WHERE id=?',(status,'등록 중단' if status=='cancelled' else '처리 실패',message,time.time(),job['id']))
            self.store.audit(job['owner_id'],'recording_failed',job['patient_id'],result='error',detail={'job_id':job['id'],'status':status},connection=c)


class Pair(BaseModel):
    patient_id:str
    base_url:str=Field(max_length=250)
class Review(BaseModel):
    text:str=Field(min_length=1,max_length=MAX_TRANSCRIPT_CHARS+3000)
    reviewed:bool
    expected_version:int=Field(ge=1)


def install_recording(app,store,clinical,require_patient,today,jobs):
    if not getattr(store, "cloud_schema_ready", False):
        with store.connect() as c:
            c.execute("CREATE TABLE IF NOT EXISTS crm_modes(patient_id TEXT PRIMARY KEY, mode TEXT NOT NULL)")
            c.execute("CREATE TABLE IF NOT EXISTS crm_chart_entries(note_id INTEGER PRIMARY KEY,patient_id TEXT NOT NULL,visit_date TEXT NOT NULL,text TEXT NOT NULL,created REAL NOT NULL)")
            if 'registration_mode' not in {r[1] for r in c.execute('PRAGMA table_info(recording_jobs)')}:
                c.execute("ALTER TABLE recording_jobs ADD COLUMN registration_mode TEXT NOT NULL DEFAULT 'auto'")
            if 'auto_reviewed' not in {r[1] for r in c.execute('PRAGMA table_info(recording_jobs)')}:
                c.execute("ALTER TABLE recording_jobs ADD COLUMN auto_reviewed INTEGER NOT NULL DEFAULT 0")

    @app.get('/api/patients/{pid}/crm-chart')
    def crm_chart(pid:str,user=Depends(clinical)):
        require_patient(pid)
        with store.connect(shared=True) as c:
            mode=c.execute('SELECT mode FROM crm_modes WHERE patient_id=?',(pid,)).fetchone()
            return {'mode':mode['mode'] if mode else 'auto','entries':[dict(r) for r in c.execute('SELECT * FROM crm_chart_entries WHERE patient_id=? ORDER BY created DESC',(pid,))],'crm_connected':False}
    @app.post('/api/patients/{pid}/crm-mode')
    async def crm_mode(pid:str,body:dict,user=Depends(clinical)):
        require_patient(pid)
        if body.get('mode') not in {'auto','manual'}:raise HTTPException(400,'등록 모드를 확인하세요.')
        with store.connect() as c:
            c.execute('INSERT INTO crm_modes VALUES(?,?) ON CONFLICT(patient_id) DO UPDATE SET mode=excluded.mode',(pid,body['mode']))
            store.audit(user['id'],'crm_mode',pid,detail=body,connection=c)
        return {'mode':body['mode']}
    def valid_link(c,token):
        if len(token)>150:raise HTTPException(410,'녹음 연결이 유효하지 않습니다.')
        row=c.execute('''SELECT l.*,u.name,u.active,u.role,u.revision,s.expires AS session_expires
          FROM recording_links l JOIN users u ON u.id=l.owner_id JOIN sessions s ON s.hash=l.session_hash
          WHERE l.hash=?''',(token_hash(token),)).fetchone()
        if not row or row['revoked'] or row['expires']<time.time() or row['session_expires']<time.time() or not row['active'] or row['role'] not in {'admin','doctor'} or row['revision']!=row['owner_revision'] or row['visit_date']!=today():
            raise HTTPException(410,'녹음 연결이 만료·취소됐습니다. PC에서 새 녹음 링크를 만드세요.')
        return dict(row)
    def public_job(c,row):
        d={k:v for k,v in dict(row).items() if k not in {'link_hash','audio_path','owner_id'}}
        d['auto_reviewed']=bool(d['auto_reviewed'])
        d.update(crm_connected=False,reviewed=False,text='',note_text='',note_version=None)
        if row['note_id']:
            note=c.execute('SELECT text,reviewed,version,status FROM notes WHERE id=?',(row['note_id'],)).fetchone()
            transcript=c.execute('SELECT text FROM transcripts WHERE id=?',(row['transcript_id'],)).fetchone()
            d.update(text=transcript['text'],note_text=note['text'],reviewed=bool(note['reviewed']),note_version=note['version'],registration_status=note['status'])
            if note['reviewed']:d['stage']='모의 CRM 자동 등록 완료 · 검토 완료'
        return d
    def get_job(c,jid,user):
        row=c.execute('SELECT * FROM recording_jobs WHERE id=?',(jid,)).fetchone()
        if not row or user['role']!='admin' and row['owner_id']!=user['id']:raise HTTPException(404,'녹음 작업이 없습니다.')
        return row
    @app.post('/api/recording-links')
    async def create_link(body:Pair,user=Depends(clinical)):
        require_patient(body.patient_id)
        try:
            parsed=urlsplit(body.base_url.strip())
            if parsed.scheme not in {'https','http'} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {'','/'} or not parsed.port and parsed.netloc.endswith(':'):raise ValueError()
            parsed.port
        except ValueError:raise HTTPException(400,'아이패드 접속 주소는 https://PC주소:포트 형식으로 입력하세요.')
        token=secrets.token_urlsafe(32);now=time.time()
        with store.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            c.execute('UPDATE recording_links SET revoked=1 WHERE session_hash=? AND job_id IS NULL',(user['session_hash'],))
            revision=c.execute('SELECT revision FROM users WHERE id=?',(user['id'],)).fetchone()[0]
            c.execute('INSERT INTO recording_links(hash,owner_id,owner_revision,session_hash,patient_id,visit_date,expires) VALUES(?,?,?,?,?,?,?)',
                (token_hash(token),user['id'],revision,user['session_hash'],body.patient_id,today(),now+LINK_SECONDS))
            store.audit(user['id'],'recording_link',body.patient_id,connection=c)
        link=body.base_url.strip().rstrip('/')+'/record?'+urlencode({'token':token})
        buf=io.BytesIO();qrcode.make(link).save(buf,format='PNG')
        return {'url':link,'qr':'data:image/png;base64,'+base64.b64encode(buf.getvalue()).decode(),'expires_in':LINK_SECONDS,'patient_id':body.patient_id,'visit_date':today(),'crm_connected':False}
    @app.post('/api/recording-links/revoke')
    async def revoke_link(user=Depends(clinical)):
        with store.connect() as c:c.execute('UPDATE recording_links SET revoked=1 WHERE session_hash=? AND job_id IS NULL',(user['session_hash'],))
        return {'ok':True}
    @app.get('/api/recording/{token}')
    def info(token:str):
        with store.connect(shared=True) as c:
            link=valid_link(c,token);job=c.execute('SELECT * FROM recording_jobs WHERE id=?',(link['job_id'],)).fetchone() if link['job_id'] else None
            return {'patient_id':link['patient_id'],'visit_date':link['visit_date'],'expires':link['expires'],'crm_connected':False,'job':public_job(c,job) if job else None}
    @app.get('/api/recording/{token}/photos')
    def report_photos(token:str):
        with store.connect(shared=True) as c:
            link=valid_link(c,token)
            rows=c.execute("SELECT id,visit_date,site FROM photos WHERE patient_id=? ORDER BY visit_date DESC,created DESC,id DESC",(link['patient_id'],)).fetchall()
            before=next((dict(r) for r in rows if r['visit_date']<link['visit_date']),None)
            current=next((dict(r) for r in rows if r['visit_date']==link['visit_date']),None)
            return {'patient_id':link['patient_id'],'visit_date':link['visit_date'],'before':before,'today':current,'history':[dict(r) for r in rows if r['visit_date']<link['visit_date']]}

    @app.get('/api/recording/{token}/photos/{photo_id}')
    def report_image(token:str,photo_id:int):
        with store.connect(shared=True) as c:
            link=valid_link(c,token)
            row=c.execute("SELECT filename FROM photos WHERE id=? AND patient_id=? AND visit_date<=?",(photo_id,link['patient_id'],link['visit_date'])).fetchone()
            if not row:raise HTTPException(404,'이 환자의 사진이 없습니다.')
        path=store.load_file('photos', row['filename'])
        if not path.is_file():raise HTTPException(404,'사진 파일이 없습니다.')
        return FileResponse(path,headers={'Cache-Control':'no-store'})

    @app.post('/api/recording/{token}',status_code=202)
    async def upload(token:str,file:UploadFile=File(...),language:str=Form('ko')):
        # A retry of the same link returns its original job, including after a lost response.
        with store.connect() as c:
            link=valid_link(c,token)
            if link['job_id']:return {'job':public_job(c,c.execute('SELECT * FROM recording_jobs WHERE id=?',(link['job_id'],)).fetchone()),'duplicate':True}
        if language not in {'ko','en','auto'}:raise HTTPException(400,'전사 언어를 확인하세요.')
        data=await file.read(LIMIT+1)
        if not data or len(data)>LIMIT:raise HTTPException(413,'녹음은 150MB 이하로 전송하세요.')
        jobs.start()
        jid='AUDIO-'+uuid.uuid4().hex[:12];suffix=Path(file.filename or '').suffix.lower()
        if suffix not in {'.m4a','.mp4','.webm','.wav','.ogg','.mp3','.aac','.flac'}:suffix='.webm'
        path=store.save_file('recording_uploads',jid+suffix,data);persisted=False
        try:
            await run_in_threadpool(validate_audio,path)
            with store.connect() as c:
                c.execute('BEGIN IMMEDIATE');link=valid_link(c,token)
                if link['job_id']:return {'job':public_job(c,c.execute('SELECT * FROM recording_jobs WHERE id=?',(link['job_id'],)).fetchone()),'duplicate':True}
                if c.execute("SELECT COUNT(*) FROM recording_jobs WHERE owner_id=? AND status IN ('queued','running')",(link['owner_id'],)).fetchone()[0]>=3:raise HTTPException(429,'녹음 처리 중인 작업은 계정당 최대 3개입니다. 잠시 후 다시 전송하세요.')
                now=time.time()
                c.execute('INSERT INTO recording_jobs(id,link_hash,owner_id,patient_id,visit_date,language,audio_path,status,stage,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                    (jid,link['hash'],link['owner_id'],link['patient_id'],link['visit_date'],language,path.name,'queued','전사 대기',now,now))
                mode=c.execute('SELECT mode FROM crm_modes WHERE patient_id=?',(link['patient_id'],)).fetchone()
                c.execute('UPDATE recording_jobs SET registration_mode=? WHERE id=?',(mode['mode'] if mode else 'auto',jid))
                c.execute('UPDATE recording_links SET job_id=? WHERE hash=?',(jid,link['hash']))
                store.audit(link['owner_id'],'recording_uploaded',link['patient_id'],detail={'job_id':jid},connection=c)
                job=public_job(c,c.execute('SELECT * FROM recording_jobs WHERE id=?',(jid,)).fetchone())
            persisted=True;jobs.wake.set();return {'job':job,'duplicate':False}
        finally:
            if not persisted:store.delete_file('recording_uploads',path.name)
    @app.get('/api/recording-jobs')
    async def list_jobs(patient_id:str,user=Depends(clinical)):
        if getattr(jobs, 'cloud', False):
            from cloud_ai import process_cloud_job
            await process_cloud_job(jobs, 'recording_jobs', user, today)
        require_patient(patient_id)
        with store.connect() as c:
            rows=c.execute('SELECT * FROM recording_jobs WHERE patient_id=? AND visit_date=? '+('' if user['role']=='admin' else 'AND owner_id=? ')+'ORDER BY created DESC LIMIT 10',(patient_id,today()) if user['role']=='admin' else (patient_id,today(),user['id'])).fetchall()
            return {'jobs':[public_job(c,r) for r in rows]}
    @app.post('/api/recording-jobs/{jid}/review')
    async def review(jid:str,body:Review,user=Depends(clinical)):
        if not body.reviewed or not body.text.strip() or '\x00' in body.text:raise HTTPException(400,'환자·원문 확인 후 검토 확인을 체크하세요.')
        with store.connect() as c:
            c.execute('BEGIN IMMEDIATE');job=get_job(c,jid,user)
            if job['status']!='succeeded':raise HTTPException(409,'자동 등록이 완료된 기록을 확인하세요.')
            note=c.execute('SELECT * FROM notes WHERE id=?',(job['note_id'],)).fetchone()
            if note['version']!=body.expected_version:raise HTTPException(409,'기록이 변경됐습니다. 다시 열어 확인하세요.')
            text=body.text.strip().replace('[검토 상태] 자동 검토 완료 (기본 형식·환자/방문 연결) · 의료진 확인 미수행','[검토 상태] 의료진 확인 완료 (모의)').replace('[검토 상태] 미검토 · 약명·수치·부정어 확인 필요','[검토 상태] 의료진 확인 완료 (모의)')
            c.execute("UPDATE notes SET text=?,reviewed=1,status='전송 성공',version=version+1 WHERE id=?",(text,job['note_id']))
            c.execute('INSERT INTO crm_chart_entries VALUES(?,?,?,?,?) ON CONFLICT(note_id) DO UPDATE SET text=excluded.text',(job['note_id'],job['patient_id'],job['visit_date'],text,time.time()))
            store.audit(user['id'],'recording_reviewed',job['patient_id'],detail={'job_id':jid,'note_id':job['note_id'],'crm_connected':False},connection=c)
        return {'ok':True,'crm_connected':False}
