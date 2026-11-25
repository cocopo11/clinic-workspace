"""Document library, versioned onboarding and demo administration."""
import json
import re
import secrets
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from fastapi import Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from storage import DEMO_PASSWORD, password_hash
from staff_portal import install_staff_portal

ROLES = {"admin", "doctor", "staff"}
KINDS = {"manual", "onboarding", "youtube_transcript"}
DOC_FIELDS = ("title", "content", "approved", "roles", "kind", "source_url", "category", "tags", "summary", "source_author", "source_date", "archived")


class Document(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    content: str = Field(min_length=1, max_length=100000)
    roles: list[str] = Field(default_factory=lambda: ["admin", "doctor", "staff"])
    approved: bool = False
    kind: str = "manual"
    source_url: str = Field(default="", max_length=500)
    category: str = Field(default="일반", max_length=50)
    tags: list[str] = Field(default_factory=list, max_length=15)
    summary: str = Field(default="", max_length=500)
    source_author: str = Field(default="", max_length=120)
    source_date: str = ""
    change_note: str = Field(default="", max_length=300)
    expected_version: int | None = Field(default=None, ge=1)


class Versioned(BaseModel):
    expected_version: int = Field(ge=1)


class Archive(Versioned):
    archived: bool


class Approval(Versioned):
    approved: bool


class Restore(Versioned):
    version: int = Field(ge=1)


class Course(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)
    roles: list[str] = Field(default_factory=lambda: ["admin", "doctor", "staff"])
    document_ids: list[str] = Field(min_length=1, max_length=30)
    active: bool = True
    expected_revision: int | None = Field(default=None, ge=1)


class Progress(BaseModel):
    version: int = Field(ge=1)
    completed: bool


class CourseActive(BaseModel):
    active: bool
    expected_revision: int = Field(ge=1)


class NewUser(BaseModel):
    id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{1,39}$")
    name: str = Field(min_length=1, max_length=60)
    role: Literal["admin", "doctor", "staff"] = "staff"


class UpdateUser(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    role: Literal["admin", "doctor", "staff"]
    active: bool
    expected_revision: int = Field(ge=1)


def install_intranet(app, store, current, manager, editor):
    def role_list(values):
        if not values or not set(values) <= ROLES:
            raise HTTPException(400, "열람 역할을 확인하세요.")
        return sorted(set(values))

    def visible(d, user, archived=False):
        if user["role"] == "admin":
            return archived or not d["archived"]
        return (not d["archived"] and user["role"] in json.loads(d["roles"]) and
                (d["approved"] or user["role"] == "doctor"))

    def get_doc(c, did, user, edit=False, archived=False):
        row = c.execute("SELECT * FROM documents WHERE id=?", (did,)).fetchone()
        if not row or not visible(row, user, archived=archived):
            raise HTTPException(404, "열람할 수 있는 자료가 없습니다.")
        if edit and user["role"] not in {"admin", "doctor"}:
            raise HTTPException(403, "문서 관리 권한이 필요합니다.")
        return dict(row)

    def metadata(c, d, related=None):
        result = {k: v for k, v in dict(d).items() if k != "content"}
        result["roles"] = json.loads(result["roles"])
        result["tags"] = json.loads(result["tags"])
        result["approved"], result["archived"] = bool(result["approved"]), bool(result["archived"])
        source = related[0].get(d['id']) if related is not None else c.execute("SELECT video_id,method,language,generated,duration,fetched FROM youtube_sources WHERE doc_id=?", (d["id"],)).fetchone()
        result["youtube_source"] = dict(source) if source else None
        result["indexed_chunks"] = related[1].get((d['id'],d['version']),0) if related is not None else c.execute("SELECT COUNT(*) FROM chunks WHERE doc_id=? AND version=?", (d["id"], d["version"])).fetchone()[0]
        return result

    def snapshot(c, did, actor, note, action):
        doc = dict(c.execute("SELECT * FROM documents WHERE id=?", (did,)).fetchone())
        c.execute("INSERT INTO document_versions VALUES(?,?,?,?,?,?)", (did, doc["version"], json.dumps(doc, ensure_ascii=False), actor, note, time.time()))
        store.audit(actor, action, did, detail={"version": doc["version"]}, connection=c)
        return {"id": did, "version": doc["version"], "indexed": False}

    def expect(d, version):
        if version is None or version != d["version"]:
            raise HTTPException(409, "문서 버전이 바뀌었습니다. 자료를 다시 열고 수정하세요.")

    def document_values(body, user):
        roles = role_list(body.roles)
        if user["role"] != "admin" and user["role"] not in roles:
            raise HTTPException(400, "작성자 역할을 열람 범위에 포함하세요.")
        if body.approved and user["role"] != "admin":
            raise HTTPException(403, "검색 사용 승인은 관리자만 할 수 있습니다.")
        if body.kind not in KINDS:
            raise HTTPException(400, "자료 종류를 확인하세요.")
        title, text = body.title.strip(), body.content.strip()
        if not title or not text or '\x00' in text:
            raise HTTPException(400, "제목과 원문을 확인하세요.")
        url = body.source_url.strip()
        if url:
            try:
                parsed = urlsplit(url)
            except ValueError:
                raise HTTPException(400, "출처 URL을 확인하세요.")
            if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
                raise HTTPException(400, "출처 URL은 http 또는 https 주소를 입력하세요.")
            host = parsed.hostname.lower()
            if body.kind == "youtube_transcript" and not (host == "youtu.be" or host == "youtube.com" or host.endswith(".youtube.com")):
                raise HTTPException(400, "유튜브 대본에는 youtube.com 또는 youtu.be 출처 주소를 입력하세요.")
        if body.source_date:
            try:
                date.fromisoformat(body.source_date)
            except ValueError:
                raise HTTPException(400, "자료 발행일을 확인하세요.")
        tags = list(dict.fromkeys(t.strip() for t in body.tags if t.strip()))
        if any(len(t) > 40 for t in tags):
            raise HTTPException(400, "태그는 각각 40자 이하로 입력하세요.")
        return {"title": title, "content": text, "roles": json.dumps(roles), "approved": int(body.approved),
                "kind": body.kind, "source_url": url, "category": body.category.strip() or "일반",
                "tags": json.dumps(tags, ensure_ascii=False), "summary": body.summary.strip(),
                "source_author": body.source_author.strip(), "source_date": body.source_date, "archived": 0}

    def write_revision(c, did, values, user, note, action):
        assignments = ",".join(f"{key}=?" for key in DOC_FIELDS)
        c.execute(f"UPDATE documents SET {assignments},version=version+1,updated=? WHERE id=?",
                  (*[values[key] for key in DOC_FIELDS], time.time(), did))
        c.execute("DELETE FROM chunks WHERE doc_id=?", (did,))
        return snapshot(c, did, user["id"], note, action)

    @app.get("/api/documents")
    def documents(q: str = "", kind: str = "", category: str = "", status: str = "active", user=Depends(current)):
        with store.connect(shared=True) as c:
            rows = c.execute("SELECT * FROM documents ORDER BY updated DESC,id").fetchall()
            source_map={r['doc_id']:{k:r[k] for k in ('video_id','method','language','generated','duration','fetched')} for r in c.execute('SELECT doc_id,video_id,method,language,generated,duration,fetched FROM youtube_sources')}
            chunk_counts={(r['doc_id'],r['version']):r['total'] for r in c.execute('SELECT doc_id,version,COUNT(*) AS total FROM chunks GROUP BY doc_id,version')}
            result = []
            for d in rows:
                if not visible(d, user, archived=user["role"] == "admin"):
                    continue
                if status == "archived" and not d["archived"] or status != "archived" and d["archived"]:
                    continue
                if status == "draft" and d["approved"] or status == "approved" and not d["approved"]:
                    continue
                if kind and d["kind"] != kind or category and d["category"] != category:
                    continue
                if q and q.casefold() not in (d["title"]+" "+d["summary"]+" "+d["tags"]+" "+d["source_author"]).casefold():
                    continue
                result.append(metadata(c, d, (source_map,chunk_counts)))
            store.audit(user["id"], "list_documents", detail={"count": len(result)}, connection=c)
        return {"documents": result}

    @app.post("/api/documents/import")
    async def import_document(file: UploadFile = File(...), user=Depends(editor)):
        name = Path((file.filename or "document.txt").replace('\\', '/')).name
        suffix = Path(name).suffix.lower()
        if suffix not in {".txt", ".md", ".srt", ".vtt"}:
            raise HTTPException(400, "TXT·Markdown·SRT·VTT 파일을 선택하세요.")
        raw = await file.read(1_000_001)
        if len(raw) > 1_000_000:
            raise HTTPException(400, "원문 파일은 1MB 이하로 선택하세요.")
        try:
            text = raw.decode("utf-8-sig").replace('\r\n', '\n').strip()
        except UnicodeDecodeError:
            raise HTTPException(400, "UTF-8로 저장한 텍스트 파일을 선택하세요.")
        if not text or len(text) > 100000 or '\x00' in text:
            raise HTTPException(400, "원문은 비어 있지 않은 10만 자 이하의 텍스트여야 합니다.")
        store.audit(user["id"], "import_document", detail={"format": suffix})
        return {"title": Path(name).stem[:120], "content": text,
                "kind": "youtube_transcript" if suffix in {".srt", ".vtt"} else "manual",
                "persisted": False, "message": "원문을 불러왔습니다. 확인 후 문서 저장을 누르세요. 자막 시간 표시는 원문에 보존합니다."}

    @app.get("/api/documents/{did}")
    def read_document(did: str, user=Depends(current)):
        with store.connect(shared=True) as c:
            d = get_doc(c, did, user, archived=user["role"] == "admin")
            result = {**metadata(c, d), "content": d["content"]}
            c.execute("INSERT INTO document_reads VALUES(?,?,?,?) ON CONFLICT(user_id,doc_id,version) DO UPDATE SET read_at=excluded.read_at", (user["id"], did, d["version"], time.time()))
            store.audit(user["id"], "read_document", did, detail={"version": d["version"]}, connection=c)
        return result

    @app.get("/api/documents/{did}/download")
    def download_document(did: str, user=Depends(current)):
        with store.connect(shared=True) as c:
            d = get_doc(c, did, user, archived=user["role"] == "admin")
            store.audit(user["id"], "download_document", did, detail={"version": d["version"]}, connection=c)
        return Response(d["content"].encode("utf-8-sig"), media_type="text/plain; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{did}-v{d["version"]}.txt"'})

    @app.post("/api/documents")
    async def add_document(body: Document, user=Depends(editor)):
        values = document_values(body, user)
        did = "DOC-"+uuid.uuid4().hex[:10]
        with store.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            cols = ",".join(DOC_FIELDS)
            c.execute(f"INSERT INTO documents(id,version,owner_id,updated,{cols}) VALUES(?,?,?,?,{','.join('?' for _ in DOC_FIELDS)})",
                      (did, 1, user["id"], time.time(), *[values[k] for k in DOC_FIELDS]))
            result = snapshot(c, did, user["id"], body.change_note or "자료 등록", "add_document")
        return result

    @app.put("/api/documents/{did}")
    async def update_document(did: str, body: Document, user=Depends(editor)):
        values = document_values(body, user)
        with store.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            old = get_doc(c, did, user, edit=True)
            expect(old, body.expected_version)
            result = write_revision(c, did, values, user, body.change_note or "자료 수정", "update_document")
        return result

    @app.post("/api/documents/{did}/approval")
    async def approve_document(did: str, body: Approval, user=Depends(manager)):
        with store.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            old = get_doc(c, did, user)
            expect(old, body.expected_version)
            if bool(old["approved"]) == body.approved:
                return {"id": did, "version": old["version"]}
            values = {k: old[k] for k in DOC_FIELDS}
            values["approved"] = int(body.approved)
            result = write_revision(c, did, values, user, "검색 승인" if body.approved else "검색 승인 해제", "approve_document")
        return result

    @app.post("/api/documents/{did}/archive")
    async def archive_document(did: str, body: Archive, user=Depends(editor)):
        with store.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            old = get_doc(c, did, user, edit=True, archived=user["role"] == "admin")
            expect(old, body.expected_version)
            if bool(old["archived"]) == body.archived:
                return {"id": did, "version": old["version"]}
            values = {k: old[k] for k in DOC_FIELDS}
            values.update(archived=int(body.archived), approved=0)
            result = write_revision(c, did, values, user, "자료 보관" if body.archived else "보관 해제 · 재승인 필요", "archive_document")
        return result

    @app.delete("/api/documents/{did}")
    async def delete_document(did: str, body: Versioned, user=Depends(editor)):
        with store.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            old = get_doc(c, did, user, edit=True, archived=user["role"] == "admin")
            expect(old, body.expected_version)
            affected = [r[0] for r in c.execute("SELECT course_id FROM onboarding_steps WHERE doc_id=?", (did,))]
            # Remove dependent rows explicitly in both SQLite and PostgreSQL.
            for table in ("chunks", "document_reads", "document_versions", "onboarding_progress",
                          "onboarding_steps", "youtube_sources", "youtube_jobs"):
                c.execute(f"DELETE FROM {table} WHERE doc_id=?", (did,))
            c.execute("DELETE FROM documents WHERE id=?", (did,))
            for cid in affected:
                remaining = c.execute("SELECT COUNT(*) FROM onboarding_steps WHERE course_id=?", (cid,)).fetchone()[0]
                c.execute("UPDATE onboarding_courses SET revision=revision+1,updated=?,active=CASE WHEN ?=0 THEN 0 ELSE active END WHERE id=?",
                          (time.time(), remaining, cid))
            store.audit(user["id"], "delete_document", did,
                        detail={"version": old["version"], "kind": old["kind"]}, connection=c)
        return {"ok": True, "id": did}

    @app.get("/api/documents/{did}/versions")
    def versions(did: str, user=Depends(editor)):
        with store.connect(shared=True) as c:
            get_doc(c, did, user, edit=True, archived=user["role"] == "admin")
            result = []
            for r in c.execute("SELECT * FROM document_versions WHERE doc_id=? ORDER BY version DESC", (did,)):
                d = json.loads(r["snapshot"])
                if user["role"] != "admin" and user["role"] not in json.loads(d["roles"]):
                    continue
                result.append({"version": r["version"], "title": d["title"], "actor": r["actor"], "note": r["note"], "created": r["created"]})
            store.audit(user["id"], "view_versions", did, connection=c)
        return {"versions": result}

    @app.post("/api/documents/{did}/restore")
    async def restore_version(did: str, body: Restore, user=Depends(manager)):
        with store.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            old = get_doc(c, did, user, archived=True)
            expect(old, body.expected_version)
            row = c.execute("SELECT snapshot FROM document_versions WHERE doc_id=? AND version=?", (did, body.version)).fetchone()
            if not row:
                raise HTTPException(404, "복원할 버전이 없습니다.")
            values = json.loads(row["snapshot"])
            values.update(approved=0, archived=0)
            result = write_revision(c, did, values, user, f"v{body.version} 원문 복원 · 재승인 필요", "restore_document")
        return result

    def course_view(c, row, user):
        course = dict(row)
        course["roles"] = json.loads(course["roles"])
        steps, blocked, completed = [], 0, 0
        progress_map={r["doc_id"]:r for r in c.execute("SELECT * FROM onboarding_progress WHERE user_id=? AND course_id=?",(user["id"],row["id"]))}
        read_versions={(r["doc_id"],r["version"]) for r in c.execute("SELECT doc_id,version FROM document_reads WHERE user_id=?",(user["id"],))}
        for d in c.execute("SELECT d.*,s.ordinal FROM onboarding_steps s JOIN documents d ON d.id=s.doc_id WHERE s.course_id=? ORDER BY s.ordinal", (row["id"],)).fetchall():
            if d["archived"] or not d["approved"] or user["role"] not in json.loads(d["roles"]) and user["role"] != "admin":
                blocked += 1
                continue
            progress = progress_map.get(d["id"])
            done = bool(progress and progress["completed"] and progress["version"] == d["version"])
            completed += int(done)
            read = (d["id"],d["version"]) in read_versions
            steps.append({"id": d["id"], "title": d["title"], "version": d["version"], "ordinal": d["ordinal"],
                          "completed": done, "read_current": bool(read), "needs_review": bool(progress and progress["completed"] and progress["version"] != d["version"])})
        total = len(steps)+blocked
        course.update(steps=steps, blocked=blocked, completed=completed, total=total, percent=int(100*completed/total) if total else 0)
        return course

    def get_course(c, cid, user):
        row = c.execute("SELECT * FROM onboarding_courses WHERE id=? AND active=1", (cid,)).fetchone()
        if not row or user["role"] != "admin" and user["role"] not in json.loads(row["roles"]):
            raise HTTPException(404, "참여할 수 있는 온보딩 과정이 없습니다.")
        return row

    @app.get("/api/onboarding")
    def onboarding(user=Depends(current)):
        with store.connect(shared=True) as c:
            rows = c.execute("SELECT * FROM onboarding_courses WHERE active=1 ORDER BY updated DESC").fetchall()
            courses = [course_view(c, r, user) for r in rows if user["role"] == "admin" or user["role"] in json.loads(r["roles"])]
            store.audit(user["id"], "view_onboarding", connection=c)
        return {"courses": courses}

    @app.post("/api/onboarding/{cid}/steps/{did}")
    async def complete_step(cid: str, did: str, body: Progress, user=Depends(current)):
        with store.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            get_course(c, cid, user)
            if not c.execute("SELECT 1 FROM onboarding_steps WHERE course_id=? AND doc_id=?", (cid, did)).fetchone():
                raise HTTPException(404, "과정에 포함된 자료가 없습니다.")
            d = get_doc(c, did, user)
            if not d["approved"] or d["version"] != body.version:
                raise HTTPException(409, "최신 승인 자료를 다시 열어 확인하세요.")
            if body.completed and not c.execute("SELECT 1 FROM document_reads WHERE user_id=? AND doc_id=? AND version=?", (user["id"], did, d["version"])).fetchone():
                raise HTTPException(409, "최신 자료를 먼저 열람한 뒤 읽음 확인을 완료하세요.")
            c.execute("INSERT INTO onboarding_progress VALUES(?,?,?,?,?,?) ON CONFLICT(user_id,course_id,doc_id) DO UPDATE SET version=excluded.version,completed=excluded.completed,completed_at=excluded.completed_at",
                      (user["id"], cid, did, d["version"], int(body.completed), time.time() if body.completed else None))
            store.audit(user["id"], "complete_onboarding" if body.completed else "undo_onboarding", did,
                        detail={"course_id": cid, "version": d["version"]}, connection=c)
        return {"ok": True}

    def validate_course(c, body):
        roles = role_list(body.roles)
        ids = list(dict.fromkeys(body.document_ids))
        if not body.title.strip():
            raise HTTPException(400, "과정 제목을 입력하세요.")
        for did in ids:
            d = c.execute("SELECT roles,approved,archived FROM documents WHERE id=?", (did,)).fetchone()
            if not d or not d["approved"] or d["archived"] or not set(roles) <= set(json.loads(d["roles"])):
                raise HTTPException(400, "대상 역할 모두가 볼 수 있는 승인 자료만 과정에 포함할 수 있습니다.")
        return roles, ids

    @app.post("/api/admin/onboarding")
    async def create_course(body: Course, user=Depends(manager)):
        cid = "ONB-"+uuid.uuid4().hex[:10]
        with store.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            roles, ids = validate_course(c, body)
            c.execute("INSERT INTO onboarding_courses VALUES(?,?,?,?,?,?,?)", (cid, body.title.strip(), body.description, json.dumps(roles), int(body.active), 1, time.time()))
            c.executemany("INSERT INTO onboarding_steps VALUES(?,?,?)", [(cid, did, i) for i, did in enumerate(ids, 1)])
            store.audit(user["id"], "create_course", cid, connection=c)
        return {"id": cid, "revision": 1}

    @app.put("/api/admin/onboarding/{cid}")
    async def update_course(cid: str, body: Course, user=Depends(manager)):
        with store.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            old = c.execute("SELECT * FROM onboarding_courses WHERE id=?", (cid,)).fetchone()
            if not old:
                raise HTTPException(404, "과정이 없습니다.")
            if body.expected_revision != old["revision"]:
                raise HTTPException(409, "과정이 변경되었습니다. 다시 열어 수정하세요.")
            roles, ids = validate_course(c, body)
            c.execute("UPDATE onboarding_courses SET title=?,description=?,roles=?,active=?,revision=revision+1,updated=? WHERE id=?", (body.title.strip(), body.description, json.dumps(roles), int(body.active), time.time(), cid))
            c.execute("DELETE FROM onboarding_steps WHERE course_id=?", (cid,))
            c.executemany("INSERT INTO onboarding_steps VALUES(?,?,?)", [(cid, did, i) for i, did in enumerate(ids, 1)])
            store.audit(user["id"], "update_course", cid, connection=c)
        return {"id": cid, "revision": old["revision"]+1}

    @app.get("/api/admin/onboarding")
    def course_admin(user=Depends(manager)):
        with store.connect(shared=True) as c:
            courses = []
            for row in c.execute("SELECT * FROM onboarding_courses ORDER BY updated DESC").fetchall():
                course = dict(row)
                course["roles"] = json.loads(course["roles"])
                course["document_ids"] = [r[0] for r in c.execute("SELECT doc_id FROM onboarding_steps WHERE course_id=? ORDER BY ordinal", (row["id"],))]
                courses.append(course)
            progress = []
            for person in c.execute("SELECT id,name,role,active FROM users WHERE active=1").fetchall():
                for course in courses:
                    if course["active"] and person["role"] in course["roles"]:
                        row = c.execute("SELECT * FROM onboarding_courses WHERE id=?", (course["id"],)).fetchone()
                        status = course_view(c, row, dict(person))
                        progress.append({"user_id": person["id"], "name": person["name"], "course_id": course["id"], "title": course["title"],
                                         **{k: status[k] for k in ("completed", "total", "percent", "blocked")}})
        return {"courses": courses, "progress": progress}

    @app.patch("/api/admin/onboarding/{cid}/active")
    async def activate_course(cid: str, body: CourseActive, user=Depends(manager)):
        with store.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            old = c.execute("SELECT * FROM onboarding_courses WHERE id=?", (cid,)).fetchone()
            if not old:
                raise HTTPException(404, "과정이 없습니다.")
            if body.expected_revision != old["revision"]:
                raise HTTPException(409, "과정이 변경되었습니다. 다시 열어 수정하세요.")
            if body.active:
                ids = [r[0] for r in c.execute("SELECT doc_id FROM onboarding_steps WHERE course_id=? ORDER BY ordinal", (cid,))]
                validate_course(c, Course(title=old["title"], roles=json.loads(old["roles"]), document_ids=ids))
            c.execute("UPDATE onboarding_courses SET active=?,revision=revision+1,updated=? WHERE id=?", (int(body.active), time.time(), cid))
            store.audit(user["id"], "update_course", cid, detail={"active": body.active}, connection=c)
        return {"id": cid, "revision": old["revision"]+1}

    @app.post("/api/admin/users")
    async def create_user(body: NewUser, user=Depends(manager)):
        if not body.name.strip():
            raise HTTPException(400, "직원 표시 이름을 입력하세요.")
        salt = secrets.token_hex(16)
        initial_password = secrets.token_urlsafe(24)
        hashed = password_hash(initial_password, salt)
        with store.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            if c.execute("SELECT 1 FROM users WHERE id=?", (body.id,)).fetchone():
                raise HTTPException(409, "이미 사용 중인 아이디입니다.")
            c.execute("INSERT INTO users(id,name,role,salt,password_hash) VALUES(?,?,?,?,?)", (body.id, body.name.strip(), body.role, salt, hashed))
            store.audit(user["id"], "create_user", body.id, detail={"role": body.role}, connection=c)
        return {"id": body.id, "revision": 1, "demo_password": initial_password}

    @app.patch("/api/admin/users/{uid}")
    async def update_user(uid: str, body: UpdateUser, user=Depends(manager)):
        if not body.name.strip():
            raise HTTPException(400, "직원 표시 이름을 입력하세요.")
        with store.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            old = c.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
            if not old:
                raise HTTPException(404, "계정이 없습니다.")
            if old["revision"] != body.expected_revision:
                raise HTTPException(409, "계정이 변경되었습니다. 목록을 새로고침하세요.")
            if uid == user["id"] and (not body.active or body.role != old["role"]):
                raise HTTPException(400, "현재 로그인한 관리자의 권한·활성 상태는 변경할 수 없습니다.")
            if old["role"] == "admin" and old["active"] and (body.role != "admin" or not body.active):
                if c.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND active=1").fetchone()[0] <= 1:
                    raise HTTPException(400, "활성 관리자를 한 명 이상 유지해야 합니다.")
            c.execute("UPDATE users SET name=?,role=?,active=?,revision=revision+1 WHERE id=?", (body.name.strip(), body.role, int(body.active), uid))
            revoke = old["role"] != body.role or bool(old["active"]) != body.active
            if revoke:
                c.execute("DELETE FROM capture_tokens WHERE session_hash IN (SELECT hash FROM sessions WHERE user_id=?)", (uid,))
                c.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
            store.audit(user["id"], "update_user", uid, detail={"role": body.role, "active": body.active, "sessions_revoked": revoke}, connection=c)
        return {"id": uid, "revision": old["revision"]+1, "sessions_revoked": revoke}

    @app.get("/api/admin/audit")
    def audit(actor: str = "", action: str = "", result: str = "", start: str = "", end: str = "",
                    offset: int = Query(0, ge=0), limit: int = Query(30, ge=1, le=100), user=Depends(manager)):
        conditions, params = [], []
        for column, value in (("actor", actor), ("action", action), ("result", result)):
            if value:
                conditions.append(f"a.{column}=?");params.append(value)
        for key, value, bound in (("start", start, ">="), ("end", end, "<")):
            if value:
                try:
                    parsed = datetime.fromisoformat(value)
                    if parsed.tzinfo is None:
                        parsed = parsed.replace(tzinfo=timezone(timedelta(hours=9)))
                    if key == "end" and len(value) == 10:
                        parsed += timedelta(days=1)
                    stamp = parsed.timestamp()
                except ValueError:
                    raise HTTPException(400, "조회 날짜를 확인하세요.")
                conditions.append(f"a.created{bound}?");params.append(stamp)
        where = " WHERE "+" AND ".join(conditions) if conditions else ""
        with store.connect(shared=True) as c:
            total = c.execute("SELECT COUNT(*) FROM audit a"+where, params).fetchone()[0]
            rows = c.execute("SELECT a.*,u.name AS actor_name FROM audit a LEFT JOIN users u ON u.id=a.actor"+where+" ORDER BY a.id DESC LIMIT ? OFFSET ?", (*params, limit, offset)).fetchall()
            items = [{**dict(r), "detail": json.loads(r["detail"])} for r in rows]
            store.audit(user["id"], "view_audit", connection=c)
        return {"entries": items, "total": total, "offset": offset, "limit": limit}

    install_staff_portal(app, store, current, manager, course_view)
