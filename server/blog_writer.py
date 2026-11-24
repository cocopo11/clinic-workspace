"""Complete blog posts, including compatibility with an already running local AI PC."""
import asyncio
import copy
import logging
import re

from ai_services import BlogGenerationError
from geo_evidence import build_evidence, select_evidence

ARTICLE_VERSION = '2026.11.18-posts-v1'
logger = logging.getLogger('clinicworkspace')
MIN_SECTION_CHARS = 200
ENDING = re.compile(r'^(마무리|정리하며|글을 마치며|핵심 정리|마지막으로)')
CITATION_FORMAT_ERRORS = {'GEO_EVIDENCE_INVALID','GEO_CITATION_MISSING'}


def citation_brief(topic,sources):
    passages=select_evidence(build_evidence(sources),topic)
    if not passages:return ''
    example=select_evidence(passages,topic,max_items=1,max_chars=1000)[0]['evidence_id']
    ids=', '.join(p['evidence_id'] for p in passages)
    return (f' 인용 형식 안내: 이번 자료의 원문 구간 번호는 {ids}입니다. '
            f'형식 예시: "answer": "그 구간에서 확인한 사실을 풀어 쓴 문장입니다. [{example}]". '
            'answer와 모든 sections의 body, faq의 answer에 실제로 해당 내용을 설명하는 구간의 대괄호 인용을 직접 붙이세요. '
            '문단이 여러 개면 각 문단 끝에 붙이세요. 번호는 제공된 passages에서 골라야 합니다. '
            'citations 배열에 번호를 적는 것만으로는 본문 인용이 되지 않습니다. '
            '동일한 구간을 여러 문단에서 다시 인용해도 되며, citations 배열에는 실제 사용한 번호만 중복 없이 적으세요. '
            '예시는 표기 형식이며 모든 문단에 그 번호를 일괄로 붙이라는 뜻은 아닙니다.')


def plain(text):
    return re.sub(r'\s+', ' ', re.sub(r'\[[SE]\d+\]', '', text or '')).strip()


def paragraphs_join(blocks):
    unique=[];seen=set()
    for block in blocks:
        for paragraph in re.split(r'\n\s*\n',block):
            key=plain(paragraph)
            if key and key not in seen:
                seen.add(key);unique.append(paragraph)
    return '\n\n'.join(unique)


def consolidate_sections(result):
    """Keep minor supporting points without making each one a thin subsection."""
    sections=result['sections']
    if not sections or not ENDING.match(sections[-1]['heading'].strip()):return
    while len(sections)>4:
        sizes=[len(plain(s['body'])) for s in sections[:-1]]
        if max(sizes,default=0)<MIN_SECTION_CHARS or min(sizes)>=MIN_SECTION_CHARS:break
        index=min(range(len(sizes)),key=sizes.__getitem__)
        neighbor=index-1 if index else 1
        blocks=[sections[index]['body'],sections[neighbor]['body']] if index==0 else [sections[neighbor]['body'],sections[index]['body']]
        sections[neighbor]['body']=paragraphs_join(blocks)
        sections.pop(index)


def sentence_repetition(blocks):
    sentences=[s.strip(' .!?。') for b in blocks for s in re.split(r'(?<=[.!?。])\s+|\n+', b) if len(s.strip())>=24]
    seen=[];repeated=0
    for sentence in sentences:
        compact=re.sub(r'\W+','',sentence).casefold()
        grams={compact[i:i+3] for i in range(max(0,len(compact)-2))}
        if any(len(grams&previous)/max(len(grams|previous),1)>.55 for previous in seen):
            repeated+=len(sentence)
        seen.append(grams)
    return round(repeated/max(sum(map(len,sentences)),1),3)


def article_metrics(result):
    sections = result.get('sections') or []
    ending = bool(sections and ENDING.match(sections[-1].get('heading', '').strip()))
    body_sections = sections[:-1] if ending else sections
    blocks = [plain(result.get('answer', ''))] + [plain(s.get('body', '')) for s in sections]
    return {'body_characters':sum(map(len, blocks)), 'introduction_characters':len(blocks[0]),
            'section_count':len(body_sections), 'has_conclusion':ending,
            'shortest_section':min((len(plain(s.get('body', ''))) for s in body_sections), default=0),
            'conclusion_characters':len(blocks[-1]) if ending else 0,
            'repeated_sentence_ratio':sentence_repetition(blocks),
            'faq_count':len(result.get('faq') or [])}


def article_issues(result, length='standard'):
    metrics=article_metrics(result);issues=[]
    minimum=2300 if length=='detailed' else 1400
    if metrics['body_characters'] < minimum:issues.append(f'출처 목록·검색 설명을 제외한 본문이 {metrics["body_characters"]}자로 짧습니다. 본문을 {minimum}자 이상으로 설명하세요.')
    if metrics['introduction_characters'] < 120:issues.append('도입에서 독자의 상황과 이 글에서 다룰 범위를 충분히 설명하세요.')
    if metrics['section_count'] < 3:issues.append('서로 다른 설명을 담은 본문 소제목이 최소 세 개 필요합니다.')
    if metrics['shortest_section'] < MIN_SECTION_CHARS:issues.append('각 본문 소제목 아래에 서로 다른 설명 문단을 충분히 작성하세요.')
    if not metrics['has_conclusion'] or metrics['conclusion_characters'] < 100:issues.append('마지막 sections 항목은 마무리라는 소제목으로 핵심과 다음 확인 사항을 정리하세요.')
    if metrics['repeated_sentence_ratio'] > .22:issues.append('도입·본문 사이에 같은 문장이 반복됩니다. 각 부분에 새 설명을 담으세요.')
    if re.search(r'(?<![A-Za-z0-9])E\d+(?![A-Za-z0-9])',' '.join([result.get('answer','')]+[s.get('body','') for s in result.get('sections',[])])):
        issues.append('본문에 E번호 같은 내부 편집 구간을 설명문으로 노출하지 마세요. 독자를 위한 문장으로 쓰세요.')
    headings=[plain(s.get('heading','')) for s in result.get('sections',[])]
    if len(set(headings))!=len(headings):issues.append('소제목을 중복하지 마세요.')
    return issues,metrics


def writing_brief(length='standard'):
    target='2800~3800' if length=='detailed' else '1800~2600'
    return (f'포스팅 작성 기준: 짧은 CS 답변이나 요약이 아니라 블로그에 올릴 전체 글을 완성하세요. 본문 목표는 {target}자입니다. '
            'title은 독자에게 자연스러운 글 제목, summary는 본문에 반복하지 않을 검색용 설명입니다. '
            'answer에는 독자의 상황과 글의 범위를 여는 도입을 두 문단으로 쓰세요. '
            'sections에는 서로 다른 본문 소제목을 세 개 이상, 각 body에 설명 문단을 두 개 이상 쓰고, 마지막 항목은 heading=마무리로 쓰세요. '
            '각 설명은 왜 그런지, 자료가 실제로 설명하는 조건, 독자가 확인할 내용을 연결하세요. faq는 필요한 경우에만 두 개까지 쓰세요. '
            '원문에 있는 사실만 풀어 쓰고 문장·결론을 반복해 분량을 채우지 마세요. 특정 약제의 주의를 모든 치료제로 확대하지 마세요. '
            '근거에 없는 의료 사실·효과·절차·가격·수치를 만들지 마세요. 숫자로 소제목을 매기지 마세요. '
            '각 answer/body/faq.answer의 실제 근거 구간을 인용하세요. 실제 관련 없는 선택 자료는 인용하지 마세요.')


async def complete_article(ai, question, sources, validate, length='standard'):
    """Never store a two-sentence answer as a completed article.

    The existing PC RPC accepts editorial feedback, so cloud deployments can
    upgrade the writing workflow without asking the user to reinstall their PC.
    Every model response still passes the normal exact-evidence validation.
    """
    if sum(len(plain(s['text'])) for s in sources) < 500:
        raise BlogGenerationError('선택 자료만으로는 전체 포스팅을 구성할 설명이 부족합니다. 상세 안내문이나 관련 영상 대본을 추가해 주세요.', 'GEO_ARTICLE_MATERIAL_NEEDED', 422)
    calls=0; candidate=None; failure=''; metrics={}
    async def invoke(topic, feedback):
        nonlocal calls
        network_retries=0;citation_retries=0
        guidance=citation_brief(topic,sources)
        request_feedback=feedback+guidance
        while True:
            calls+=1
            try:
                result=validate(await ai.generate_blog(topic,sources,feedback=request_feedback),sources)
                logger.info('GEO article call=%s metrics=%s',calls,article_metrics(result))
                return result
            except BlogGenerationError as error:
                logger.warning('GEO article call=%s code=%s',calls,error.code)
                if error.code in CITATION_FORMAT_ERRORS and not citation_retries:
                    citation_retries+=1
                    request_feedback=(feedback+guidance+' 이전 응답은 본문 인용 형식 검사에서 거절됐습니다. '
                        +str(error)[:250]+' 내용을 임의로 보충하지 말고, 각 본문 문자열의 실제 구간 인용을 포함해 JSON 전체를 다시 작성하세요.')
                    continue
                if error.code in {'AI_UPSTREAM_ERROR','AI_CONNECTION_INTERRUPTED'} and not network_retries:
                    network_retries+=1
                    if (await ai.status()).get('chat_ready'):continue
                raise
    try:
        async with asyncio.timeout(230):
            for attempt in range(2):
                try:
                    candidate=await invoke(question,writing_brief(length)+(' 보완할 점: '+failure if failure else ''))
                    issues,metrics=article_issues(candidate,length)
                    if not issues:return {**candidate,'article_metrics':metrics},calls
                    failure=' '.join(issues)
                    if metrics['section_count'] >= 3 and metrics['body_characters'] >= 700:
                        break  # Preserve a useful draft and expand only the missing parts.
                except BlogGenerationError as error:
                    failure=str(error)
                    if error.code.startswith('AI_'):raise
                    if error.code not in {'GEO_OUTPUT_TRUNCATED','GEO_ARTICLE_INCOMPLETE',*CITATION_FORMAT_ERRORS} and candidate is None and attempt:
                        raise
            if not candidate or article_metrics(candidate)['section_count'] < 3:
                try:
                    candidate=await invoke('블로그 포스팅 구성 · '+question,
                        '포스팅 작성 기준: 이번 단계는 긴 글을 쓰기 위한 목차와 구성만 만듭니다. answer에는 짧은 도입을 쓰세요. '
                        'sections 배열에는 서로 다른 설명 소제목 세 개와 마지막 마무리를 포함해 정확히 네 항목을 반드시 작성하세요. '
                        '각 body는 이후 확장할 내용을 원문 근거로 짧게 정리하고 인용하세요. summary는 검색용 설명, faq는 빈 배열입니다. '
                        '질문을 반복하는 목차 대신 실제 자료의 구분·조건·독자의 확인 사항을 나누세요. 원문에 없는 사실은 쓰지 마세요.')
                except BlogGenerationError as error:failure=str(error)
            # Expand an actual model-created outline; do not fabricate topic sections.
            if candidate and article_metrics(candidate)['section_count'] >= 3:
                expanded=copy.deepcopy(candidate)
                citations=list(candidate['citations'])
                if len(plain(expanded['answer'])) < 160:
                    piece=await invoke('블로그 도입 · '+question,
                        '본문 확장: 이 글을 읽을 독자의 상황과 궁금증, 다룰 범위를 여는 도입을 세 문단, 약 삼백 자로 쓰세요. '
                        '결론을 반복하지 말고 선택한 자료에 나온 구분과 확인할 관점을 소개하세요. answer에 완성 도입을 쓰고 sections와 faq는 빈 배열이어도 됩니다. '
                        '각 문단의 근거를 인용하고 자료에 없는 의료 사실을 추가하지 마세요.')
                    expanded['answer']=paragraphs_join([expanded['answer'],piece['answer']]);citations.extend(piece['citations'])
                if not ENDING.match(expanded['sections'][-1]['heading'].strip()):
                    piece=await invoke('블로그 마무리 · '+question,
                        '본문 확장: 이 주제의 블로그 글을 끝맺는 마무리만 작성하세요. answer에 약 이백 자로 '
                        '핵심 구분과 독자가 다음에 확인할 사항을 연결하세요. 새로운 의학적 사실은 추가하지 마세요. '
                        '원문 구간을 인용하고 sections와 faq는 빈 배열을 사용하세요.')
                    expanded['sections'].append({'heading':'마무리','body':piece['answer']})
                    citations.extend(piece['citations'])
                consolidate_sections(expanded)
                needs_expansion=bool(article_issues(expanded,length)[0])
                for index,section in enumerate(expanded['sections'] if needs_expansion else []):
                    last=index==len(expanded['sections'])-1
                    desired=300 if last else (700 if length=='detailed' else 500)
                    if last and len(plain(section['body']))>=100:continue
                    if len(plain(section['body']))>=desired:continue
                    for part in range(2):
                        if part and len(plain(section['body'])) >= (100 if last else MIN_SECTION_CHARS):break
                        brief=(f'본문 확장: 전체 글의 주제는 {question}입니다. 지금 쓸 부분은 "{section["heading"]}"입니다. '
                               f'이 부분의 기존 문단 뒤에 추가할 새 설명을 약 {desired}자로 작성하세요. '
                               '기존 문장을 다시 쓰지 말고, 선택 자료가 설명한 배경·조건·구분을 독자가 이해하도록 풀어 쓰세요. '
                               'answer에 추가 문단만 쓰고 sections와 faq는 빈 배열을 사용하세요. '
                               '제공 원문만 사용하고 각 문단 끝에 근거 구간을 인용하세요. 원문에 없는 의료 사실을 추가하지 마세요. '
                               '다시 쓰지 않을 기존 문단: '+plain(section['body'])[:280])
                        piece=await invoke(question+' · '+section['heading'],brief)
                        section['body']=paragraphs_join([section['body'],piece['answer']]+[s['body'] for s in piece.get('sections',[])])
                        citations.extend(piece['citations'])
                consolidate_sections(expanded)
                used=set(re.findall(r'\[(S\d+)\]', expanded['answer']+' '.join(s['body'] for s in expanded['sections'])+' '.join(f['answer'] for f in expanded.get('faq',[]))))
                unique=[];seen=set()
                for citation in citations:
                    key=(citation['source_id'],citation['quote'])
                    if citation['source_id'] in used and key not in seen:seen.add(key);unique.append(citation)
                expanded['citations']=unique[:12]
                expanded=validate(expanded,sources)
                issues,metrics=article_issues(expanded,length)
                candidate=expanded
                logger.info('GEO article completed calls=%s metrics=%s',calls,metrics)
                if not issues:return {**expanded,'article_metrics':metrics},calls
                failure=' '.join(issues)
    except TimeoutError as error:
        raise BlogGenerationError('전체 포스팅 작성 응답이 지연되었습니다. PC 연결 상태를 확인한 뒤 다시 작성해 주세요.', 'GEO_ARTICLE_TIMEOUT', 503) from error
    if candidate:failure=' '.join(article_issues(candidate,length)[0]) or failure
    raise BlogGenerationError('완성된 포스팅 기준을 충족하지 못해 단문 원고를 저장하지 않았습니다. '+failure[:400], 'GEO_ARTICLE_INCOMPLETE', 422)
