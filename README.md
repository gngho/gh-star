# GitHub 핫 레포 → 인스타그램 카드뉴스 에이전트

스타가 급증 중인 GitHub 레포를 매일 발굴하고, 1개를 심층 리뷰해 인스타그램 캐러셀 카드뉴스로 만드는 에이전트.

전체 설계는 [SPEC.md](SPEC.md) 참조.

## 현재 상태

**M1(수집·선정) · M2(조사·원고) · M3(디자인·렌더) 구현 완료.** 발행은 수동이라 M5 는 취소됐다.

| 마일스톤 | 단계 | 상태 |
|---|---|---|
| M1 | `collect`, `select` | ✅ 동작 |
| M2 | `research`, `compose` | ✅ 동작 (실측 $1.10/건) |
| M3 | `render` | ✅ 동작 (1080×1350 JPEG) |
| M3 | `design-sync` | ✅ 동작 (피그마 토큰 → CSS) |
| M3 | `illustrate` | ✅ 동작 (역할별 내용 기반 그래픽) |
| M4 | GitHub Actions 일일 수집·선정 | ✅ 동작 (조사부터는 로컬, SPEC 9.0) |
| ~~M5~~ | ~~`publish`, 토큰 갱신~~ | **취소** — 발행은 수동 (SPEC 8.1) |

## 설치

새 PC 에서 처음 시작한다면 **[SETUP.md](SETUP.md)** 를 따라가면 된다.

```bash
py -0p                      # 설치된 파이썬 확인 (3.12 이상이면 된다)
py -3.14 -m venv .venv      # 위에서 본 버전으로 바꿔서
.venv\Scripts\python.exe -m pip install -e .
.venv\Scripts\python.exe -m playwright install chromium
copy .env.example .env      # GITHUB_TOKEN 만 채운다
```

`GITHUB_TOKEN` 은 없어도 동작하지만 **축소 모드**가 된다. GraphQL 배치 조회를
쓸 수 없어 워치리스트 전체가 아니라 당일 검색 결과만 스냅샷되므로,
star velocity 정확도가 떨어진다. 토큰은 `public_repo` 읽기 권한이면 충분하다.

## 사용

```bash
python -m agent run --open  # 조사→원고→그래픽→렌더를 한 번에 (매일 쓰는 명령)
python -m agent mark-published   # 올린 뒤 발행 이력에 기록 + 커밋·푸시

python -m agent status      # 스냅샷 축적 현황
python -m agent collect     # 후보 수집 + 점수화
python -m agent select      # 심층 리뷰 대상 1개 + 예비 2개 선정
python -m agent research    # 에이전트가 저장소를 직접 읽고 근거 수집
python -m agent compose     # 조사 결과로 카드 10장 원고 생성
python -m agent illustrate  # 역할별 내용 기반 그래픽 생성
python -m agent render      # 원고를 1080×1350 JPEG 카드로 렌더
python -m agent design-sync # 피그마 토큰으로 카드 CSS 재생성 (수동, 일일 실행 아님)
python -m agent avatar      # 계정 프로필 사진 후보 생성 (수동, 일일 실행 아님)
```

### 올릴 때

`caption.md` 는 두 부분이다. `====` 구분선 **위까지만** 인스타 캡션에 붙여넣는다.
구분선 아래는 **추천 오디오 3건**이고, 발행할 때 보는 메모다 — 같이 붙이면
캡션에 그대로 실린다. `agent run` 이 끝날 때 터미널에도 같이 찍어준다.

오디오는 곡명이 아니라 **앱 검색창에 넣을 검색어**로 준다. 인스타 오디오 목록은
지역·계정 유형에 따라 다르고(비즈니스 계정은 상업용 음원이 많이 막힌다) 에이전트가
조회할 수 없어서, 없는 곡을 지목하지 않기 위한 선택이다. 고르는 건 사람이 한다.

`render` 와 `illustrate` 는 Playwright/Chromium 이 필요하다:
`python -m playwright install chromium`

`research` 와 `compose` 는 Claude 인증이 필요하다. 로컬에서 Claude Code 로그인으로
돌아가며 **API 키는 필요 없다** — 자세한 이유는 SPEC.md 9.0.

공통 옵션: `--date YYYY-MM-DD` (기준일), `--dry-run` (파일 미기록), `-v` (디버그 로그).

Windows 콘솔에서 한글이 깨지면 `chcp 65001` 을 먼저 실행한다.

## 매일 실행해야 하는 이유

`Δ1d` 는 스냅샷 2일치, `Δ7d` 는 7일치가 쌓여야 계산된다. 그전까지는 GitHub
Trending 의 "stars today" 로 `Δ1d` 를 대신하며, 이 데이터가 없는 후보는
점수 산정 시 **중앙값(0.5)으로 간주**된다 — 없는 데이터가 만점으로도, 0점으로도
취급되지 않게 하기 위해서다. `conf` 컬럼이 그 후보의 점수 중 실측 비율을 보여준다.

즉 **오늘부터 매일 `collect` 를 돌려야 일주일 뒤 제대로 된 velocity 가 나온다.**

## 테스트

```bash
python -m pytest tests -q
```

## 구조

```
agent/
├── __main__.py     CLI
├── config.py       config.yaml + .env 로드
├── paths.py        파일 경로 규약 (SPEC 2.2)
├── models.py       단계 간 인터페이스 스키마
├── github_api.py   REST + GraphQL (레이트리밋 대응)
├── trending.py     Trending 스크래핑 (실패해도 파이프라인 유지)
├── scoring.py      백분위 정규화 + 가중 합산
├── collect.py      소스 A/B/C 통합 → 후보 점수화
├── select.py       2단계 필터 (무호출 → README 검증)
├── schema.py       Pydantic → 엄격한 json_schema 변환
├── llm.py          Agent SDK 래퍼 (구조적 출력 + 비용 상한)
├── research.py     선행 로딩 + 근거 강제 조사
├── compose.py      카드 원고 생성 (자동 보정 + 재생성)
├── render.py       Jinja2 → Playwright → JPEG (오버플로 자동 축소)
├── imagery.py      역할별 카드 상단 그래픽
├── avatar.py       계정 프로필 사진 (Gemini 이미지 모델)
├── design_sync.py  피그마 토큰 → CSS (순수 함수, 네트워크 없음)
└── tools/          에이전트에 노출하는 읽기 전용 GitHub 툴 6종

templates/
├── card.html.j2    카드 레이아웃 (role 별 분기)
├── imagery.html.j2 상단 그래픽 (수치·터미널·번호·이모지)
└── tokens.css      디자인 토큰 — design-sync 가 덮어쓴다
assets/fonts/       Pretendard (OFL, 번들 — CI 에 한글 폰트가 없다)
assets/profile/     계정 프로필 사진 후보 (avatar 가 생성)
```

## 프로필 사진

계정 아이콘은 Gemini 이미지 모델(Nano Banana)로 만든다.

```bash
python -m agent avatar --dry-run   # 호출 없이 프롬프트만 확인
python -m agent avatar             # 컨셉 3종을 1080×1080 JPEG 로 생성
python -m agent avatar --pro       # 더 비싸고 더 정확한 모델
```

`GEMINI_API_KEY` 가 필요하다 (<https://aistudio.google.com/apikey>). **이 키는
프로필 사진에만 쓰인다** — 일일 파이프라인은 키가 없어도 전부 돈다.

### 무료 등급이면 API 로는 안 나온다

Gemini 이미지 모델은 **무료 등급 쿼터가 `limit: 0`** 이라, 결제를 연결하지 않으면
첫 호출부터 429 가 난다. 기다려도 풀리지 않는다 (`agent avatar` 가 이 429 를
진짜 레이트리밋과 구분해 안내한다). 결제 없이 만들려면 웹 경로를 쓴다:

```bash
python -m agent avatar --prompts            # assets/profile/prompts.md 생성
#   → AI Studio(https://aistudio.google.com) 에 붙여넣고 1:1 로 생성, 내려받기
python -m agent avatar --import 받은파일.png  # 1080×1080 정사각 JPEG 로 다듬기
```

`--import` 는 파일명을 유지하므로 컨셉 키(`star-surge.png` 등)로 저장해두면
나중에 비교하기 쉽다.

색은 카드와 같은 `design/tokens.json` 에서 읽는다. 피그마에서 브랜드 색을 바꿨다면
프로필도 다시 뽑아야 피드에서 어긋나지 않는다.

프로필 사진은 피드에서 **지름 40px 원**으로 잘린다. 그래서 프롬프트가 글자·얼굴·
가는 선·질감을 전부 금지하고 여백을 60% 확보하도록 강제한다. 나온 결과가
마음에 안 들면 `agent/avatar.py` 의 `CONCEPTS` 에서 `subject` 한 줄만 고치면 된다 —
나머지 제약은 컨셉과 무관하게 공유된다.

## 디자인

카드 디자인의 원본은 피그마다:
<https://www.figma.com/design/8aUoxhjjQW5Gklo7XakrPS>

| 페이지 | 내용 |
|---|---|
| **Style Guide** | 색 토큰 11종(브랜드·서피스·텍스트), 타이포 스케일 6단, 여백, 카드 컴포넌트(배지·코드블록·각주) |
| **Card Templates** | 역할별 카드 8종을 캐러셀 순서대로. **실제 문구가 아니라 "여기에 무엇이 들어가는지"** 를 대괄호 `[ ]` 로 적어둔 구조 명세다 |

카드 상단 470px 은 그래픽 영역이다. **커버와 마무리는 예외** — 둘은 간결해야
하는 카드라 여백이 정보다. 그래픽은 `illustrate` 가 역할에 맞춰 만든다
(수치·터미널·번호·이모지). 렌더 시 `posts/{date}-{slug}/images/{번호}.jpg` 를
찾아 붙이고, **없으면 그 영역을 그리지 않는다** — 피그마의 "여기에 이미지가
삽입됩니다" 점선 박스는 설명이지 결과물이 아니기 때문이다.


색·타이포·여백은 전부 **피그마 변수**로 정의돼 있다. 변수로 잡히지 않은 값은
추출되지 않아 코드와 어긋나므로, 새 값을 쓸 땐 반드시 변수부터 만든다.

```
피그마 ──[MCP 로 변수 덤프]──▶ design/tokens.json ──[design-sync]──▶ templates/tokens.css
```

색을 바꾸려면 피그마에서 변수를 고치고, 토큰을 다시 추출해 `design/tokens.json`
에 반영한 뒤 `design-sync` + `render` 를 돌린다.

**추출과 생성을 나눈 이유**: 피그마 Variables REST API 는 읽기도 Enterprise
전용이라 student 플랜에서는 403 이 난다. 값이 읽히는 건 MCP 경로뿐인데 MCP 는
Claude 세션에 붙어 있어 헤드리스 CLI 가 못 부른다. 그래서 추출 결과를 커밋된
JSON 으로 떨어뜨리고, 생성만 CLI 로 만들었다 — 이쪽은 네트워크도 자격증명도
없이 결정적으로 돈다. `tokens.json` 의 diff 가 곧 "디자인이 바뀌었다"는 신호다.

색은 템플릿에 하드코딩하지 않는다. 파생색은 `color-mix()` 로 토큰에서 만든다 —
테스트가 색 리터럴을 잡아낸다.
