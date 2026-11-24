"""Private persistent assets; local files are a disposable response cache only."""
import hashlib
import mimetypes
from pathlib import Path

AREAS = {"photos", "recording_uploads"}


def key(area, name):
    if area not in AREAS or not name or Path(name).name != name or '\\' in name or name in {'.', '..'}:
        raise ValueError('Invalid asset name')
    return 'clinicworkspace/assets/' + area + '/' + name


class CloudFiles:
    def __init__(self, directory):
        self.directory = Path(directory)

    def save(self, area, name, data):
        from vercel.blob.sync import put_blob
        # Photo names and recording IDs are unique; baseline images are deterministic.
        put_blob(key(area, name), data, access='private', allow_overwrite=True,
                 content_type=mimetypes.guess_type(name)[0] or 'application/octet-stream')
        target = self.directory / area / name
        target.parent.mkdir(exist_ok=True)
        target.write_bytes(data)
        return target

    def load(self, area, name):
        from vercel.blob import BlobNotFoundError
        from vercel.blob.sync import get_blob
        from fastapi import HTTPException
        target = self.directory / area / name
        key(area, name)
        if not target.is_file():
            try:result = get_blob(key(area, name), access='private')
            except BlobNotFoundError:
                raise HTTPException(404, '파일을 찾지 못했습니다.') from None
            target.parent.mkdir(exist_ok=True)
            target.write_bytes(result.body)
        return target

    def delete(self, area, name):
        from vercel.blob.sync import delete_blob
        delete_blob(key(area, name))
        (self.directory / area / name).unlink(missing_ok=True)
