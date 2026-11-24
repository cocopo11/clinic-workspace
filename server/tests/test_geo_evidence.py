"""Exact citation spans, caption formatting and mixed-source association regressions."""
import copy
import unittest
import unicodedata

from ai_services import BlogGenerationError
from geo import validate_blog
from geo_evidence import build_evidence, select_evidence, resolve_blog_evidence, EvidenceError


def article(eid):
    return {'sufficient':True,'title':'자료로 확인하는 안내','summary':'공개 자료의 내용을 정리합니다.','answer':'촬영 전 확인 항목을 안내합니다. ['+eid+']','sections':[],'faq':[],'citations':[eid]}


class EvidenceTests(unittest.TestCase):
    def test_every_span_is_verbatim_in_original_unicode_and_line_breaks(self):
        raw='자동 수집 대본 · 검토 전\r\n출처: https://example.org\r\n\r\n[00:00:01] '+unicodedata.normalize('NFD','녹음 원문은 수정하지 않습니다.')+'\r\n[00:00:05] 촬영일과 촬영 부위를 확인합니다.\u00a0\t다음 안내입니다.\r\n'*20
        sources=[{'source_id':'S1','title':'영상 대본','text':raw}];passages=build_evidence(sources,chunk_size=180)
        self.assertGreater(len(passages),3)
        for passage in passages:
            self.assertEqual(raw[passage['start']:passage['end']],passage['text'])
            resolved=resolve_blog_evidence(article(passage['evidence_id']),passages,sources)
            cite=validate_blog(resolved,sources)['citations'][0];self.assertEqual(cite['quote'],passage['text']);self.assertEqual(cite['evidence_id'],passage['evidence_id'])
    def test_mixed_sources_cannot_pair_video_quote_with_official_page(self):
        sources=[{'source_id':'S1','title':'병원 소개','text':'공식 홈페이지에는 병원 진료 분야를 안내합니다.'},{'source_id':'S2','title':'공개 대본','text':'[00:02:11] 사진 비교 전 촬영일과 환자 식별번호를 확인합니다.'}]
        old={**article('E1'),'answer':'촬영 전 확인 항목을 안내합니다. [S1]','citations':[{'source_id':'S1','quote':sources[1]['text']}]}
        with self.assertRaises(BlogGenerationError):validate_blog(old,sources)
        passages=build_evidence(sources);p=next(p for p in passages if p['source_id']=='S2')
        resolved=resolve_blog_evidence(article(p['evidence_id']),passages,sources);validated=validate_blog(resolved,sources)
        self.assertEqual(validated['citations'][0]['source_id'],'S2');self.assertEqual(validated['citations'][0]['quote'],sources[1]['text']);self.assertIn('[S2]',validated['answer'])
    def test_long_transcript_selects_late_relevant_passage_and_keeps_original_offsets(self):
        text='\n'.join('[00:%02d:%02d] 일반적인 공개 자료 안내와 촬영 준비에 관한 설명을 이어갑니다.'%(i//60,i%60) for i in range(450))+'\n[00:09:50] 식별번호 대조는 동명이인 구분을 위해 촬영일과 함께 확인합니다.'
        sources=[{'source_id':'S1','title':'공식 자료','text':'공식 홈페이지에 병원 진료 분야를 안내합니다.'},{'source_id':'S2','title':'긴 대본','text':text}]
        all_passages=build_evidence(sources);selected=select_evidence(all_passages,'동명이인 식별번호 대조는 어떻게 하나요?',max_chars=3500,max_items=12)
        self.assertLessEqual(sum(len(p['text']) for p in selected),3500);self.assertLessEqual(len(selected),12)
        self.assertEqual({p['source_id'] for p in selected},{'S1','S2'})
        p=next(p for p in selected if '동명이인' in p['text']);resolved=resolve_blog_evidence(article(p['evidence_id']),selected,sources)
        self.assertEqual(resolved['citations'][0]['quote'],text[p['start']:p['end']]);self.assertGreater(p['start'],10000)
    def test_fabricated_evidence_ids_and_source_markers_are_rejected(self):
        sources=[{'source_id':'S1','title':'자료','text':'원문에서 확인하는 사진 촬영 안내입니다.'}];passages=build_evidence(sources)
        for bad in ({**article('E999')},{**article('E1'),'answer':'직접 출처를 만듭니다. [S1]'},{**article('E1'),'citations':[{'source_id':'S1','quote':'복사한 문장'}]}):
            with self.assertRaises(EvidenceError):resolve_blog_evidence(bad,passages,sources)
    def test_changed_source_and_tampered_offsets_are_rejected(self):
        sources=[{'source_id':'S1','title':'자료','text':'원문에서 확인하는 사진 촬영 안내입니다.'}];passages=build_evidence(sources)
        with self.assertRaises(EvidenceError):resolve_blog_evidence(article('E1'),passages,[{**sources[0],'text':'이후 변경된 원문입니다.'}])
        resolved=resolve_blog_evidence(article('E1'),passages,sources);resolved['citations'][0]['start']+=1
        with self.assertRaises(BlogGenerationError):validate_blog(resolved,sources)
    def test_multiple_passages_same_source_and_combined_markers(self):
        sources=[{'source_id':'S1','title':'자료','text':'촬영 전에 자료의 이름을 확인합니다. '*30}];passages=build_evidence(sources,chunk_size=100)
        output={**article('E1'),'answer':'자료의 확인 항목을 정리합니다. ［ e1, E2 ］','citations':['E1','E2']}
        result=resolve_blog_evidence(output,passages,sources);self.assertEqual(len(result['citations']),2);validate_blog(result,sources)
    def test_citation_list_can_be_rebuilt_from_explicit_valid_body_ids(self):
        sources=[{'source_id':'S1','title':'자료','text':'원문에서 확인하는 사진 촬영 안내입니다.'}];passages=build_evidence(sources)
        result=resolve_blog_evidence({**article('E1'),'citations':[]},passages,sources)
        self.assertEqual(result['citations'][0]['quote'],sources[0]['text'])


if __name__=='__main__':unittest.main()
