"""Source-grounded publishing workbench. No external publishing or patient-data reads."""
import hashlib
import html
import json
import logging
import re
import time
import uuid
from datetime import date, timedelta
from urllib.parse import urlsplit
from typing import Literal

from fastapi import Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from ai_services import AIUnavailable, BlogGenerationError, BLOG_ENGINE_VERSION

GEO_ENGINE_VERSION = "2026.11.16-geo-fix3"


def url(value, optional=True):
    if not value and optional:
        return ""
    try:
        p = urlsplit(value)
        if p.scheme not in {"http", "https"} or not p.hostname or p.username or p.password:
            raise ValueError()
        return value
    except ValueError:
        raise HTTPException(400, "출처·게시 주소는 올바른 http 또는 https URL을 입력하세요.")


def day(value):
    try:
        date.fromisoformat(value)
        return value
    except ValueError:
        raise HTTPException(400, "날짜를 확인하세요.")


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def norm(text):
    return re.sub(r"\s+", " ", text).strip()


def validate_blog(result, sources):
    """Reject invented citations/numbers; factual entailment still needs human review."""
    allowed = {s["source_id"]: s for s in sources}
    if not isinstance(result, dict):
        raise BlogGenerationError("LLM 응답이 원고 객체 형식이 아닙니다.")
    result = dict(result)
    # Some local model/server combinations serialize booleans as JSON strings.
    # Only canonical boolean strings are accepted; all grounding checks still run.
    if isinstance(result.get("sufficient"), str) and result["sufficient"].strip().lower() in {"true", "false"}:
        result["sufficient"] = result["sufficient"].strip().lower() == "true"
    if not isinstance(result.get("sufficient"), bool):
        raise BlogGenerationError("LLM 응답의 sufficient 항목이 true/false 형식이 아닙니다.")
    if result["sufficient"] is False:
        reason = str(result.get("reason", "")).strip()[:250]
        if not reason:
            reason = "선택한 자료에서 이 주제를 설명할 근거를 찾지 못했다고 모델이 응답했습니다."
        raise BlogGenerationError(reason, "GEO_INSUFFICIENT_EVIDENCE", 422)
    def refs(text):
        if not isinstance(text, str):
            return text
        text = text.replace("［", "[").replace("］", "]")
        text = re.sub(r"\[\s*(S\d+)\s*\]", lambda m: "[" + m[1].upper() + "]", text, flags=re.I)
        return re.sub(r"\[\s*S\d+(?:\s*[,·/]\s*S\d+)+\s*\]", lambda m: " ".join("[" + s.upper() + "]" for s in re.findall(r"S\d+", m[0], re.I)), text, flags=re.I)
    result["answer"] = refs(result.get("answer"))
    for field, text_key in (("sections", "body"), ("faq", "answer")):
        if isinstance(result.get(field), list):
            result[field] = [{**item, text_key: refs(item.get(text_key))} if isinstance(item, dict) else item for item in result[field]]
    for field, limit in (("title", 140), ("summary", 500), ("answer", 3000)):
        if not isinstance(result.get(field), str) or not result[field].strip() or len(result[field]) > limit:
            raise BlogGenerationError("LLM 원고 형식이 올바르지 않습니다. 다시 생성하세요.")
    sections, faq = result.get("sections"), result.get("faq")
    if not isinstance(sections, list) or not 0 <= len(sections) <= 6 or not isinstance(faq, list) or len(faq) > 4:
        raise BlogGenerationError("LLM 본문·FAQ 형식을 확인할 수 없습니다. 다시 생성하세요.")
    citations = result.get("citations")
    if not isinstance(citations, list) or not 1 <= len(citations) <= 12:
        raise BlogGenerationError("LLM이 본문을 작성했지만 사용할 인용문을 반환하지 않았습니다. 실제 원문 인용을 추가해 다시 작성하세요.", "GEO_CITATION_MISSING")
    verified = []
    for item in citations:
        if not isinstance(item, dict) or item.get("source_id") not in allowed:
            raise BlogGenerationError("LLM이 제공되지 않은 출처를 사용했습니다.")
        quote = item.get("quote", "")
        if not isinstance(quote, str) or len(norm(quote)) < 8 or norm(quote) not in norm(allowed[item["source_id"]]["text"]):
            raise BlogGenerationError("LLM 인용문이 자료 원문과 일치하지 않아 원고 저장을 중단했습니다.")
        citation = {"source_id": item["source_id"], "quote": quote}
        if "evidence_id" in item:
            start, end = item.get("start"), item.get("end")
            if (not isinstance(item["evidence_id"], str) or not re.fullmatch(r"E\d+", item["evidence_id"])
                    or type(start) is not int or type(end) is not int or not 0 <= start < end <= len(allowed[item["source_id"]]["text"])
                    or allowed[item["source_id"]]["text"][start:end] != quote):
                raise BlogGenerationError("인용 구간의 원문 위치가 일치하지 않습니다.", "GEO_EVIDENCE_INVALID")
            citation.update(evidence_id=item["evidence_id"], start=start, end=end, time_label=str(item.get("time_label", ""))[:30])
        verified.append(citation)
    cited = {i["source_id"] for i in verified}
    blocks = [result["answer"]]
    for section in sections:
        if not isinstance(section, dict) or any(not isinstance(section.get(k), str) or not section[k].strip() or len(section[k]) > 3000 for k in ("heading", "body")):
            raise BlogGenerationError("LLM 문단 형식이 올바르지 않습니다.")
        blocks.extend([section["heading"], section["body"]])
    for item in faq:
        if not isinstance(item, dict) or any(not isinstance(item.get(k), str) or not item[k].strip() or len(item[k]) > 2000 for k in ("question", "answer")):
            raise BlogGenerationError("LLM FAQ 형식이 올바르지 않습니다.")
        blocks.extend([item["question"], item["answer"]])
    # Each answer paragraph needs at least one source whose exact quote was checked.
    for block in [result["answer"]] + [s["body"] for s in sections] + [f["answer"] for f in faq]:
        refs = re.findall(r"\[(S\d+)\]", block)
        if not refs or any(ref not in cited for ref in refs):
            raise BlogGenerationError("문단별 출처가 누락되었거나 인용이 검증되지 않았습니다. 다시 생성하세요.")
    prose = " ".join([result["title"], result["summary"], *blocks])
    clean = re.sub(r"\[S\d+\]", "", prose)
    evidence = " ".join(allowed[s]["text"] for s in cited)
    if any(n not in evidence for n in re.findall(r"\d+(?:\.\d+)?", clean)):
        raise BlogGenerationError("자료에 없는 숫자가 원고에서 발견되었습니다. 수치가 있는 공개 자료를 보강하세요.")
    missing = result.get("missing_information", [])
    if not isinstance(missing, list) or len(missing) > 5 or any(not isinstance(x, str) or len(x) > 300 for x in missing):
        raise BlogGenerationError("추가 확인 항목 형식이 올바르지 않습니다.")
    return {**result, "citations": verified, "missing_information": missing}


def blog_markdown(result, question, sources, article=False):
    parts = (["# " + result["title"], result["answer"]] if article else
             ["# " + result["title"], result["summary"], "## " + question, result["answer"]])
    for s in result["sections"]:
        parts += ["## " + s["heading"], s["body"]]
    if result["faq"]:
        parts.append("## 자주 묻는 질문")
        for f in result["faq"]:
            parts += ["### " + f["question"], f["answer"]]
    parts.append("## 근거 자료")
    for s in sources:
        parts.append(f'[{s["source_id"]}] {s["title"]} · {s["url"] or "제공 원문"} · 확인일 {s["checked_on"]}')
    return "\n\n".join(parts)


def quality(text, sources, others=()):
    warnings = []
    for pattern, message in [
        (r"100\s*%|완치|부작용\s*(?:없|제로)|무조건|효과\s*보장|최고|유일|1위", "효과 보장·우월성 표현 후보를 확인하세요."),
        (r"환자\s*(?:후기|사례)|치료\s*경험담|전후\s*사진", "치료 경험·사례 표현과 자료 사용 범위를 확인하세요."),
        (r"DEMO-\d+|\b01[016789][- ]?\d{3,4}[- ]?\d{4}\b|\d{6}-[1-4]\d{6}", "개인정보·가상환자 식별자 후보가 있습니다. 공개 원고에서 제거하세요."),
    ]:
        if re.search(pattern, text, re.I):
            warnings.append(message)
    if len(text.strip()) < 350:
        warnings.append("원고가 짧습니다. 질문에 필요한 설명이 충분한지 확인하세요.")
    if not re.search(r"\[S\d+\]", text):
        warnings.append("본문 출처 표기가 없습니다.")
    if not sources or any(not s["url"] for s in sources):
        warnings.append("공개 출처 URL이 없는 자료가 있습니다.")
    if any(norm(text) == norm(t) for t in others):
        warnings.append("저장된 다른 원고와 본문이 같습니다. 중복 주제를 확인하세요.")
    article_part=text.split('## 근거 자료',1)[0]
    prose='\n'.join(line for line in article_part.splitlines() if not re.match(r'^#{1,3} ',line))
    prose=re.sub(r'\[S\d+\]','',prose)
    from blog_writer import sentence_repetition
    if sentence_repetition([prose])>.22:
        warnings.append('같은 내용의 문장 반복이 많습니다. 소제목별로 다른 설명을 담아 다시 작성하세요.')
    if re.search(r'(?<![A-Za-z0-9])E\d+(?![A-Za-z0-9])',prose):
        warnings.append('내부 근거 구간 번호가 설명문에 노출되어 있습니다. 독자를 위한 문장으로 정리하세요.')
    return {"warnings": warnings, "characters": len(text), "body_characters":len(re.sub(r'\s+',' ',prose).strip()), "source_count": len(sources),
            "note": "형식·문구 검사입니다. 사실성·의학적 타당성·광고 적법성을 자동 보증하지 않습니다."}


class SourceInput(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    text: str = Field(min_length=8, max_length=24000)
    url: str = Field(default="", max_length=1000)
    checked_on: str
    public_confirmed: bool = False
    expected_revision: int | None = None


class GenerateInput(BaseModel):
    question: str = Field(min_length=3, max_length=200)
    source_ids: list[str] = Field(min_length=1, max_length=5)
    planned_on: str = ""
    channel: str = Field(default="블로그", max_length=40)
    plan_id: str = ""
    expected_revision: int | None = None
    length: Literal['standard','detailed'] = 'standard'


class DraftInput(BaseModel):
    expected_revision: int
    title: str = Field(min_length=1, max_length=140)
    summary: str = Field(default="", max_length=500)
    body: str = Field(min_length=1, max_length=50000)
    planned_on: str = ""
    channel: str = Field(default="블로그", max_length=40)


def install_geo(app, store, editor, ai, server_version="unknown"):
    if not getattr(store, "cloud_schema_ready", False):
        with store.connect() as c:
            c.executescript("""
            CREATE TABLE IF NOT EXISTS geo_profile(id INTEGER PRIMARY KEY CHECK(id=1),data TEXT NOT NULL,revision INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS geo_sources(id TEXT PRIMARY KEY,title TEXT NOT NULL,text TEXT NOT NULL,url TEXT NOT NULL,checked_on TEXT NOT NULL,revision INTEGER NOT NULL,doc_id TEXT NOT NULL DEFAULT '',doc_version INTEGER);
            CREATE TABLE IF NOT EXISTS geo_drafts(id TEXT PRIMARY KEY,data TEXT NOT NULL,revision INTEGER NOT NULL,updated REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS geo_observations(id TEXT PRIMARY KEY,data TEXT NOT NULL,created REAL NOT NULL);
            """)
            c.execute("INSERT OR IGNORE INTO geo_profile VALUES(1,?,1)", (json.dumps({"name": "가상 예시의원", "website": "https://clinic.example.org/", "address": "", "phone": "", "source_url": "https://clinic.example.org/", "checked_on": "2026-11-16", "confirmed": False}, ensure_ascii=False),))
            c.execute("INSERT OR IGNORE INTO geo_sources VALUES(?,?,?,?,?,1,'',NULL)", ("GS-OFFICIAL", "가상 예시의원 공식 홈페이지 소개", "가상 예시의원 공식 홈페이지는 모발이식, 탈모치료, 헤드스파를 진료 분야로 안내합니다.", "https://clinic.example.org/", "2026-11-16"))

    def profile(c):
        r = c.execute("SELECT * FROM geo_profile WHERE id=1").fetchone()
        return {**json.loads(r["data"]), "revision": r["revision"]}

    def source_current(c, s):
        if s.get("doc_id"):
            d = c.execute("SELECT version,approved,archived FROM documents WHERE id=?", (s["doc_id"],)).fetchone()
            return bool(d and d["approved"] and not d["archived"] and d["version"] == s["doc_version"])
        return True

    def get_draft(c, did):
        r = c.execute("SELECT * FROM geo_drafts WHERE id=?", (did,)).fetchone()
        if not r:
            raise HTTPException(404, "원고를 찾을 수 없습니다.")
        d = {**json.loads(r["data"]), "id": did, "revision": r["revision"], "updated": r["updated"]}
        stale = profile(c)["revision"] != d.get("profile_revision")
        for s in d.get("sources", []):
            now = c.execute("SELECT * FROM geo_sources WHERE id=?", (s["id"],)).fetchone()
            if not now or now["revision"] != s["revision"] or not source_current(c, s):
                stale = True
        d["source_stale"] = stale
        d["quality"] = quality(d.get("body", ""), d.get("sources", []), [json.loads(x[0]).get("body", "") for x in c.execute("SELECT data FROM geo_drafts WHERE id!=?", (did,))])
        return d

    def write(c, d, expected=None):
        did = d["id"]
        data = {k: v for k, v in d.items() if k not in {"id", "revision", "updated", "source_stale", "quality"}}
        if expected is None:
            c.execute("INSERT INTO geo_drafts VALUES(?,?,1,?)", (did, json.dumps(data, ensure_ascii=False), time.time()))
        elif not c.execute("UPDATE geo_drafts SET data=?,revision=revision+1,updated=? WHERE id=? AND revision=?", (json.dumps(data, ensure_ascii=False), time.time(), did, expected)).rowcount:
            raise HTTPException(409, "원고 버전이 바뀌었습니다. 목록에서 다시 열어주세요.")
        return get_draft(c, did)

    @app.get("/api/geo/workspace")
    async def workspace(user=Depends(editor)):
        with store.connect() as c:
            sources = [{**dict(s), "current": source_current(c, dict(s))} for s in c.execute("SELECT * FROM geo_sources ORDER BY id")]
            drafts = [get_draft(c, r[0]) for r in c.execute("SELECT id FROM geo_drafts ORDER BY updated DESC LIMIT 200").fetchall()]
            observations = [{"id": r["id"], **json.loads(r["data"])} for r in c.execute("SELECT * FROM geo_observations ORDER BY created DESC LIMIT 500")]
            return {"server_version": server_version, "version": GEO_ENGINE_VERSION, "llm_version": BLOG_ENGINE_VERSION, "profile": profile(c), "sources": sources, "drafts": drafts, "observations": observations, "chat_model": ai.chat_model}

    @app.put("/api/geo/profile")
    async def save_profile(body: dict, user=Depends(editor)):
        fields = {k: str(body.get(k, "")).strip()[:1000] for k in ("name", "website", "address", "phone", "source_url", "checked_on")}
        if not fields["name"] or body.get("confirmed") is not True:
            raise HTTPException(400, "병원 정보와 출처를 확인한 뒤 확인 항목을 선택하세요.")
        url(fields["website"], False); url(fields["source_url"], False); day(fields["checked_on"])
        with store.connect() as c:
            if not c.execute("UPDATE geo_profile SET data=?,revision=revision+1 WHERE id=1 AND revision=?", (json.dumps({**fields, "confirmed": True}, ensure_ascii=False), body.get("expected_revision"))).rowcount:
                raise HTTPException(409, "병원 정보가 변경되었습니다. 다시 불러오세요.")
            store.audit(user["id"], "geo_profile", "clinic", connection=c)
            return profile(c)

    def source_values(body):
        if not body.public_confirmed or not body.title.strip() or not body.text.strip():
            raise HTTPException(400, "원문과 외부 공개 가능 여부를 확인하세요.")
        return (body.title.strip(), body.text.strip(), url(body.url), day(body.checked_on))

    @app.post("/api/geo/sources")
    async def add_source(body: SourceInput, user=Depends(editor)):
        values = source_values(body); sid = "GS-" + uuid.uuid4().hex[:12]
        with store.connect() as c:
            c.execute("INSERT INTO geo_sources VALUES(?,?,?,?,?,1,'',NULL)", (sid, *values))
            store.audit(user["id"], "geo_source_create", sid, connection=c)
        return {"id": sid}

    @app.put("/api/geo/sources/{sid}")
    async def update_source(sid: str, body: SourceInput, user=Depends(editor)):
        values = source_values(body)
        with store.connect() as c:
            if not c.execute("UPDATE geo_sources SET title=?,text=?,url=?,checked_on=?,revision=revision+1 WHERE id=? AND revision=?", (*values, sid, body.expected_revision)).rowcount:
                raise HTTPException(409, "자료 버전이 바뀌었습니다. 다시 열어주세요.")
            store.audit(user["id"], "geo_source_edit", sid, connection=c)
        return {"ok": True}

    @app.post("/api/geo/import-document")
    async def import_document(body: dict, user=Depends(editor)):
        if body.get("public_confirmed") is not True:
            raise HTTPException(400, "인트라넷 승인과 외부 공개 허용은 별개입니다. 공개 가능 여부를 확인하세요.")
        with store.connect() as c:
            d = c.execute("SELECT * FROM documents WHERE id=?", (str(body.get("doc_id", "")),)).fetchone()
            if not d or d["archived"] or not d["approved"] or (user["role"] != "admin" and user["role"] not in json.loads(d["roles"])):
                raise HTTPException(404, "승인된 열람 가능 자료를 선택하세요.")
            if d["version"] != body.get("expected_version"):
                raise HTTPException(409, "원문 버전이 변경되었습니다. 자료를 다시 선택하세요.")
            if len(d["content"]) > 24000:
                raise HTTPException(400, "긴 원문은 필요한 구간을 복사해 공개 자료로 등록하세요. (24,000자 이내)")
            sid = "GS-DOC-" + d["id"]
            checked = day(str(body.get("checked_on", "")))
            c.execute("INSERT INTO geo_sources VALUES(?,?,?,?,?,1,?,?) ON CONFLICT(id) DO UPDATE SET title=excluded.title,text=excluded.text,url=excluded.url,checked_on=excluded.checked_on,revision=geo_sources.revision+1,doc_id=excluded.doc_id,doc_version=excluded.doc_version", (sid, d["title"], d["content"][:24000], d["source_url"], checked, d["id"], d["version"]))
            store.audit(user["id"], "geo_source_import", sid, detail={"version": d["version"]}, connection=c)
        return {"id": sid}

    async def generate_draft(body: GenerateInput, user, article=False):
        if server_version != GEO_ENGINE_VERSION or BLOG_ENGINE_VERSION != GEO_ENGINE_VERSION:
            raise HTTPException(409, "서버·GEO·생성 모듈의 버전이 다릅니다. FIX3 업데이트를 적용하고 서버를 재시작하세요.")
        if body.planned_on:
            day(body.planned_on)
        with store.connect() as c:
            p = profile(c); sources = []
            for sid in dict.fromkeys(body.source_ids):
                r = c.execute("SELECT * FROM geo_sources WHERE id=?", (sid,)).fetchone()
                if not r or not source_current(c, dict(r)):
                    raise HTTPException(409, "선택 자료가 변경되었거나 사용 중지되었습니다. 최신 원문을 다시 가져오세요.")
                sources.append({**dict(r), "source_id": "S" + str(len(sources) + 1)})
        if sum(len(s["text"]) for s in sources) > 28000:
            raise HTTPException(400, "선택 자료가 너무 깁니다. 필요한 구간을 별도 자료로 등록하세요. (합계 28,000자)")
        # One bounded repair attempt. Relevance refusals get a second, narrower
        # editorial instruction; invalid output never becomes a fake template draft.
        feedback = ""
        if article:
            from blog_writer import complete_article
            result,generation_attempts=await complete_article(ai,body.question,sources,validate_blog,body.length)
            used=set(re.findall(r'\[(S\d+)\]',result['answer']+' '.join(s['body'] for s in result['sections'])+' '.join(f['answer'] for f in result['faq'])))
            sources=[s for s in sources if s['source_id'] in used]
            result['citations']=[c for c in result['citations'] if c['source_id'] in used]
        for attempt in range(0 if article else 2):
            try:
                candidate = (await ai.generate_blog(body.question, sources) if not feedback
                             else await ai.generate_blog(body.question, sources, feedback=feedback))
                result = validate_blog(candidate, sources)
                break
            except BlogGenerationError as exc:
                logging.getLogger("clinicworkspace").warning("GEO generation rejected code=%s attempt=%s sources=%s chars=%s", exc.code, attempt+1, len(sources), sum(len(s["text"]) for s in sources))
                feedback = str(exc)
                if attempt:
                    if exc.code == "GEO_INSUFFICIENT_EVIDENCE":
                        count = sum(len(s["text"]) for s in sources)
                        raise BlogGenerationError(f"선택 자료 {len(sources)}개({count:,}자)로 이 주제의 초안을 작성하지 못했습니다. {feedback} 공개 자료를 보강하거나 자료에 나온 내용을 주제로 선택하세요.", exc.code, 422) from exc
                    raise BlogGenerationError("LLM 원고 형식을 자동으로 다시 작성했지만 검사를 통과하지 못했습니다. " + feedback, exc.code, exc.status_code) from exc
        draft = {"id": "G-" + uuid.uuid4().hex[:12], "question": body.question, "title": result["title"], "summary": result["summary"], "body": blog_markdown(result, body.question, sources,article=article), "sources": sources, "citations": result["citations"], "profile": p, "profile_revision": p["revision"], "planned_on": body.planned_on, "channel": body.channel, "stage": "review", "review": None, "published_url": "", "model": ai.chat_model, "generation_attempts": generation_attempts if article else attempt + 1, "missing_information": result.get("missing_information", [])}
        if article:
            from blog_writer import ARTICLE_VERSION
            draft.update(format='article',article_version=ARTICLE_VERSION,article_length=body.length,article_metrics=result['article_metrics'])
        with store.connect() as c:
            if body.plan_id:
                planned = get_draft(c, body.plan_id)
                if planned["stage"] != "planned" or body.expected_revision is None:
                    raise HTTPException(409, "초안이 이미 생성된 일정입니다. 원고 목록에서 확인하세요.")
                draft["id"] = body.plan_id
            d = write(c, draft, body.expected_revision if body.plan_id else None)
            store.audit(user["id"], "geo_generate", d["id"], connection=c)
            return d

    @app.post('/api/geo/generate')
    async def generate(body: GenerateInput, user=Depends(editor)):
        return await generate_draft(body,user)

    @app.post('/api/geo/articles')
    async def generate_article(body: GenerateInput, user=Depends(editor)):
        return await generate_draft(body,user,article=True)

    @app.put("/api/geo/drafts/{did}")
    async def save_draft(did: str, body: DraftInput, user=Depends(editor)):
        if body.planned_on:
            day(body.planned_on)
        with store.connect() as c:
            d = get_draft(c, did)
            d.update(body.model_dump(exclude={"expected_revision"}), stage="review", review=None)
            # Keep the visible H1 aligned with the editable page title.
            d["body"] = re.sub(r"^# [^\n]*", lambda m: "# " + d["title"], d["body"], count=1)
            return write(c, d, body.expected_revision)

    @app.post("/api/geo/drafts/{did}/review")
    async def review(did: str, body: dict, user=Depends(editor)):
        if not all(body.get(k) is True for k in ("facts", "medical", "publication", "warnings_ack")):
            raise HTTPException(400, "근거·의료 내용·공개 범위와 자동 점검 결과를 모두 확인하세요.")
        with store.connect() as c:
            d = get_draft(c, did)
            if body.get("expected_revision") != d["revision"]:
                raise HTTPException(409, "원고 버전이 변경되었습니다. 다시 열어주세요.")
            if not d["body"].strip() or d["stage"] == "planned":
                raise HTTPException(400, "원고를 먼저 생성하세요.")
            if d["source_stale"]:
                raise HTTPException(409, "원문 또는 병원 정보가 바뀌었습니다. 최신 자료로 다시 생성하세요.")
            d["review"] = {"actor": user["id"], "name": user["name"], "at": time.time(), "hash": digest(d["body"]), "checks": ["facts", "medical", "publication", "warnings_ack"]}
            d["stage"] = "ready"
            d = write(c, d, body.get("expected_revision"))
            store.audit(user["id"], "geo_review", did, connection=c)
            return d

    def exportable(d):
        if not d.get("review") or d["source_stale"] or d["review"]["hash"] != digest(d["body"]):
            raise HTTPException(409, "최신 원문·병원 정보 기준 검토를 마친 원고만 내보내거나 게시 URL을 기록할 수 있습니다.")

    @app.get("/api/geo/drafts/{did}/export")
    async def export(did: str, format: str = "md", user=Depends(editor)):
        with store.connect() as c:
            d = get_draft(c, did); exportable(d)
            store.audit(user["id"], "geo_export", did, detail={"format": format}, connection=c)
        if format not in {"md", "html"}:
            raise HTTPException(400, "Markdown 또는 HTML 형식만 지원합니다.")
        p = d["profile"]
        footer = "\n\n---\n병원 정보\n" + "\n".join(str(p.get(k, "")) for k in ("name", "website", "address", "phone") if p.get(k)) if p.get("confirmed") else ""
        content = d["body"] + footer
        if format == "html":
            e = html.escape
            lines = []
            for para in content.split("\n\n"):
                match = re.match(r"^(#{1,3}) (.*)$", para, re.S)
                lines.append(f'<h{len(match[1])}>{e(match[2])}</h{len(match[1])}>' if match else '<p>' + e(para).replace('\n', '<br>') + '</p>')
            schema = {"@context": "https://schema.org", "@type": "BlogPosting", "headline": d["title"], "description": d["summary"], "articleBody": content, "inLanguage": "ko", "citation": [s["url"] for s in d["sources"] if s["url"]]}
            # No fake publication date, author credentials, rating or FAQ rich-result promise.
            data = json.dumps(schema, ensure_ascii=False).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
            content = f'<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{e(d["title"])}</title><meta name="description" content="{e(d["summary"], quote=True)}"><style>body{{max-width:760px;margin:40px auto;padding:0 22px;font:17px/1.9 sans-serif;color:#19263b}}p{{overflow-wrap:anywhere}}h1{{line-height:1.4}}</style><article>{"".join(lines)}</article><script type="application/ld+json">{data}</script></html>'
        return Response(content, media_type="text/html" if format == "html" else "text/markdown", headers={"Content-Disposition": f'attachment; filename="{did}.{format}"'})

    @app.post("/api/geo/drafts/{did}/publication")
    async def publication(did: str, body: dict, user=Depends(editor)):
        published = url(str(body.get("url", "")), False)
        with store.connect() as c:
            d = get_draft(c, did); exportable(d)
            if body.get("expected_revision") != d["revision"]:
                raise HTTPException(409, "원고 버전이 변경되었습니다. 다시 열어주세요.")
            d.update(stage="published", published_url=published, recorded_on=time.time())
            d = write(c, d, body.get("expected_revision"))
            store.audit(user["id"], "geo_publication_record", did, connection=c)
            return d

    @app.post("/api/geo/plan")
    async def plan(body: dict, user=Depends(editor)):
        start = date.fromisoformat(day(str(body.get("start_on", ""))))
        with store.connect() as c:
            sources = c.execute("SELECT * FROM geo_sources ORDER BY id").fetchall()
            sources = [dict(s) for s in sources if source_current(c, dict(s))]
            if not sources:
                raise HTTPException(400, "공개 자료를 먼저 등록하세요.")
            p = profile(c); ids = []
            for i in range(4):
                s = sources[i % len(sources)]
                topic = ["핵심 안내", "자주 묻는 질문", "상담 전 확인할 내용", "안내 내용 다시 점검하기"][i]
                question = s["title"] + " · " + topic
                planned = (start + timedelta(days=7*i)).isoformat()
                if any(json.loads(r[0]).get("question") == question and json.loads(r[0]).get("planned_on") == planned for r in c.execute("SELECT data FROM geo_drafts")):
                    continue
                did = "G-" + uuid.uuid4().hex[:12]
                write(c, {"id": did, "title": question, "question": question, "summary": "", "body": "", "sources": [], "profile": p, "profile_revision": p["revision"], "planned_on": planned, "channel": "블로그", "stage": "planned", "review": None, "published_url": "", "suggested_source_ids": [s["id"]]})
                ids.append(did)
            return {"created": len(ids), "ids": ids}

    @app.post("/api/geo/observations")
    async def observation(body: dict, user=Depends(editor)):
        values = {k: str(body.get(k, "")).strip() for k in ("question", "service", "answer", "observed_on", "search_mode")}
        if not values["question"] or not values["service"] or not values["answer"] or any(len(v) > 20000 for v in values.values()):
            raise HTTPException(400, "질문·서비스·실제 관찰 답변을 입력하세요.")
        day(values["observed_on"])
        urls = list(dict.fromkeys(re.findall(r'https?://[^\s<>\[\]()"\u3002]+', values["answer"])))[:50]
        with store.connect() as c:
            p = profile(c); host = urlsplit(p["website"]).hostname
            host = host.removeprefix("www.") if host else ""
            own = [u for u in urls if (urlsplit(u.rstrip('.,')).hostname or "").removeprefix("www.") == host and host]
            values.update(mentioned=p["name"].replace(" ", "") in values["answer"].replace(" ", ""), own_citations=own, urls=urls, website=p["website"], actor=user["id"])
            oid = "O-" + uuid.uuid4().hex[:12]
            c.execute("INSERT INTO geo_observations VALUES(?,?,?)", (oid, json.dumps(values, ensure_ascii=False), time.time()))
            store.audit(user["id"], "geo_observation", oid, connection=c)
            return {"id": oid, **values}
