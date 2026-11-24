import argparse
import socket
import sys
from pathlib import Path

import uvicorn

parser = argparse.ArgumentParser(description="ClinicWorkspace fictional clinic workflow demo")
parser.add_argument("--host", default="127.0.0.1", help="LAN phone demo: 0.0.0.0; PC-only: 127.0.0.1")
parser.add_argument("--port", default=8000, type=int)
parser.add_argument("--cert", help="Optional trusted TLS certificate PEM path")
parser.add_argument("--key", help="Optional TLS private-key PEM path")
args = parser.parse_args()
if bool(args.cert) != bool(args.key):
    parser.error("Both --cert and --key are required for HTTPS")
print("\n가상 데이터 전용 데모 서버입니다. 실제 환자 자료를 입력하지 마세요.")
# Reserve the listener before importing app: importing initializes the store
# and job recovery, which must not run in a failed second server process.
listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
if sys.platform == "win32" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
try:
    listener.bind((args.host, args.port))
    listener.listen(128)
except OSError as error:
    listener.close()
    print(f"업무 서버 포트 {args.port}를 사용할 수 없습니다. 기존 서버 창을 확인하세요. ({error})")
    raise SystemExit(1)
from app import APP_VERSION
print("실행 버전:", APP_VERSION)
print("실행 폴더:", Path(__file__).resolve().parent)
print("PC 접속:", ("https" if args.cert else "http")+f"://localhost:{args.port}")
try:
    addresses = sorted({r[4][0] for r in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)})
    print("LAN 주소 후보:", ", ".join(addresses))
except OSError:
    pass
print("같은 네트워크의 휴대폰에서는 PC의 IPv4 주소를 사용하세요.")
print("시연 계정: admin / doctor / staff; 공통 비밀번호: ClinicDemo42!")
print("종료: Ctrl+C\n")
try:
    uvicorn.Server(uvicorn.Config("app:app", host=args.host, port=args.port, access_log=False,
                  ssl_certfile=args.cert, ssl_keyfile=args.key)).run(sockets=[listener])
finally:
    listener.close()
