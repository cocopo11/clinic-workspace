# Clinic Workspace

아이폰으로 촬영한 사진을 아이패드에서 이전 방문 사진과 나란히 보고, 그 자리에서 녹음한 경과를 환자별 차트에 남기는 진료 업무 프로토타입입니다. 직원은 자료실에 승인된 문서와 영상 대본을 바탕으로 고객 질문에 답할 수 있습니다.

의원 채용 과제를 계기로 시작한 개인 프로젝트입니다. 이 공개판은 가상 환자, 합성 그림, 예시 업무 문서를 사용합니다. 진료차트 등록 대상은 프로젝트 내부의 모의 CRM입니다.

[온라인 데모 열기](https://clinic-workspace-demo.vercel.app/)

온라인에서 **바로 체험하기**를 누르면 방문자별 가상 데이터가 생성됩니다. 이 공간 안에서는 `admin`, `doctor`, `staff`와 `ClinicDemo42!`로 역할을 체험할 수 있습니다. 운영자의 기존 데이터와 비밀번호는 분리됩니다. 공간은 24시간 후 만료되며 파일 한도는 100MB입니다. 다른 기기에서도 같은 데이터를 보려면 **다른 기기에서 같은 체험 공간 열기**로 연결합니다. AI 기능은 운영 PC 연결 상태에 따라 이용할 수 있습니다.

## 로컬에서 시작하기

Python 3.12 기준입니다. 저장소 루트에서 실행합니다.

~~~bash
python -m venv .venv
~~~

Windows PowerShell에서는 아래 명령으로 실행할 수 있습니다.

~~~powershell
.\.venv\Scripts\python.exe -m pip install -r server/requirements.txt
.\.venv\Scripts\python.exe server/run.py
~~~

macOS와 Linux에서는 다음과 같이 실행합니다.

~~~bash
.venv/bin/python -m pip install -r server/requirements.txt
.venv/bin/python server/run.py
~~~

브라우저에서 [업무 홈](http://127.0.0.1:8000)을 엽니다. 예시 계정은 의료진 `doctor`, 일반 직원 `staff`, 관리자 `admin`이며 로컬 데모 공통 비밀번호는 `ClinicDemo42!`입니다. 첫 실행 때 SQLite와 예시 데이터를 생성합니다. 아래의 추가 명령은 가상환경을 활성화한 터미널 기준입니다.

## 처음 볼 때

1. **환자와 방문을 구분하는 방식** — `doctor`로 로그인해 진료 사진·기록에서 ‘김하늘’을 검색합니다. 동명이인 중 `DEMO-101`을 선택하면 이전 방문의 합성 그림이 표시됩니다. 다른 탭에서 [모바일 진료](http://127.0.0.1:8000/mobile)를 열고 같은 환자에 임의의 풍경 사진을 올립니다. 첫 탭에서 오늘 사진이 바뀌는 모습을 확인할 수 있습니다.
2. **같은 자료실, 다른 열람 범위** — 관리자와 일반 직원으로 각각 로그인해 자료실을 비교합니다. 관리자 전용 계정 점검 문서는 역할에 따라 열람 범위가 달라지는 예시입니다.
3. **음성에서 기록까지** — AI 설치 후 모바일 진료에서 사진 비교·녹음을 엽니다. 자동 모드에서는 전사 결과가 모의 차트에 기록됩니다. 수동 모드에서는 원문을 확인한 뒤 등록합니다. 자동 모드는 데모의 저장 정책이며 전사의 임상적 정확성 판정을 뜻하지 않습니다.
4. **근거를 따라가는 검색** — AI 설치 후 직원 계정으로 CS 위키에 ‘예약을 변경해 달라는 고객에게 바로 변경됐다고 안내해도 되나요?’를 입력합니다. 답변에 나온 문구를 자료실의 ‘예약 변경 응대 · 예시’ 원문과 대조할 수 있습니다.

휴대폰·아이패드에서 같은 PC에 접속할 때는 해당 PC의 LAN 주소를 사용합니다. 아이패드 마이크에는 신뢰된 HTTPS가 필요합니다. 같은 사설 Wi-Fi에서 기본 서버를 종료한 뒤 `python -m pip install -r server/requirements_https.txt -r deployment/vercel/requirements.txt`, `python server/easy_https.py`를 순서대로 실행하면 연결 및 기기 등록 주소가 표시됩니다.

## 로컬 AI 켜기

Ollama를 PC에서 실행한 뒤 아래 명령으로 음성·영상 처리 도구와 모델을 준비합니다. 기본 설정은 CPU용 Whisper small, 문서 임베딩은 bge-m3, 답변 모델은 qwen3:4b입니다. 첫 모델 다운로드에는 인터넷 연결이 필요합니다.

~~~bash
python -m pip install -r server/requirements-ai.txt
python server/prepare_models.py
~~~

모델을 변경하려면 `server/config.example.json`을 같은 폴더의 `config.json`으로 복사하고 설정을 바꾼 뒤 서버를 재시작합니다. 유튜브 자료 등록은 자막을 먼저 시도하고, 지원되는 영상에서 자막을 얻지 못하면 음성 전사를 시도합니다. 직원이 승인한 문서 버전이 위키 검색 대상입니다.

## 데이터 흐름

~~~mermaid
flowchart TD
  A["휴대폰 촬영"] --> B["FastAPI · 환자와 세션 확인"]
  B --> C["사진 파일과 방문 메타데이터 저장"]
  C --> D["PC · 태블릿 사진 비교"]
  D --> E["경과 녹음"]
  E --> F["Whisper 전사"]
  F --> G{"접수 시 등록 모드"}
  G -->|자동| H["모의 진료차트"]
  G -->|수동| I["원문 확인 · 등록"]
  I --> H
~~~

| 설계 결정 | 구현과 확인 방법 |
| --- | --- |
| 촬영 대상을 기기 간에 일치시킨다 | 이름으로 후보를 찾고 식별번호로 환자를 선택합니다. 서버는 촬영 세션을 환자·방문일에 연결합니다. [촬영 API](server/app.py)와 [다른 클라이언트에서 저장 결과를 확인하는 테스트](server/tests/test_app.py)에서 볼 수 있습니다. |
| 검색 전에 문서 권한을 적용한다 | 역할·승인·보관 여부·현재 버전으로 후보를 제한합니다. 코사인 유사도 기준을 넘은 문단을 임베딩 점수 90%와 글자 쌍 일치 점수 10%로 정렬해 최대 세 건을 답변 생성에 사용합니다. [검색 코드](server/app.py) · [승인된 영상 대본 검색 테스트](server/tests/test_youtube.py) |
| 오래된 화면에서 다른 직원의 변경을 덮어쓰지 않게 한다 | 문서 변경 요청에 예상 버전을 포함합니다. 서버에서 현재 버전과 다르면 HTTP 409를 반환합니다. [문서 API](server/intranet.py) · [권한·충돌·복원 테스트](server/tests/test_intranet.py) |
| 화면을 열 때 필요한 데이터만 불러온다 | 같은 세션에서 동시에 들어온 동일 조회를 합치고 완료 후에는 다시 조회합니다. 문서 목록은 문서별 반복 질의 대신 관련 메타데이터를 모아 조회합니다. [클라이언트 테스트](server/tests/test_request_coalescing.cjs) · [문서 수 증가 시 쿼리 수 테스트](server/tests/test_intranet.py) |

**백엔드 구성.** FastAPI가 인증, 접근 이력, 파일 업로드와 등록 작업을 담당합니다. 기본 저장소는 SQLite이고 PostgreSQL 어댑터도 포함합니다. 별도 빈 PostgreSQL DB를 사용하려면 연결 문자열을 환경변수 `CLINICWORKSPACE_DATABASE_URL`에 설정하고 실행합니다. 이 로컬 실행 방식에서 사진 파일은 PC에 저장되고 DB에는 파일 참조와 방문 정보가 저장됩니다. [저장소](server/storage.py) · [PostgreSQL 어댑터](server/postgres_store.py)

**AI 기능의 범위.** 사진은 사용자가 나란히 비교합니다. AI는 녹음 전사, 문서 검색·답변 생성, 공개 자료 기반 블로그 초안 작성에 사용합니다. 인용을 확인할 수 없는 원고는 저장 단계에서 거절될 수 있습니다. 이 저장소의 자동 테스트는 권한·저장·근거 검증 동작을 확인하며, 음성 인식률이나 답변 정확도 벤치마크는 별도 과제입니다.

## 테스트

저장소 루트에서 다음 명령을 실행합니다. API 테스트는 임시 DB와 테스트용 AI를 사용합니다. HTTPS 관련 테스트에는 cryptography가 필요합니다.

~~~bash
python -m pip install -r server/requirements_https.txt -r deployment/vercel/requirements.txt
cd server
python -m unittest discover -s tests
cd ..
node --test server/tests/*.cjs
~~~

GitHub Actions 설정은 Python API 및 JavaScript 클라이언트 테스트를 실행합니다. 기본 실행 단위는 로컬 웹앱입니다. 클라우드용 게이트웨이와 빌드 절차는 [배포 안내](deployment/README.md)에 있습니다. 독립 C++ 릴스 편집기는 별도 프로젝트입니다.

## 공개 데모의 보안 경계

- 방문자별 PostgreSQL 스키마와 비공개 Blob 경로를 사용합니다. 서버가 발급한 무작위 HttpOnly 쿠키로 공간을 선택하고, 세션과 환자 자료는 해당 공간에서만 조회합니다.
- 운영 계정의 공개된 기본 비밀번호를 교체하고 기존 세션·촬영 연결을 무효화합니다. 새 직원 계정에는 각각 임의 비밀번호를 발급합니다.
- 로그인·쓰기·체험 공간 생성·AI 요청 한도는 DB 트랜잭션으로 공유합니다. 서버 인스턴스가 달라져도 동일한 제한이 적용됩니다.
- 반복 인증 실패는 5분 단위로 집계하고, 접근 이력은 최대 90일·50,000건을 유지합니다. 인증 만료 시 사진 자동 조회를 중단합니다.
- 원격 파일과 DB 저장 중 일부만 성공한 경우 삭제를 시도하고, 실패한 삭제는 영속 장부에 남겨 재시도합니다. 일일 정리 작업은 만료된 공간과 오래된 미참조 파일을 삭제합니다.
- 공개 HTML에는 nonce 기반 CSP를 적용합니다. HTTPS·HttpOnly·SameSite 쿠키와 API 권한·CSRF 검사를 함께 사용합니다.

[보안 회귀 테스트](server/tests/test_security.py)와 [배포 안내](deployment/README.md)를 참고하세요. 공유용 체험 연결은 해당 공간에 접근하는 비밀 링크이므로 본인 기기에만 전달해야 합니다. 이 프로젝트는 실환자용 운영 서비스 인증이나 외부 침투 테스트를 받은 제품은 아닙니다.
