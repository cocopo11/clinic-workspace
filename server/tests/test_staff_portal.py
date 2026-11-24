"""Notice permission/version workflow and personalized dashboard regressions."""
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app import create_app, settings_from_env
from storage import Store
from test_app import FakeAI


class StaffPortalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        cfg = settings_from_env()
        cfg["data_dir"] = self.temp.name
        self.app = create_app(cfg, FakeAI())
        self.clients = []
        self.admin, self.doctor, self.staff = [self.login(uid) for uid in ("admin", "doctor", "staff")]

    def tearDown(self):
        for client in self.clients:
            client.close()
        self.temp.cleanup()

    def login(self, uid):
        client = TestClient(self.app)
        self.clients.append(client)
        result = client.post('/api/login', json={"username": uid, "password": "ClinicDemo42!"})
        self.assertEqual(result.status_code, 200)
        client.headers['X-CSRF-Token'] = result.json()['csrf']
        return client

    def create(self, **changes):
        body = {"title": "업무 안내", "body": "가상 직원 공지 내용입니다."}
        body.update(changes)
        result = self.admin.post('/api/notices', json=body)
        self.assertEqual(result.status_code, 200, result.text)
        return result.json()['id']

    def edit(self, nid, **changes):
        body = self.admin.get('/api/notices/' + nid).json()
        body['expected_version'] = body['version']
        body.update(changes)
        return self.admin.put('/api/notices/' + nid, json=body)

    def dashboard(self, client=None):
        return (client or self.staff).get('/api/portal').json()

    def test_auth_csrf_and_only_admin_writes(self):
        guest = TestClient(self.app)
        for path in ('/api/portal', '/api/notices'):
            self.assertEqual(guest.get(path).status_code, 401)
        guest.close()
        nid = self.create()
        for client in (self.staff, self.doctor):
            self.assertEqual(client.post('/api/notices', json={"title": "x", "body": "x"}).status_code, 403)
            self.assertEqual(client.put('/api/notices/' + nid, json={"title": "x", "body": "x", "expected_version": 1}).status_code, 403)
            self.assertEqual(client.post(f'/api/notices/{nid}/archive', json={"expected_version": 1, "archived": True}).status_code, 403)
            self.assertEqual(client.get('/api/notices?state=all').status_code, 403)
        csrf = self.admin.headers.pop('X-CSRF-Token')
        self.assertEqual(self.admin.post('/api/notices', json={"title": "x", "body": "x"}).status_code, 403)
        self.admin.headers['X-CSRF-Token'] = csrf

    def test_scope_draft_archival_no_title_or_body_leak(self):
        public = self.create()
        secret = self.create(title='SECRET-TITLE', body='SECRET-BODY', roles=['admin', 'doctor'])
        draft = self.create(title='DRAFT-TITLE', published=False)
        for nid in (secret, draft):
            self.assertEqual(self.staff.get('/api/notices/' + nid).status_code, 404)
            self.assertEqual(self.staff.post(f'/api/notices/{nid}/read', json={"expected_version": 1}).status_code, 404)
        self.assertEqual(self.doctor.get('/api/notices/' + secret).status_code, 200)
        data = json.dumps(self.dashboard()) + self.staff.get('/api/notices').text
        self.assertNotIn('SECRET-', data)
        self.assertNotIn('DRAFT-', data)
        self.assertEqual(self.admin.post(f'/api/notices/{public}/archive', json={"expected_version": 1, "archived": True}).status_code, 200)
        self.assertEqual(self.staff.get('/api/notices/' + public).status_code, 404)
        self.assertEqual(self.dashboard()['notices']['unread'], 0)
        self.assertEqual(self.admin.post(f'/api/notices/{public}/archive', json={"expected_version": 2, "archived": False}).status_code, 200)
        self.assertEqual(self.dashboard()['notices']['unread'], 1)

    def test_read_is_explicit_per_employee_and_revision(self):
        nid = self.create()
        self.staff.get('/api/notices/' + nid)
        self.assertEqual(self.dashboard()['notices']['unread'], 1)
        path = f'/api/notices/{nid}/read'
        self.assertEqual(self.staff.post(path, json={"expected_version": 1}).status_code, 200)
        self.assertEqual(self.dashboard()['notices']['unread'], 0)
        self.assertEqual(self.dashboard(self.doctor)['notices']['unread'], 1)
        self.assertEqual(self.edit(nid, body='수정된 안내').status_code, 200)
        self.assertEqual(self.dashboard()['notices']['unread'], 1)
        self.assertEqual(self.staff.post(path, json={"expected_version": 1}).status_code, 409)
        self.assertEqual(self.staff.post(path, json={"expected_version": 2}).status_code, 200)
        actions = self.admin.get('/api/admin/audit', params={"actor": "staff", "action": "read_notice"}).json()['entries']
        self.assertEqual(actions[0]['detail']['version'], 2)

    def test_conflict_validation_and_draft_publish(self):
        for changes in ({"title": " "}, {"body": " "}, {"roles": []}, {"roles": ['guest']}):
            body = {"title": "안내", "body": "본문", **changes}
            self.assertEqual(self.admin.post('/api/notices', json=body).status_code, 400)
        nid = self.create(published=False)
        self.assertEqual(self.admin.post(f'/api/notices/{nid}/read', json={"expected_version": 1}).status_code, 409)
        self.assertEqual(self.edit(nid, published=True).status_code, 200)
        self.assertEqual(self.admin.put('/api/notices/' + nid, json={"title": "x", "body": "x", "expected_version": 1}).status_code, 409)
        self.assertEqual(self.admin.post(f'/api/notices/{nid}/archive', json={"expected_version": 1, "archived": True}).status_code, 409)
        self.assertEqual(self.dashboard()['notices']['unread'], 1)

    def test_search_pinning_pagination_and_unread_filter(self):
        first = self.create(title='고정 필독', pinned=True)
        second = self.create(title='일반 공지', body='검색 키워드')
        self.create(title='의료 전용', roles=['doctor'])
        result = self.staff.get('/api/notices?limit=1').json()
        self.assertEqual(result['total'], 2)
        self.assertEqual(result['notices'][0]['id'], first)
        self.assertNotIn('body', result['notices'][0])
        self.assertEqual(self.staff.get('/api/notices?offset=1&limit=1').json()['notices'][0]['id'], second)
        self.assertEqual(self.staff.get('/api/notices?q=키워드').json()['total'], 1)
        self.staff.post(f'/api/notices/{first}/read', json={"expected_version": 1})
        self.assertEqual(self.staff.get('/api/notices?unread=true').json()['notices'][0]['id'], second)

    def test_dashboard_leave_is_personal_and_approval_queue_is_admin_only(self):
        day = (datetime.now(timezone(timedelta(hours=9))) + timedelta(days=10)).date().isoformat()
        ids = []
        for client in (self.staff, self.doctor):
            result = client.post('/api/leaves', json={"date": day, "days": 1, "reason": "개인 사유"})
            self.assertEqual(result.status_code, 200, result.text)
            ids.append(client.get('/api/leaves').json()['leaves'][0]['id'])
        home = self.dashboard()
        self.assertEqual(home['user']['id'], 'staff')
        self.assertEqual(home['leave']['pending'], 1)
        self.assertEqual(len(home['leave']['recent']), 1)
        self.assertNotIn('approvals', home)
        self.assertEqual(self.dashboard(self.admin)['approvals']['leave_count'], 2)
        self.assertEqual(self.admin.post(f'/api/leaves/{ids[0]}/decision', json={"approve": True}).status_code, 200)
        home = self.dashboard()
        self.assertEqual(home['leave']['balance'], 7)
        self.assertEqual(home['leave']['pending'], 0)
        self.assertEqual(home['leave']['upcoming'][0]['date'], day)
        self.assertEqual(self.dashboard(self.doctor)['leave']['balance'], 8)

    def test_dashboard_document_visibility_read_and_onboarding_latest_version(self):
        before = self.dashboard()
        self.assertNotIn('D06', [d['id'] for d in before['documents']['items']])
        self.staff.get('/api/documents/D02')
        self.assertEqual(self.dashboard()['documents']['unread'], before['documents']['unread'] - 1)
        self.staff.post('/api/onboarding/ONB-INTRO/steps/D02', json={"version": 1, "completed": True})
        self.assertEqual(self.dashboard()['onboarding']['completed'], 1)
        self.assertEqual(self.dashboard(self.doctor)['onboarding']['completed'], 0)
        doc = self.admin.get('/api/documents/D02').json()
        doc.update(expected_version=doc['version'], content='새 온보딩 원문')
        self.assertEqual(self.admin.put('/api/documents/D02', json=doc).status_code, 200)
        after = self.dashboard()
        self.assertEqual(after['onboarding']['completed'], 0)
        self.assertEqual(after['documents']['unread'], before['documents']['unread'])
        self.assertIn('D02', [d['id'] for d in after['documents']['items']])

    def test_additive_initialization_preserves_records_and_acknowledgments(self):
        nid = self.create()
        self.staff.post(f'/api/notices/{nid}/read', json={"expected_version": 1})
        Store(self.temp.name).initialize()
        self.assertEqual(self.dashboard()['notices']['unread'], 0)
        self.assertEqual(self.staff.get('/api/notices/' + nid).json()['body'], '가상 직원 공지 내용입니다.')


if __name__ == '__main__':
    unittest.main()
