"""Clinic workflow demo. Never load real patient data into this prototype."""
import asyncio
import hashlib
import io
import json
import logging
import os
import re
import secrets
import socket
import sqlite3
import tempfile
import time
import uuid
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode, urlsplit

import numpy as np
import qrcode
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageDraw, ImageOps, UnidentifiedImageError
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from ai_services import AIUnavailable, LocalAI, split_document, BLOG_ENGINE_VERSION
from storage import Store, token_hash
from intranet import install_intranet
from youtube_import import YouTubeJobs, install_youtube
from recording import RecordingJobs, install_recording
from geo import install_geo, GEO_ENGINE_VERSION

HERE = Path(__file__).resolve().parent
CLINICAL = {"admin", "doctor"}
APP_VERSION = "2026.11.16-geo-fix3"
# This Korean demo uses current dates only. A fixed KST offset avoids relying
# on an OS IANA database, which is absent in a normal Windows Python install.
KST = timezone(timedelta(hours=9), "KST")
logger = logging.getLogger("clinicworkspace")


def today_in_korea(moment=None):
    moment = moment or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        raise ValueError("An aware datetime is required")
    return moment.astimezone(KST).date().isoformat()


def settings_from_env():
    config = {}
    path = HERE / "config.json"
    if path.exists():
        config = json.loads(path.read_text(encoding="utf-8"))
    defaults = {"ollama_url": "http://127.0.0.1:11434", "chat_model": "qwen3:4b",
                "embedding_model": "bge-m3", "whisper_model": "small", "whisper_device": "cpu",
                "whisper_compute": "int8", "data_dir": str(HERE / "data"),
                "model_cache": str(HERE / "models"), "public_base_url": "", "secure_cookie": False,
                "retrieval_min_score": .40}
    defaults.update(config)
    defaults.setdefault("database_url", "")
    for k in defaults:
        value = os.environ.get("CLINICWORKSPACE_" + k.upper())
        if value is not None:
            defaults[k] = (value.lower() == "true") if k == "secure_cookie" else float(value) if k == "retrieval_min_score" else value
    return defaults


def make_sample():
    im = Image.new("RGB", (540, 560), "#e7eadf")
    d = ImageDraw.Draw(im)
    d.ellipse((100, 35, 440, 465), fill="#d5dbc9", outline="#a9b79d", width=2)
    d.line((270, 60, 270, 440), fill="#adb8a0", width=2)
    d.line((105, 250, 435, 250), fill="#adb8a0", width=2)
    d.text((95, 500), "SYNTHETIC DIAGRAM / NOT A PATIENT", fill="#3e5641")
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def create_app(settings=None, ai=None, youtube_importer=None):
    cfg = settings or settings_from_env()
    store = Store(cfg["data_dir"], cfg.get("database_url"), cfg.get("schema"))
    from security import harden_owner, consume_limits, client_key
    if cfg.get("owner_password"):
        harden_owner(store, cfg["owner_password"])
    if cfg.get("cloud"):
        from cloud_files import CloudFiles
        store.cloud_files = CloudFiles(store.directory, cfg.get("schema"), store=store)
    if ai is None and cfg.get("cloud"):
        from cloud_ai import CloudAI, RemoteYouTubeImporter
        ai = CloudAI(cfg)
        youtube_importer = RemoteYouTubeImporter(ai)
    ai = ai or LocalAI(cfg)
    youtube_jobs = YouTubeJobs(store, ai, youtube_importer)
    youtube_jobs.ai = ai
    recording_jobs = RecordingJobs(store, ai, today_in_korea)
    if cfg.get("cloud"):
        youtube_jobs.cloud = recording_jobs.cloud = True
    @asynccontextmanager
    async def lifespan(app):
        youtube_jobs.start()
        recording_jobs.start()
        try:
            yield
        finally:
            youtube_jobs.close()
            recording_jobs.close()
    app = FastAPI(title="ClinicWorkspace workflow demo", docs_url=None, redoc_url=None, lifespan=lifespan)
    instance_id = uuid.uuid4().hex
    install_id = hashlib.sha256(str(HERE.resolve()).casefold().encode()).hexdigest()[:20]
    app.state.youtube_jobs = youtube_jobs
    app.state.recording_jobs = recording_jobs
    app.state.store, app.state.ai, app.state.cfg = store, ai, cfg
    subscribers = defaultdict(set)
    sample = make_sample()
    # Fictional baseline photos, same diagram, no treatment outcome illustrated.
    for pid in ("DEMO-101", "DEMO-103"):
        with store.connect() as c:
            if not c.execute("SELECT 1 FROM photos WHERE patient_id=? AND visit_date='2026-10-24'", (pid,)).fetchone():
                name = f"baseline-{pid}.png"
                store.save_file("photos", name, sample)
                c.execute("INSERT INTO photos(patient_id,visit_date,site,filename,created) VALUES(?,?,?,?,?)",
                          (pid, "2026-10-24", "정수리", name, time.time()))

    def today():
        return today_in_korea()

    @app.exception_handler(AIUnavailable)
    async def unavailable(request, exc):
        return JSONResponse({"detail": str(exc), "code": getattr(exc, "code", "AI_UNAVAILABLE")}, status_code=getattr(exc, "status_code", 503))

    @app.middleware("http")
    async def headers(request, call_next):
        perf_started=time.perf_counter()
        # Browser writes must be same-origin. Capture uses a scoped bearer token instead of cookies.
        if request.method in {"POST", "PUT", "DELETE", "PATCH"}:
            origin = request.headers.get("origin")
            host = request.headers.get("host", "")
            if origin and urlsplit(origin).netloc != host:
                return JSONResponse({"detail": "다른 출처의 요청은 허용하지 않습니다."}, status_code=403)
        try:
            if request.url.path.startswith('/api/'):
                request.state.authenticated_user = await run_in_threadpool(store.session, request.cookies.get('gm_session'))
                if not request.state.authenticated_user:
                    consume_limits(store, [('anonymous:'+client_key(request), 120, 60)])
            response = await call_next(request)
        except HTTPException as error:
            response = JSONResponse({'detail': error.detail}, status_code=error.status_code, headers=error.headers)
        except Exception as error:
            error_id = uuid.uuid4().hex[:12]
            cause=error.__cause__ or error.__context__ or error
            diag=getattr(cause,'diag',None)
            logger.error('Server error summary [%s] method=%s type=%s cause=%s code=%s table=%s constraint=%s',error_id,request.method,type(error).__name__,type(cause).__name__,getattr(cause,'sqlstate',''),getattr(diag,'table_name',''),getattr(diag,'constraint_name',''))
            # Log the traceback locally; never send it, recordings, cookies or
            # capture-token URLs to the browser in an error response.
            logger.exception("Server error [%s] %s", error_id, request.method)
            response = JSONResponse({
                "detail": "서버 처리 중 오류가 발생했습니다. 실행 창의 오류 번호를 확인하세요.",
                "code": "INTERNAL_ERROR", "error_id": error_id,
            }, status_code=500)
        route = request.scope.get("route")
        if response.status_code in {401, 403} or response.status_code == 404 and request.url.path.startswith("/api/documents/"):
            store.audit(getattr(request.state, "actor", "anonymous"), "access_denied",
                        request.path_params.get("did", getattr(route, "path", "api")), result="denied",
                        detail={"method": request.method, "status": response.status_code, "route": getattr(route, "path", "api")})
        response.headers["Server-Timing"] = f'app;dur={(time.perf_counter()-perf_started)*1000:.1f}'
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-ClinicWorkspace-Version"] = APP_VERSION
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        if request.url.path == "/" or request.url.path.startswith(("/api/", "/capture", "/record", "/static/")):
            response.headers["Cache-Control"] = "no-store"
        return response

    def current(request: Request):
        user = request.state.authenticated_user if hasattr(request.state, "authenticated_user") else store.session(request.cookies.get("gm_session"))
        if not user:
            raise HTTPException(401, "로그인이 필요합니다.")
        if request.method not in {"GET", "HEAD"}:
            if not secrets.compare_digest(request.headers.get("X-CSRF-Token", ""), user["csrf"]):
                raise HTTPException(403, "요청 검증에 실패했습니다. 다시 로그인하세요.")
        request.state.actor = user["id"]
        if request.method not in {"GET", "HEAD"}:
            consume_limits(store, [("write:"+user["id"], 120, 60)])
        return user

    def clinical(user=Depends(current)):
        if user["role"] not in CLINICAL:
            raise HTTPException(403, "진료 자료는 의료진·관리자 계정만 접근할 수 있습니다.")
        return user

    def manager(user=Depends(current)):
        if user["role"] != "admin":
            raise HTTPException(403, "관리자 권한이 필요합니다.")
        return user

    def editor(user=Depends(current)):
        if user["role"] not in CLINICAL:
            raise HTTPException(403, "문서 등록·승인은 의료진·관리자만 할 수 있습니다.")
        return user

    @app.post('/api/admin/reset-clinical')
    async def reset_clinical(body:dict,user=Depends(manager)):
        if body.get('confirm')!='RESET_CLINICAL_DEMO':
            raise HTTPException(400,'초기화할 항목을 확인하세요.')
        with store.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            if c.execute("SELECT COUNT(*) FROM recording_jobs WHERE status IN ('queued','running')").fetchone()[0]:
                raise HTTPException(409,'녹음 전사 처리 중입니다. 완료된 뒤 다시 초기화하세요.')
            files=[r['filename'] for r in c.execute('SELECT filename FROM photos')]
            audio=[r['audio_path'] for r in c.execute('SELECT audio_path FROM recording_jobs')]
            counts={table:c.execute('SELECT COUNT(*) FROM '+table).fetchone()[0] for table in ('photos','notes','transcripts','recording_jobs','crm_chart_entries')}
            for table in ('crm_chart_entries','recording_jobs','recording_links','capture_tokens','notes','transcripts','photos','crm_modes'):
                c.execute('DELETE FROM '+table)
            for pid in ('DEMO-101','DEMO-103'):
                name=f'baseline-{pid}.png'
                store.save_file('photos', name, sample)
                c.execute('INSERT INTO photos(patient_id,visit_date,site,filename,created) VALUES(?,?,?,?,?)',(pid,'2026-10-24','정수리',name,time.time()))
            store.audit(user['id'],'reset_clinical_demo','all-demo-patients',detail=counts,connection=c)
        for name in files:
            if name not in {'baseline-DEMO-101.png','baseline-DEMO-103.png'}:
                store.delete_file('photos', name)
        for name in audio:
            store.delete_file('recording_uploads', name)
        for pid in ('DEMO-101','DEMO-102','DEMO-103'):
            await publish(pid,{'reset':True})
        return {'ok':True,'deleted':counts,'baseline_photos':2}

    def require_patient(pid):
        with store.connect(shared=True) as c:
            p = c.execute("SELECT * FROM patients WHERE id=?", (pid,)).fetchone()
        if not p:
            raise HTTPException(404, "가상환자를 찾지 못했습니다.")
        return dict(p)

    install_recording(app, store, clinical, require_patient, today, recording_jobs)

    @app.get('/mobile', response_class=HTMLResponse)
    def mobile_workspace():
        return FileResponse(HERE/'static'/'mobile.html',headers={'Cache-Control':'no-store'})

    @app.get('/record', response_class=HTMLResponse)
    def tablet_recorder():
        return FileResponse(HERE/'static'/'record.html')

    @app.get('/ipad-check', response_class=HTMLResponse)
    def ipad_connection_check():
        return FileResponse(HERE/'static'/'ipad-check.html',headers={'Cache-Control':'no-store'})

    @app.get('/api/connection')
    def connection_settings(user=Depends(clinical)):
        return {'public_base_url':cfg['public_base_url']}

    @app.get("/", response_class=HTMLResponse)
    def index():
        return FileResponse(HERE / "static" / "index.html")

    @app.get("/capture", response_class=HTMLResponse)
    def capture_page():
        return FileResponse(HERE / "static" / "capture.html")

    @app.get("/api/health")
    def health():
        return {"ok": True, "demo": True, "today": today(), "version": APP_VERSION,
                "intranet_version": "2026.11.17-notices-home", "cloud": bool(cfg.get("cloud")),
                "instance_id": instance_id, "install_id": install_id,
                "geo_version": GEO_ENGINE_VERSION, "llm_version": BLOG_ENGINE_VERSION,
                "geo_ready": APP_VERSION == GEO_ENGINE_VERSION == BLOG_ENGINE_VERSION}

    class Login(BaseModel):
        username: str = Field(min_length=1, max_length=40)
        password: str = Field(min_length=1, max_length=150)

    @app.post("/api/login")
    async def login(body: Login, request: Request):
        consume_limits(store, [("login-ip:"+client_key(request), 60, 60),
                               ("login-user:"+body.username.casefold(), 10, 60)])
        result = await run_in_threadpool(store.login, body.username, body.password)
        if not result:
            store.audit("anonymous", "login_failed", result="denied")
            raise HTTPException(401, "아이디 또는 비밀번호가 일치하지 않습니다.")
        token, csrf, user = result
        store.audit(user["id"], "login")
        response = JSONResponse({"user": user, "csrf": csrf})
        response.set_cookie("gm_session", token, httponly=True, samesite="strict",
                            secure=cfg["secure_cookie"], max_age=8*3600, path="/")
        return response

    @app.get("/api/me")
    def me(user=Depends(current)):
        return {"user": {k: user[k] for k in ("id", "name", "role")}, "csrf": user["csrf"]}

    @app.post("/api/logout")
    async def logout(user=Depends(current)):
        with store.connect() as c:
            store.audit(user["id"], "logout", connection=c)
            c.execute("DELETE FROM sessions WHERE hash=?", (user["session_hash"],))
            c.execute("DELETE FROM capture_tokens WHERE session_hash=?", (user["session_hash"],))
        response = JSONResponse({"ok": True})
        response.delete_cookie("gm_session", path="/")
        return response

    @app.get("/api/users")
    def users(user=Depends(manager)):
        with store.connect(shared=True) as c:
            return {"users": [dict(r) for r in c.execute("SELECT id,name,role,active,revision,balance FROM users ORDER BY id")]}

    @app.get("/api/status")
    async def status(user=Depends(current)):
        state = await ai.status()
        with store.connect() as c:
            total = c.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        state.update({"indexed_chunks": total, "public_base_url": cfg["public_base_url"], "today": today()})
        return state

    @app.get("/api/patients")
    def patients(q: str = "", user=Depends(clinical)):
        with store.connect(shared=True) as c:
            rows = c.execute("SELECT * FROM patients WHERE name LIKE ? OR id LIKE ? ORDER BY id",
                             ("%"+q[:80]+"%", "%"+q[:80]+"%")).fetchall()
        return {"patients": [dict(r) for r in rows], "today": today()}

    @app.get("/api/patients/{pid}/photos")
    def photos(pid: str, user=Depends(clinical)):
        p = require_patient(pid)
        with store.connect(shared=True) as c:
            rows = c.execute("SELECT id,visit_date,site,created FROM photos WHERE patient_id=? ORDER BY visit_date DESC,created DESC,id DESC", (pid,)).fetchall()
        current_photos = [dict(r) for r in rows if r["visit_date"] == today()]
        prior = [dict(r) for r in rows if r["visit_date"] < today()]
        # The full prior-photo list supports visit selection on PC and tablet.
        return {"patient": p, "today": current_photos[0] if current_photos else None,
                "before": prior[0] if prior else None, "history": prior, "visit_date": today()}

    @app.get("/api/photos/{photo_id}")
    def photo(photo_id: int, user=Depends(clinical)):
        with store.connect(shared=True) as c:
            row = c.execute("SELECT filename FROM photos WHERE id=?", (photo_id,)).fetchone()
        if not row:
            raise HTTPException(404, "사진이 없습니다.")
        return FileResponse(store.load_file("photos", row["filename"]), headers={"Cache-Control": "no-store"})

    async def publish(pid, data):
        for queue in list(subscribers[pid]):
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            queue.put_nowait(data)

    @app.get("/api/patients/{pid}/events")
    async def events(pid: str, request: Request, user=Depends(clinical)):
        require_patient(pid)
        async def stream():
            queue = asyncio.Queue(maxsize=10)
            subscribers[pid].add(queue)
            try:
                yield "event: ready\ndata: {}\n\n"
                while not await request.is_disconnected():
                    if not store.session(request.cookies.get("gm_session")):
                        break
                    try:
                        data = await asyncio.wait_for(queue.get(), timeout=12)
                        yield "event: photo\ndata: " + json.dumps(data) + "\n\n"
                    except asyncio.TimeoutError:
                        yield ": heartbeat\n\n"
            finally:
                subscribers[pid].discard(queue)
        return StreamingResponse(stream(), media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    class Pair(BaseModel):
        patient_id: str
        base_url: str = Field(max_length=250)

    @app.post("/api/capture-sessions")
    async def pairing(body: Pair, user=Depends(clinical)):
        require_patient(body.patient_id)
        url = urlsplit(body.base_url.strip())
        if url.scheme not in {"http", "https"} or not url.netloc or url.username or url.password or url.query or url.fragment or url.path not in {"", "/"}:
            raise HTTPException(400, "접속 주소는 http(s)://주소:포트 형식으로 입력하세요.")
        token = secrets.token_urlsafe(32)
        with store.connect() as c:
            c.execute("DELETE FROM capture_tokens WHERE expires<? OR consumed=1", (time.time(),))
            c.execute("UPDATE capture_tokens SET consumed=1 WHERE patient_id=? AND session_hash=?", (body.patient_id, user["session_hash"]))
            c.execute("INSERT INTO capture_tokens VALUES(?,?,?,?,?,?,0)",
                      (token_hash(token), body.patient_id, today(), "정수리", user["session_hash"], time.time()+600))
        link = body.base_url.rstrip("/")+"/capture?"+urlencode({"token": token})
        image = qrcode.make(link)
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        import base64
        store.audit(user["id"], "create_capture", body.patient_id)
        return {"url": link, "qr": "data:image/png;base64,"+base64.b64encode(buf.getvalue()).decode(),
                "expires_in": 600, "patient_id": body.patient_id}

    @app.post("/api/capture-sessions/revoke")
    async def revoke_capture(user=Depends(clinical)):
        with store.connect() as c:
            c.execute("UPDATE capture_tokens SET consumed=1 WHERE session_hash=?", (user["session_hash"],))
        return {"ok": True}

    def valid_capture(token, c):
        row = c.execute("""SELECT t.* FROM capture_tokens t JOIN sessions s ON s.hash=t.session_hash
          WHERE t.hash=? AND t.expires>? AND t.consumed=0 AND s.expires>?""",
                        (token_hash(token), time.time(), time.time())).fetchone()
        if not row:
            raise HTTPException(410, "촬영 연결이 만료·사용·취소되었습니다. PC에서 다시 연결하세요.")
        if row["visit_date"] != today():
            raise HTTPException(410, "방문일이 바뀌었습니다. PC에서 다시 연결하세요.")
        return dict(row)

    @app.get("/api/capture/{token}")
    def capture_info(token: str):
        if len(token) > 150:
            raise HTTPException(410, "유효하지 않은 촬영 연결입니다.")
        with store.connect(shared=True) as c:
            row = valid_capture(token, c)
        return {"patient_id": row["patient_id"], "site": row["site"],
                "visit_date": row["visit_date"], "expires": row["expires"], "demo": True}

    async def save_image(upload, pid, capture_token=None):
        data = await upload.read(15*1024*1024+1)
        if len(data) > 15*1024*1024:
            raise HTTPException(413, "사진은 15MB 이하로 선택하세요.")
        filename = uuid.uuid4().hex+".jpg"
        path = store.directory / "photos" / filename
        try:
            with Image.open(io.BytesIO(data)) as im:
                if im.width*im.height > 30_000_000:
                    raise HTTPException(413, "사진 해상도가 너무 큽니다.")
                im = ImageOps.exif_transpose(im).convert("RGB")
                im.thumbnail((2400, 2400))
                buffer = io.BytesIO()
                im.save(buffer, "JPEG", quality=90)
                store.save_file("photos", filename, buffer.getvalue())
        except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as e:
            raise HTTPException(400, "JPEG·PNG·WebP 사진을 선택하세요. HEIC는 JPEG로 변환하세요.") from e
        try:
            with store.connect() as c:
                c.execute("BEGIN IMMEDIATE")
                if capture_token:
                    row = valid_capture(capture_token, c)
                    pid = row["patient_id"]
                    c.execute("UPDATE capture_tokens SET consumed=1 WHERE hash=?", (token_hash(capture_token),))
                cur = c.execute("INSERT INTO photos(patient_id,visit_date,site,filename,created) VALUES(?,?,?,?,?)",
                                (pid, today(), "정수리", filename, time.time()))
                photo_id = cur.lastrowid
        except Exception:
            store.delete_file("photos", filename)
            raise
        await publish(pid, {"photo_id": photo_id})
        return {"photo_id": photo_id, "patient_id": pid, "visit_date": today()}

    @app.post("/api/capture/{token}")
    async def phone_upload(token: str, file: UploadFile = File(...)):
        with store.connect() as c:
            row = valid_capture(token, c)
        return await save_image(file, row["patient_id"], token)

    @app.post("/api/patients/{pid}/photos")
    async def desktop_upload(pid: str, file: UploadFile = File(...), user=Depends(clinical)):
        require_patient(pid)
        result = await save_image(file, pid)
        store.audit(user["id"], "upload_photo", pid)
        return result

    @app.get("/api/sample-image")
    def sample_image(user=Depends(clinical)):
        return Response(sample, media_type="image/png")

    @app.post("/api/transcribe")
    async def transcribe(patient_id: str = Form(...), language: str = Form("ko"),
                         file: UploadFile = File(...), user=Depends(clinical)):
        require_patient(patient_id)
        if language not in {"ko", "en", "auto"}:
            raise HTTPException(400, "전사 언어를 확인하세요.")
        data = await file.read(25*1024*1024+1)
        if not data or len(data) > 25*1024*1024:
            raise HTTPException(413, "녹음 파일은 25MB 이하로 선택하세요.")
        suffix = Path(file.filename or "audio.webm").suffix.lower()
        if suffix not in {".wav", ".mp3", ".m4a", ".mp4", ".webm", ".ogg", ".flac", ".aac"}:
            suffix = ".webm"
        name = None
        try:
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
                name = f.name
                f.write(data)
            def validate_audio():
                import av
                try:
                    with av.open(name) as media:
                        if not media.streams.audio:
                            raise ValueError("no audio")
                        if media.duration:
                            duration = media.duration/av.time_base
                        else:
                            duration = 0
                            for frame in media.decode(audio=0):
                                duration += frame.samples/frame.sample_rate
                                if duration > 300:
                                    break
                        if duration > 300:
                            raise HTTPException(413, "시연 전사는 5분 이하로 제한했습니다.")
                except HTTPException:
                    raise
                except Exception as e:
                    raise HTTPException(400, "음성 파일을 읽지 못했습니다.") from e
            await run_in_threadpool(validate_audio)
            result = await run_in_threadpool(ai.transcribe, name, language)
        finally:
            if name:
                Path(name).unlink(missing_ok=True)
        if not result["text"]:
            return {**result, "patient_id": patient_id, "warning": "음성을 인식하지 못했습니다. 녹음과 언어를 확인하세요."}
        with store.connect() as c:
            cur = c.execute("INSERT INTO transcripts(patient_id,user_id,text,language,segments,created) VALUES(?,?,?,?,?,?)",
                            (patient_id, user["id"], result["text"], result["language"], json.dumps(result["segments"], ensure_ascii=False), time.time()))
            result["transcript_id"] = cur.lastrowid
        store.audit(user["id"], "transcribe", patient_id)
        return {**result, "patient_id": patient_id, "warning": "실제 전사 결과입니다. 약명·숫자·부정어와 환자를 확인한 뒤 사용하세요."}

    class Note(BaseModel):
        patient_id: str
        text: str = Field(min_length=1, max_length=15000)
        reviewed: bool
        simulate_fail: bool = False

    @app.post("/api/notes")
    async def save_note(body: Note, user=Depends(clinical)):
        require_patient(body.patient_id)
        if not body.reviewed:
            raise HTTPException(400, "모의 검토 확인이 필요합니다.")
        key = hashlib.sha256((body.patient_id+"\0"+body.text).encode()).hexdigest()
        with store.connect() as c:
            row = c.execute("SELECT * FROM notes WHERE hash=?", (key,)).fetchone()
            if row and row["status"] == "전송 성공":
                return {"note": dict(row), "duplicate": True, "crm_connected": False}
            state = "전송 실패" if body.simulate_fail else "전송 성공"
            if row:
                c.execute("UPDATE notes SET attempts=attempts+1,status=? WHERE id=?", (state, row["id"]))
            else:
                c.execute("INSERT INTO notes(patient_id,text,hash,user_id,attempts,status,created) VALUES(?,?,?,?,?,?,?)",
                          (body.patient_id, body.text, key, user["id"], 1, state, time.time()))
            row = c.execute("SELECT * FROM notes WHERE hash=?", (key,)).fetchone()
        return {"note": dict(row), "duplicate": False, "crm_connected": False}

    @app.get("/api/notes")
    def list_notes(user=Depends(clinical)):
        with store.connect(shared=True) as c:
            return {"notes": [dict(r) for r in c.execute("SELECT * FROM notes ORDER BY id DESC")]}

    class Leave(BaseModel):
        date: str
        days: float
        reason: str = Field(default="", max_length=80)

    @app.get("/api/leaves")
    def list_leaves(user=Depends(current)):
        with store.connect(shared=True) as c:
            rows = c.execute("SELECT l.*,u.name FROM leaves l JOIN users u ON u.id=l.user_id " +
                ("ORDER BY l.id DESC" if user["role"] == "admin" else "WHERE l.user_id=? ORDER BY l.id DESC"),
                () if user["role"] == "admin" else (user["id"],)).fetchall()
            balance = c.execute("SELECT balance FROM users WHERE id=?", (user["id"],)).fetchone()[0]
        return {"leaves": [dict(r) for r in rows], "balance": balance}

    @app.post("/api/leaves")
    async def apply_leave(body: Leave, user=Depends(current)):
        try:
            day = datetime.strptime(body.date, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(400, "휴가일을 확인하세요.")
        if day.isoformat() < today() or body.days not in {0.5, 1}:
            raise HTTPException(400, "오늘 이후 날짜와 연차·반차를 선택하세요.")
        try:
            with store.connect() as c:
                if c.execute("SELECT balance FROM users WHERE id=?", (user["id"],)).fetchone()[0] < body.days:
                    raise HTTPException(400, "잔여 연차가 부족합니다.")
                c.execute("INSERT INTO leaves(user_id,date,days,reason,status,created) VALUES(?,?,?,?,?,?)",
                          (user["id"], body.date, body.days, body.reason, "pending", time.time()))
        except sqlite3.IntegrityError:
            raise HTTPException(409, "같은 날짜에 이미 신청한 내역이 있습니다.")
        return {"ok": True}

    class Decision(BaseModel):
        approve: bool

    @app.post("/api/leaves/{lid}/decision")
    async def decide(lid: int, body: Decision, user=Depends(manager)):
        with store.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute("SELECT * FROM leaves WHERE id=?", (lid,)).fetchone()
            if not row:
                raise HTTPException(404, "신청이 없습니다.")
            if row["status"] != "pending":
                raise HTTPException(409, "이미 처리한 신청입니다.")
            if body.approve:
                changed = c.execute("UPDATE users SET balance=balance-? WHERE id=? AND balance>=?",
                                    (row["days"], row["user_id"], row["days"])).rowcount
                if not changed:
                    raise HTTPException(409, "승인 시점의 잔여 연차가 부족합니다.")
            c.execute("UPDATE leaves SET status=?,approver=? WHERE id=?",
                      ("approved" if body.approve else "rejected", user["id"], lid))
        return {"ok": True}

    install_intranet(app, store, current, manager, editor)
    install_youtube(app, store, current, editor, youtube_jobs)
    install_geo(app, store, editor, ai, server_version=APP_VERSION)

    @app.post("/api/documents/reindex")
    async def reindex(user=Depends(editor)):
        async with ai.index_lock:
            digest = await ai.embedding_digest()
            with store.connect() as c:
                rows = [dict(r) for r in c.execute("SELECT * FROM documents WHERE approved=1 AND archived=0")
                        if user["role"] == "admin" or user["role"] in json.loads(r["roles"])]
            count = 0
            for d in rows:
                pieces = split_document(d["content"])
                vectors = await ai.embed([d["title"]+"\n"+text for text in pieces])
                with store.connect() as c:
                    current_doc = c.execute("SELECT version,approved,archived FROM documents WHERE id=?", (d["id"],)).fetchone()
                    if not current_doc or not current_doc["approved"] or current_doc["archived"] or current_doc["version"] != d["version"]:
                        continue
                    c.execute("DELETE FROM chunks WHERE doc_id=?", (d["id"],))
                    c.executemany("INSERT INTO chunks(doc_id,version,ordinal,text,vector,model,digest) VALUES(?,?,?,?,?,?,?)", [
                        (d["id"], d["version"], i, text, json.dumps(vector.tolist()), ai.embedding_model, digest)
                        for i, (text, vector) in enumerate(zip(pieces, vectors))])
                count += len(pieces)
            store.audit(user["id"], "reindex", count)
            return {"chunks": count, "model": ai.embedding_model, "digest": digest}

    class Question(BaseModel):
        question: str = Field(min_length=1, max_length=1000)

    async def prepare_missing_wiki_index(user,digest):
        # Approved sources only; staff can prepare only documents they may read.
        async with ai.index_lock:
            for d in store.allowed_documents(user['role']):
                with store.connect() as c:
                    ready=c.execute('SELECT 1 FROM chunks WHERE doc_id=? AND version=? AND model=? AND digest=? LIMIT 1',(d['id'],d['version'],ai.embedding_model,digest)).fetchone()
                    source_doc=c.execute('SELECT * FROM documents WHERE id=?',(d['id'],)).fetchone()
                if ready:continue
                if not source_doc or source_doc['version']!=d['version'] or not source_doc['approved'] or source_doc['archived']:continue
                if user['role']!='admin' and user['role'] not in json.loads(source_doc['roles']):continue
                pieces=split_document(source_doc['content'])
                vectors=await ai.embed([d['title']+'\n'+text for text in pieces])
                with store.connect() as c:
                    c.execute('BEGIN IMMEDIATE')
                    current_doc=c.execute('SELECT * FROM documents WHERE id=?',(d['id'],)).fetchone()
                    if not current_doc or not current_doc['approved'] or current_doc['archived'] or current_doc['version']!=d['version']:continue
                    if user['role']!='admin' and user['role'] not in json.loads(current_doc['roles']):continue
                    c.execute('DELETE FROM chunks WHERE doc_id=?',(d['id'],))
                    c.executemany('INSERT INTO chunks(doc_id,version,ordinal,text,vector,model,digest) VALUES(?,?,?,?,?,?,?)',[(d['id'],d['version'],i,text,json.dumps(vector.tolist()),ai.embedding_model,digest) for i,(text,vector) in enumerate(zip(pieces,vectors))])
                    store.audit(user['id'],'wiki_auto_index',d['id'],detail={'version':d['version'],'chunks':len(pieces)},connection=c)

    async def retrieve(question, user):
        allowed = store.allowed_documents(user["role"])
        ids = [d["id"] for d in allowed]
        if not ids:
            return []
        digest = await ai.embedding_digest()
        await prepare_missing_wiki_index(user,digest)
        ids=[d['id'] for d in store.allowed_documents(user['role'])]
        if not ids:return []
        # Permissions are applied before reading chunk texts and before the LLM prompt.
        with store.connect() as c:
            rows = c.execute("""SELECT ch.*,d.title,d.source_url,d.kind FROM chunks ch JOIN documents d ON d.id=ch.doc_id
              WHERE ch.doc_id IN ("""+",".join("?" for _ in ids)+""" ) AND d.approved=1 AND d.archived=0 AND ch.version=d.version
              AND ch.model=? AND ch.digest=?""", (*ids, ai.embedding_model, digest)).fetchall()
        if not rows:
            raise HTTPException(409, "현재"+" 권한에 맞는 최신 검색 색인이 없습니다. 의료진·관리자로 자료 색인을 생성하세요.")
        query = (await ai.embed([question]))[0]
        qgrams = {question[i:i+2] for i in range(len(question)-1) if question[i:i+2].strip()}
        ranked = []
        for r in rows:
            vec = np.asarray(json.loads(r["vector"]), dtype=np.float32)
            if vec.shape != query.shape:
                raise HTTPException(409, "임베딩 차원이 바뀌었습니다. 색인을 다시 만드세요.")
            cosine = float(vec @ query)
            lexical = sum(1 for g in qgrams if g in r["text"])/max(len(qgrams), 1)
            score = .9*cosine+.1*lexical
            if cosine >= cfg["retrieval_min_score"]:
                ranked.append({"doc_id": r["doc_id"], "chunk_id": r["id"], "title": r["title"],
                    "version": r["version"], "text": r["text"], "source_url": r["source_url"], "kind": r["kind"],
                    "cosine": round(cosine, 4), "rank_score": round(score, 4)})
        ranked.sort(key=lambda s: s["rank_score"], reverse=True)
        return [{**s, "source_id": "C"+str(i+1)} for i, s in enumerate(ranked[:3])]

    @app.post("/api/wiki/search")
    async def semantic_search(body: Question, user=Depends(current)):
        sources = await retrieve(body.question, user)
        store.audit(user["id"], "semantic_search", ",".join(s["doc_id"] for s in sources))
        return {"sources": sources, "embedding_model": ai.embedding_model,
                "method": "실제"+" 임베딩 코사인 유사도 + 키워드 보조", "role": user["role"]}

    @app.post("/api/wiki/ask")
    async def ask(body: Question, user=Depends(current)):
        # Explicit clinical decisions are routed to a human rather than generated for CS.
        if re.search(r"(효과.*보장|완치|진단해|약.*(늘려|줄여|끊|바꿔)|복용량|처방해)", body.question):
            store.audit(user["id"], "rag_handoff")
            return {"status": "handoff", "answer": "개별 의료 판단은 담당 의료진에게 전달하세요.",
                    "sources": [], "citations": [], "llm_used": False}
        sources = await retrieve(body.question, user)
        if not sources:
            store.audit(user["id"], "rag_insufficient")
            return {"status": "insufficient", "answer": "관련 근거를 찾지 못했습니다. 담당자 확인 후 안내하세요.",
                    "sources": [], "citations": [], "llm_used": False}
        result = await ai.generate(body.question, sources)
        store.audit(user["id"], "rag_answer", ",".join(s["doc_id"] for s in sources))
        return {**result, "sources": sources, "llm_used": True,
                "chat_model": ai.chat_model, "embedding_model": ai.embedding_model,
                "review_required": True}

    if cfg.get("cloud"):
        from cloud_runtime import install_cloud_routes
        install_cloud_routes(app, store, current, manager, cfg)

    app.mount("/static", StaticFiles(directory=HERE/"static"), name="static")
    return app


app = None if os.environ.get("CLINICWORKSPACE_CLOUD") == "1" else create_app()
