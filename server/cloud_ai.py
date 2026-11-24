"""Cloud data stays in Postgres; only bounded inference inputs reach the local PC."""
import asyncio
import base64
import json
import os
import time
import tempfile
import wave
from contextlib import contextmanager
from pathlib import Path

import httpx
import numpy as np

from ai_services import AIUnavailable, BlogGenerationError

OFFLINE = '로컬 AI PC가 꺼져 있거나 연결되지 않았습니다. 일반 업무는 계속 이용할 수 있습니다.'


@contextmanager
def inference_audio_parts(path):
    """Keep existing PC connectors' 5-minute/25MB bounds for each inference input.

    The original recording remains one upload/job. Long inputs are losslessly
    decoded to 16kHz mono PCM in bounded parts; offsets preserve transcript time.
    Temporary parts are removed on success or failure.
    """
    from recording import validate_audio
    duration=validate_audio(path)
    if duration<=280 and Path(path).stat().st_size<=25*1024*1024:
        yield [(Path(path),0.0)]
        return
    import av
    rate=16000;part_bytes=280*rate*2
    with tempfile.TemporaryDirectory(prefix='clinicworkspace-audio-') as directory:
        parts=[];writer=None;written=0;total=0
        def write(pcm):
            nonlocal writer,written,total
            while pcm:
                if writer is None:
                    target=Path(directory)/f'part-{len(parts):03d}.wav'
                    writer=wave.open(str(target),'wb');writer.setnchannels(1);writer.setsampwidth(2);writer.setframerate(rate)
                    parts.append((target,total/(rate*2)));written=0
                count=min(len(pcm),part_bytes-written)
                writer.writeframesraw(pcm[:count]);pcm=pcm[count:];written+=count;total+=count
                if written==part_bytes:writer.close();writer=None
        try:
            with av.open(str(path)) as media:
                resampler=av.AudioResampler(format='s16',layout='mono',rate=rate)
                for frame in media.decode(audio=0):
                    for converted in resampler.resample(frame):write(converted.to_ndarray().tobytes())
                for converted in resampler.resample(None):write(converted.to_ndarray().tobytes())
        finally:
            if writer is not None:writer.close()
        if not parts:raise AIUnavailable('녹음에서 음성 구간을 읽지 못했습니다.')
        yield parts


class CloudAI:
    def __init__(self, settings, endpoint=None, secret=None):
        self.settings = settings
        self.chat_model, self.embedding_model = settings['chat_model'], settings['embedding_model']
        self.index_lock, self.llm_lock = asyncio.Lock(), asyncio.Semaphore(1)
        self.endpoint = endpoint or os.environ['CLINICWORKSPACE_AI_BRIDGE'].rstrip('/')
        self.secret = secret or os.environ['CLOUD_BACKEND_KEY']
        self._article_capability = (False, 0)

    async def request(self, method, arguments=None):
        try:
            async with httpx.AsyncClient(timeout=10 if method=="status" else 250, follow_redirects=False) as client:
                r = await client.post(self.endpoint + '/__cloud/infer', json={'method': method, 'arguments': arguments or {}},
                                      headers={'X-ClinicWorkspace-Backend': self.secret})
            if not r.is_success:
                data = r.json()
                raise BlogGenerationError(data.get('detail', OFFLINE), data.get('code', 'AI_OFFLINE'), r.status_code)
            return r.json()
        except AIUnavailable:
            raise
        except Exception as error:
            raise AIUnavailable(OFFLINE) from error

    async def status(self):
        try:
            return {**await self.request('status'), 'cloud': True}
        except AIUnavailable as error:
            return {'ollama': False, 'chat_ready': False, 'embedding_ready': False, 'cloud': True,
                    'chat_model': self.chat_model, 'embedding_model': self.embedding_model,
                    'whisper_model': self.settings['whisper_model'], 'models': [], 'message': str(error),
                    'code':getattr(error,'code','AI_OFFLINE')}

    async def embedding_digest(self):
        return (await self.request('embedding_digest'))['digest']

    async def embed(self, texts):
        vectors=[]
        for start in range(0,len(texts),32):
            vectors.extend((await self.request('embed', {'texts': texts[start:start+32]}))['vectors'])
        return np.asarray(vectors, dtype=np.float32)

    async def generate(self, question, sources):
        return await self.request('generate', {'question': question, 'sources': sources})

    async def generate_blog(self, question, sources, feedback=''):
        if feedback.startswith(('포스팅 작성 기준:', '본문 확장:')):
            native,expires=self._article_capability
            if expires<time.time():
                native=bool((await self.status()).get('blog_article_writer'))
                self._article_capability=(native,time.time()+60)
            if not native:
                # Older PCs label feedback as a validation failure and append an
                # instruction to write a SHORT answer. Put the editorial direction
                # in the supported topic field instead, without that retry prompt.
                question=question+'\n\n블로그 편집 방향: '+feedback
                feedback=''
        return await self.request('generate_blog', {'question': question, 'sources': sources, 'feedback': feedback})

    def transcribe(self, path, language='ko', **kwargs):
        async def transfer(part):
            aid=(await self.request('audio_begin',{'suffix':part.suffix,'language':language}))['id']
            try:
                offset=0
                with part.open('rb') as f:
                    while raw:=f.read(1024*1024):
                        await self.request('audio_part',{'id':aid,'offset':offset,'data':base64.b64encode(raw).decode()})
                        offset+=len(raw)
                return await self.request('audio_finish',{'id':aid})
            finally:
                try:await self.request('audio_abort',{'id':aid})
                except AIUnavailable:pass
        async def collect(parts):
            text=[];segments=[];detected=language
            for part,offset in parts:
                result=await transfer(part)
                if result.get('text','').strip():text.append(result['text'].strip())
                detected=result.get('language',detected)
                segments.extend({**s,'start':s['start']+offset,'end':s['end']+offset} for s in result.get('segments',[]))
            return {'text':'\n\n'.join(text),'segments':segments,'language':detected}
        with inference_audio_parts(path) as parts:
            return asyncio.run(collect(parts))


class RemoteYouTubeImporter:
    def __init__(self, ai):
        self.ai = ai

    def fetch(self, url, mode, language, report, cancel):
        report('로컬 PC에서 영상 자료 처리 중', 25)
        return asyncio.run(self.ai.request('youtube', {'url': url, 'mode': mode, 'language': language}))


async def process_cloud_job(jobs, table, owner, today=None):
    """Polling drives one durable queued job; no untracked serverless background thread.

    Claims live in Postgres. Concurrent polls cannot claim the same job. A crashed
    invocation becomes failed after five minutes, with an explicit retry message.
    """
    from fastapi.concurrency import run_in_threadpool
    stamp = time.time()
    with jobs.store.connect(shared=True) as c:
        query = f"SELECT * FROM {table} WHERE (status='queued' OR (status='running' AND updated<?))" + ('' if owner['role'] == 'admin' else ' AND owner_id=?') + ' ORDER BY created LIMIT 1'
        row = c.execute(query, (stamp-300,) if owner['role'] == 'admin' else (stamp-300,owner['id'])).fetchone()
    if not row:
        return
    if row['status']=='running':
        with jobs.store.connect() as c:
            c.execute(f"UPDATE {table} SET status='failed',stage='처리 시간 초과',updated=? WHERE id=? AND status='running' AND updated<?",
                      (stamp,row['id'],stamp-300))
        return
    state = await jobs.ai.status()
    if not state.get('local_ai_rpc'):
        with jobs.store.connect() as c:
            c.execute(f"UPDATE {table} SET stage='로컬 AI PC 연결 대기' WHERE id=? AND status='queued' AND stage<>'로컬 AI PC 연결 대기'",(row['id'],))
        return  # Saved audio/job remains queued; browsing ordinary data stays available.
    with jobs.store.connect() as c:
        row = c.execute(f"SELECT * FROM {table} WHERE id=? AND status='queued'", (row['id'],)).fetchone()
        if not row:
            return
        c.execute(f"UPDATE {table} SET status='running',stage='로컬 PC AI 처리 중',updated=? WHERE id=?", (time.time(), row['id']))
        job = dict(row)
    if table == 'recording_jobs':
        await run_in_threadpool(jobs.store.load_file, 'recording_uploads', job['audio_path'])
    await run_in_threadpool(jobs.process, job)
