"""Public YouTube captions/audio ingestion. No cookies, proxies or auth bypass."""
import html
import importlib.util
import json
import math
import re
import shutil
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Literal
from urllib.parse import parse_qs,urlsplit

from fastapi import Depends,HTTPException
from pydantic import BaseModel,Field
from ai_services import AIUnavailable,TranscriptionCancelled

MAX_SECONDS=1800
MAX_BYTES=120*1024*1024
METHODS={'manual_caption':'유튜브 제공 자막','auto_caption':'유튜브 자동 생성 자막','whisper':'Whisper 음성 전사'}

class ImportProblem(Exception):
    def __init__(self,code,message): super().__init__(message);self.code=code
class NoCaptions(ImportProblem):
    def __init__(self):super().__init__('NO_CAPTIONS','사용할 수 있는 자막이 없습니다. 자동 또는 음성 전사 방식으로 실행하세요.')
class Cancelled(Exception):pass


def canonical_video_url(value):
    try:
        p=urlsplit(value.strip())
        if p.scheme not in {'http','https'} or p.username or p.password or p.port not in {None,80,443}:raise ValueError()
        host=(p.hostname or '').lower();parts=[x for x in p.path.split('/') if x]
        if host=='youtu.be' and len(parts)==1:vid=parts[0]
        elif host in {'youtube.com','www.youtube.com','m.youtube.com','music.youtube.com'}:
            if p.path=='/watch':
                values=parse_qs(p.query).get('v',[])
                if len(values)!=1:raise ValueError()
                vid=values[0]
            elif len(parts)==2 and parts[0] in {'shorts','embed','live'}:vid=parts[1]
            else:raise ValueError()
        else:raise ValueError()
        if not re.fullmatch(r'[A-Za-z0-9_-]{11}',vid):raise ValueError()
        return vid,'https://www.youtube.com/watch?v='+vid
    except (ValueError,TypeError):
        raise ImportProblem('INVALID_URL','유튜브 영상 한 개의 watch·youtu.be·Shorts 링크를 입력하세요. 채널·재생목록 링크는 지원하지 않습니다.')


def normalize_segments(rows):
    result=[]
    for row in rows:
        text=html.unescape(str(row.get('text',''))).replace('\x00','').strip()
        if not text:continue
        try:
            start=float(row.get('start',0));end=float(row['end']) if 'end' in row else start+float(row.get('duration',0))
        except (ValueError,TypeError):raise ImportProblem('BAD_CAPTIONS','자막의 시간 정보를 읽지 못했습니다.')
        if not math.isfinite(start) or not math.isfinite(end) or start<0 or end<start:raise ImportProblem('BAD_CAPTIONS','자막 시간 정보를 확인하세요.')
        if end>MAX_SECONDS+1:raise ImportProblem('TOO_LONG','30분 이하의 영상 한 개를 선택하세요.')
        if result and result[-1]['text']==text and result[-1]['start']==round(start,2):continue
        result.append({'start':round(start,2),'end':round(end,2),'text':text})
    if not result:raise ImportProblem('EMPTY_TRANSCRIPT','대본으로 저장할 자막이나 음성을 찾지 못했습니다.')
    return result


def timestamp(seconds):
    value=int(seconds);return f'{value//3600:02d}:{value%3600//60:02d}:{value%60:02d}'


def render_transcript(source,url):
    rows=normalize_segments(source['segments'])
    text='\n'.join(f'[{timestamp(r["start"])}] {r["text"]}' for r in rows)
    content=f'자동 수집 대본 · 검토 전\n출처: {url}\n수집 방식: {METHODS[source["method"]]}\n언어: {source["language"]}\n\n{text}'
    if len(content)>100000:raise ImportProblem('TEXT_TOO_LARGE','대본이 10만 자를 넘습니다. 짧은 영상이나 구간별 원문 파일을 사용하세요.')
    return rows,content


class YouTubeImporter:
    def __init__(self,ai):self.ai=ai
    @staticmethod
    def dependencies():
        return {'captions':bool(importlib.util.find_spec('youtube_transcript_api')),
                'audio':bool(importlib.util.find_spec('yt_dlp')),
                'runtime':'deno' if shutil.which('deno') else 'node' if shutil.which('node') else '',
                'max_seconds':MAX_SECONDS,'max_audio_mb':MAX_BYTES//1024//1024}
    def captions(self,video_id,language,report,cancel):
        try:
            import requests
            from youtube_transcript_api import YouTubeTranscriptApi
            from youtube_transcript_api import _errors
        except ImportError:raise ImportProblem('DEPENDENCY_MISSING','유튜브 처리 도구가 없습니다. python -m pip install -r server/requirements-ai.txt을 실행하고 서버를 다시 시작하세요.')
        class Session(requests.Session):
            def request(self,*args,**kwargs):kwargs.setdefault('timeout',(5,15));return super().request(*args,**kwargs)
        report('유튜브 자막 확인 중',10)
        if cancel():raise Cancelled()
        try:
            with Session() as session:
                options=list(YouTubeTranscriptApi(http_client=session).list(video_id))
                if not options:raise NoCaptions()
                preference=[language,'ko','en'] if language!='auto' else ['ko','en']
                def rank(t):
                    code=t.language_code.split('-')[0]
                    return (preference.index(code) if code in preference else 99,bool(t.is_generated))
                chosen=sorted(options,key=rank)[0]
                fetched=chosen.fetch()
                rows=normalize_segments(fetched.to_raw_data())
                if cancel():raise Cancelled()
                return {'segments':rows,'language':fetched.language_code,'generated':bool(fetched.is_generated),
                        'method':'auto_caption' if fetched.is_generated else 'manual_caption',
                        'duration':max(r['end'] for r in rows),'title':'YouTube 대본 · '+video_id,'author':'','date':''}
        except (NoCaptions,ImportProblem,Cancelled):raise
        except (_errors.TranscriptsDisabled,_errors.NoTranscriptFound):raise NoCaptions()
        except (_errors.RequestBlocked,_errors.IpBlocked):
            raise ImportProblem('YOUTUBE_BLOCKED','YouTube가 이 네트워크의 자동 요청을 차단했습니다. 여기서 수집을 중단합니다. 병원에서 받은 자막·원본 음성을 사용하거나 일반 PC 네트워크에서 확인하세요.')
        except (_errors.VideoUnavailable,_errors.VideoUnplayable,_errors.AgeRestricted):
            raise ImportProblem('VIDEO_UNAVAILABLE','공개 접근 가능한 영상인지 확인하세요. 비공개·로그인·연령 확인이 필요한 영상은 처리하지 않습니다.')
        except Exception:
            raise ImportProblem('CAPTIONS_FAILED','YouTube 자막 요청에 실패했습니다. 네트워크·영상 접근 상태를 확인하거나 음성 전사 방식을 사용하세요.')
    def metadata(self,url,source):
        # Optional metadata only. Its failure never invents a title or blocks a valid transcript.
        try:
            import requests
            r=requests.get('https://www.youtube.com/oembed',params={'url':url,'format':'json'},timeout=(5,10),allow_redirects=False)
            if r.status_code==200:
                data=r.json();source['title']=str(data.get('title') or source['title'])[:120];source['author']=str(data.get('author_name') or '')[:120]
        except Exception:pass
        return source
    def audio(self,url,language,report,cancel):
        try:import yt_dlp
        except ImportError:raise ImportProblem('DEPENDENCY_MISSING','음성 수집 도구가 없습니다. python -m pip install -r server/requirements-ai.txt을 실행하세요.')
        class QuietLogger:
            def debug(self,*args):pass
            def warning(self,*args):pass
            def error(self,*args):pass
        def check_info(info,*,incomplete=False):
            if cancel():raise Cancelled()
            if info.get('is_live') or info.get('live_status') in {'is_live','is_upcoming','post_live'}:
                raise ImportProblem('LIVE_UNSUPPORTED','진행 중인 라이브·예정 영상은 지원하지 않습니다. 업로드가 완료된 영상을 선택하세요.')
            duration=info.get('duration')
            if duration and duration>MAX_SECONDS:raise ImportProblem('TOO_LONG','30분 이하 영상을 선택하세요.')
            if not incomplete and not duration:raise ImportProblem('UNKNOWN_DURATION','영상 길이를 확인하지 못해 다운로드를 중단했습니다.')
            if info.get('availability') in {'private','premium_only','subscriber_only','needs_auth'}:
                raise ImportProblem('VIDEO_UNAVAILABLE','로그인 없이 접근할 수 있는 영상을 선택하세요.')
        def hook(info):
            if cancel():raise Cancelled()
            size=info.get('downloaded_bytes',0)
            if size>MAX_BYTES:raise ImportProblem('AUDIO_TOO_LARGE','음성 파일이 120MB를 넘어서 중단했습니다.')
            total=info.get('total_bytes') or info.get('total_bytes_estimate') or 0
            if total>MAX_BYTES:raise ImportProblem('AUDIO_TOO_LARGE','120MB 이하의 음성만 처리할 수 있습니다.')
            report('영상 음성 받는 중',20+int(20*min(1,size/total)) if total else 20)
        runtimes={name:{'path':shutil.which(name)} for name in ('deno','node') if shutil.which(name)}
        with tempfile.TemporaryDirectory(prefix='clinicworkspace-youtube-') as td:
            opts={'quiet':True,'no_warnings':True,'logger':QuietLogger(),'noplaylist':True,'playlistend':1,
                  'format':'bestaudio[ext=m4a]/bestaudio','outtmpl':str(Path(td)/'audio.%(ext)s'),
                  'max_filesize':MAX_BYTES,'socket_timeout':15,'retries':0,'fragment_retries':0,
                  'skip_unavailable_fragments':False,'progress_hooks':[hook],'match_filter':check_info,
                  'js_runtimes':runtimes,'remote_components':set(),'cachedir':False}
            report('영상 음성 확인 중',15)
            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info=ydl.extract_info(url,download=True)
                    if not info or info.get('_type') in {'playlist','multi_video'}:raise ImportProblem('NOT_SINGLE_VIDEO','영상 한 개의 주소를 입력하세요.')
                    path=Path(ydl.prepare_filename(info))
                candidates=[p for p in Path(td).iterdir() if p.is_file() and p.suffix not in {'.part','.ytdl','.json'}]
                if not path.is_file():path=candidates[0] if len(candidates)==1 else path
                if not path.is_file():raise ImportProblem('AUDIO_UNAVAILABLE','다운로드된 음성을 찾지 못했습니다.')
                if path.stat().st_size>MAX_BYTES:raise ImportProblem('AUDIO_TOO_LARGE','음성 파일이 120MB를 넘습니다.')
                if cancel():raise Cancelled()
                import av
                with av.open(str(path)) as media:
                    duration=float(media.duration/av.time_base) if media.duration else float(info.get('duration') or 0)
                if duration<=0 or duration>MAX_SECONDS+1:raise ImportProblem('TOO_LONG','30분 이하의 음성만 전사할 수 있습니다.')
                report('Whisper 음성 전사 중 · 첫 실행은 모델 준비에 시간이 걸립니다',45)
                result=self.ai.transcribe(path,language=language,on_progress=lambda p:report('Whisper 음성 전사 중',45+int(45*p)),should_cancel=cancel)
                rows=normalize_segments(result['segments'])
                uploaded=str(info.get('upload_date') or '')
                return {'segments':rows,'language':result['language'],'generated':True,'method':'whisper','duration':duration,
                        'title':str(info.get('title') or 'YouTube 음성 대본')[:120],
                        'author':str(info.get('channel') or info.get('uploader') or '')[:120],
                        'date':f'{uploaded[:4]}-{uploaded[4:6]}-{uploaded[6:]}' if re.fullmatch(r'\d{8}',uploaded) else ''}
            except (Cancelled,TranscriptionCancelled):raise Cancelled()
            except ImportProblem:raise
            except AIUnavailable:
                if cancel():raise Cancelled()
                raise ImportProblem('TRANSCRIPTION_FAILED','Whisper 전사에 실패했습니다. python server/prepare_models.py과 모델 설정을 확인하세요.')
            except Exception as exc:
                if cancel():raise Cancelled()
                message=str(exc).lower()
                if any(s in message for s in ('sign in','confirm you','bot','429','403','captcha')):
                    raise ImportProblem('YOUTUBE_BLOCKED','YouTube가 음성 요청을 차단하거나 로그인을 요구했습니다. 이 영상의 수집을 중단합니다.')
                if not runtimes:
                    raise ImportProblem('JS_RUNTIME_MISSING','음성 수집에 필요한 실행 도구가 없습니다. 공식 Node.js 22 이상 또는 Deno 2.3 이상을 설치하고 서버를 다시 시작하세요.')
                raise ImportProblem('DOWNLOAD_FAILED','영상 음성을 받지 못했습니다. 영상 접근 상태·네트워크와 유튜브 처리 도구를 확인하세요.')
    def fetch(self,url,mode,language,report,cancel):
        vid,url=canonical_video_url(url)
        if mode!='audio':
            try:
                source=self.captions(vid,language,report,cancel)
                report('영상 제목과 출처 확인 중',80)
                return self.metadata(url,source)
            except NoCaptions:
                if mode=='captions':raise
                report('자막 없음 · 음성 전사로 전환',15)
        return self.audio(url,language,report,cancel)


class YouTubeJobs:
    def __init__(self,store,ai,importer=None):
        self.store=store;self.importer=importer or YouTubeImporter(ai)
        self.stop_event=threading.Event();self.wake=threading.Event();self.lock=threading.Lock();self.thread=None
    def start(self):
        if getattr(self, "cloud", False):return
        with self.lock:
            if self.thread and self.thread.is_alive():return
            with self.store.connect() as c:
                c.execute("UPDATE youtube_jobs SET status='failed',stage='서버 중단',error_code='SERVER_INTERRUPTED',error_message='서버가 재시작되어 작업이 중단됐습니다. 링크를 다시 등록하세요.',updated=? WHERE status='running'",(time.time(),))
            self.stop_event.clear();self.thread=threading.Thread(target=self.work,name='youtube-import',daemon=True);self.thread.start()
    def close(self):
        self.stop_event.set();self.wake.set()
        if self.thread:self.thread.join(timeout=.2)
    def is_cancelled(self,job):
        if self.stop_event.is_set():return True
        with self.store.connect() as c:
            row=c.execute('SELECT cancel_requested,status FROM youtube_jobs WHERE id=?',(job['id'],)).fetchone()
            owner=c.execute('SELECT active,role,revision FROM users WHERE id=?',(job['owner_id'],)).fetchone()
        return not row or row['cancel_requested'] or row['status']!='running' or not owner or not owner['active'] or owner['role'] not in {'admin','doctor'} or owner['revision']!=job['owner_revision']
    def work(self):
        while not self.stop_event.is_set():
            job=None
            with self.store.connect() as c:
                c.execute('BEGIN IMMEDIATE')
                row=c.execute("SELECT * FROM youtube_jobs WHERE status='queued' ORDER BY created LIMIT 1").fetchone()
                if row:
                    job=dict(row);c.execute("UPDATE youtube_jobs SET status='running',stage='처리 시작',updated=? WHERE id=?",(time.time(),job['id']))
            if job:self.process(job)
            else:self.wake.wait(1);self.wake.clear()
    def process(self,job):
        last=[0,'']
        def report(stage,progress):
            if self.is_cancelled(job):raise Cancelled()
            now=time.monotonic()
            if stage==last[1] and now-last[0]<.5:return
            last[:]=[now,stage]
            with self.store.connect() as c:c.execute("UPDATE youtube_jobs SET stage=?,progress=?,updated=? WHERE id=? AND status='running'",(stage,max(0,min(99,int(progress))),time.time(),job['id']))
        try:
            if self.is_cancelled(job):raise Cancelled()
            source=self.importer.fetch(job['url'],job['mode'],job['language'],report,lambda:self.is_cancelled(job))
            rows,content=render_transcript(source,job['url'])
            if self.is_cancelled(job):raise Cancelled()
            report('대본을 미승인 자료로 저장 중',95)
            did='DOC-'+uuid.uuid4().hex[:10];title=(job['title'] or source['title']).strip()[:120]
            with self.store.connect() as c:
                c.execute('BEGIN IMMEDIATE')
                current=c.execute('SELECT cancel_requested,status FROM youtube_jobs WHERE id=?',(job['id'],)).fetchone()
                owner=c.execute('SELECT active,role,revision FROM users WHERE id=?',(job['owner_id'],)).fetchone()
                if self.stop_event.is_set() or not current or current['cancel_requested'] or current['status']!='running' or not owner or not owner['active'] or owner['role'] not in {'admin','doctor'} or owner['revision']!=job['owner_revision']:raise Cancelled()
                c.execute('INSERT INTO documents(id,title,content,version,approved,roles,kind,source_url,updated,category,tags,summary,owner_id,source_author,source_date) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                          (did,title,content,1,0,job['roles'],'youtube_transcript',job['url'],time.time(),'유튜브 대본',json.dumps(['자동 수집','검토 필요'],ensure_ascii=False),METHODS[source['method']]+' · 원문 검토 후 위키 검색 사용 승인 · 검색 준비 자동',job['owner_id'],source['author'],source['date']))
                doc=dict(c.execute('SELECT * FROM documents WHERE id=?',(did,)).fetchone())
                c.execute('INSERT INTO document_versions VALUES(?,?,?,?,?,?)',(did,1,json.dumps(doc,ensure_ascii=False),job['owner_id'],'영상 링크에서 자동 수집 · 관리자 검토 전',time.time()))
                c.execute('INSERT INTO youtube_sources VALUES(?,?,?,?,?,?,?,?)',(did,job['video_id'],source['method'],source['language'],int(source['generated']),source['duration'],time.time(),json.dumps(rows,ensure_ascii=False)))
                c.execute("UPDATE youtube_jobs SET status='succeeded',stage='대본 저장 완료 · 검토·승인 필요',progress=100,doc_id=?,title=?,updated=? WHERE id=?",(did,title,time.time(),job['id']))
                self.store.audit(job['owner_id'],'youtube_imported',did,detail={'job_id':job['id'],'method':source['method'],'version':1},connection=c)
        except (Cancelled,TranscriptionCancelled):self.finish(job,'cancelled','CANCELLED','작업이 취소되었거나 계정 상태가 변경되었습니다. 대본을 저장하지 않았습니다.')
        except ImportProblem as exc:self.finish(job,'failed',exc.code,str(exc))
        except Exception:self.finish(job,'failed','IMPORT_FAILED','대본 처리에 실패했습니다. 처리 도구·네트워크·모델 상태를 확인하세요.')
    def finish(self,job,status,code,message):
        with self.store.connect() as c:
            c.execute("UPDATE youtube_jobs SET status=?,stage=?,error_code=?,error_message=?,updated=? WHERE id=? AND status='running'",(status,'작업 취소' if status=='cancelled' else '작업 실패',code,message,time.time(),job['id']))
            self.store.audit(job['owner_id'],'youtube_cancelled' if status=='cancelled' else 'youtube_failed',job['id'],result='denied' if status=='cancelled' else 'error',detail={'code':code},connection=c)


class YouTubeRequest(BaseModel):
    url:str=Field(min_length=1,max_length=1000)
    title:str=Field(default='',max_length=120)
    mode:Literal['auto','captions','audio']='auto'
    language:Literal['ko','en','auto']='ko'
    roles:list[str]=Field(default_factory=lambda:['admin','doctor','staff'])


def install_youtube(app,store,current,editor,jobs):
    def visible(row,user):return user['role']=='admin' or row['owner_id']==user['id']
    def can_open(row,user):
        return user['role']=='admin' or (not row['doc_archived'] and user['role'] in json.loads(row['doc_roles'] or '[]'))
    def public(row,user):
        result={k:v for k,v in dict(row).items() if k not in {'roles','owner_revision','doc_roles','doc_archived'}}
        if row['doc_id'] and not can_open(row,user):
            result['doc_id']=None;result['title']='열람 범위가 변경된 자료'
        return result
    select_jobs='SELECT j.*,d.roles AS doc_roles,d.archived AS doc_archived FROM youtube_jobs j LEFT JOIN documents d ON d.id=j.doc_id '
    @app.get('/api/youtube/status')
    async def status(user=Depends(editor)):
        if getattr(jobs, 'cloud', False):
            try:return {**await jobs.ai.request('youtube_status'), 'cloud':True, 'connected':True}
            except AIUnavailable:
                return {'captions':False,'audio':False,'runtime':'','cloud':True,'connected':False,
                        'max_seconds':MAX_SECONDS,'max_audio_mb':MAX_BYTES//1024//1024,
                        'message':'로컬 AI PC를 연결하면 영상 자료를 처리할 수 있습니다.'}
        return YouTubeImporter.dependencies()
    @app.get('/api/youtube/jobs')
    async def list_jobs(user=Depends(editor)):
        if getattr(jobs, 'cloud', False):
            from cloud_ai import process_cloud_job
            await process_cloud_job(jobs, 'youtube_jobs', user)
        with store.connect() as c:
            rows=c.execute(select_jobs+('' if user['role']=='admin' else 'WHERE j.owner_id=? ')+'ORDER BY j.created DESC LIMIT 30',() if user['role']=='admin' else (user['id'],)).fetchall()
        return {'jobs':[public(r,user) for r in rows]}
    @app.post('/api/youtube/jobs',status_code=202)
    async def create_job(body:YouTubeRequest,user=Depends(editor)):
        try:vid,url=canonical_video_url(body.url)
        except ImportProblem as exc:raise HTTPException(400,str(exc))
        roles=sorted(set(body.roles))
        if not roles or not set(roles)<={'admin','doctor','staff'} or user['role']!='admin' and user['role'] not in roles:raise HTTPException(400,'열람 역할을 확인하세요.')
        jobs.start()
        with store.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            owner=c.execute('SELECT revision FROM users WHERE id=?',(user['id'],)).fetchone()
            existing=c.execute(select_jobs+"WHERE j.owner_id=? AND j.video_id=? AND (j.status IN ('queued','running') OR (j.status='succeeded' AND d.archived=0)) ORDER BY j.created DESC LIMIT 1",(user['id'],vid)).fetchone()
            if existing and existing['owner_revision']==owner['revision']:
                return {'job':public(existing,user),'reused':True}
            if c.execute("SELECT COUNT(*) FROM youtube_jobs WHERE owner_id=? AND status IN ('queued','running')",(user['id'],)).fetchone()[0]>=3:raise HTTPException(429,'대기·처리 중인 영상은 계정당 최대 3개입니다.')
            jid='YT-'+uuid.uuid4().hex[:12];now=time.time()
            c.execute('INSERT INTO youtube_jobs(id,owner_id,owner_revision,video_id,url,title,mode,language,roles,status,stage,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',(jid,user['id'],owner['revision'],vid,url,body.title.strip(),body.mode,body.language,json.dumps(roles),'queued','처리 대기',now,now))
            self_job=dict(c.execute('SELECT * FROM youtube_jobs WHERE id=?',(jid,)).fetchone())
            store.audit(user['id'],'youtube_queued',jid,detail={'video_id':vid,'mode':body.mode},connection=c)
        jobs.wake.set();return {'job':public(self_job,user),'reused':False}
    @app.post('/api/youtube/jobs/{jid}/cancel')
    async def cancel_job(jid:str,user=Depends(editor)):
        with store.connect() as c:
            c.execute('BEGIN IMMEDIATE');row=c.execute('SELECT * FROM youtube_jobs WHERE id=?',(jid,)).fetchone()
            if not row or not visible(row,user):raise HTTPException(404,'작업이 없습니다.')
            if row['status'] not in {'queued','running'}:raise HTTPException(409,'이미 완료되거나 중단된 작업입니다.')
            c.execute("UPDATE youtube_jobs SET cancel_requested=1,status=CASE WHEN status='queued' THEN 'cancelled' ELSE status END,stage='취소 요청 · 현재 단계 종료 대기',updated=? WHERE id=?",(time.time(),jid))
            store.audit(user['id'],'youtube_cancel_requested',jid,connection=c)
        return {'ok':True}
