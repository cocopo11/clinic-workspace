# 클라우드 배포

기본 소스는 `server/`이며, 배포용 게이트웨이는 `deployment/vercel/`에 있습니다. 아래 명령은 `build/vercel/`에 배포 파일을 생성합니다. Python 백엔드 사본은 빌드할 때 현재 소스로 생성합니다.

```bash
python deployment/build.py
```

Vercel에서는 생성된 폴더를 프로젝트 루트로 배포합니다. 설치 명령은 `npm install --ignore-scripts`, Node 버전은 24.x이며 별도 프레임워크 빌드 단계는 없습니다. `vercel.json`에 Python 함수, 게이트웨이와 정적 파일 구성이 포함되어 있습니다.

## 기존 서비스 교체

```bash
python deployment/build.py --existing-service
```

이 모드는 로그인 화면에 기존 서비스의 데모 비밀번호 `Demo1234!`를 표시합니다. 기존 DB에 저장된 비밀번호는 DB의 계정 상태를 따릅니다. 로컬 공개판 기본 데모 비밀번호는 `ClinicDemo42!`입니다.

호환 빌드는 기존 DB 스키마 표시, 비공개 Blob 경로, 내부 HTTP 헤더 및 `GOODMONA_` 환경변수 이름을 유지합니다. 연결 규약은 기존 Windows 로컬 AI 프로그램과 호환됩니다. 클라우드 저장소와 PC 연결 설정에는 기존 프로젝트의 환경변수를 사용합니다.

| 변수 | 용도 |
| --- | --- |
| `DATABASE_URL` | PostgreSQL 연결 문자열 |
| `BLOB_READ_WRITE_TOKEN` | 비공개 파일과 암호화된 PC 연결 설정 저장 |
| `CLOUD_BACKEND_KEY` | 게이트웨이와 Python 함수의 상호 인증 |
| `CONFIG_KEY` | PC 연결 설정 암호화 |
| `GOODMONA_AI_BRIDGE` | 이 클라우드 사이트의 HTTPS origin |
| `PUBLIC_BASE_URL` | 선택적 모바일 접속 주소, 생략 시 AI bridge origin 사용 |

프로젝트 설정에서 암호를 입력하고, 코드 저장소에는 예시 값만 기록합니다. 사이트의 `/__connect`에서 로컬 AI PC 연결을 관리합니다. 연결 정보 암호화에 사이트 origin을 사용하므로 기존 서비스를 교체할 때 같은 사이트 주소를 사용합니다.

## 게이트웨이 테스트

빌드한 폴더에서 의존성을 설치하고 테스트를 실행합니다.

```bash
cd build/vercel
npm ci --ignore-scripts
npm test
```

API·데이터 모델 테스트는 저장소 루트 README의 Python 테스트 명령을 사용합니다.
