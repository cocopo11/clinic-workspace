import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient
from app import create_app, settings_from_env, make_sample
from test_app import FakeAI
from security import consume_limits, harden_owner
from cloud_files import key, CloudFiles


class SecurityTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.cfg={**settings_from_env(),'data_dir':self.temp.name}
        self.app=create_app(self.cfg,FakeAI())
        self.store=self.app.state.store
        self.client=TestClient(self.app)
    def tearDown(self):
        self.client.close();self.temp.cleanup()
    def login(self):
        r=self.client.post('/api/login',json={'username':'admin','password':'ClinicDemo42!'})
        self.assertEqual(r.status_code,200,r.text)
        self.client.headers['X-CSRF-Token']=r.json()['csrf']
        return r
    def test_login_limit_survives_new_application_instance(self):
        for _ in range(10):
            self.assertEqual(self.client.post('/api/login',json={'username':'unknown','password':'wrong'}).status_code,401)
        other=TestClient(create_app(self.cfg,FakeAI()))
        r=other.post('/api/login',json={'username':'unknown','password':'wrong'})
        self.assertEqual(r.status_code,429);self.assertIn('Retry-After',r.headers)
    def test_forged_cookie_cannot_bypass_anonymous_limit(self):
        consume_limits(self.store,[('anonymous:testclient',1,60)])
        with self.store.connect() as c:
            c.execute('UPDATE security_buckets SET count=120')
        self.client.cookies.set('gm_session','forged')
        self.assertEqual(self.client.get('/api/patients/DEMO-101/photos').status_code,429)
    def test_limits_are_atomic_and_expire(self):
        consume_limits(self.store,[('one',1,60)],now=100)
        with self.assertRaises(HTTPException):consume_limits(self.store,[('two',1,60),('one',1,60)],now=101)
        consume_limits(self.store,[('two',1,60)],now=102)
        consume_limits(self.store,[('one',1,60)],now=161)
    def test_denials_aggregate_and_retention_runs(self):
        with self.store.connect() as c:
            c.execute("INSERT INTO audit(actor,action,created) VALUES('old','old',0)")
        for _ in range(12):self.assertEqual(self.client.get('/api/patients/DEMO-101/photos').status_code,401)
        with self.store.connect() as c:
            rows=c.execute('SELECT * FROM audit').fetchall()
        self.assertEqual(len(rows),1)
        self.assertEqual(json.loads(rows[0]['detail'])['repeated'],12)
    def test_owner_rotation_revokes_sessions_and_exposed_passwords(self):
        self.login();cookie=self.client.cookies.get('gm_session')
        private='Private-test-only-secret-not-for-deploy!'
        harden_owner(self.store,private)
        self.assertIsNone(self.store.session(cookie))
        self.assertIsNone(self.store.login('admin','ClinicDemo42!'))
        self.assertIsNone(self.store.login('doctor','ClinicDemo42!'))
        result=self.store.login('admin',private);self.assertIsNotNone(result)
        harden_owner(self.store,private)
        self.assertIsNotNone(self.store.session(result[0]))
        with self.store.connect() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM photos').fetchone()[0],2)
    def test_new_accounts_have_distinct_random_passwords(self):
        self.login()
        a=self.client.post('/api/admin/users',json={'id':'new-a','name':'A','role':'staff'}).json()
        b=self.client.post('/api/admin/users',json={'id':'new-b','name':'B','role':'staff'}).json()
        self.assertNotEqual(a['demo_password'],b['demo_password'])
        self.assertIsNone(self.store.login('new-a','ClinicDemo42!'))
        self.assertIsNotNone(self.store.login('new-a',a['demo_password']))
    def test_photo_insert_failure_deletes_remote_blob(self):
        self.login()
        remote={}
        class Files:
            def save(_,area,name,data):
                remote[area,name]=data
                return Path(self.temp.name)/area/name
            def delete(_,area,name):remote.pop((area,name),None)
        self.store.cloud_files=Files();original=self.store.connect
        class Broken:
            def __init__(self,c):self.c=c
            def execute(self,sql,*args):
                if sql.startswith('INSERT INTO photos('):raise sqlite3.IntegrityError('test failure')
                return self.c.execute(sql,*args)
            def __getattr__(self,name):return getattr(self.c,name)
        @contextmanager
        def connect(*args,**kwargs):
            with original(*args,**kwargs) as c:yield Broken(c)
        with patch.object(self.store,'connect',connect):
            response=self.client.post('/api/patients/DEMO-101/photos',files={'file':('fake.png',make_sample(),'image/png')})
        self.assertEqual(response.status_code,500);self.assertFalse(remote)
    def test_private_blob_prefix_and_failed_delete_journal(self):
        a='demo_'+'a'*32;b='demo_'+'b'*32
        self.assertNotEqual(key('photos','same.jpg',a),key('photos','same.jpg',b))
        self.assertNotEqual(key('photos','same.jpg',a),key('photos','same.jpg'))
        with self.assertRaises(ValueError):key('photos','../bad',a)
        with self.assertRaises(ValueError):key('photos','same.jpg','public')
        files=CloudFiles(self.temp.name,a,store=self.store)
        with patch('vercel.blob.sync.delete_blob',side_effect=RuntimeError('offline')):
            self.assertFalse(files.delete('photos','failed.jpg'))
        with self.store.connect() as c:
            self.assertEqual(c.execute('SELECT state FROM asset_ledger').fetchone()[0],'delete')
        with patch('vercel.blob.sync.delete_blob'):
            self.assertTrue(files.delete('photos','failed.jpg'))
        with self.store.connect() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM asset_ledger').fetchone()[0],0)
    def test_quota_rejects_before_remote_write(self):
        files=CloudFiles(self.temp.name,'demo_'+'a'*32,store=self.store)
        with self.store.connect() as c:c.execute('INSERT INTO asset_ledger VALUES(?,?,?,?,?)',('photos','existing.jpg',100*1024*1024,'stored',0))
        with patch('vercel.blob.sync.put_blob') as put:
            with self.assertRaises(HTTPException) as error:files.save('photos','new.jpg',b'x')
            self.assertEqual(error.exception.status_code,413);put.assert_not_called()

if __name__=='__main__':unittest.main()
