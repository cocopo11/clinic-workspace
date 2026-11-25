"""Private assets, isolated visitor prefixes and durable failed-delete retries."""
import mimetypes
import re
import time
from pathlib import Path

AREAS = {'photos', 'recording_uploads'}


def key(area, name, namespace=None):
    if area not in AREAS or not name or Path(name).name != name or '\\' in name or name in {'.', '..'}:
        raise ValueError('Invalid asset name')
    if namespace and not re.fullmatch(r'demo_[0-9a-f]{32}', namespace):
        raise ValueError('Invalid workspace')
    prefix = f'clinicworkspace/workspaces/{namespace}/assets/' if namespace else 'clinicworkspace/assets/'
    return prefix+area+'/'+name


class CloudFiles:
    def __init__(self, directory, namespace=None, store=None):
        self.directory, self.namespace, self.store = Path(directory), namespace, store

    def save(self, area, name, data):
        from vercel.blob.sync import put_blob
        target = self.directory / area / name
        blob_key = key(area, name, self.namespace)
        baseline = name in {'baseline-DEMO-101.png', 'baseline-DEMO-103.png'}
        if self.store and not baseline:
            with self.store.connect() as c:
                total = c.execute("SELECT COALESCE(SUM(size),0) FROM asset_ledger WHERE area!='parts'").fetchone()[0]
                old = c.execute('SELECT size FROM asset_ledger WHERE area=? AND name=?', (area,name)).fetchone()
                if self.namespace and total-(old[0] if old else 0)+len(data)>100*1024*1024:
                    from fastapi import HTTPException
                    raise HTTPException(413, '체험 공간의 파일 용량은 100MB까지입니다. 기존 자료를 정리해 주세요.')
                c.execute('INSERT INTO asset_ledger VALUES(?,?,?,?,?) ON CONFLICT(area,name) DO UPDATE SET size=excluded.size,state=excluded.state,updated=excluded.updated',
                          (area,name,len(data),'pending',time.time()))
        try:
            put_blob(blob_key, data, access='private', allow_overwrite=True,
                     content_type=mimetypes.guess_type(name)[0] or 'application/octet-stream')
            target.parent.mkdir(exist_ok=True)
            target.write_bytes(data)
            if self.store and not baseline:
                with self.store.connect() as c:
                    c.execute("UPDATE asset_ledger SET state='stored' WHERE area=? AND name=?", (area,name))
        except Exception:
            if not baseline:
                self.delete(area,name)
            raise
        return target

    def load(self, area, name):
        from vercel.blob import BlobNotFoundError
        from vercel.blob.sync import get_blob
        from fastapi import HTTPException
        target = self.directory / area / name
        blob_key = key(area, name, self.namespace)
        if not target.is_file():
            try:
                result = get_blob(blob_key, access='private')
            except BlobNotFoundError:
                raise HTTPException(404, '파일을 찾지 못했습니다.') from None
            target.parent.mkdir(exist_ok=True)
            target.write_bytes(result.body)
        return target

    def delete(self, area, name):
        from vercel.blob.sync import delete_blob
        blob_key = key(area, name, self.namespace)
        if self.store:
            with self.store.connect() as c:
                c.execute("INSERT INTO asset_ledger VALUES(?,?,0,'delete',?) ON CONFLICT(area,name) DO UPDATE SET state='delete',updated=excluded.updated", (area,name,time.time()))
        try:
            delete_blob(blob_key)
        except Exception:
            if not self.store:
                raise
            # The persisted ledger is retried by maintenance; don't hide the
            # original failed database operation with a second storage exception.
            return False
        (self.directory / area / name).unlink(missing_ok=True)
        if self.store:
            with self.store.connect() as c:
                c.execute('DELETE FROM asset_ledger WHERE area=? AND name=?', (area,name))
        return True

    def cleanup(self):
        """Retry deletions and reconcile old uploads against durable DB records."""
        from vercel.blob.sync import query_blobs
        deleted = 0
        with self.store.connect() as c:
            pending = [dict(r) for r in c.execute("SELECT area,name FROM asset_ledger WHERE state='delete' LIMIT 100")]
        for row in pending:
            deleted += bool(self.delete(row['area'],row['name']))
        prefix = f'clinicworkspace/workspaces/{self.namespace}/assets/' if self.namespace else 'clinicworkspace/assets/'
        # The SDK iterator follows cursor pages without loading the whole catalog.
        for blob in query_blobs(prefix=prefix, page_size=100):
            if blob.uploaded_at.timestamp()>time.time()-86400:
                continue
            tail = blob.pathname[len(prefix):].split('/')
            if len(tail)!=2 or tail[0] not in AREAS:
                continue
            area,name=tail
            with self.store.connect(shared=True) as c:
                if area=='photos':
                    referenced=c.execute('SELECT 1 FROM photos WHERE filename=?',(name,)).fetchone()
                else:
                    referenced=c.execute("SELECT 1 FROM recording_jobs WHERE audio_path=? AND status IN ('queued','running')",(name,)).fetchone()
            if not referenced:
                deleted += bool(self.delete(area,name))
        return deleted
