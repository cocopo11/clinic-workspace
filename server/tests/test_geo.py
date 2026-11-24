"""GEO permissions, source versions, grounded generation and publication guards."""
import asyncio
import copy
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from app import create_app, settings_from_env
from ai_services import AIUnavailable, BlogGenerationError, LocalAI
from geo import validate_blog
from geo_evidence import build_evidence
from test_app import FakeAI


def generated(sources):
    return {"sufficient": True, "title": "가상 예시의원 진료 안내", "summary": "공식 자료로 확인하는 진료 안내입니다.",
            "answer": "공식 홈페이지는 모발이식, 탈모치료, 헤드스파를 안내합니다. [S1]",
            "sections": [{"heading": "공식 자료 확인", "body": "방문 전 공식 홈페이지의 진료 안내를 확인할 수 있습니다. [S1]"}],
            "faq": [{"question": "어떤 분야를 안내하나요?", "answer": "모발이식, 탈모치료, 헤드스파를 안내합니다. [S1]"}],
            "citations": [{"source_id": "S1", "quote": sources[0]["text"]}]}


def selected_result(result, evidence_id="E1"):
    result=copy.deepcopy(result)
    result['answer']=result['answer'].replace('[S1]','['+evidence_id+']')
    for field,key in (('sections','body'),('faq','answer')):
        for item in result[field]:item[key]=item[key].replace('[S1]','['+evidence_id+']')
    result['citations']=[evidence_id]
    return result


class BlogAI(FakeAI):
    result = None
    def __init__(self):
        super().__init__()
        self.results = []
        self.blog_calls = []
    async def generate_blog(self, question, sources, feedback=""):
        self.blog_calls.append({"question": question, "feedback": feedback})
        self.seen_sources = sources
        if self.result == 'fail':
            raise AIUnavailable('TEST model unavailable')
        if self.results:
            return copy.deepcopy(self.results.pop(0))
        return copy.deepcopy(self.result) if self.result else generated(sources)


class GEOTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.cfg = settings_from_env(); self.cfg['data_dir'] = self.tmp.name
        self.ai = BlogAI(); self.app = create_app(self.cfg, self.ai)
        self.clients = []
        self.admin, self.doctor, self.staff = [self.login(n) for n in ('admin', 'doctor', 'staff')]
    def tearDown(self):
        for c in self.clients: c.close()
        self.tmp.cleanup()
    def login(self, name):
        c = TestClient(self.app); self.clients.append(c)
        r = c.post('/api/login', json={'username': name, 'password': 'ClinicDemo42!'})
        self.assertEqual(r.status_code, 200); c.headers['X-CSRF-Token'] = r.json()['csrf']; return c
    def workspace(self):
        return self.admin.get('/api/geo/workspace').json()
    def generate(self, **extra):
        r = self.admin.post('/api/geo/generate', json={'question': '어떤 진료를 안내하나요?', 'source_ids': ['GS-OFFICIAL'], **extra})
        self.assertEqual(r.status_code, 200, r.text); return r.json()
    def approve(self, d):
        return self.admin.post('/api/geo/drafts/'+d['id']+'/review', json={'expected_revision': d['revision'], 'facts': True, 'medical': True, 'publication': True, 'warnings_ack': True})
    def test_auth_permissions_csrf(self):
        self.assertEqual(TestClient(self.app).get('/api/geo/workspace').status_code, 401)
        for path in ('/api/geo/workspace',): self.assertEqual(self.staff.get(path).status_code, 403)
        self.assertEqual(self.staff.post('/api/geo/generate', json={'question':'자료 질문','source_ids':['GS-OFFICIAL']}).status_code,403)
        self.assertEqual(self.doctor.get('/api/geo/workspace').status_code,200)
        self.admin.headers.pop('X-CSRF-Token')
        self.assertEqual(self.admin.post('/api/geo/plan',json={'start_on':'2026-11-16'}).status_code,403)
    def test_health_and_workspace_identify_loaded_component_versions(self):
        expected='2026.11.16-geo-fix3'
        health=self.admin.get('/api/health').json();self.assertEqual(health['version'],expected);self.assertEqual(health['geo_version'],expected);self.assertEqual(health['llm_version'],expected);self.assertTrue(health['geo_ready'])
        workspace=self.workspace();self.assertEqual(workspace['server_version'],expected);self.assertEqual(workspace['version'],expected);self.assertEqual(workspace['llm_version'],expected)
    def test_llm_source_selection_and_durable_store(self):
        d = self.generate(); self.assertEqual(d['model'],self.ai.chat_model)
        self.assertEqual(len(self.ai.seen_sources),1); self.assertIn('[S1]',d['body'])
        again = create_app(self.cfg, BlogAI())
        self.assertEqual(again.state.store.path,self.app.state.store.path)
        c = TestClient(again); r = c.post('/api/login',json={'username':'admin','password':'ClinicDemo42!'})
        self.assertEqual(c.get('/api/geo/workspace').json()['drafts'][0]['id'],d['id']); c.close()
    def test_review_export_edit_and_publication(self):
        d = self.generate(); endpoint='/api/geo/drafts/'+d['id']
        self.assertEqual(self.admin.get(endpoint+'/export').status_code,409)
        self.assertEqual(self.admin.post(endpoint+'/review',json={'expected_revision':1}).status_code,400)
        r=self.approve(d); self.assertEqual(r.status_code,200,r.text); d=r.json()
        r=self.admin.get(endpoint+'/export?format=html');self.assertEqual(r.status_code,200)
        schema=json.loads(re.search(r'application/ld\+json">(.*?)</script>',r.text).group(1));self.assertEqual(schema['@type'],'BlogPosting');self.assertNotIn('datePublished',schema)
        self.assertEqual(self.admin.post(endpoint+'/publication',json={'expected_revision':d['revision'],'url':'javascript:alert(1)'}).status_code,400)
        r=self.admin.post(endpoint+'/publication',json={'expected_revision':d['revision'],'url':'https://example.com/post'});self.assertEqual(r.status_code,200);d=r.json()
        self.assertEqual(d['stage'],'published')
        body={k:d[k] for k in ('title','summary','body','channel','planned_on')};body['body']+='\n\n수정 내용';body['expected_revision']=d['revision']
        r=self.admin.put(endpoint,json=body);self.assertEqual(r.status_code,200,r.text);self.assertIsNone(r.json()['review'])
        self.assertEqual(self.admin.get(endpoint+'/export').status_code,409)
        self.assertEqual(self.admin.put(endpoint,json=body).status_code,409)
        self.assertEqual(self.approve(d).status_code,409)
    def test_source_change_invalidates_review(self):
        d=self.approve(self.generate()).json();s=self.workspace()['sources'][0]
        r=self.admin.put('/api/geo/sources/'+s['id'],json={**s,'text':s['text']+' 최신 자료입니다.','expected_revision':s['revision'],'public_confirmed':True})
        self.assertEqual(r.status_code,200,r.text)
        self.assertTrue(self.workspace()['drafts'][0]['source_stale'])
        self.assertEqual(self.admin.get('/api/geo/drafts/'+d['id']+'/export').status_code,409)
    def test_profile_change_invalidates_review(self):
        d=self.approve(self.generate()).json();p=self.workspace()['profile']
        r=self.admin.put('/api/geo/profile',json={**p,'confirmed':True,'expected_revision':p['revision']});self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(self.admin.get('/api/geo/drafts/'+d['id']+'/export').status_code,409)
    def test_document_requires_public_confirmation_and_tracks_original(self):
        r=self.admin.post('/api/documents',json={'title':'공개 유튜브 대본','content':'공개 영상에서 모발이식, 탈모치료를 설명합니다.','approved':True,'kind':'youtube_transcript','source_url':'https://www.youtube.com/watch?v=abc12345678'})
        self.assertEqual(r.status_code,200,r.text);did=r.json()['id']
        body={'doc_id':did,'expected_version':1,'checked_on':'2026-11-16'}
        self.assertEqual(self.admin.post('/api/geo/import-document',json=body).status_code,400)
        self.assertEqual(self.admin.post('/api/geo/import-document',json={**body,'public_confirmed':True}).status_code,200)
        sid='GS-DOC-'+did;d=self.generate(source_ids=[sid]);d=self.approve(d).json()
        r=self.admin.post('/api/documents/'+did+'/archive',json={'expected_version':1,'archived':True});self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(self.admin.get('/api/geo/drafts/'+d['id']+'/export').status_code,409)
        self.assertEqual(self.admin.post('/api/geo/generate',json={'question':'자료 질문','source_ids':[sid]}).status_code,409)
    def test_quality_flags_and_xss_safe_export(self):
        d=self.generate();endpoint='/api/geo/drafts/'+d['id']
        r=self.admin.put(endpoint,json={'expected_revision':1,'title':'<script>alert(1)</script>','body':d['body']+'\n\n100% 완치 <script>alert(1)</script>','summary':'" onmouseover="x'})
        self.assertEqual(r.status_code,200,r.text);d=r.json();self.assertTrue(d['quality']['warnings'])
        d=self.approve(d).json();text=self.admin.get(endpoint+'/export?format=html').text
        self.assertNotIn('<script>alert(1)</script>',text);self.assertIn('&lt;script&gt;',text)
        schema=json.loads(re.search(r'application/ld\+json">(.*?)</script>',text).group(1));self.assertEqual(schema['headline'],'<script>alert(1)</script>')
    def test_invalid_generation_does_not_save_fake_draft(self):
        source={**self.workspace()['sources'][0],'source_id':'S1'}
        for mutate,status,code in (
            (lambda d:d['citations'][0].update(quote='원문에 존재하지 않는 인용입니다'),502,'GEO_INVALID_OUTPUT'),
            (lambda d:d.update(answer='진료 분야를 안내합니다 [S9]'),502,'GEO_INVALID_OUTPUT'),
            (lambda d:d.update(answer='1000회 치료로 안내합니다 [S1]'),502,'GEO_INVALID_OUTPUT'),
            (lambda d:d.update(sufficient=False,reason='자료에 비용 정보가 없습니다.'),422,'GEO_INSUFFICIENT_EVIDENCE')):
            result=generated([source]);mutate(result);self.ai.result=result
            r=self.admin.post('/api/geo/generate',json={'question':'자료 질문','source_ids':['GS-OFFICIAL']})
            self.assertEqual(r.status_code,status,r.text);self.assertEqual(r.json()['code'],code)
        self.ai.result='fail';self.assertEqual(self.admin.post('/api/geo/generate',json={'question':'자료 질문','source_ids':['GS-OFFICIAL']}).status_code,503)
        self.assertEqual(self.workspace()['drafts'],[])
    def test_single_sentence_source_accepts_short_article_without_filler(self):
        source={**self.workspace()['sources'][0],'source_id':'S1'}
        self.ai.result={**generated([source]),'sections':[],'faq':[]}
        d=self.generate();self.assertEqual(d['generation_attempts'],1);self.assertIn('모발이식',d['body']);self.assertNotIn('## 자주 묻는 질문',d['body'])
        self.assertEqual(len(self.ai.blog_calls),1)
    def test_canonical_string_boolean_and_combined_references_are_normalized(self):
        source={**self.workspace()['sources'][0],'source_id':'S1'}
        self.ai.result={**generated([source]),'sufficient':'true','answer':'모발이식, 탈모치료, 헤드스파를 안내합니다. ［ s1 ］','sections':[],'faq':[]}
        d=self.generate();self.assertEqual(d['generation_attempts'],1);self.assertIn('[S1]',d['body'])
        source2={**source,'source_id':'S2'};result={**generated([source]),'answer':'진료 분야를 안내합니다. [S1, S2]','citations':[{'source_id':'S1','quote':source['text']},{'source_id':'S2','quote':source2['text']}]}
        validated=validate_blog(result,[source,source2]);self.assertIn('[S1] [S2]',validated['answer'])
        result['answer']='진료 분야를 안내합니다 [S1, S9]'
        with self.assertRaises(BlogGenerationError):validate_blog(result,[source,source2])
    def test_premature_refusal_retries_with_narrower_instruction(self):
        source={**self.workspace()['sources'][0],'source_id':'S1'}
        self.ai.results=[{'sufficient':False,'reason':'한 문장이라 긴 원고를 작성할 수 없습니다.'},{**generated([source]),'sections':[],'faq':[]}]
        d=self.generate();self.assertEqual(d['generation_attempts'],2)
        self.assertIn('한 문장',self.ai.blog_calls[1]['feedback']);self.assertEqual(len(self.workspace()['drafts']),1)
    def test_missing_citation_repairs_once_instead_of_becoming_evidence_503(self):
        source={**self.workspace()['sources'][0],'source_id':'S1'};good=generated([source]);bad={**good,'citations':[]}
        self.ai.results=[bad,good];d=self.generate();self.assertEqual(d['generation_attempts'],2);self.assertIn('인용문',self.ai.blog_calls[1]['feedback'])
    def test_persistent_refusal_identifies_missing_information_and_stops(self):
        self.ai.result={'sufficient':False,'reason':'공개 자료에 치료 비용이 없습니다.'}
        r=self.admin.post('/api/geo/generate',json={'question':'치료 비용은 얼마인가요?','source_ids':['GS-OFFICIAL']})
        self.assertEqual(r.status_code,422,r.text);self.assertIn('치료 비용',r.json()['detail']);self.assertIn('선택 자료 1개',r.json()['detail']);self.assertEqual(len(self.ai.blog_calls),2);self.assertEqual(self.workspace()['drafts'],[])
    def test_partial_draft_preserves_additional_information_for_review(self):
        source={**self.workspace()['sources'][0],'source_id':'S1'}
        self.ai.result={**generated([source]),'missing_information':['방문 전 상담 예약 절차는 담당자 확인이 필요합니다.']}
        d=self.generate();self.assertEqual(len(d['missing_information']),1);self.assertEqual(d['stage'],'review');self.assertIsNone(d['review'])
    def test_real_localai_transport_and_api_repair_premature_refusal(self):
        source={**self.workspace()['sources'][0],'source_id':'S1'}
        minimal={**generated([source]),'sections':[],'faq':[],'missing_information':[],'reason':'원문 진료 분야를 설명할 수 있습니다.'}
        replies=[{'sufficient':False,'reason':'자료가 짧습니다.'},selected_result(minimal)];calls=[]
        def respond(request):
            calls.append(json.loads(request.content))
            return httpx.Response(200,json={'done_reason':'stop','message':{'content':json.dumps(replies.pop(0),ensure_ascii=False)}})
        client=httpx.AsyncClient
        def factory(**kwargs):return client(transport=httpx.MockTransport(respond))
        app=create_app(self.cfg,LocalAI(self.cfg));c=TestClient(app)
        login=c.post('/api/login',json={'username':'admin','password':'ClinicDemo42!'});c.headers['X-CSRF-Token']=login.json()['csrf']
        try:
            with patch('ai_services.httpx.AsyncClient',side_effect=factory):
                r=c.post('/api/geo/generate',json={'question':'가상 예시의원의 진료 분야를 소개해주세요.','source_ids':['GS-OFFICIAL']})
            self.assertEqual(r.status_code,200,r.text);self.assertEqual(r.json()['generation_attempts'],2)
            self.assertIn('자료가 짧습니다',json.loads(calls[1]['messages'][1]['content'])['previous_validation_error'])
        finally:c.close()
    def test_youtube_and_official_sources_bind_quote_to_correct_source_via_real_transport(self):
        content='자동 수집 대본 · 검토 전\n출처: https://www.youtube.com/watch?v=abc12345678\n수집 방식: 실제 Whisper 전사\n언어: ko\n\n[00:00:10] 사진 비교 전에는 촬영일과 촬영 부위를 함께 확인합니다.\n[00:00:18] 환자 식별번호도 함께 대조합니다.'
        r=self.admin.post('/api/documents',json={'title':'공개 영상 대본 · 사진 비교','content':content,'approved':True,'kind':'youtube_transcript','source_url':'https://www.youtube.com/watch?v=abc12345678'})
        self.assertEqual(r.status_code,200,r.text);did=r.json()['id']
        r=self.admin.post('/api/geo/import-document',json={'doc_id':did,'expected_version':1,'checked_on':'2026-11-16','public_confirmed':True});self.assertEqual(r.status_code,200,r.text)
        calls=[]
        def respond(request):
            body=json.loads(request.content);calls.append(body);context=json.loads(body['messages'][1]['content'])
            passage=next(p for p in context['passages'] if '촬영일' in p['text'])
            out={'sufficient':True,'title':'사진 비교 전 확인할 내용','summary':'공개 대본의 사진 비교 안내입니다.','answer':'사진 비교 전에는 촬영일과 촬영 부위, 환자 식별번호를 확인합니다. ['+passage['evidence_id']+']','sections':[],'faq':[],'citations':[passage['evidence_id']],'reason':'원문에 확인 항목이 있습니다.','missing_information':[]}
            return httpx.Response(200,json={'done_reason':'stop','message':{'content':json.dumps(out,ensure_ascii=False)}})
        client=httpx.AsyncClient
        def factory(**kwargs):return client(transport=httpx.MockTransport(respond))
        app=create_app(self.cfg,LocalAI(self.cfg));c=TestClient(app);r=c.post('/api/login',json={'username':'admin','password':'ClinicDemo42!'});c.headers['X-CSRF-Token']=r.json()['csrf']
        try:
            with patch('ai_services.httpx.AsyncClient',side_effect=factory):r=c.post('/api/geo/generate',json={'question':'사진 비교 전 확인할 것은 무엇인가요?','source_ids':['GS-OFFICIAL','GS-DOC-'+did]})
            self.assertEqual(r.status_code,200,r.text);d=r.json();self.assertEqual(d['generation_attempts'],1);self.assertIn('[S2]',d['body']);self.assertEqual(d['citations'][0]['source_id'],'S2');cite=d['citations'][0];self.assertEqual(content[cite['start']:cite['end']],cite['quote']);self.assertEqual(cite['time_label'],'00:00:10')
            self.assertEqual(calls[0]['format']['properties']['citations']['items']['type'],'string')
        finally:c.close()
    def test_four_week_plan_retries_and_convert(self):
        for expected in (4,0):
            r=self.admin.post('/api/geo/plan',json={'start_on':'2026-11-16'});self.assertEqual(r.status_code,200,r.text);self.assertEqual(r.json()['created'],expected)
        plans=self.workspace()['drafts'];self.assertEqual(sorted(d['planned_on'] for d in plans),['2026-11-16','2026-11-23','2026-11-30','2026-12-07'])
        plan=plans[0];d=self.generate(plan_id=plan['id'],expected_revision=plan['revision']);self.assertEqual(d['id'],plan['id']);self.assertEqual(len(self.workspace()['drafts']),4)
        self.assertEqual(self.approve(plans[1]).status_code,400)
    def test_observation_exact_domain_and_persistence(self):
        for answer,own in [('가상 예시의원 https://clinic.example.org/service',True),('가상 예시의원 https://clinic.example.org.evil.example/post',False),('다른 병원만 소개합니다.',False)]:
            r=self.admin.post('/api/geo/observations',json={'question':'탈모 병원 질문','service':'TEST','answer':answer,'observed_on':'2026-11-16','search_mode':'검색 사용'})
            self.assertEqual(r.status_code,200,r.text);self.assertEqual(bool(r.json()['own_citations']),own)
        self.assertEqual(len(self.workspace()['observations']),3)


class BlogTransportTests(unittest.TestCase):
    def test_localai_makes_actual_chat_request_with_schema(self):
        calls=[]
        def respond(request):
            body=json.loads(request.content);calls.append(body)
            return httpx.Response(200,json={'message':{'content':json.dumps({'sufficient':False})}})
        client=httpx.AsyncClient
        def factory(**kwargs): return client(transport=httpx.MockTransport(respond))
        ai=LocalAI(settings_from_env())
        with patch('ai_services.httpx.AsyncClient',side_effect=factory):
            result=asyncio.run(ai.generate_blog('공개 자료 질문',[{'source_id':'S1','title':'자료','text':'실제 원문 데이터'}]))
        self.assertFalse(result['sufficient']);self.assertFalse(calls[0]['stream']);self.assertIn('citations',calls[0]['format']['properties']);self.assertIn('실제 원문 데이터',calls[0]['messages'][1]['content'])
        schema=calls[0]['format'];self.assertEqual(list(schema['properties'])[0],'title');self.assertFalse(schema['additionalProperties']);self.assertEqual(schema['properties']['sections']['minItems'],0)
        identifiers=schema['properties']['citations']['items']['enum'];self.assertEqual(identifiers,['E1'])
        context=json.loads(calls[0]['messages'][1]['content']);self.assertEqual(context['passages'][0]['evidence_id'],'E1');self.assertEqual(context['passages'][0]['text'],'실제 원문 데이터');self.assertNotIn('source_id',schema['properties']['citations']['items'])
    def test_malformed_and_truncated_json_have_repairable_errors(self):
        client=httpx.AsyncClient;ai=LocalAI(settings_from_env())
        for payload,code in (({'message':{'content':'{not json'}},'GEO_INVALID_OUTPUT'),({'done_reason':'length','message':{'content':'{"title":'}},'GEO_OUTPUT_TRUNCATED')):
            def factory(**kwargs): return client(transport=httpx.MockTransport(lambda request:httpx.Response(200,json=payload)))
            with patch('ai_services.httpx.AsyncClient',side_effect=factory):
                with self.assertRaises(BlogGenerationError) as context:
                    asyncio.run(ai.generate_blog('공개 자료 질문',[{'source_id':'S1','title':'자료','text':'실제 원문 데이터입니다.'}]))
                self.assertEqual(context.exception.code,code)


if __name__=='__main__': unittest.main()
