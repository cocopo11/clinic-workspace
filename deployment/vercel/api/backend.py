"""Private FastAPI function behind the authenticated same-origin gateway."""
import os
import sys
from pathlib import Path

os.environ['GOODMONA_CLOUD'] = '1'
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))

from app import create_app, settings_from_env
from cloud_entry import protect_cloud_app

settings = settings_from_env()
settings.update(
    cloud=True,
    database_url=os.environ['DATABASE_URL'],
    data_dir='/tmp/clinic-workspace-cache',
    secure_cookie=True,
    public_base_url=os.environ.get('PUBLIC_BASE_URL', os.environ.get('GOODMONA_AI_BRIDGE', '')),
)
app = create_app(settings)
protect_cloud_app(app)
