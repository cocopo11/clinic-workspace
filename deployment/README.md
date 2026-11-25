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

이 옵션은 기존 호출 명령과의 호환을 위해 유지됩니다. 공개된 기존 비밀번호는 더 이상 운영 데이터에 접근할 수 없습니다. 배포 전에 아래 운영자 비밀번호를 준비해야 합니다.

호환 빌드는 기존 DB 스키마 표시, 비공개 Blob 경로, 내부 HTTP 헤더 및 `GOODMONA_` 환경변수 이름을 유지합니다. 연결 규약은 기존 Windows 로컬 AI 프로그램과 호환됩니다. 클라우드 저장소와 PC 연결 설정에는 기존 프로젝트의 환경변수를 사용합니다.

| 변수 | 용도 |
| --- | --- |
| `CLINICWORKSPACE_ADMIN_PASSWORD` | 필수: 24자 이상 무작위 운영자 비밀번호. 첫 적용 또는 값 변경 시 기존 세션을 모두 무효화 |
| `CRON_SECRET` | 필수: 일일 공간·파일 정리 호출의 무작위 인증 비밀 |
| `DATABASE_URL` | PostgreSQL 연결 문자열 |
| `BLOB_READ_WRITE_TOKEN` | 비공개 파일과 암호화된 PC 연결 설정 저장 |
| `CLOUD_BACKEND_KEY` | 게이트웨이와 Python 함수의 상호 인증 |
| `CONFIG_KEY` | PC 연결 설정 암호화 |
| `GOODMONA_AI_BRIDGE` | 이 클라우드 사이트의 HTTPS origin |
| `CONNECTION_SITE_ORIGIN` | 선택적 기존 PC 설정 암호화 origin, 공개 주소 변경 시 기존 연결을 읽는 데 사용 |
| `PUBLIC_BASE_URL` | 선택적 모바일 접속 주소, 생략 시 AI bridge origin 사용 |

프로젝트 설정에서 암호를 입력하고, 코드 저장소에는 예시 값만 기록합니다. 사이트의 `/__connect`에서 로컬 AI PC 연결을 관리합니다. 연결 정보 암호화에 사이트 origin을 사용합니다. 공개 주소를 변경할 때는 `CONNECTION_SITE_ORIGIN`에 기존 origin을 지정해 저장된 연결을 재사용합니다. 기존 PC 프로그램의 연결 관리 화면은 프로그램에 설정된 주소에서 열립니다.

## 게이트웨이 테스트

빌드한 폴더에서 의존성을 설치하고 테스트를 실행합니다.

```bash
cd build/vercel
npm ci --ignore-scripts
npm test
```

API·데이터 모델 테스트는 저장소 루트 README의 Python 테스트 명령을 사용합니다.

## 체험 공간과 정리

공개 방문자는 `/api/demo/start`를 통해 24시간짜리 공간을 생성합니다. 최대 10개 공간, IP별 시간당 생성 3회, 전체 일일 생성 20회로 제한합니다. 각 공간은 하루 쓰기 300회, 파일 100MB, AI 요청 시간당 20회(전체 60회)까지입니다. 생성 한도는 자동 초기화되며 쿠키를 지워도 서버의 생성 횟수는 유지됩니다.

공간은 `demo_<32자리 무작위 hex>` 스키마와 별도 Blob prefix를 사용합니다. 초기 자료는 코드에 포함된 가상 자료뿐이며 운영 DB를 복제하지 않습니다. 개인별 로그인 세션을 다시 사용해 다른 스키마에 접근할 수 없습니다. 공간의 관리자 역할도 운영 자료 이전 기능은 이용할 수 없습니다. 공유 연결은 URL fragment로 전달하고 즉시 주소 표시줄에서 제거합니다.

Vercel Cron이 매일 03:17 UTC(12:17 KST) `/__maintenance`를 호출합니다. `Authorization: Bearer <CRON_SECRET>`가 필요합니다. 만료 후 접근은 즉시 차단되며 물리 삭제는 다음 정리 작업에서 진행합니다. 진행 중이던 요청 보호를 위해 최소 10분의 유예를 두고, 삭제 실패 시 카탈로그와 파일 장부를 유지해 다음 작업에서 재시도합니다. 새 공간 생성 시에도 만료 공간 정리를 시도합니다.

운영자는 로그인 화면의 **운영자 로그인**을 선택하고 `admin`과 비공개 환경변수의 비밀번호로 접속합니다. 비밀번호·DB URL·Blob 토큰은 공개 ZIP이나 GitHub에 넣지 마세요. 로컬 기본 데모는 여전히 `127.0.0.1` 전용이며, 로컬 기본 계정을 그대로 외부 공개하면 안 됩니다.

Zod는 보안 수정 버전 4.6.6으로 override하고 lockfile에 고정했습니다. `npm audit --omit=dev`와 게이트웨이 테스트를 함께 실행해 업데이트를 검증합니다.
