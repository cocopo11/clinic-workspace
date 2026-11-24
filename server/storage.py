"""SQLite storage for a demo with fictional patients and staff only."""
import hashlib
import hmac
import json
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

DEMO_PASSWORD = "ClinicDemo42!"
CLOUD_SCHEMA_VERSION = "2026.11.17-cloud-v2"


def password_hash(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 240000).hex()


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


class Store:
    def __init__(self, directory, database_url=None):
        self.database_url = database_url
        self.cloud_files = None
        self.cloud_schema_ready = False
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        (self.directory / "photos").mkdir(exist_ok=True)
        self.path = self.directory / "clinicworkspace.sqlite3"
        self.initialize()

    def save_file(self, area, name, data):
        if self.cloud_files:
            return self.cloud_files.save(area, name, data)
        target = self.directory / area / name
        target.parent.mkdir(exist_ok=True)
        target.write_bytes(data)
        return target

    def load_file(self, area, name):
        if self.cloud_files:
            return self.cloud_files.load(area, name)
        return self.directory / area / name

    def delete_file(self, area, name):
        if self.cloud_files:
            return self.cloud_files.delete(area, name)
        (self.directory / area / name).unlink(missing_ok=True)

    @contextmanager
    def connect(self, *, shared=False):
        if self.database_url:
            from postgres_store import connect
            with connect(self.database_url, shared=shared) as connection:
                yield connection
            return
        con = sqlite3.connect(self.path, timeout=15)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        if shared:con.execute("BEGIN IMMEDIATE")
        try:
            yield con
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    def initialize(self):
        if self.database_url:
            with self.connect() as c:
                c.execute("CREATE TABLE IF NOT EXISTS clinicworkspace_schema(id INTEGER PRIMARY KEY CHECK(id=1),version TEXT NOT NULL)")
                row=c.execute("SELECT version FROM clinicworkspace_schema WHERE id=1").fetchone()
                self.cloud_schema_ready=bool(row and row[0]==CLOUD_SCHEMA_VERSION)
            if self.cloud_schema_ready:return
        with self.connect() as c:
            c.execute("PRAGMA journal_mode=WAL")
            c.executescript("""
            CREATE TABLE IF NOT EXISTS users (
              id TEXT PRIMARY KEY, name TEXT NOT NULL, role TEXT NOT NULL,
              salt TEXT NOT NULL, password_hash TEXT NOT NULL, balance REAL DEFAULT 8);
            CREATE TABLE IF NOT EXISTS sessions (
              hash TEXT PRIMARY KEY, user_id TEXT REFERENCES users(id), csrf TEXT,
              expires REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS patients (
              id TEXT PRIMARY KEY, name TEXT NOT NULL, prior_date TEXT);
            CREATE TABLE IF NOT EXISTS photos (
              id INTEGER PRIMARY KEY AUTOINCREMENT, patient_id TEXT REFERENCES patients(id),
              visit_date TEXT NOT NULL, site TEXT NOT NULL, filename TEXT NOT NULL,
              created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS capture_tokens (
              hash TEXT PRIMARY KEY, patient_id TEXT REFERENCES patients(id),
              visit_date TEXT, site TEXT, session_hash TEXT, expires REAL, consumed INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS transcripts (
              id INTEGER PRIMARY KEY AUTOINCREMENT, patient_id TEXT, user_id TEXT,
              text TEXT, language TEXT, segments TEXT, created REAL);
            CREATE TABLE IF NOT EXISTS notes (
              id INTEGER PRIMARY KEY AUTOINCREMENT, patient_id TEXT, text TEXT, hash TEXT UNIQUE,
              user_id TEXT, attempts INTEGER, status TEXT, created REAL);
            CREATE TABLE IF NOT EXISTS leaves (
              id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT REFERENCES users(id),
              date TEXT NOT NULL, days REAL NOT NULL, reason TEXT, status TEXT,
              approver TEXT, created REAL);
            CREATE UNIQUE INDEX IF NOT EXISTS live_leave ON leaves(user_id,date)
              WHERE status IN ('pending','approved');
            CREATE TABLE IF NOT EXISTS documents (
              id TEXT PRIMARY KEY, title TEXT NOT NULL, content TEXT NOT NULL,
              version INTEGER NOT NULL, approved INTEGER NOT NULL, roles TEXT NOT NULL,
              kind TEXT NOT NULL, source_url TEXT, updated REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS chunks (
              id INTEGER PRIMARY KEY AUTOINCREMENT, doc_id TEXT REFERENCES documents(id) ON DELETE CASCADE,
              version INTEGER, ordinal INTEGER, text TEXT, vector TEXT,
              model TEXT NOT NULL, digest TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS audit (
              id INTEGER PRIMARY KEY AUTOINCREMENT, actor TEXT, action TEXT,
              object_id TEXT, created REAL);
            CREATE TABLE IF NOT EXISTS document_versions (
              doc_id TEXT REFERENCES documents(id), version INTEGER,
              snapshot TEXT NOT NULL, actor TEXT NOT NULL, note TEXT NOT NULL,
              created REAL NOT NULL, PRIMARY KEY(doc_id,version));
            CREATE TABLE IF NOT EXISTS document_reads (
              user_id TEXT REFERENCES users(id), doc_id TEXT REFERENCES documents(id),
              version INTEGER NOT NULL, read_at REAL NOT NULL,
              PRIMARY KEY(user_id,doc_id,version));
            CREATE TABLE IF NOT EXISTS notices (
              id TEXT PRIMARY KEY, title TEXT NOT NULL, body TEXT NOT NULL,
              roles TEXT NOT NULL, pinned INTEGER NOT NULL DEFAULT 0,
              published INTEGER NOT NULL DEFAULT 1, archived INTEGER NOT NULL DEFAULT 0,
              version INTEGER NOT NULL DEFAULT 1, created_by TEXT REFERENCES users(id),
              updated_by TEXT REFERENCES users(id), created REAL NOT NULL, updated REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS notice_reads (
              user_id TEXT REFERENCES users(id), notice_id TEXT REFERENCES notices(id),
              version INTEGER NOT NULL, read_at REAL NOT NULL,
              PRIMARY KEY(user_id,notice_id,version));
            CREATE INDEX IF NOT EXISTS notices_order ON notices(archived,published,pinned DESC,updated DESC);
            CREATE TABLE IF NOT EXISTS youtube_jobs (
              id TEXT PRIMARY KEY, owner_id TEXT REFERENCES users(id), owner_revision INTEGER,
              video_id TEXT NOT NULL, url TEXT NOT NULL, title TEXT NOT NULL,
              mode TEXT NOT NULL, language TEXT NOT NULL, roles TEXT NOT NULL,
              status TEXT NOT NULL, stage TEXT NOT NULL, progress INTEGER NOT NULL DEFAULT 0,
              cancel_requested INTEGER NOT NULL DEFAULT 0, error_code TEXT NOT NULL DEFAULT '',
              error_message TEXT NOT NULL DEFAULT '', doc_id TEXT REFERENCES documents(id),
              created REAL NOT NULL, updated REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS youtube_sources (
              doc_id TEXT PRIMARY KEY REFERENCES documents(id), video_id TEXT NOT NULL,
              method TEXT NOT NULL, language TEXT NOT NULL, generated INTEGER NOT NULL,
              duration REAL NOT NULL, fetched REAL NOT NULL, segments TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS youtube_owner_video ON youtube_jobs(owner_id,video_id);
            CREATE TABLE IF NOT EXISTS recording_links (
              hash TEXT PRIMARY KEY, owner_id TEXT REFERENCES users(id), owner_revision INTEGER NOT NULL,
              session_hash TEXT NOT NULL, patient_id TEXT REFERENCES patients(id), visit_date TEXT NOT NULL,
              expires REAL NOT NULL, revoked INTEGER NOT NULL DEFAULT 0, job_id TEXT UNIQUE);
            CREATE TABLE IF NOT EXISTS recording_jobs (
              id TEXT PRIMARY KEY, link_hash TEXT UNIQUE REFERENCES recording_links(hash),
              owner_id TEXT REFERENCES users(id), patient_id TEXT REFERENCES patients(id), visit_date TEXT NOT NULL,
              language TEXT NOT NULL, audio_path TEXT NOT NULL, status TEXT NOT NULL, stage TEXT NOT NULL,
              progress INTEGER NOT NULL DEFAULT 0, error TEXT NOT NULL DEFAULT '',
              transcript_id INTEGER REFERENCES transcripts(id), note_id INTEGER REFERENCES notes(id),
              created REAL NOT NULL, updated REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS onboarding_courses (
              id TEXT PRIMARY KEY, title TEXT NOT NULL, description TEXT NOT NULL,
              roles TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
              revision INTEGER NOT NULL DEFAULT 1, updated REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS onboarding_steps (
              course_id TEXT REFERENCES onboarding_courses(id),
              doc_id TEXT REFERENCES documents(id), ordinal INTEGER NOT NULL,
              PRIMARY KEY(course_id,doc_id));
            CREATE TABLE IF NOT EXISTS onboarding_progress (
              user_id TEXT REFERENCES users(id), course_id TEXT REFERENCES onboarding_courses(id),
              doc_id TEXT REFERENCES documents(id), version INTEGER NOT NULL,
              completed INTEGER NOT NULL, completed_at REAL,
              PRIMARY KEY(user_id,course_id,doc_id));
            """)
            # Additive migration: retain existing users, sessions, photos and documents.
            migrations = {
                "users": {"active": "INTEGER NOT NULL DEFAULT 1", "revision": "INTEGER NOT NULL DEFAULT 1"},
                "documents": {"category": "TEXT NOT NULL DEFAULT '일반'", "tags": "TEXT NOT NULL DEFAULT '[]'",
                    "summary": "TEXT NOT NULL DEFAULT ''", "owner_id": "TEXT NOT NULL DEFAULT ''",
                    "source_author": "TEXT NOT NULL DEFAULT ''", "source_date": "TEXT NOT NULL DEFAULT ''",
                    "archived": "INTEGER NOT NULL DEFAULT 0"},
                "audit": {"result": "TEXT NOT NULL DEFAULT 'success'", "detail": "TEXT NOT NULL DEFAULT '{}'"},
                "notes": {"reviewed": "INTEGER NOT NULL DEFAULT 1", "source": "TEXT NOT NULL DEFAULT 'manual'",
                    "visit_date": "TEXT NOT NULL DEFAULT ''", "version": "INTEGER NOT NULL DEFAULT 1"},
            }
            for table, columns in migrations.items():
                existing = {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}
                for name, declaration in columns.items():
                    if name not in existing:
                        c.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")
            c.execute("CREATE INDEX IF NOT EXISTS audit_time ON audit(id DESC)")
            for uid, name, role in [("admin", "운영 관리자(가상)", "admin"),
                                    ("doctor", "진료 의료진(가상)", "doctor"),
                                    ("staff", "CS 직원(가상)", "staff")]:
                salt = secrets.token_hex(16)
                c.execute("INSERT OR IGNORE INTO users(id,name,role,salt,password_hash) VALUES(?,?,?,?,?)",
                          (uid, name, role, salt, password_hash(DEMO_PASSWORD, salt)))
            c.executemany("INSERT OR IGNORE INTO patients VALUES(?,?,?)", [
                ("DEMO-101", "김하늘", "2026-10-24"),
                ("DEMO-102", "김하늘", None),
                ("DEMO-103", "박시연", "2026-10-24")])
            seed_docs = [
                ("D01", "예약 변경 응대 · 예시", "예약 변경을 원하는 고객에게 희망 일정을 확인한 뒤 예약 담당자에게 전달합니다. 담당자 확인 전에는 예약 변경 완료라고 안내하지 않습니다. 이 문서는 데모용 가상 정책입니다.", ["admin", "doctor", "staff"], "manual"),
                ("D02", "동명이인 확인 · 온보딩 예시", "환자 이름이 같으면 환자 식별번호를 추가 확인합니다. 이름만 보고 차트를 확정하지 않습니다. 이 문서는 데모용 가상 정책입니다.", ["admin", "doctor", "staff"], "onboarding"),
                ("D03", "진료 문의 전달 기준 · 예시", "개별 치료 효과, 약물 조정, 진단, 치료 효과 보장에 관한 질문은 담당 의료진에게 전달합니다. 직원이 진단이나 효과 보장을 하지 않습니다. 이 문서는 데모용 가상 정책입니다.", ["admin", "doctor", "staff"], "manual"),
                ("D04", "경과 사진 확인 · 의료진 예시", "이전 사진과 오늘 사진을 비교하기 전에 환자 식별번호, 촬영일, 촬영 부위를 확인합니다. 원본 사진을 확인하고 경과는 의료진이 판단합니다. 이 문서는 데모용 가상 정책입니다.", ["admin", "doctor"], "manual"),
                ("D05", "연차 신청과 승인 · 예시", "직원은 날짜와 연차 또는 반차를 선택해 신청합니다. 관리자의 승인 후 잔여 연차가 차감됩니다. 같은 날짜에 중복 신청할 수 없습니다. 이 문서는 데모용 가상 정책입니다.", ["admin", "doctor", "staff"], "onboarding"),
                ("D06", "관리자 전용 계정 점검 · 예시", "관리자 전용 검증 문구는 DEMO-ONLY-9X 입니다. 이 문서는 관리자만 열람할 수 있습니다. 실제 비밀번호나 환자 정보는 포함하지 않은 권한 검사 시연 문서입니다.", ["admin"], "manual")]
            for did, title, text, roles, kind in seed_docs:
                c.execute("INSERT OR IGNORE INTO documents(id,title,content,version,approved,roles,kind,source_url,updated) VALUES(?,?,?,?,?,?,?,?,?)",
                          (did, title, text, 1, 1, json.dumps(roles), kind, "", time.time()))
            for row in c.execute("SELECT * FROM documents").fetchall():
                c.execute("INSERT OR IGNORE INTO document_versions VALUES(?,?,?,?,?,?)",
                    (row["id"], row["version"], json.dumps(dict(row), ensure_ascii=False), "migration",
                     "기존 현재 버전 보존 · 과거 수정자 정보 없음", row["updated"]))
            created = c.execute("INSERT OR IGNORE INTO onboarding_courses(id,title,description,roles,updated) VALUES(?,?,?,?,?)",
                ("ONB-INTRO", "신규 직원 기본 업무", "가상 매뉴얼을 읽고 각 자료의 읽음 확인을 완료하세요.",
                 json.dumps(["admin", "doctor", "staff"]), time.time())).rowcount
            if created:
                c.executemany("INSERT INTO onboarding_steps VALUES(?,?,?)",
                    [("ONB-INTRO", did, i) for i, did in enumerate(("D02", "D01", "D05"), 1)])

    def audit(self, actor, action, object_id="", result="success", detail=None, connection=None):
        args = (actor, action, str(object_id), time.time(), result, json.dumps(detail or {}, ensure_ascii=False))
        if connection is not None:
            connection.execute("INSERT INTO audit(actor,action,object_id,created,result,detail) VALUES(?,?,?,?,?,?)", args)
            return
        with self.connect() as c:
            c.execute("INSERT INTO audit(actor,action,object_id,created,result,detail) VALUES(?,?,?,?,?,?)", args)

    def login(self, username, password):
        with self.connect() as c:
            row = c.execute("SELECT * FROM users WHERE id=?", (username,)).fetchone()
            if not row or not row["active"] or not hmac.compare_digest(row["password_hash"], password_hash(password, row["salt"])):
                return None
            token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
            c.execute("DELETE FROM sessions WHERE expires<?", (time.time(),))
            c.execute("INSERT INTO sessions VALUES(?,?,?,?)", (token_hash(token), username, csrf, time.time()+8*3600))
            return token, csrf, {k: row[k] for k in ("id", "name", "role")}

    def session(self, token):
        if not token:
            return None
        with self.connect(shared=True) as c:
            row = c.execute("""SELECT u.id,u.name,u.role,u.balance,s.csrf,s.hash AS session_hash
              FROM sessions s JOIN users u ON s.user_id=u.id WHERE s.hash=? AND s.expires>? AND u.active=1""",
                            (token_hash(token), time.time())).fetchone()
            return dict(row) if row else None

    def allowed_documents(self, role):
        with self.connect() as c:
            rows = c.execute("SELECT id,title,version,roles,approved,updated FROM documents WHERE approved=1 AND archived=0").fetchall()
            return [dict(r) for r in rows if role == "admin" or role in json.loads(r["roles"])]
