"""Build the cloud wrapper while retaining compatibility with the original service."""
import argparse
import re
import shutil
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('--existing-service', action='store_true',
                    help='Use the original service demo password in login examples')
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
    if args.existing_service:
        text = text.replace('ClinicDemo42!', 'Demo1234!')
    if source.parent.name == 'static':
        text = re.sub(r'([?&]v=)[A-Za-z0-9._-]+', r'\g<1>20261124-portfolio', text)
    target = backend / source.relative_to(root / 'server')
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding='utf-8')
print(output)
