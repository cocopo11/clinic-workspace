"""Bounded Korean vocabulary hints; never substitutes words in recognized text."""
from pathlib import Path


def korean_hints(settings,language):
    if language!='ko':return {}
    path=Path(settings.get('whisper_terms_file',Path(__file__).with_name('transcription_terms.txt')))
    if not path.is_absolute():path=Path(__file__).parent/path
    terms=[]
    if path.is_file():
        for line in path.read_text(encoding='utf-8-sig').splitlines():
            term=line.strip()
            if term and not term.startswith('#') and len(term)<=50 and term not in terms:terms.append(term)
    vocabulary=', '.join(terms)[:700]
    return {'initial_prompt':'한국어 병원 상담 및 진료 경과 기록.',**({'hotwords':vocabulary} if vocabulary else {})}
