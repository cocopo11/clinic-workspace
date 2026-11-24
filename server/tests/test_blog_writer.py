"""Full-post contract: structure, grounding, expansion and no short-answer saves."""
import copy
import asyncio
import json
import re
import tempfile
import unittest
from unittest.mock import patch
import httpx
from fastapi.testclient import TestClient
from app import create_app,settings_from_env
from blog_writer import article_issues,article_metrics,writing_brief,citation_brief
from geo_evidence import build_evidence,select_evidence
from ai_services import BlogGenerationError,LocalAI
from test_app import FakeAI

INTRO='''병원에서 제공하는 공개 안내를 읽다 보면 짧은 답변만으로는 이해하기 어려운 부분이 남습니다. 어떤 내용을 먼저 확인해야 하는지, 안내문에 적힌 설명을 어디까지 받아들여야 하는지 정리하면 이후 문의도 훨씬 구체적으로 준비할 수 있습니다.

이 글에서는 공개 자료를 살펴보는 순서와 설명의 범위를 구분하는 방법, 궁금한 내용을 정리하는 방법을 함께 알아봅니다. 안내문의 제목만 보고 판단하기보다 본문과 출처를 차근차근 확인하는 데 초점을 맞추겠습니다.'''
SECTIONS=[
 ('안내문이 다루는 범위부터 살펴보세요','''먼저 안내문이 누구를 위해 작성되었는지와 어떤 상황을 설명하는지 살펴봅니다. 방문을 준비하는 사람에게 필요한 내용인지, 이미 안내를 받은 뒤 참고할 내용인지에 따라 읽어야 할 부분이 달라질 수 있습니다. 제목에 익숙한 표현이 있더라도 본문에서 정한 범위까지 함께 읽는 것이 좋습니다.

자료의 출처와 작성 시점도 내용을 이해하는 단서입니다. 다른 페이지를 옮겨 적은 글이라면 원래 자료가 무엇인지 확인하고, 서로 다른 자료를 함께 읽을 때에는 각각 어떤 주제를 다루는지 구분해 둡니다. 한 문장만 떼어 읽으면 빠지기 쉬운 조건이 앞뒤 문단에 적혀 있을 수 있습니다.

처음 읽을 때 모든 내용을 외우려 하기보다는 안내문이 설명하는 큰 흐름을 파악해 보세요. 자신이 찾던 내용이 어느 부분에 있는지 표시해 두면 다시 확인하기가 편합니다. 자료에서 설명하지 않는 부분까지 같은 기준으로 판단하기보다 확인된 내용과 남은 궁금증을 나누어 정리할 수 있습니다.'''),
 ('공개된 설명과 개인 상황을 구분하세요','''공개 안내문은 여러 독자가 함께 볼 수 있도록 공통적인 내용을 설명합니다. 따라서 글에서 다루는 조건과 독자 자신의 상황이 같은지를 먼저 살피는 과정이 필요합니다. 설명을 읽었다는 사실과 개인적인 판단을 마쳤다는 사실을 같은 의미로 받아들이지 않는 것이 핵심입니다.

본문에 특정 상황을 가리키는 표현이 나온다면 그 조건을 생략하지 않고 이해해 보세요. 한 부분에서 설명한 내용을 다른 상황에도 그대로 적용할 수 있는지는 별도로 확인해야 합니다. 자료가 제공하는 정보의 범위를 구분하면 설명이 충분한 부분과 추가 안내가 필요한 부분도 더 분명하게 보입니다.

모호하게 느껴지는 문장이 있다면 임의로 뜻을 넓혀 해석하기보다 해당 문장과 함께 읽은 내용을 적어 두는 방법이 있습니다. 다른 자료와 표현이 달라 보일 때에도 문맥과 작성 목적을 비교하며 차이를 정리할 수 있습니다. 이런 준비는 이후 담당자에게 무엇을 확인하고 싶은지 구체적으로 전달하는 데 도움이 됩니다.'''),
 ('궁금한 내용을 질문으로 정리해 보세요','''자료를 읽고 난 뒤에는 확인한 내용과 확인하지 못한 내용을 나누어 적어 봅니다. 이미 안내문에 적힌 사실은 어느 부분에서 읽었는지 표시하고, 설명이 부족한 부분은 짧은 질문으로 바꿔 볼 수 있습니다. 자신의 생각이나 추측을 자료의 설명과 섞지 않으면 질문의 초점도 더 뚜렷해집니다.

질문을 준비할 때는 단순히 내용이 맞는지를 묻기보다 어떤 문장을 어떻게 이해했는지 함께 정리해 두세요. 필요한 조건을 빠뜨리지 않았는지, 다른 부분의 설명과 연결해 읽었는지 살펴보는 과정도 유용합니다. 이렇게 정리한 질문은 담당자가 독자의 궁금증을 파악하는 데 필요한 맥락을 담을 수 있습니다.

문의한 뒤 새로 안내받은 내용은 기존에 적어 둔 질문과 함께 확인합니다. 설명을 들으며 이해한 부분과 여전히 남은 부분을 구분해 두면 같은 문장을 반복해서 찾아 읽는 수고를 줄일 수 있습니다. 공개 자료는 이러한 확인 과정을 준비하는 출발점으로 활용하고, 개인적인 판단이 필요한 부분은 해당 상황에 맞게 따로 확인합니다.''')]
ENDING='''공개 안내를 읽는 과정에서는 자료가 다루는 주제와 조건을 먼저 파악하고, 확인된 설명과 자신의 상황을 구분하는 태도가 중요합니다. 본문에 적힌 내용을 넘어 추측하기보다 남은 궁금증을 질문으로 정리하면 다음 안내를 이해하는 데도 도움이 됩니다.

관심 있는 내용을 찾았다면 제목뿐 아니라 출처와 앞뒤 문단까지 함께 살펴보세요. 이 글에서 정리한 순서를 참고해 필요한 부분을 표시하고 질문을 준비하면, 공개 자료를 더 차분하고 구체적으로 활용할 수 있습니다.'''
SOURCE='\n\n'.join([INTRO,*[body for _,body in SECTIONS],ENDING])

def article():
 return {'sufficient':True,'title':'병원 공개 안내, 어떤 순서로 읽으면 좋을까요?','summary':'자료의 범위와 조건을 살피고 질문을 준비하는 방법을 정리합니다.',
  'answer':INTRO+' [S1]','sections':[{'heading':heading,'body':body+' [S1]'} for heading,body in SECTIONS]+[{'heading':'마무리','body':ENDING+' [S1]'}],
  'faq':[],'citations':[{'source_id':'S1','quote':SOURCE}],'missing_information':[]}

class WriterAI(FakeAI):
 def __init__(self):super().__init__();self.calls=[];self.short=False;self.expand=False
 async def generate_blog(self,question,sources,feedback=''):
  self.calls.append((question,feedback))
  result=article();result['citations']=[{'source_id':'S1','quote':sources[0]['text']}]
  if self.short:return {**result,'answer':'자료에서 확인할 내용을 안내합니다. [S1]','sections':[]}
  if self.expand and feedback.startswith('포스팅 작성 기준:'):
   result['answer']='자료의 조건을 확인하세요. [S1]'
   for section in result['sections']:section['body']='원문에서 확인할 내용입니다. [S1]'
  elif self.expand and feedback.startswith('본문 확장:'):
   result['sections']=[]
   if '도입' in question:result['answer']=INTRO+' [S1]'
   else:
    heading=question.split(' · ')[-1]
    result['answer']=(ENDING if heading=='마무리' else dict(SECTIONS)[heading])+' [S1]'
  return result

class ArticleTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();cfg=settings_from_env();cfg['data_dir']=self.temp.name
  self.ai=WriterAI();self.app=create_app(cfg,self.ai);self.client=TestClient(self.app)
  r=self.client.post('/api/login',json={'username':'admin','password':'ClinicDemo42!'});self.client.headers['X-CSRF-Token']=r.json()['csrf']
  r=self.client.post('/api/geo/sources',json={'title':'공개 자료 활용 안내','text':SOURCE,'url':'https://example.org/guide','checked_on':'2026-11-18','public_confirmed':True})
  self.assertEqual(r.status_code,200,r.text);self.sid=r.json()['id']
 def tearDown(self):self.client.close();self.temp.cleanup()
 def generate(self,**extra):return self.client.post('/api/geo/articles',json={'question':'공개 안내문을 어떻게 읽으면 좋을까요?','source_ids':[self.sid],**extra})
 def test_complete_post_has_intro_sections_ending_and_separate_meta_description(self):
  r=self.generate();self.assertEqual(r.status_code,200,r.text);d=r.json()
  self.assertEqual(d['format'],'article');self.assertGreaterEqual(d['article_metrics']['body_characters'],1400)
  self.assertEqual(d['article_metrics']['section_count'],3);self.assertTrue(d['article_metrics']['has_conclusion'])
  self.assertNotIn(d['summary'],d['body']);self.assertNotIn('## '+d['question'],d['body'])
  self.assertIn('## 마무리',d['body']);self.assertEqual(d['stage'],'review');self.assertIsNone(d['review'])
  self.assertEqual(self.client.get('/api/geo/drafts/'+d['id']+'/export').status_code,409)
 def test_short_answer_is_not_saved_as_a_full_post(self):
  self.ai.short=True;r=self.generate();self.assertEqual(r.status_code,422,r.text)
  self.assertEqual(r.json()['code'],'GEO_ARTICLE_INCOMPLETE')
  self.assertEqual(self.client.get('/api/geo/workspace').json()['drafts'],[])
 def test_one_sentence_material_requests_better_sources_without_fake_filler(self):
  r=self.generate(source_ids=['GS-OFFICIAL']);self.assertEqual(r.status_code,422,r.text)
  self.assertEqual(r.json()['code'],'GEO_ARTICLE_MATERIAL_NEEDED');self.assertFalse(self.ai.calls)
 def test_section_expansion_completes_an_existing_model_outline(self):
  self.ai.expand=True;r=self.generate();self.assertEqual(r.status_code,200,r.text)
  self.assertGreater(r.json()['generation_attempts'],2);self.assertGreaterEqual(r.json()['article_metrics']['body_characters'],1400)
 def test_unused_selected_source_does_not_appear_in_the_post_or_references(self):
  r=self.generate(source_ids=[self.sid,'GS-OFFICIAL']);self.assertEqual(r.status_code,200,r.text)
  self.assertEqual([s['id'] for s in r.json()['sources']],[self.sid]);self.assertEqual(r.json()['quality']['source_count'],1)
  self.assertNotIn('공식 홈페이지 소개',r.json()['body'])
 def test_repeated_sentences_cannot_satisfy_post_quality_by_length(self):
  out=article()
  for section in out['sections']:section['body']=SECTIONS[0][1]+' [S1]'
  issues,metrics=article_issues(out);self.assertGreater(metrics['repeated_sentence_ratio'],.22);self.assertTrue(any('반복' in x for x in issues))
 def test_small_word_changes_do_not_hide_repeated_explanations(self):
  out=article()
  for i,section in enumerate(out['sections']):
   section['body']=SECTIONS[0][1].replace('안내문','공개 안내문' if i%2 else '해당 안내문')+' [S1]'
  issues,metrics=article_issues(out);self.assertGreater(metrics['repeated_sentence_ratio'],.22);self.assertTrue(any('반복' in x for x in issues))
 def test_editorial_evidence_ids_are_not_public_explanations(self):
  out=article();out['sections'][0]['body']+=' 이는 E12에서 설명한 내용입니다.'
  self.assertTrue(any('편집 구간' in x for x in article_issues(out)[0]))
 def test_staff_cannot_create_public_posts(self):
  c=TestClient(self.app);r=c.post('/api/login',json={'username':'staff','password':'ClinicDemo42!'});c.headers['X-CSRF-Token']=r.json()['csrf']
  self.assertEqual(c.post('/api/geo/articles',json={'question':'공개 자료 안내','source_ids':[self.sid]}).status_code,403);c.close()
 def test_temporary_bridge_failure_is_retried_only_when_chat_is_ready(self):
  original=self.ai.generate_blog;first=True
  async def flaky(*args,**kwargs):
   nonlocal first
   if first:
    first=False
    raise BlogGenerationError('응답이 일시적으로 끊겼습니다.','AI_UPSTREAM_ERROR',503)
   return await original(*args,**kwargs)
  async def ready():return {'chat_ready':True}
  self.ai.generate_blog=flaky;self.ai.status=ready
  r=self.generate();self.assertEqual(r.status_code,200,r.text);self.assertEqual(r.json()['generation_attempts'],2)
 def test_required_connector_update_is_not_retried_as_editorial_failure(self):
  calls=[]
  async def unavailable(*args,**kwargs):
   calls.append(True)
   raise BlogGenerationError('PC 연결 프로그램을 업데이트하세요.','AI_UPDATE_REQUIRED',503)
  self.ai.generate_blog=unavailable
  r=self.generate();self.assertEqual(r.status_code,503,r.text)
  self.assertEqual(r.json()['code'],'AI_UPDATE_REQUIRED');self.assertEqual(len(calls),1)
 def test_missing_inline_evidence_is_repaired_with_actual_available_ids(self):
  original=self.ai.generate_blog;feedbacks=[]
  async def malformed(question,sources,feedback=''):
   feedbacks.append(feedback)
   if len(feedbacks)==1:raise BlogGenerationError('본문 인용을 확인하세요.','GEO_EVIDENCE_INVALID',502)
   return await original(question,sources,feedback)
  self.ai.generate_blog=malformed
  r=self.generate();self.assertEqual(r.status_code,200,r.text)
  self.assertEqual(r.json()['generation_attempts'],2)
  self.assertIn('"answer": "',feedbacks[0]);self.assertIn('[E',feedbacks[0])
  self.assertIn('이전 응답은 본문 인용 형식 검사에서 거절',feedbacks[1])
 def test_conclusion_expansion_also_repairs_missing_inline_evidence(self):
  original=self.ai.generate_blog;ending_calls=[]
  async def malformed(question,sources,feedback=''):
   if question.startswith('블로그 마무리'):
    ending_calls.append(feedback)
    if len(ending_calls)==1:raise BlogGenerationError('문단 인용이 없습니다.','GEO_EVIDENCE_INVALID',502)
    result=await original(question,sources,feedback);result['answer']=ENDING+' [S1]';result['sections']=[]
    return result
   result=await original(question,sources,feedback);result['sections']=result['sections'][:-1]
   return result
  self.ai.generate_blog=malformed
  r=self.generate();self.assertEqual(r.status_code,200,r.text)
  self.assertEqual(r.json()['generation_attempts'],3);self.assertEqual(len(ending_calls),2)
 def test_full_post_format_failure_can_fall_back_to_a_grounded_model_outline(self):
  original=self.ai.generate_blog;calls=[]
  async def malformed(question,sources,feedback=''):
   calls.append(question)
   if not question.startswith('블로그 포스팅 구성'):
    raise BlogGenerationError('문단 인용이 없습니다.','GEO_EVIDENCE_INVALID',502)
   return await original(question,sources,feedback)
  self.ai.generate_blog=malformed
  r=self.generate();self.assertEqual(r.status_code,200,r.text)
  self.assertEqual(r.json()['generation_attempts'],5);self.assertEqual(len(calls),5)
 def test_persistent_invalid_evidence_is_bounded_and_never_saved(self):
  calls=[]
  async def malformed(*args,**kwargs):
   calls.append(True);raise BlogGenerationError('없는 구간입니다.','GEO_EVIDENCE_INVALID',502)
  self.ai.generate_blog=malformed
  r=self.generate();self.assertEqual(r.status_code,422,r.text)
  self.assertEqual(len(calls),6);self.assertEqual(self.client.get('/api/geo/workspace').json()['drafts'],[])
 def test_citation_brief_lists_only_the_passages_selected_for_this_call(self):
  sources=[{'source_id':'S1','title':'긴 안내','text':('\n'.join(f'안내 {i}: '+('준비 범위를 확인하세요. '*24) for i in range(100)))+'\n핵심 질문 전용 설명을 확인하세요.'}]
  topic='핵심 질문 전용 설명';passages=select_evidence(build_evidence(sources),topic)
  allowed={p['evidence_id'] for p in passages};brief=citation_brief(topic,sources)
  self.assertLess(len(allowed),len(build_evidence(sources)))
  self.assertEqual(set(re.findall(r'E\d+',brief)),allowed)
 def test_useful_body_is_preserved_when_only_the_conclusion_is_missing(self):
  original=self.ai.generate_blog
  async def missing_ending(question,sources,feedback=''):
   result=await original(question,sources,feedback)
   if question.startswith('블로그 마무리'):
    result['answer']=ENDING+' [S1]';result['sections']=[]
   else:result['sections']=result['sections'][:-1]
   return result
  self.ai.generate_blog=missing_ending
  r=self.generate();self.assertEqual(r.status_code,200,r.text)
  self.assertIn(SECTIONS[0][1],r.json()['body']);self.assertTrue(r.json()['article_metrics']['has_conclusion'])
  self.assertEqual(r.json()['generation_attempts'],2)
 def test_minor_supporting_point_is_merged_without_discarding_its_content(self):
  original=self.ai.generate_blog
  async def small_point(*args,**kwargs):
   result=await original(*args,**kwargs)
   result['sections'].insert(1,{'heading':'짧은 추가 안내','body':'자료의 설명 범위도 함께 확인해 두면 질문을 정리하는 데 도움이 됩니다. [S1]'})
   return result
  self.ai.generate_blog=small_point
  r=self.generate();self.assertEqual(r.status_code,200,r.text)
  self.assertIn('자료의 설명 범위도 함께 확인해 두면',r.json()['body'])
  self.assertEqual(r.json()['article_metrics']['section_count'],3);self.assertEqual(r.json()['generation_attempts'],1)
 def test_updated_pc_uses_the_full_post_prompt_and_resolves_real_source_spans(self):
  payloads=[];out=article();out['citations']=['E1']
  out['answer']=out['answer'].replace('[S1]','[E1]')
  for section in out['sections']:section['body']=section['body'].replace('[S1]','[E1]')
  def respond(request):
   payloads.append(json.loads(request.content))
   return httpx.Response(200,json={'done_reason':'stop','message':{'content':json.dumps(out,ensure_ascii=False)}})
  real_client=httpx.AsyncClient
  def factory(**kwargs):return real_client(transport=httpx.MockTransport(respond))
  ai=LocalAI(settings_from_env())
  with patch('ai_services.httpx.AsyncClient',side_effect=factory):
   result=asyncio.run(ai.generate_blog('공개 자료를 읽는 방법',[{'source_id':'S1','title':'공개 안내','text':SOURCE}],writing_brief()))
  self.assertFalse(article_issues(result)[0]);self.assertIn(result['citations'][0]['quote'],SOURCE)
  request=json.loads(payloads[0]['messages'][1]['content'])
  self.assertIn('editorial_brief',request);self.assertNotIn('retry_instruction',request)
  self.assertGreaterEqual(payloads[0]['options']['num_predict'],6000)

if __name__=='__main__':unittest.main()
