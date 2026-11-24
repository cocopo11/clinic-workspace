"""Certificate handling, restricted setup page and actual verified dual-port API flow."""
import asyncio
import io
import ipaddress
import plistlib
import ssl
import tempfile
import threading
import time
import unittest
import wave
from contextlib import asynccontextmanager,redirect_stdout
from pathlib import Path
from urllib.parse import urlsplit,parse_qs
from unittest.mock import patch

import httpx
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from app import create_app,settings_from_env,make_sample
from test_app import FakeAI
from easy_https import (SetupError,bind_socket,bootstrap_server,certificate_profile,
                        prepare_certificates,private_ip,serve_app,main)


class TranscriptionAI(FakeAI):
    def transcribe(self,path,language):
        return {'text':'가상 녹음의 전사 결과입니다.','language':language,'segments':[{'start':0,'end':1,'text':'가상 녹음의 전사 결과입니다.'}]}


class CertificateTests(unittest.TestCase):
    def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.directory=Path(self.tmp.name)
    def tearDown(self):self.tmp.cleanup()
    def test_only_private_addresses_are_accepted(self):
        self.assertEqual(str(private_ip('192.168.50.23')),'192.168.50.23')
        for value in ('8.8.8.8','127.0.0.1','0.0.0.0','localhost','https://192.168.50.23','192.168.50.23;cmd'):
            with self.assertRaises(SetupError):private_ip(value)
    def test_key_pair_san_constraints_and_reuse(self):
        m=prepare_certificates(self.directory,'192.168.50.23')
        ca=x509.load_pem_x509_certificate(m['root_path'].read_bytes());leaf=x509.load_pem_x509_certificate(m['cert_path'].read_bytes())
        leaf.verify_directly_issued_by(ca);self.assertFalse(leaf.extensions.get_extension_for_class(x509.BasicConstraints).value.ca)
        san=leaf.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        self.assertIn(ipaddress.ip_address('192.168.50.23'),san.get_values_for_type(x509.IPAddress))
        self.assertIn('localhost',san.get_values_for_type(x509.DNSName))
        constraints=ca.extensions.get_extension_for_class(x509.NameConstraints)
        self.assertTrue(constraints.critical);self.assertEqual(len(constraints.value.permitted_subtrees),5)
        old=m['cert_path'].read_bytes();again=prepare_certificates(self.directory,'192.168.50.23')
        self.assertEqual(again['fingerprint'],m['fingerprint']);self.assertEqual(old,m['cert_path'].read_bytes())
    def test_ip_change_keeps_root_profile_and_renews_leaf(self):
        m=prepare_certificates(self.directory,'192.168.50.23');profile=certificate_profile(m);leaf=m['cert_path'].read_bytes()
        other=prepare_certificates(self.directory,'192.168.1.99')
        self.assertEqual(certificate_profile(other),profile);self.assertNotEqual(leaf,other['cert_path'].read_bytes())
    def test_profile_contains_one_public_certificate_and_is_removable(self):
        m=prepare_certificates(self.directory,'192.168.50.23');p=plistlib.loads(certificate_profile(m))
        self.assertFalse(p['PayloadRemovalDisallowed']);self.assertEqual(len(p['PayloadContent']),1)
        self.assertEqual(p['PayloadContent'][0]['PayloadType'],'com.apple.security.root')
        self.assertEqual(p['PayloadContent'][0]['PayloadContent'],m['root_der'])
        self.assertNotIn(b'PRIVATE KEY',certificate_profile(m))
        self.assertEqual(x509.load_der_x509_certificate(p['PayloadContent'][0]['PayloadContent']).fingerprint(hashes.SHA256()).hex().upper(),m['fingerprint'])
    def test_incomplete_or_corrupt_root_is_not_silently_replaced(self):
        key=self.directory/'root-key.pem';key.write_bytes(b'keep this existing file')
        with self.assertRaises(SetupError):prepare_certificates(self.directory,'192.168.50.23')
        (self.directory/'root-cert.pem').write_bytes(b'bad certificate')
        with self.assertRaises(SetupError):prepare_certificates(self.directory,'192.168.50.23')
        self.assertEqual(key.read_bytes(),b'keep this existing file')
    def test_bootstrap_has_only_public_routes_and_download_profile(self):
        m=prepare_certificates(self.directory,'192.168.50.23');server=bootstrap_server(m,'192.168.50.23',8000,8443,0,'127.0.0.1')
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            with httpx.Client(base_url=f'http://127.0.0.1:{server.server_port}',trust_env=False) as c:
                page=c.get('/');self.assertEqual(page.status_code,200);self.assertIn(m['name'],page.text)
                profile=c.get('/ClinicWorkspace-iPad.mobileconfig');self.assertIn('application/x-apple-aspen-config',profile.headers['content-type'])
                self.assertEqual(plistlib.loads(profile.content)['PayloadContent'][0]['PayloadContent'],m['root_der'])
                self.assertEqual(c.head('/ClinicWorkspace-iPad.mobileconfig').content,b'')
                for path in ('/root-key.pem','/server-key.pem','/api/patients','/../root-key.pem','/static/root-key.pem'):
                    self.assertEqual(c.get(path).status_code,404)
        finally:server.shutdown();server.server_close();thread.join(2)
    def test_existing_port_fails_before_importing_application(self):
        sock=bind_socket('127.0.0.1',0)
        try:
            with patch('sys.argv',['easy_https.py','--ip','192.168.50.23','--http-port',str(sock.getsockname()[1])]),patch.dict('sys.modules',{'app':None}),redirect_stdout(io.StringIO()) as output:
                self.assertEqual(main(),1);self.assertIn('Ctrl+C',output.getvalue())
        finally:sock.close()


class DualListenerTests(unittest.IsolatedAsyncioTestCase):
    async def test_shared_login_photo_notification_and_auto_recording_over_verified_tls(self):
        with tempfile.TemporaryDirectory() as td:
            material=prepare_certificates(Path(td)/'cert','192.168.50.23')
            http_socket=bind_socket('127.0.0.1',0);tls_socket=bind_socket('127.0.0.1',0)
            http_url=f'http://127.0.0.1:{http_socket.getsockname()[1]}';tls_url=f'https://127.0.0.1:{tls_socket.getsockname()[1]}'
            cfg=settings_from_env();cfg.update(data_dir=str(Path(td)/'data'),public_base_url=tls_url)
            app=create_app(cfg,TranscriptionAI());counts={'start':0,'stop':0};original=app.router.lifespan_context
            @asynccontextmanager
            async def lifecycle(app):
                counts['start']+=1
                async with original(app):yield
                counts['stop']+=1
            app.router.lifespan_context=lifecycle
            ready=threading.Event();stop=threading.Event();task=asyncio.create_task(serve_app(app,material,http_socket,tls_socket,on_ready=ready.set,shutdown_event=stop))
            try:
                self.assertTrue(await asyncio.to_thread(ready.wait,5))
                context=ssl.create_default_context(cafile=str(material['root_path']))
                async with httpx.AsyncClient(base_url=http_url,trust_env=False,timeout=5) as pc,httpx.AsyncClient(base_url=tls_url,verify=context,trust_env=False,timeout=5) as tablet:
                    login=await pc.post('/api/login',json={'username':'doctor','password':'ClinicDemo42!'});self.assertEqual(login.status_code,200)
                    pc.headers['X-CSRF-Token']=login.json()['csrf']
                    self.assertEqual((await pc.get('/api/connection')).json()['public_base_url'],tls_url)
                    self.assertEqual((await tablet.get('/ipad-check')).status_code,200)
                    pair=await pc.post('/api/capture-sessions',json={'patient_id':'DEMO-101','base_url':tls_url});photo_token=parse_qs(urlsplit(pair.json()['url']).query)['token'][0]
                    async with pc.stream('GET','/api/patients/DEMO-101/events') as response:
                        lines=response.aiter_lines();self.assertEqual(await anext(lines),'event: ready');await anext(lines);await anext(lines)
                        uploaded=await tablet.post('/api/capture/'+photo_token,files={'file':('test.png',make_sample(),'image/png')});self.assertEqual(uploaded.status_code,200)
                        self.assertEqual(await asyncio.wait_for(anext(lines),3),'event: photo')
                    self.assertIsNotNone((await pc.get('/api/patients/DEMO-101/photos')).json()['today'])
                    pair=await pc.post('/api/recording-links',json={'patient_id':'DEMO-101','base_url':tls_url});token=parse_qs(urlsplit(pair.json()['url']).query)['token'][0]
                    raw=io.BytesIO()
                    with wave.open(raw,'wb') as w:w.setnchannels(1);w.setsampwidth(2);w.setframerate(8000);w.writeframes(b'\x00\x00'*8000)
                    uploaded=await tablet.post('/api/recording/'+token,files={'file':('record.wav',raw.getvalue(),'audio/wav')});self.assertEqual(uploaded.status_code,202)
                    deadline=time.monotonic()+5
                    while time.monotonic()<deadline:
                        job=(await pc.get('/api/recording-jobs?patient_id=DEMO-101')).json()['jobs'][0]
                        if job['status'] not in {'queued','running'}:break
                        await asyncio.sleep(.02)
                    self.assertEqual(job['status'],'succeeded',job);self.assertFalse(job['reviewed']);self.assertFalse(job['crm_connected'])
                    self.assertEqual(len((await pc.get('/api/notes')).json()['notes']),1)
                    self.assertTrue((await tablet.post('/api/recording/'+token,files={'file':('record.wav',raw.getvalue(),'audio/wav')})).json()['duplicate'])
            finally:
                stop.set();await asyncio.wait_for(task,8);http_socket.close();tls_socket.close()
            self.assertEqual(counts,{'start':1,'stop':1})


if __name__=='__main__':unittest.main()
