"""Local demo setup: one app, PC loopback HTTP, tablet TLS, public setup page.

Never installs a trust anchor, changes a firewall, or serves any private key.
"""
import argparse
import asyncio
import base64
import html
import io
import ipaddress
import json
import os
import plistlib
import signal
import socket
import threading
import uuid
import webbrowser
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import qrcode
import uvicorn
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

PRIVATE_NETWORKS=tuple(ipaddress.ip_network(n) for n in ('10.0.0.0/8','172.16.0.0/12','192.168.0.0/16'))


class SetupError(RuntimeError):pass


def private_ip(value):
    try:ip=ipaddress.IPv4Address(value)
    except ValueError:raise SetupError('PC의 사설 IPv4 주소를 확인하세요.')
    if not any(ip in n for n in PRIVATE_NETWORKS):raise SetupError('승인된 사설 Wi-Fi의 PC IPv4 주소를 사용하세요.')
    return ip


def detect_ip(preferred='192.0.2.42'):
    try:addresses={r[4][0] for r in socket.getaddrinfo(socket.gethostname(),None,socket.AF_INET)}
    except OSError:addresses=set()
    if preferred in addresses:return str(private_ip(preferred))
    # UDP connect selects a local route without transmitting a packet.
    try:
        with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as s:
            s.connect(('192.168.1.1',9));candidate=s.getsockname()[0]
        return str(private_ip(candidate))
    except (OSError,SetupError):pass
    for address in sorted(addresses):
        try:return str(private_ip(address))
        except SetupError:pass
    raise SetupError('Wi-Fi의 PC 주소를 찾지 못했습니다. --ip 옵션으로 PC IPv4 주소를 지정하세요.')


def default_directory():
    if os.name=='nt':
        base=os.environ.get('LOCALAPPDATA')
        if not base:raise SetupError('Windows 사용자 데이터 폴더를 찾지 못했습니다.')
        return Path(base)/'ClinicWorkspaceDemo'/'https'
    return Path.home()/'.local'/'share'/'ClinicWorkspaceDemo'/'https'


def private_write(path,data):
    # Exclusive staging file with owner-only mode; never expose through the web app.
    temporary=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'wb') as f:f.write(data)
        os.replace(temporary,path)
    finally:temporary.unlink(missing_ok=True)


def key_bytes(key):return key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption())
def cert_bytes(cert):return cert.public_bytes(serialization.Encoding.PEM)
def public_bytes(key):return key.public_key().public_bytes(serialization.Encoding.DER,serialization.PublicFormat.SubjectPublicKeyInfo)


def prepare_certificates(directory,address):
    address=private_ip(address);directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    if os.name!='nt':directory.chmod(0o700)
    ca_path=directory/'root-cert.pem';ca_key_path=directory/'root-key.pem'
    cert_path=directory/'server-cert.pem';key_path=directory/'server-key.pem'
    now=datetime.now(timezone.utc)
    if ca_path.exists()!=ca_key_path.exists():raise SetupError('기존 연결 인증서 파일 일부가 없습니다. HTTPS 사용자 데이터 폴더를 복원하세요. 기존 인증서를 자동 교체하지 않았습니다.')
    if ca_path.exists():
        try:
            ca=x509.load_pem_x509_certificate(ca_path.read_bytes());ca_key=serialization.load_pem_private_key(ca_key_path.read_bytes(),None)
            ca.verify_directly_issued_by(ca)
            if public_bytes(ca_key)!=ca.public_key().public_bytes(serialization.Encoding.DER,serialization.PublicFormat.SubjectPublicKeyInfo):raise ValueError()
            if not ca.extensions.get_extension_for_class(x509.BasicConstraints).value.ca or ca.not_valid_after_utc<now+timedelta(days=7):raise ValueError()
        except Exception:raise SetupError('기존 연결 인증서를 사용할 수 없습니다. 자동으로 신뢰 인증서를 바꾸지 않았습니다. 인증서 상태를 확인하세요.')
    else:
        ca_key=rsa.generate_private_key(public_exponent=65537,key_size=3072)
        short=uuid.uuid4().hex[:8].upper();name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'ClinicWorkspace Demo '+short)])
        permitted=[x509.IPAddress(n) for n in PRIVATE_NETWORKS]+[x509.IPAddress(ipaddress.ip_network('127.0.0.0/8')),x509.DNSName('localhost')]
        ca=(x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now-timedelta(minutes=10)).not_valid_after(now+timedelta(days=365))
            .add_extension(x509.BasicConstraints(ca=True,path_length=0),critical=True)
            .add_extension(x509.KeyUsage(False,False,False,False,False,True,True,None,None),critical=True)
            .add_extension(x509.NameConstraints(permitted_subtrees=permitted,excluded_subtrees=None),critical=True)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),critical=False)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()),critical=False).sign(ca_key,hashes.SHA256()))
        private_write(ca_key_path,key_bytes(ca_key));private_write(ca_path,cert_bytes(ca))
    reuse=False
    if cert_path.exists() and key_path.exists():
        try:
            cert=x509.load_pem_x509_certificate(cert_path.read_bytes());key=serialization.load_pem_private_key(key_path.read_bytes(),None)
            cert.verify_directly_issued_by(ca)
            reuse=(address in cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(x509.IPAddress)
                   and cert.not_valid_after_utc>now+timedelta(days=14) and public_bytes(key)==cert.public_key().public_bytes(serialization.Encoding.DER,serialization.PublicFormat.SubjectPublicKeyInfo))
        except Exception:pass
    if not reuse:
        key=rsa.generate_private_key(public_exponent=65537,key_size=3072)
        name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'ClinicWorkspace local demo')])
        cert=(x509.CertificateBuilder().subject_name(name).issuer_name(ca.subject).public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now-timedelta(minutes=10)).not_valid_after(min(now+timedelta(days=90),ca.not_valid_after_utc-timedelta(days=1)))
            .add_extension(x509.BasicConstraints(ca=False,path_length=None),critical=True)
            .add_extension(x509.KeyUsage(True,False,True,False,False,False,False,None,None),critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),critical=False)
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(address),x509.IPAddress(ipaddress.ip_address('127.0.0.1')),x509.DNSName('localhost')]),critical=False)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()),critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),critical=False).sign(ca_key,hashes.SHA256()))
        private_write(key_path,key_bytes(key));private_write(cert_path,cert_bytes(cert))
    fingerprint=ca.fingerprint(hashes.SHA256()).hex().upper()
    return {'cert_path':cert_path,'key_path':key_path,'root_path':ca_path,'root_der':ca.public_bytes(serialization.Encoding.DER),
            'name':ca.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value,'fingerprint':fingerprint}


def certificate_profile(material):
    identifier='local.clinicworkspace.demo.'+material['fingerprint'][:16].lower()
    uid=lambda suffix:str(uuid.uuid5(uuid.NAMESPACE_URL,identifier+suffix)).upper()
    cert={'PayloadType':'com.apple.security.root','PayloadVersion':1,'PayloadIdentifier':identifier+'.root','PayloadUUID':uid('root'),
          'PayloadDisplayName':material['name'],'PayloadCertificateFileName':'ClinicWorkspace-Demo-Root.cer','PayloadContent':material['root_der']}
    return plistlib.dumps({'PayloadType':'Configuration','PayloadVersion':1,'PayloadIdentifier':identifier,'PayloadUUID':uid('profile'),
        'PayloadDisplayName':material['name'],'PayloadDescription':'이 PC의 로컬 HTTPS 시연 연결용 인증서입니다. VPN·MDM·계정 설정은 포함하지 않습니다.',
        'PayloadOrganization':'ClinicWorkspace local workflow demo','PayloadRemovalDisallowed':False,'PayloadContent':[cert]})


def setup_page(material,address,http_port=8000,tls_port=8443,setup_port=8080):
    url=f'http://{address}:{setup_port}/';secure=f'https://{address}:{tls_port}/ipad-check'
    image=io.BytesIO();qrcode.make(url).save(image,format='PNG');qr=base64.b64encode(image.getvalue()).decode()
    name=html.escape(material['name']);fp=':'.join(material['fingerprint'][i:i+2] for i in range(0,64,2))
    return f'''<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="referrer" content="no-referrer"><title>아이패드 연결 준비</title>
<style>*{{box-sizing:border-box}}body{{margin:0;padding:30px 18px;background:#f3f5ef;color:#253e33;font:17px/1.7 system-ui,sans-serif}}main{{max-width:760px;margin:auto}}h1{{font-size:32px;line-height:1.3}}section{{background:white;border:1px solid #dce4d8;padding:24px;border-radius:18px;margin:18px 0}}h2{{font-size:22px}}.button{{display:block;background:#315b49;color:white;text-decoration:none;text-align:center;border-radius:12px;padding:15px;margin:12px 0;font-weight:650}}.secondary{{background:#e8eee4;color:#315b49}}.small{{font-size:14px;color:#607265}}code{{word-break:break-all}}img{{width:180px;height:180px}}summary{{cursor:pointer}}#pc{{display:none}}</style>
<main><span class="small">처음 한 번만 · 가상 데이터 시연용</span><h1>아이패드 녹음,<br>연결 준비부터 한 화면에서.</h1>
<section id="pc"><h2>PC에서는 이 QR만 보여주세요.</h2><img src="data:image/png;base64,{qr}" alt="아이패드 연결 준비 QR"><p>아이패드 Safari에서 QR을 열어 아래 두 단계를 진행합니다.</p><code>{url}</code><a class="button secondary" href="http://127.0.0.1:{http_port}/" target="_blank" rel="noopener">PC 진료 화면 열기</a></section>
<section><h2>1. 연결 인증서 설치</h2><p>아래 버튼 → 다운로드 허용 → <strong>설정 → 프로파일이 다운로드됨 → 설치</strong> 순서로 진행하세요.</p><a class="button" href="/ClinicWorkspace-iPad.mobileconfig">아이패드 연결 인증서 받기</a><p class="small">설치할 이름: <strong>{name}</strong><br>설정에 항목이 안 보이면 일반 → VPN 및 기기 관리에서 찾으세요. 다운로드 후 8분 안에 설치합니다.</p></section>
<section><h2>2. 이 인증서 신뢰 켜기</h2><p><strong>설정 → 일반 → 정보 → 인증서 신뢰 설정</strong>에서 <strong>{name}</strong>을 켜세요.</p><p class="small">이 PC가 로컬 주소에 발급한 인증서를 신뢰하도록 허용하는 단계입니다. 기기에서 직접 결정하며 자동 변경하지 않습니다.</p><a class="button" href="{secure}">설치 후 HTTPS 연결 확인</a><p>연결 확인이 끝나면 PC 진료 화면에서 환자 선택 → 아이패드 녹음 QR을 새로 만드세요.</p></section>
<section><details><summary>인증서 확인·문제 해결·제거</summary><p>PC 화면과 아이패드에 표시된 인증서 이름을 확인하세요. 브라우저의 인증서 경고를 무시하고 진행하지 않습니다.</p><p class="small">SHA-256 지문<br><code>{fp}</code></p><p>이 프로파일에는 공개 인증서 1개만 들어 있습니다. VPN·MDM·계정·개인키는 포함하지 않습니다. 시연이 끝나면 설정 → 일반 → VPN 및 기기 관리에서 해당 프로파일을 제거할 수 있습니다.</p><p>페이지가 안 열리면 같은 Wi-Fi인지, 서버 창이 열려 있는지, Windows의 사설망 접속 허용 상태를 확인하세요. 인증서 설치를 조직에서 제한하는 기기는 담당자에게 요청해야 합니다.</p></details></section>
<script>if(['127.0.0.1','localhost'].includes(location.hostname))document.getElementById('pc').style.display='block';</script></main></html>'''.encode()


def bootstrap_server(material,address,http_port,tls_port,setup_port=8080,host='0.0.0.0'):
    page=setup_page(material,address,http_port,tls_port,setup_port);profile=certificate_profile(material)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_HEAD(self):self.do_GET(head=True)
        def do_GET(self,head=False):
            peer=ipaddress.ip_address(self.client_address[0])
            if not peer.is_loopback and not any(peer in n for n in PRIVATE_NETWORKS):self.send_error(403);return
            path=urlsplit(self.path).path
            if path=='/':content,mime=page,'text/html; charset=utf-8'
            elif path=='/ClinicWorkspace-iPad.mobileconfig':content,mime=profile,'application/x-apple-aspen-config'
            else:self.send_error(404);return
            self.send_response(200);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(content)))
            self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.send_header('Referrer-Policy','no-referrer')
            if path.endswith('.mobileconfig'):self.send_header('Content-Disposition','attachment; filename="ClinicWorkspace-iPad.mobileconfig"')
            self.end_headers()
            if not head:self.wfile.write(content)
    return ThreadingHTTPServer((host,setup_port),Handler)


def bind_socket(host,port):
    s=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
    try:
        if os.name=='nt':s.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
        else:s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
        s.bind((host,port));s.listen(128);return s
    except OSError:
        s.close();raise SetupError(f'{port}번 포트가 이미 사용 중이거나 열 수 없습니다. 기존 서버 창을 Ctrl+C로 종료한 뒤 다시 실행하세요.')


class ListenerServer(uvicorn.Server):
    @contextmanager
    def capture_signals(self):yield


async def serve_app(app,material,http_socket,tls_socket,on_ready=None,shutdown_event=None):
    servers=[ListenerServer(uvicorn.Config(app,host='127.0.0.1',port=http_socket.getsockname()[1],lifespan='off',access_log=False,timeout_graceful_shutdown=5)),
             ListenerServer(uvicorn.Config(app,host='0.0.0.0',port=tls_socket.getsockname()[1],lifespan='off',access_log=False,
                 ssl_certfile=str(material['cert_path']),ssl_keyfile=str(material['key_path']),timeout_graceful_shutdown=5))]
    old_handlers={}
    def stop_all(*args):
        for server in servers:server.should_exit=True
    if threading.current_thread() is threading.main_thread():
        for sig in (signal.SIGINT,signal.SIGTERM):old_handlers[sig]=signal.signal(sig,stop_all)
    async def monitor():
        for _ in range(300):
            if any(s.should_exit for s in servers):return
            if all(s.started for s in servers):
                if on_ready:on_ready()
                break
            await asyncio.sleep(.1)
        else:stop_all();raise SetupError('서버 시작을 완료하지 못했습니다. 실행 창을 확인하세요.')
        while not all(s.should_exit for s in servers):
            if shutdown_event and shutdown_event.is_set():stop_all();return
            await asyncio.sleep(.1)
    tasks=[]
    try:
        # One FastAPI lifespan and one worker set shared by both ports.
        async with app.router.lifespan_context(app):
            tasks=[asyncio.create_task(s.serve(sockets=[sock])) for s,sock in zip(servers,(http_socket,tls_socket))]
            watcher=asyncio.create_task(monitor());tasks.append(watcher)
            await asyncio.wait(tasks,return_when=asyncio.FIRST_COMPLETED)
            stop_all()
            results=await asyncio.gather(*tasks,return_exceptions=True)
            for result in results:
                if isinstance(result,BaseException):raise result
    finally:
        stop_all()
        for task in tasks:
            if not task.done():task.cancel()
        for sig,handler in old_handlers.items():signal.signal(sig,handler)


def main():
    p=argparse.ArgumentParser(description='ClinicWorkspace iPad local HTTPS setup')
    p.add_argument('--ip');p.add_argument('--http-port',type=int,default=8000);p.add_argument('--tls-port',type=int,default=8443)
    p.add_argument('--setup-port',type=int,default=8080);p.add_argument('--directory',type=Path);p.add_argument('--no-browser',action='store_true');args=p.parse_args()
    sockets=[];setup=None
    try:
        address=str(private_ip(args.ip)) if args.ip else detect_ip()
        for host,port in (('127.0.0.1',args.http_port),('0.0.0.0',args.tls_port)):sockets.append(bind_socket(host,port))
        material=prepare_certificates(args.directory or default_directory(),address)
        setup=bootstrap_server(material,address,args.http_port,args.tls_port,args.setup_port)
        threading.Thread(target=setup.serve_forever,name='ipad-setup',daemon=True).start()
        os.environ['CLINICWORKSPACE_PUBLIC_BASE_URL']=f'https://{address}:{args.tls_port}'
        # Import only after all ports are reserved: a failed second launch cannot reset running jobs.
        from app import app
        from app import APP_VERSION
        print('\n아이패드 연결 서버를 시작합니다. 이 창을 열어 두세요.\n')
        print('실행 버전:', APP_VERSION)
        print('실행 폴더:', Path(__file__).resolve().parent)
        print(f'PC 진료 화면: http://127.0.0.1:{args.http_port}')
        print(f'아이패드 첫 연결: http://{address}:{args.setup_port}')
        print(f'아이패드 녹음 주소: https://{address}:{args.tls_port}')
        print('처음 한 번: 안내 페이지에서 인증서 설치 → 인증서 신뢰 켜기\n종료: Ctrl+C\n')
        def ready():
            if not args.no_browser:
                try:webbrowser.open(f'http://127.0.0.1:{args.setup_port}/')
                except (OSError,webbrowser.Error):print(f'브라우저에서 http://127.0.0.1:{args.setup_port}/ 를 직접 여세요.')
        asyncio.run(serve_app(app,material,*sockets,on_ready=ready));return 0
    except (SetupError,OSError) as e:print('\n연결 준비를 완료하지 못했습니다:',e);return 1
    finally:
        if setup:setup.shutdown();setup.server_close()
        for sock in sockets:sock.close()


if __name__=='__main__':raise SystemExit(main())
