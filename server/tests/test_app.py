"""Deterministic API tests. FakeAI is ONLY an injected test double, never a runtime fallback."""
import asyncio
import io
import json
import os
import tempfile
import unittest
import zoneinfo
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

import numpy as np
from fastapi.testclient import TestClient

from ai_services import AIUnavailable, validate_generation
from app import create_app, make_sample, settings_from_env, today_in_korea


class FakeAI:
    embedding_model = "TEST-ONLY-EMBEDDING"
    chat_model = "TEST-ONLY-LLM"
    def __init__(self):
        self.index_lock = asyncio.Lock()
        self.seen_sources = []
    async def status(self):
        return {"ollama": False, "embedding_ready": False, "chat_ready": False,
                "whisper_model": "TEST", "models": [], "chat_model": self.chat_model,
                "embedding_model": self.embedding_model}
    async def embedding_digest(self):
        return "TEST-DIGEST"
    async def embed(self, texts):
        # Deliberately simple: validates application flow, not semantic model quality.
        vec = np.asarray([[1, .01*(len(t)%5), .01*(len(t)%3)] for t in texts], dtype=np.float32)
        return vec / np.linalg.norm(vec, axis=1, keepdims=True)
    async def generate(self, question, sources):
        self.seen_sources = sources
        return {"status": "insufficient", "answer": "TEST-ONLY", "citations": []}


class ApplicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        cfg = settings_from_env()
        cfg["data_dir"] = self.temp.name
        self.ai = FakeAI()
        self.app = create_app(cfg, self.ai)
        self.admin = TestClient(self.app)
        self.doctor = TestClient(self.app)
        self.staff = TestClient(self.app)
        self.phone = TestClient(self.app)
        for client, username in [(self.admin, "admin"), (self.doctor, "doctor"), (self.staff, "staff")]:
            r = client.post("/api/login", json={"username": username, "password": "ClinicDemo42!"})
            self.assertEqual(r.status_code, 200)
            client.headers["X-CSRF-Token"] = r.json()["csrf"]

    def tearDown(self):
        for c in (self.admin, self.doctor, self.staff, self.phone):
            c.close()
        self.temp.cleanup()

    def new_capture(self, pid="DEMO-101"):
        r = self.doctor.post("/api/capture-sessions", json={"patient_id": pid, "base_url": "http://testserver"})
        self.assertEqual(r.status_code, 200)
        return parse_qs(urlsplit(r.json()["url"]).query)["token"][0]

    def upload(self, token, data=None):
        return self.phone.post("/api/capture/"+token, files={"file": ("photo.png", data or make_sample(), "image/png")})

    def test_login_and_server_roles(self):
        self.assertEqual(self.phone.get("/api/me").status_code, 401)
        self.assertEqual(self.phone.post("/api/login", json={"username": "admin", "password": "bad"}).status_code, 401)
        self.assertEqual(self.staff.get("/api/patients").status_code, 403)
        self.assertEqual(self.staff.get("/api/users").status_code, 403)
        self.assertEqual(self.doctor.get("/api/users").status_code, 403)
        self.assertEqual(len(self.admin.get("/api/users").json()["users"]), 3)
        self.assertEqual(self.staff.post("/api/capture-sessions", json={"patient_id": "DEMO-101", "base_url": "http://testserver"}).status_code, 403)

    def test_patient_search_and_leave_without_os_timezone_data(self):
        original_path = zoneinfo.TZPATH
        try:
            zoneinfo.reset_tzpath(())
            zoneinfo.ZoneInfo.clear_cache()
            with patch("zoneinfo._common.load_tzdata", side_effect=zoneinfo.ZoneInfoNotFoundError("No timezone data")):
                r = self.admin.get("/api/health")
                self.assertEqual(r.status_code, 200)
                self.assertEqual(r.json()["today"], today_in_korea())
                r = self.doctor.get("/api/patients", params={"q": "김하늘"})
                self.assertEqual(r.status_code, 200)
                self.assertEqual({p["id"] for p in r.json()["patients"]}, {"DEMO-101", "DEMO-102"})
                day = (date.fromisoformat(today_in_korea()) + timedelta(days=1)).isoformat()
                r = self.staff.post("/api/leaves", json={"date": day, "days": 1, "reason": "가상 검증"})
                self.assertEqual(r.status_code, 200)
                leave = self.staff.get("/api/leaves").json()["leaves"][0]
                self.assertEqual(leave["date"], day)
                self.assertEqual(leave["status"], "pending")
        finally:
            zoneinfo.reset_tzpath(original_path)
            zoneinfo.ZoneInfo.clear_cache()

    def test_unexpected_server_error_has_safe_json_and_error_id(self):
        with patch.object(self.app.state.store, "session", side_effect=RuntimeError("TEST PRIVATE STACK MESSAGE")):
            with self.assertLogs("clinicworkspace", level="ERROR") as log:
                r = self.admin.get("/api/patients", params={"q": "김하늘"})
        self.assertEqual(r.status_code, 500)
        self.assertIn("application/json", r.headers["content-type"])
        self.assertEqual(r.json()["code"], "INTERNAL_ERROR")
        self.assertRegex(r.json()["error_id"], r"^[a-f0-9]{12}$")
        self.assertNotIn("TEST PRIVATE STACK MESSAGE", r.text)
        self.assertTrue(any(r.json()["error_id"] in line for line in log.output))
        self.assertEqual(r.headers["cache-control"], "no-store")

    def test_csrf_and_cross_origin(self):
        old = self.admin.headers.pop("X-CSRF-Token")
        self.assertEqual(self.admin.post("/api/logout").status_code, 403)
        self.admin.headers["X-CSRF-Token"] = old
        self.assertEqual(self.admin.post("/api/logout", headers={"Origin": "https://outside.example"}).status_code, 403)

    def test_photo_other_client_persistence_and_replay(self):
        token = self.new_capture()
        self.assertEqual(self.phone.get("/api/capture/"+token).json()["patient_id"], "DEMO-101")
        r = self.upload(token)
        self.assertEqual(r.status_code, 200)
        photo_id = r.json()["photo_id"]
        self.assertEqual(self.doctor.get("/api/patients/DEMO-101/photos").json()["today"]["id"], photo_id)
        self.assertEqual(self.staff.get(f"/api/photos/{photo_id}").status_code, 403)
        self.assertEqual(self.doctor.get(f"/api/photos/{photo_id}").headers["content-type"], "image/jpeg")
        self.assertIsNone(self.doctor.get("/api/patients/DEMO-102/photos").json()["today"])
        self.assertEqual(self.upload(token).status_code, 410)
        # A new application instance reopens the same SQLite file.
        cfg = dict(self.app.state.cfg)
        new_app = create_app(cfg, FakeAI())
        self.assertTrue((Path(cfg["data_dir"])/"photos").glob("*.jpg"))
        with new_app.state.store.connect() as c:
            self.assertIsNotNone(c.execute("SELECT 1 FROM photos WHERE id=?", (photo_id,)).fetchone())

    def test_invalid_image_does_not_consume_capture(self):
        token = self.new_capture()
        self.assertEqual(self.upload(token, b"not an image").status_code, 400)
        self.assertEqual(self.upload(token).status_code, 200)

    def test_expired_and_revoked_capture(self):
        token = self.new_capture()
        self.doctor.post("/api/capture-sessions/revoke")
        self.assertEqual(self.upload(token).status_code, 410)
        token = self.new_capture()
        with self.app.state.store.connect() as c:
            c.execute("UPDATE capture_tokens SET expires=0")
        self.assertEqual(self.upload(token).status_code, 410)

    def test_logout_revokes_session_and_capture(self):
        token = self.new_capture()
        self.assertEqual(self.doctor.post("/api/logout").status_code, 200)
        self.assertEqual(self.doctor.get("/api/patients").status_code, 401)
        self.assertEqual(self.upload(token).status_code, 410)

    def test_leave_permissions_and_no_double_deduction(self):
        day = (date.today()+timedelta(days=7)).isoformat()
        r = self.staff.post("/api/leaves", json={"date": day, "days": 1})
        self.assertEqual(r.status_code, 200)
        lid = self.staff.get("/api/leaves").json()["leaves"][0]["id"]
        self.assertEqual(self.staff.post(f"/api/leaves/{lid}/decision", json={"approve": True}).status_code, 403)
        self.assertEqual(self.admin.post(f"/api/leaves/{lid}/decision", json={"approve": True}).status_code, 200)
        self.assertEqual(self.admin.post(f"/api/leaves/{lid}/decision", json={"approve": True}).status_code, 409)
        self.assertEqual(self.staff.get("/api/leaves").json()["balance"], 7)
        self.assertEqual(self.staff.post("/api/leaves", json={"date": day, "days": 1}).status_code, 409)

    def test_retrieval_filters_before_llm_context(self):
        self.assertEqual(self.admin.post("/api/documents/reindex").status_code, 200)
        self.assertEqual(self.staff.post("/api/documents/reindex").status_code, 403)
        visible = self.staff.get("/api/documents").json()["documents"]
        self.assertNotIn("D06", [d["id"] for d in visible])
        r = self.staff.post("/api/wiki/ask", json={"question": "관리자 전용 검증 문구를 알려주세요"})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["llm_used"])
        self.assertNotIn("D06", [s["doc_id"] for s in self.ai.seen_sources])
        self.assertNotIn("DEMO-ONLY-9X", json.dumps(self.ai.seen_sources))

    def test_document_revision_invalidates_index(self):
        self.admin.post("/api/documents/reindex")
        d = next(x for x in self.admin.get("/api/documents").json()["documents"] if x["id"] == "D01")
        d["expected_version"] = d["version"]
        d["content"] = "새 문서 내용이며 이전 안내는 철회했습니다."
        r = self.admin.put("/api/documents/D01", json=d)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["version"], 2)
        with self.app.state.store.connect() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM chunks WHERE doc_id='D01'").fetchone()[0], 0)

    def test_unapproved_document_not_retrieved(self):
        body = {"title": "미승인", "content": "UNAPPROVED-MARKER-123", "approved": False}
        r = self.admin.post("/api/documents", json=body)
        self.assertEqual(r.status_code, 200)
        self.admin.post("/api/documents/reindex")
        sources = self.staff.post("/api/wiki/search", json={"question": "미승인 내용"}).json()["sources"]
        self.assertNotIn(r.json()["id"], [s["doc_id"] for s in sources])

    def test_transcription_role_and_invalid_audio(self):
        files={"file": ("sample.wav", b"invalid audio", "audio/wav")}
        self.assertEqual(self.staff.post("/api/transcribe", data={"patient_id": "DEMO-101"}, files=files).status_code, 403)
        self.assertEqual(self.doctor.post("/api/transcribe", data={"patient_id": "DEMO-101"}, files=files).status_code, 400)

    def test_clinical_handoff_does_not_call_llm(self):
        r = self.staff.post("/api/wiki/ask", json={"question": "치료 효과를 보장하나요?"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "handoff")
        self.assertFalse(r.json()["llm_used"])

    def test_mock_crm_is_explicit_and_duplicate_safe(self):
        body={"patient_id": "DEMO-101", "text": "가상 기록", "reviewed": False}
        self.assertEqual(self.doctor.post("/api/notes", json=body).status_code, 400)
        body["reviewed"] = True
        first=self.doctor.post("/api/notes", json=body).json()
        again=self.doctor.post("/api/notes", json=body).json()
        self.assertFalse(first["crm_connected"])
        self.assertTrue(again["duplicate"])
        self.assertEqual(first["note"]["id"], again["note"]["id"])


class GenerationValidationTests(unittest.TestCase):
    def test_fabricated_quote_and_numbers_are_rejected(self):
        sources=[{"source_id":"C1","text":"예약 담당자가 변경 가능한 일정을 확인한 뒤 안내합니다."}]
        good={"sufficient":True,"answer":"담당자가 일정을 확인한 뒤 안내합니다.","citations":[{"source_id":"C1","quote":sources[0]["text"]}]}
        self.assertEqual(validate_generation(good,sources)["status"],"answered")
        bad={**good,"answer":"예약 비용은 30000원입니다."}
        self.assertEqual(validate_generation(bad,sources)["status"],"insufficient")
        bad={**good,"citations":[{"source_id":"C1","quote":"예약 변경은 항상 무료이며 바로 완료됩니다."}]}
        self.assertEqual(validate_generation(bad,sources)["status"],"insufficient")


class KoreanDateTests(unittest.TestCase):
    def test_date_changes_at_korean_midnight(self):
        self.assertEqual(today_in_korea(datetime(2026, 11, 14, 14, 59, tzinfo=timezone.utc)), "2026-11-14")
        self.assertEqual(today_in_korea(datetime(2026, 11, 14, 15, 0, tzinfo=timezone.utc)), "2026-11-15")


if __name__ == "__main__":
    unittest.main(verbosity=2)
