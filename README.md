# hermes-agent-project

> English: [README_en.md](README_en.md)

OpenAI Dots, Claude Tag처럼 **Slack 안에 상주하며 항상 켜져 있는 AI 동료**를 만드는 PoC다.
**[Hermes Agent](https://github.com/NousResearch/hermes-agent)**(Nous Research)와 자체 호스팅한 OpenAI 호환
vLLM 모델 위에 구축했다. 코드에서 에이전트 이름은 **Pilot**이다.

오픈소스 에이전트 프레임워크를 팀 채팅에 실제로 띄워 둘 수 있는 수준으로 만들려면 무엇이 필요한지 — 스스로 해도
되는 일과 반드시 물어봐야 하는 일, 채널별·사람별 메모리와 권한 범위, 사용량 제한 — 를 직접 이해하는 것이 목적이다.
코드와 `docs/`의 설계·리서치 문서 대부분은 **Claude Code**와 함께 작성했다.

## Pilot이 할 수 있는 것

| 영역 | 구현 내용 | 위치 |
|---|---|---|
| 실행 전 확인 | 모든 툴 호출에 Dots 방식 **Allow / Ask / Block** 규칙 적용, YAML 수정 시 즉시 반영 | `poc/plugins/pilot-rules`, `poc/config/pilot-rules.yaml` |
| Slack 읽기 | 채널 기록·스레드·고정 메시지·채널 목록 읽기 전용 툴, Claude Tag의 채널 간 접근 규칙 적용 | `poc/plugins/pilot-slack-read` |
| 채팅 매너 | 스레드별 음소거, 👎 피드백, 채널별 "자동 응답" 설정, `!status` / `!routines` / `!feedback` 명령, 잡담에는 `[SILENT]` | `poc/plugins/pilot-slack-controls`, `poc/config/SOUL.md` |
| 범위가 정해진 메모리 | 채널 / 그룹 DM / DM별 노트, 공개 채널에서만 워크스페이스 노트, 장소별 과거 세션 (Hermes 공용 메모리 대체) | `poc/plugins/pilot-memory` |
| 루틴 | 브리핑·리서치·채널 감시 같은 예약 작업이 만들어진 채널/DM 범위 안에서만 동작 | `poc/plugins/pilot-routines`, `poc/scripts/setup-routines.sh` |
| 사람별 격리 | 각 사용자의 1:1 DM은 전용 빈 Hermes 프로필로, 채널은 공용 서비스 프로필로 라우팅 | `poc/scripts/provision-personal-dms.py` |
| 실행 샌드박스 | 터미널·파일 툴은 게이트웨이 밖 브로커가 만드는 일회용·네트워크 제한 Docker 워커에서 실행, 외부 접속은 허용 목록제 | `poc/plugins/pilot-sandbox`, `poc/sandbox/` |
| 사용량 제한 | 서명된 요청 출처, 모델 앞단의 루프백 쿼터 프록시(조직/채널/DM별 월간 토큰 원자적 예약), 사용량 리포트 | `poc/usage/`, `poc/plugins/pilot-usage`, `poc/scripts/report-*.py` |
| 로케일 | 시간대는 소유자의 Slack 프로필 기준(없으면 호스트 → UTC), 답변 언어는 질문 언어를 따름 | `poc/scripts/detect-timezone.py` |

## 디렉터리 구조

```
docs/                 리서치·설계 노트와 PoC 진행 기록
  HANDOFF.md          현재 상태, 실행·테스트·디버깅 방법, 주의사항, 남은 과제 (여기서 시작)
  01-research.md      Claude / ChatGPT / Grok의 에이전트 구조, OpenClaw vs Hermes vs NemoClaw 비교
  02-poc-plan.md      단계별 계획과 진행 상황
  03…08-*.md          Dots/Tag 매핑, 격리·메모리, 루틴 범위, DM 프로필, 경계, 로케일
poc/
  docker-compose.yml  Hermes 컨테이너 1개 = 에이전트 1개
  config/             config.yaml 템플릿, SOUL.md(페르소나), 규칙, Slack 앱 매니페스트, 정책
  plugins/            위에 나열한 pilot-* Hermes 플러그인
  sandbox/ usage/     실행 브로커·외부 접속 제어, 쿼터 프록시
  scripts/            초기화, 스모크·행동 테스트, 프로비저닝, 리포트, 경계 런처
  tests/              단위 테스트 (플러그인 폴더 밖에 둔다 — Hermes가 그 안의 .py를 모두 import하기 때문)
```

## 시작하기

### 준비물

- Docker(Windows는 Linux 컨테이너 모드의 Docker Desktop)와 Python 3
- 툴 호출을 지원하는 OpenAI 호환 모델 엔드포인트(vLLM + Qwen으로 테스트, `poc/config/config.yaml`에서 설정)
- Socket Mode Slack 앱 — `poc/config/slack-manifest.json`으로 생성

### 실행

```bash
cd poc
python3 -m pip install -r requirements-host.txt
python3 scripts/init.py   # data/와 .env 생성 (API 키 생성, 시간대 감지)
# .env에 SLACK_BOT_TOKEN, SLACK_APP_TOKEN, SLACK_ALLOWED_USERS 등 입력
docker compose up -d
./scripts/smoke-test.sh
```

compose 플러그인이 없을 때:

```bash
docker run -d --name pilot-agent --restart unless-stopped \
  -v "$PWD/data:/opt/data" -p 127.0.0.1:8642:8642 -p 127.0.0.1:9119:9119 --env-file .env \
  -e API_SERVER_ENABLED=true -e API_SERVER_HOST=0.0.0.0 -e HERMES_DASHBOARD=1 \
  nousresearch/hermes-agent:v2026.9.24 gateway run
```

- OpenAI 호환 API: `http://127.0.0.1:8642/v1`
- Hermes 대시보드(세션, 로그, 예약 작업, 사용량): `http://127.0.0.1:9119` — 로그인 정보는 `.env`의
  `HERMES_DASHBOARD_BASIC_AUTH_*`
- 규칙 원본은 `poc/config/pilot-rules.yaml`, 실제 적용본은 `poc/data/pilot-rules.yaml`(수정 시 즉시 반영)
- `poc/data/`(실행 상태)와 `poc/.env`(시크릿)는 gitignore 대상

### 테스트

```bash
# 실행 중인 컨테이너 안에서 단위 테스트
docker exec pilot-agent /opt/hermes/.venv/bin/python /opt/data/tests/pilot-rules/test_rules.py
docker exec pilot-agent /opt/hermes/.venv/bin/python /opt/data/tests/pilot-slack-read/test_slack_read.py
docker exec pilot-agent /opt/hermes/.venv/bin/python /opt/data/tests/pilot-slack-controls/test_controls.py
docker exec pilot-agent /opt/hermes/.venv/bin/python /opt/data/tests/pilot-memory/test_scoped_memory.py

# Hermes 설치 없이 호스트에서 바로 도는 테스트
python3 poc/tests/test_quota.py
python3 poc/tests/test_model_proxy.py

# API를 통한 종단 간 행동 테스트 (모델을 바꾼 뒤에는 꼭 실행)
cd poc && ./scripts/behaviour-tests.sh        # 7개 전부, 약 2~3분
```

행동 테스트는 에이전트가 공개 채널을 이름으로 읽는지, DM 내용을 절대 유출하지 않는지, 무인 실행 중 파괴적 명령이
승인 게이트에 막히는지, 잡담에는 침묵하는지, 짧고 정확하게 답하는지, 질문한 언어(한국어/영어)로 답하는지를 확인한다.

## 참고

- 이 저장소의 Slack ID, IP(`203.0.113.x` 문서용 대역), 이름은 모두 예시 값이다.
- PoC 단계라 개인 프로필들이 컨테이너 하나를 공유한다. 상태는 분리되지만 강한 보안 경계는 아니다 — 무엇이 강제되고
  무엇이 아직 아닌지는 `docs/07-phase2-boundaries.md`를 참고한다.
