"""Exact source spans for blog citations. Models select IDs, never copy quotes."""
import re


class EvidenceError(ValueError):
    pass


TIME = re.compile(r"\[(?P<time>(?:\d{1,3}:){1,2}\d{2}(?:[.,]\d+)?)\]")


def build_evidence(sources, chunk_size=480):
    passages = []
    for source in sources:
        raw = source["text"]
        cursor = 0
        while cursor < len(raw):
            stop = min(cursor + chunk_size, len(raw))
            if stop < len(raw):
                # Prefer caption/paragraph boundaries without losing short lines.
                boundary = raw.rfind("\n", cursor + chunk_size // 3, stop)
                if boundary < 0:
                    boundary = raw.rfind(" ", cursor + chunk_size // 3, stop)
                if boundary >= cursor:
                    stop = boundary + 1
            segment = raw[cursor:stop]
            start = cursor + len(segment) - len(segment.lstrip())
            end = stop - (len(segment) - len(segment.rstrip()))
            cursor = stop
            quote = raw[start:end]
            clean = TIME.sub("", quote)
            # Skip transcript metadata blocks, but keep original text for every span.
            substantive = [line for line in clean.splitlines() if line.strip() and not re.match(r"^(자동 수집 대본|출처:|수집 방식:|언어:)", line.strip())]
            if len(" ".join(substantive).strip()) < 8:
                continue
            time = TIME.search(quote)
            passages.append({"evidence_id": "E" + str(len(passages) + 1),
                "source_id": source["source_id"], "title": source["title"],
                "text": quote, "start": start, "end": end,
                "time_label": time["time"] if time else ""})
    return passages


def select_evidence(passages, topic, max_chars=16000, max_items=60):
    if len(passages) <= max_items and sum(len(p["text"]) for p in passages) <= max_chars:
        return passages
    # Bounded lexical selection for long transcripts; no extra model or API needed.
    def compact(text):
        return re.sub(r"[^가-힣a-z0-9]", "", text.casefold())
    query = compact(topic)
    grams = {query[i:i+2] for i in range(max(0, len(query)-1))}
    terms = set(re.findall(r"[가-힣a-z0-9]{2,}", topic.casefold()))
    def score(p):
        text = compact(p["text"])
        return sum(g in text for g in grams) + 3 * sum(t in p["text"].casefold() for t in terms)
    ranked = sorted(enumerate(passages), key=lambda pair: (-score(pair[1]), pair[0]))
    chosen, length = {}, 0
    # Give every selected source at least one passage before filling by relevance.
    seen = set()
    priority = []
    for index, passage in ranked:
        if passage["source_id"] not in seen:
            priority.append((index, passage)); seen.add(passage["source_id"])
    priority += ranked
    for index, passage in priority:
        if index in chosen:
            continue
        if len(chosen) < max_items and length + len(passage["text"]) <= max_chars:
            chosen[index] = passage
            length += len(passage["text"])
    return [chosen[index] for index in sorted(chosen)]


def resolve_blog_evidence(result, passages, sources):
    if not isinstance(result, dict):
        raise EvidenceError("LLM 원고가 JSON 객체 형식이 아닙니다.")
    if result.get("sufficient") is False or str(result.get("sufficient")).strip().lower() == "false":
        return result
    available = {p["evidence_id"]: p for p in passages}
    originals = {s["source_id"]: s["text"] for s in sources}
    declared = result.get("citations")
    if not isinstance(declared, list) or any(not isinstance(x, str) or x not in available for x in declared):
        raise EvidenceError("인용에는 제공된 원문 구간 번호(E번호)만 사용하세요. 인용문이나 출처 번호를 직접 만들지 마세요.")
    used = []
    def render(text):
        if not isinstance(text, str):
            raise EvidenceError("원고 문단은 문자열로 작성하세요.")
        text = text.replace("［", "[").replace("］", "]")
        text = re.sub(r"\[\s*E\d+(?:\s*[,·/]\s*E\d+)*\s*\]", lambda m: " ".join("[" + x.upper() + "]" for x in re.findall(r"E\d+", m[0], re.I)), text, flags=re.I)
        if re.search(r"\[\s*S\d+", text, re.I):
            raise EvidenceError("본문 인용은 [E번호]를 사용하세요. 출처 S번호는 서버가 연결합니다.")
        refs = re.findall(r"\[(E\d+)\]", text)
        if not refs or any(ref not in available for ref in refs):
            raise EvidenceError("본문 문단마다 실제 제공된 [E번호]를 붙이세요. 없는 구간 번호는 사용할 수 없습니다.")
        for ref in refs:
            if ref not in used:
                used.append(ref)
        return re.sub(r"\[(E\d+)\]", lambda m: "[" + available[m[1]]["source_id"] + "]", text)
    resolved = dict(result)
    resolved["answer"] = render(result.get("answer"))
    for field, text_key in (("sections", "body"), ("faq", "answer")):
        if not isinstance(result.get(field), list):
            raise EvidenceError("추가 설명과 FAQ는 배열로 반환하세요. 없으면 빈 배열을 사용하세요.")
        resolved[field] = []
        for item in result[field]:
            if not isinstance(item, dict):
                raise EvidenceError("추가 설명·FAQ 문단의 형식을 확인하세요.")
            resolved[field].append({**item, text_key: render(item.get(text_key))})
    citations = []
    for ref in used:
        p = available[ref]
        raw = originals.get(p["source_id"])
        if raw is None or raw[p["start"]:p["end"]] != p["text"]:
            raise EvidenceError("저장된 원문 구간이 변경되어 인용을 연결할 수 없습니다.")
        citations.append({"source_id": p["source_id"], "quote": raw[p["start"]:p["end"]],
            "evidence_id": ref, "start": p["start"], "end": p["end"], "time_label": p["time_label"]})
    resolved["citations"] = citations
    return resolved
