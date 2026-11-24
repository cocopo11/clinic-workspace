"""Role-scoped notices and the signed-in employee's work dashboard."""
import json
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel, Field


class NoticeInput(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    body: str = Field(min_length=1, max_length=20000)
    roles: list[str] = Field(default_factory=lambda: ["admin", "doctor", "staff"], max_length=3)
    pinned: bool = False
    published: bool = True


class NoticeEdit(NoticeInput):
    expected_version: int = Field(ge=1)


class NoticeRead(BaseModel):
    expected_version: int = Field(ge=1)


class NoticeArchive(NoticeRead):
    archived: bool


def install_staff_portal(app, store, current, manager, course_view):
    def allowed(row, user):
        return user["role"] == "admin" or (
            row["published"] and not row["archived"] and user["role"] in json.loads(row["roles"]))

    def get_notice(c, nid, user):
        row = c.execute("SELECT * FROM notices WHERE id=?", (nid,)).fetchone()
        if not row or not allowed(row, user):
            raise HTTPException(404, "열람할 수 있는 공지가 없습니다.")
        return row

    def expect(row, version):
        if row["version"] != version:
            raise HTTPException(409, "다른 창에서 공지가 변경되었습니다. 최신 공지를 다시 열어주세요.")

    def values(body):
        title, content = body.title.strip(), body.body.strip()
        roles = sorted(set(body.roles))
        if not title or not content:
            raise HTTPException(400, "공지 제목과 내용을 입력하세요.")
        if not roles or not set(roles) <= {"admin", "doctor", "staff"}:
            raise HTTPException(400, "공지를 열람할 역할을 하나 이상 선택하세요.")
        return title, content, json.dumps(roles), int(body.pinned), int(body.published)

    def view(c, row, user, detail=False):
        result = {key: row[key] for key in (
            "id", "title", "pinned", "published", "archived", "version", "created", "updated")}
        result["roles"] = json.loads(row["roles"])
        result["read"] = bool(c.execute(
            "SELECT 1 FROM notice_reads WHERE user_id=? AND notice_id=? AND version=?",
            (user["id"], row["id"], row["version"])).fetchone())
        if detail:
            result["body"] = row["body"]
        return result

    @app.get("/api/notices")
    def notices(q: str = Query(default="", max_length=120),
                      state: Literal["published", "draft", "archived", "all"] = "published",
                      unread: bool = False, offset: int = Query(default=0, ge=0),
                      limit: int = Query(default=20, ge=1, le=100), user=Depends(current)):
        if state != "published" and user["role"] != "admin":
            raise HTTPException(403, "공지 관리 권한이 필요합니다.")
        query = q.strip().casefold()
        with store.connect(shared=True) as c:
            result = []
            for row in c.execute("SELECT * FROM notices ORDER BY pinned DESC,updated DESC,id"):
                if not allowed(row, user):
                    continue
                if state == "published" and (not row["published"] or row["archived"]):
                    continue
                if state == "draft" and (row["published"] or row["archived"]):
                    continue
                if state == "archived" and not row["archived"]:
                    continue
                if query and query not in (row["title"] + "\n" + row["body"]).casefold():
                    continue
                item = view(c, row, user)
                if unread and item["read"]:
                    continue
                result.append(item)
        return {"notices": result[offset:offset+limit], "total": len(result), "offset": offset, "limit": limit}

    @app.post("/api/notices")
    async def create_notice(body: NoticeInput, user=Depends(manager)):
        data = values(body)
        nid, stamp = "N-" + uuid.uuid4().hex[:12], time.time()
        with store.connect() as c:
            c.execute("INSERT INTO notices(id,title,body,roles,pinned,published,created_by,updated_by,created,updated) "
                      "VALUES(?,?,?,?,?,?,?,?,?,?)", (nid, *data, user["id"], user["id"], stamp, stamp))
            store.audit(user["id"], "create_notice", nid, detail={"version": 1}, connection=c)
        return {"id": nid, "version": 1}

    @app.get("/api/notices/{nid}")
    def notice(nid: str, user=Depends(current)):
        with store.connect(shared=True) as c:
            row = get_notice(c, nid, user)
            result = view(c, row, user, detail=True)
            store.audit(user["id"], "view_notice", nid, detail={"version": row["version"]}, connection=c)
        return result

    @app.put("/api/notices/{nid}")
    async def edit_notice(nid: str, body: NoticeEdit, user=Depends(manager)):
        data = values(body)
        with store.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            row = get_notice(c, nid, user)
            expect(row, body.expected_version)
            if row["archived"]:
                raise HTTPException(409, "보관을 해제한 뒤 공지를 수정하세요.")
            c.execute("UPDATE notices SET title=?,body=?,roles=?,pinned=?,published=?,version=version+1,"
                      "updated_by=?,updated=? WHERE id=?", (*data, user["id"], time.time(), nid))
            version = row["version"] + 1
            store.audit(user["id"], "update_notice", nid, detail={"version": version}, connection=c)
        return {"id": nid, "version": version}

    @app.post("/api/notices/{nid}/archive")
    async def archive_notice(nid: str, body: NoticeArchive, user=Depends(manager)):
        with store.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            row = get_notice(c, nid, user)
            expect(row, body.expected_version)
            changed = bool(row["archived"]) != body.archived
            if changed:
                c.execute("UPDATE notices SET archived=?,version=version+1,updated_by=?,updated=? WHERE id=?",
                          (int(body.archived), user["id"], time.time(), nid))
                store.audit(user["id"], "archive_notice", nid,
                            detail={"version": row["version"]+1, "archived": body.archived}, connection=c)
        return {"id": nid, "version": row["version"] + int(changed)}

    @app.post("/api/notices/{nid}/read")
    async def read_notice(nid: str, body: NoticeRead, user=Depends(current)):
        with store.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            row = get_notice(c, nid, user)
            expect(row, body.expected_version)
            if row["archived"] or not row["published"]:
                raise HTTPException(409, "게시 중인 공지만 확인할 수 있습니다.")
            c.execute("INSERT INTO notice_reads VALUES(?,?,?,?) ON CONFLICT(user_id,notice_id,version) "
                      "DO UPDATE SET read_at=excluded.read_at", (user["id"], nid, row["version"], time.time()))
            store.audit(user["id"], "read_notice", nid, detail={"version": row["version"]}, connection=c)
        return {"ok": True}

    @app.get("/api/portal")
    def portal(user=Depends(current)):
        today = datetime.now(timezone(timedelta(hours=9))).date().isoformat()
        with store.connect(shared=True) as c:
            # One database snapshot keeps cards and counts consistent during concurrent edits.
            me = c.execute("SELECT id,name,role,balance FROM users WHERE id=?", (user["id"],)).fetchone()
            own = [dict(r) for r in c.execute(
                "SELECT id,date,days,status,created FROM leaves WHERE user_id=? ORDER BY created DESC,id DESC", (user["id"],))]
            waiting = sum(r["status"] == "pending" for r in own)
            upcoming = sorted((r for r in own if r["status"] == "approved" and r["date"] >= today), key=lambda r: r["date"])
            news = [view(c, r, user) for r in c.execute(
                "SELECT * FROM notices WHERE published=1 AND archived=0 ORDER BY pinned DESC,updated DESC,id") if allowed(r, user)]
            unread_news = [n for n in news if not n["read"]]
            docs = []
            for d in c.execute("SELECT d.id,d.title,d.version,d.roles,d.updated,d.kind FROM documents d LEFT JOIN document_reads r ON r.doc_id=d.id AND r.version=d.version AND r.user_id=? WHERE d.approved=1 AND d.archived=0 AND r.doc_id IS NULL ORDER BY d.updated DESC,d.id", (user['id'],)):
                if user["role"] != "admin" and user["role"] not in json.loads(d["roles"]):
                    continue
                docs.append({key: d[key] for key in ("id", "title", "version", "updated", "kind")})
            courses = [course_view(c, r, user) for r in c.execute(
                "SELECT * FROM onboarding_courses WHERE active=1 ORDER BY updated DESC,id")
                if user["role"] == "admin" or user["role"] in json.loads(r["roles"])]
            completed, total = sum(r["completed"] for r in courses), sum(r["total"] for r in courses)
            education = {"completed": completed, "total": total, "percent": int(100*completed/total) if total else 0,
                         "courses": [{key: r[key] for key in ("id", "title", "completed", "total", "percent", "blocked")} for r in courses]}
            result = {"user": dict(me), "today": today, "leave": {"balance": me["balance"], "pending": waiting,
                      "recent": own[:5], "upcoming": upcoming[:3]},
                      "notices": {"unread": len(unread_news), "items": news[:5]},
                      "documents": {"unread": len(docs), "items": docs[:5]}, "onboarding": education}
            if user["role"] == "admin":
                leaves = [dict(r) for r in c.execute(
                    "SELECT l.id,l.date,l.days,u.name FROM leaves l JOIN users u ON u.id=l.user_id "
                    "WHERE l.status='pending' ORDER BY l.created,l.id")]
                drafts = [dict(r) for r in c.execute(
                    "SELECT id,title,version FROM documents WHERE approved=0 AND archived=0 ORDER BY updated,id")]
                result["approvals"] = {"leave_count": len(leaves), "document_count": len(drafts),
                                       "leaves": leaves[:5], "documents": drafts[:5]}
        return result
