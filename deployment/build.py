"""Build the cloud wrapper while retaining compatibility with the original service."""
import argparse
import re
import shutil
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('--existing-service', action='store_true',
                    help='Retained for compatibility with existing deployment commands')
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
output = root / 'build/vercel'
if output.exists():
    shutil.rmtree(output)
shutil.copytree(root / 'deployment/vercel', output)
backend = output / 'backend'
backend.mkdir()
# Internal identifiers are retained for persisted Blob assets, DB schema markers,
# the existing Node gateway, and Windows AI connector compatibility.
compatibility = {
    'CLINICWORKSPACE_': 'GOODMONA_',
    'X-ClinicWorkspace-': 'X-Goodmona-',
    'x-clinicworkspace-': 'x-goodmona-',
    'clinicworkspace_schema': 'goodmona_schema',
    'clinicworkspace_mark_changed': 'goodmona_mark_changed',
    'clinicworkspace/assets/': 'goodmona/assets/',
    'clinicworkspace/upload-parts/': 'goodmona/upload-parts/',
}
paths = [*root.joinpath('server').glob('*.py'),
         root / 'server/transcription_terms.txt',
         *root.joinpath('server/static').glob('*')]
for source in paths:
    if not source.is_file():
        continue
    text = source.read_text(encoding='utf-8')
    if source.suffix == '.py':
        for current, legacy in compatibility.items():
            text = text.replace(current, legacy)
    if source.parent.name == 'static':
        text = re.sub(r'([?&]v=)[A-Za-z0-9._-]+', r'\g<1>20261127-layout', text)
    target = backend / source.relative_to(root / 'server')
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding='utf-8')
# Ship the complete page styling with HTML. A slow or filtered CSS request
# must not leave reviewers looking at a partially styled application.
for page in (backend / 'static').glob('*.html'):
    def inline_style(match):
        name = match.group(1)
        css = (backend / 'static' / name).read_text(encoding='utf-8')
        if '</style' in css.lower():
            raise ValueError('Invalid bundled stylesheet')
        return '<style data-stylesheet="'+name+'">'+css+'</style>'
    html = re.sub(r'<link\s+rel="stylesheet"\s+href="/static/([A-Za-z0-9_-]+\.css)(?:\?[^"<>]*)?"\s*/?>',
                  inline_style, page.read_text(encoding='utf-8'))
    page.write_text(html, encoding='utf-8')
print(output)
