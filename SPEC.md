# GitHub 핫 레포 → 인스타그램 카드뉴스 자동화 에이전트 기능명세서

- 문서 버전: v2.3
- 작성일: 2026-08-23
- 상태: M1·M2·M3 구현 완료, M4·M5 미착수
- 변경: v1.1 — 카드 디자인 원본을 피그마로 두는 `design-sync` 흐름 추가 (7.1, 7.2)
- 변경: v1.2 — 파이프라인 효율 재검토 반영 (워치리스트 스냅샷, GraphQL 배치, 지연 검증, 선행 로딩, 저장소 비대화 관리)
- 변경: v1.3 — M2 구현 반영 (output_format 스키마 강제, max_budget_usd 상한, 자동 보정/재생성 분리, 실측 비용)
- 변경: v1.4 — M3 렌더링 구현 반영 (코드 줄 쪼개짐 방지, eyebrow 중복 가드, 실측 기반 코드 줄 상한 조정)
- 변경: v1.5 — design-sync 구현 반영 (추출/생성 분리, Variables REST API 는 읽기도 Enterprise 전용이라는 정정, 색 하드코딩 금지)
- 변경: v1.6 — 카드 상단 이미지 영역 신설, 페이지 인디케이터 제거, 피그마 템플릿을 구조 명세로 전환
- 변경: v1.7 — illustrate 단계 추가 (GitHub OG·아바타만 사용, 카드별 음영 변주)
- 변경: v1.8 — CI 무인화 범위를 수집·선정까지로 확정 (9.0), 로컬 일괄 실행 명령 run 추가
- 변경: v1.9 — API 자동 발행 취소, 수동 발행으로 전환 (M5 폐기, Pages·토큰 갱신·워크플로 B/C 제거)
- 변경: v2.0 — 타겟 독자를 입문자로 변경 (톤 재정의, architecture 카드 제거·what_is_it 추가, 난이도 게이트 신설)
- 변경: v2.1 — 본문을 문장 단위로 줄바꿈, 분량 하한(85자·2~3문장) 도입
- 변경: v2.2 — 커버·마무리를 간결한 구성으로 복귀 (이미지 제외, 70자 상한, 출처·라이선스 자동 채움)
- 변경: v2.3 — 카드 상단 그래픽을 역할별 내용 기반으로 재설계 (아바타 반복 폐기)

---

## 1. 개요

### 1.1 한 줄 정의

GitHub에서 스타가 급증 중인 레포지토리를 매일 자동으로 발굴하고, 그중 1개를 심층 조사한 뒤, 인스타그램 캐러셀 카드뉴스 10장으로 제작해 발행하는 에이전트.

### 1.2 배경

최초 구상은 "블로그 자동 작성"이었으나 발행 대상 조사 결과 다음이 확인되었다.

| 플랫폼 | 글쓰기 자동화 가능 여부 |
|---|---|
| 티스토리 | 불가. Open API가 2024년 2월 완전 종료됨 |
| Velog | 공식 API 없음. 비공식 GraphQL + 브라우저 쿠키 의존 |
| 인스타그램 | **가능.** Meta 공식 Content Publishing API 유지 중 |

따라서 발행 계층의 안정성을 기준으로 인스타그램 카드뉴스로 방향을 확정했다.

### 1.3 목표

1. 사람의 개입을 **하루 1회, 명령 하나**로 축소한다. 수집·선정은 무인이고, 조사부터는 로컬에서 `python -m agent run` 한 번으로 끝난다.
2. 레포 1개를 **실제 코드와 이슈를 읽고** 심층 리뷰한다. README 요약 수준의 얕은 콘텐츠를 만들지 않는다.
3. 품질 저하 시 **조용히 잘못된 결과를 내보내지 않고 멈춘다.** 마지막 관문은 사람의 눈이다.

### 1.4 비목표 (v1 범위 밖)

- 릴스·스토리·단일 이미지 게시물 (캐러셀만 지원)
- 다국어 (한국어만)
- 댓글·DM 자동 응대
- 다중 계정 운영
- **API 자동 발행** — 비용 대비 이득이 없어 접었다 (8.1). 사람이 확인하고 올린다.
- 자동 팔로우/좋아요 등 인게이지먼트 조작 행위 — **영구히 범위 밖**

### 1.5 전제 환경

| 항목 | 값 |
|---|---|
| 언어 / 런타임 | Python 3.12 |
| 실행 환경 | 로컬(Windows 11) 및 GitHub Actions(ubuntu-latest) |
| 본문 생성 | Claude Agent SDK (`claude-agent-sdk`, Python) |
| 모델 | `claude-opus-5` |
| 저장소 | 신규 Git 저장소 (GitHub 원격 필요) |

Python Agent SDK는 Node.js를 요구하지 않으며 Claude Code CLI를 자동 설치한다. 로컬에 Node가 없어도 무방하다.

---

## 2. 시스템 아키텍처

### 2.1 파이프라인

6단계 단방향 파이프라인이며, 각 단계는 **독립 실행 가능한 CLI 서브커맨드**다.

```
collect  →  select  →  research  →  compose  →  render  →  publish
  수집        선정        심층조사      원고생성      카드렌더      발행
```

설계 원칙: **단계 간 인터페이스는 파일(JSON/JPEG)로만 연결한다.** 메모리나 전역 상태를 공유하지 않는다. 이렇게 하면 어느 단계가 실패해도 앞 단계의 산출물을 그대로 두고 해당 단계만 재실행할 수 있고, 원고 품질이 마음에 안 들 때 `compose`부터 다시 돌릴 수 있다.

**의도적으로 병렬화하지 않는다.** 하루 1건을 만드는 배치 작업이므로 전체 실행이 3분이든 15분이든 결과는 같다. 여기서 최적화할 대상은 **왕복 횟수와 토큰**이지 벽시계 시간이 아니다. 동시성은 복잡도와 디버깅 난이도만 올린다.

### 2.2 단계별 입출력 규약

| 단계 | 커맨드 | 입력 | 출력 |
|---|---|---|---|
| 수집 | `python -m agent collect` | GitHub API, Trending HTML, `data/watchlist.json`, `data/snapshots/*.json` | `data/watchlist.json`(갱신), `data/snapshots/{date}.json`, `data/candidates/{date}.json` |
| 선정 | `python -m agent select` | `data/candidates/{date}.json`, `data/published.json`, `data/blocklist.json` | `data/selection/{date}.json` |
| 조사 | `python -m agent research` | `data/selection/{date}.json` | `data/research/{date}-{slug}.json` |
| 원고 | `python -m agent compose` | `data/research/{date}-{slug}.json` | `posts/{date}-{slug}/content.json`, `caption.md` |
| 렌더 | `python -m agent render` | `posts/{date}-{slug}/content.json` | `posts/{date}-{slug}/cards/01.jpg` … `10.jpg` |
| 발행 | `python -m agent publish` | `posts/{date}-{slug}/` 전체 | `data/published.json` 갱신 |

- `{date}`: `YYYY-MM-DD` (KST 기준)
- `{slug}`: `{owner}__{repo}`를 소문자·안전문자로 정규화한 값 (예: `anthropics__claude-code`)
- 모든 단계는 `--date`와 `--dry-run` 옵션을 공통 지원한다.

이 6단계 외에 **`design-sync`** 서브커맨드가 있다. 피그마의 디자인을 코드 템플릿에 반영하는 명령으로, 일일 파이프라인에 포함되지 않고 디자인이 바뀔 때만 사람이 수동 실행한다(7.2).

### 2.3 디렉터리 구조

```
github-star_blog/
├── SPEC.md
├── config.yaml                  # 운영 설정 (2.4 참조)
├── pyproject.toml
├── agent/
│   ├── __main__.py              # CLI 진입점
│   ├── collect.py
│   ├── select.py
│   ├── research.py              # Agent SDK 호출
│   ├── compose.py
│   ├── render.py
│   ├── publish.py
│   ├── models.py                # Pydantic 스키마 전체
│   └── tools/                   # Agent에 노출할 인프로세스 MCP 툴
├── templates/
│   ├── card.html.j2             # Jinja2 카드 템플릿
│   └── tokens.css               # 피그마 토큰에서 생성 (7.2)
├── design/
│   ├── tokens.json              # 피그마 변수 추출본 (Git 커밋 — drift 감지용)
│   └── baseline/                # 역할별 기준 스냅샷 (시각 회귀 비교)
├── assets/fonts/                # 한글 폰트 번들
├── data/                        # 파이프라인 중간 산출물 (Git 추적)
├── posts/                       # 최종 산출물 (Git 추적, Pages로 배포)
└── .github/workflows/           # 워크플로 A/B/C
```

`data/`와 `posts/`를 Git으로 추적하는 이유: 스냅샷 이력이 곧 star velocity 계산의 원천이고, 카드 이미지가 곧 인스타그램에 넘길 공개 URL의 실체이기 때문이다.

**저장소 비대화 관리**: 카드 JPEG는 하루 약 3MB, 1년이면 1GB에 가까워진다. 다음으로 관리한다.

- CI 체크아웃은 `fetch-depth: 1` (얕은 클론). 이력이 커져도 실행 시간에 영향이 없다.
- 발행 후 90일이 지난 `posts/*/cards/`와 `images/`는 정리한다. 인스타그램에 이미 올라간 뒤라 원본이 필요 없고, 언제든 `render` 로 다시 만들 수 있다. `content.json`과 `caption.md`는 이력으로 남긴다.

### 2.4 설정 파일 (`config.yaml`)

```yaml
collect:
  search_min_stars: 200          # Search API 후보 하한
  search_pushed_within_days: 14
  trending_languages: ["", "python", "typescript", "rust", "go"]
  trending_since: daily

score:
  w_delta_1d: 0.45
  w_delta_7d: 0.25
  w_recency: 0.15
  w_topic_match: 0.15

select:
  min_stars: 500
  republish_block_days: 90
  topic_filter: ["ai", "llm", "agent", "machine-learning"]   # 확정: AI/LLM/에이전트 중심
  backup_count: 2

compose:
  card_count: 10
  max_title_chars: 24
  max_body_chars: 90
  max_hashtags: 30
  tone: "코딩·AI를 이제 시작한 사람에게 친구가 설명하듯. 전문용어는 괄호로 풀고, 과장은 금지"

render:
  width: 1080
  height: 1350                   # 4:5
  jpeg_quality: 90
  theme: dark
  visual_regression_threshold: 0.90   # 기준 스냅샷과의 유사도 하한

design:                          # design-sync 전용. 일일 파이프라인에서는 읽지 않음
  figma_file_key: ""
  figma_node_ids:
    cover: ""
    feature: ""
    code: ""
    outro: ""

publish:
  timezone: "Asia/Seoul"
```

---

## 3. 수집 명세 (`collect`)

### 3.1 star velocity를 직접 계산해야 하는 이유

GitHub API는 레포의 **일별 스타 증가량을 제공하지 않는다.** `/repos/{owner}/{repo}`는 현재 누적 스타만 준다. Stargazers API(`Accept: application/vnd.github.star+json`)는 개별 스타의 `starred_at` 타임스탬프를 주지만 페이지네이션이 **최대 40,000건**으로 제한되어, 스타가 많은 레포일수록 최근 구간을 조회할 수 없다. 매일 조회한다 해도 레포당 수백 요청이 필요해 현실적이지 않다.

따라서 **자체 스냅샷 축적**이 velocity의 정답 경로이며, 데이터가 쌓이기 전 첫날들을 위해 Trending 페이지를 부트스트랩 소스로 병용한다.

### 3.2 소스 A — GitHub Search API

- 엔드포인트: `GET /search/repositories`
- 쿼리 예: `stars:>200 pushed:>2026-08-09 sort:stars order:desc`
- `config.select.topic_filter`가 설정된 경우 `topic:ai` 등을 쿼리에 추가
- 인증: `GITHUB_TOKEN` (Actions 기본 토큰 사용)

**제약과 대응**

| 제약 | 대응 |
|---|---|
| Search API는 코어 API와 별개로 **인증 시 분당 30요청** 제한 | 요청 간 2초 간격, 재시도 시 지수 백오프 |
| 쿼리당 **최대 1,000건**까지만 페이지네이션 가능 | 언어별·스타 구간별로 쿼리를 쪼개 후보 풀 확보 |
| 결과에 velocity 정보 없음 | 스냅샷(소스 C)과 조인해서 산출 |

### 3.3 소스 B — GitHub Trending 스크래핑

- 대상: `https://github.com/trending?since=daily` 및 언어별 변형
- 추출 항목: `owner/repo`, 설명, 언어, 누적 스타, **"N stars today"**
- "N stars today"는 GitHub가 직접 계산한 당일 증가량이므로, 스냅샷이 없는 초기에도 즉시 velocity 신호로 쓸 수 있다.

**이것은 비공식 인터페이스다.** HTML 구조가 예고 없이 바뀔 수 있으므로 다음을 명세에 못박는다.

1. 파서는 **셀렉터 실패를 예외가 아닌 빈 결과로 처리**하고 경고 로그를 남긴다.
2. 소스 B가 0건이어도 파이프라인은 **소스 A + 소스 C만으로 정상 진행**한다. 절대 파이프라인을 중단시키지 않는다.
3. 요청은 하루 최대 6회(언어 수만큼)로 제한하고, `If-None-Match`(ETag) 캐시를 사용하며, 요청 간 3초 지연을 둔다.
4. `User-Agent`에 프로젝트 식별자와 연락 가능한 저장소 URL을 명시한다.

### 3.4 소스 C — 자체 스냅샷 DB

`data/snapshots/{date}.json`:

```json
{
  "captured_at": "2026-08-23T10:00:00+09:00",
  "repos": {
    "owner/repo": { "stars": 12034, "forks": 890, "open_issues": 45 }
  }
}
```

**스냅샷 대상은 "오늘의 후보 풀"이 아니라 `data/watchlist.json`이다.** 매일 바뀌는 검색 결과를 그대로 스냅샷하면, 어제 풀에 없던 레포는 Δ를 계산할 수 없어 `null` 처리되고 결국 **매일 꾸준히 상위권에 있는 큰 레포만 velocity를 갖게 된다.** 이는 "새로 뜨는 레포를 찾는다"는 목적과 정반대다.

- `watchlist.json` = 최근 30일간 후보 풀에 한 번이라도 등장한 레포의 합집합 (상한 500개, 30일 미등장 시 제거)
- 매일 이 워치리스트 **전체**의 스타 수를 기록한다. 오늘 검색에 안 잡힌 레포도 계속 추적되므로, 갑자기 튀어오를 때 Δ7d가 이미 준비되어 있다.
- 조회는 **GraphQL API로 100개씩 배치**한다. REST로 개별 조회하면 500개 = 500요청이지만 GraphQL은 5요청이면 끝난다.
- `Δ1d = stars(today) - stars(today-1)`, `Δ7d = stars(today) - stars(today-7)`
- 해당 날짜 스냅샷이 없으면 그 델타는 `null`로 두고 가중치를 재정규화한다. (누락된 날을 0으로 취급하면 "급증하지 않았다"는 잘못된 신호가 된다.)
- 보관 기간: 180일. 이후 자동 정리.

### 3.5 스코어링

```
score = w_delta_1d    · norm(Δ1d)
      + w_delta_7d    · norm(Δ7d)
      + w_recency     · recency_bonus
      + w_topic_match · topic_score
```

- `norm()`: 후보 풀 내 백분위 정규화(0~1). 절대값을 쓰면 초대형 레포가 항상 이긴다.
- `recency_bonus`: 최초 커밋이 180일 이내면 1.0, 이후 선형 감쇠 — 이미 유명한 레포보다 새로 뜨는 레포를 우대
- `topic_score`: `topic_filter`와의 일치도. 필터가 비어 있으면 0.5 고정
- 소스 B의 "stars today"가 있으면 `Δ1d` 자리에 우선 사용한다.

### 3.6 출력

`data/candidates/{date}.json` — 상위 50개를 점수 내림차순으로, 각 항목에 스코어 구성요소를 모두 포함(선정 근거 추적용).

---

## 4. 선정 명세 (`select`)

### 4.1 하드 필터 (하나라도 걸리면 탈락)

| 조건 | 이유 |
|---|---|
| 누적 스타 < `min_stars`(500) | 소개할 만큼 검증되지 않음 |
| `archived == true` 또는 `fork == true` | 죽었거나 원본이 아님 |
| README 부재 또는 500자 미만 | 심층 조사 불가 |
| 라이선스 없음 | 인용·소개 시 법적 불명확 |
| `data/published.json`에 최근 90일 내 발행 이력 | 중복 |
| `data/blocklist.json`에 등재 | 사람이 부적합 판정한 레포 |
| 기본 브랜치 최근 커밋 90일 초과 | 사실상 방치 |

**검증은 전수가 아니라 위에서부터 하나씩 한다.** 후보 50개에 README·라이선스 조회를 전부 돌리면 매일 50회 이상의 추가 API 호출이 낭비된다. 대신 이렇게 한다.

1. Search API 응답에 이미 들어 있는 필드(`archived`, `fork`, `license`, `pushed_at`, `stargazers_count`)로 **추가 호출 없이** 걸러낸다.
2. 남은 후보를 점수 순으로 정렬한다.
3. 1위부터 README를 조회해 검증하고, **통과하는 순간 멈춘다.**
4. primary 1개 + backup 2개가 확보되면 종료한다.

일반적으로 3~5회 호출로 끝난다. 이 순서는 비용 방어이기도 하다 — 부적합 레포를 `research`에 넘겨 40턴을 태운 뒤에야 실패를 알아채는 상황을 막는다.

### 4.2 소프트 제외 (카테고리 기반)

다음 유형은 "핫한 레포"이긴 하나 심층 리뷰 대상으로 부적합하므로 제외한다.

- awesome-list / 링크 모음 (`awesome-` 접두, README가 링크 목록 위주)
- 학습 자료·로드맵·인터뷰 대비 (`tutorial`, `roadmap`, `interview`, `course` 토픽)
- 특정 회사 채용·이벤트 홍보성 저장소
- NSFW·불법 콘텐츠

### 4.3 스팸성 급증 탐지

스타 급증이 조작일 가능성을 다음 휴리스틱으로 걸러낸다. 하나라도 해당하면 **경고 플래그**를 붙이고 후순위로 밀되, 자동 탈락시키지는 않는다(오탐 가능).

- 스타 대비 포크 비율이 비정상적으로 낮음 (stars/forks > 500)
- 스타 급증일에 커밋·릴리스·이슈 활동이 전무
- 컨트리뷰터 1명 + 스타 수천 + 생성 7일 이내

### 4.4 출력

`data/selection/{date}.json`:

```json
{
  "date": "2026-08-23",
  "primary": { "full_name": "owner/repo", "score": 0.87, "reason": "...", "flags": [] },
  "backups": [ { "full_name": "...", "score": 0.81 }, { "full_name": "...", "score": 0.79 } ]
}
```

예비 2개를 두는 이유: 조사 단계에서 primary가 부적합으로 판명(예: README가 사실상 비어 있음)될 때 파이프라인을 살리기 위함이다.

---

## 5. 심층 조사 명세 (`research`) — Claude Agent SDK

### 5.1 왜 Agent SDK인가

단일 API 호출로 README를 요약하면 "README를 다시 쓴 글"밖에 안 나온다. 이 단계의 목적은 에이전트가 **스스로 저장소를 탐색하며** 다음을 알아내는 것이다.

- 실제 진입점 코드가 어떻게 생겼는가
- 최근 이슈에서 사용자들이 무엇을 겪고 있는가
- 릴리스 노트상 최근 무엇이 바뀌어 지금 뜨는가

이는 다단계 탐색이 필요하므로 `query()`가 아닌 **`ClaudeSDKClient`(세션 유지)** 를 사용한다.

### 5.2 에이전트 구성

```python
options = ClaudeAgentOptions(
    model="claude-opus-5",
    system_prompt=RESEARCH_SYSTEM_PROMPT,
    mcp_servers={"gh": github_tools_server},
    allowed_tools=[
        "mcp__gh__get_readme",
        "mcp__gh__list_repo_tree",
        "mcp__gh__get_file",
        "mcp__gh__recent_issues",
        "mcp__gh__recent_releases",
        "mcp__gh__contributor_stats",
        "WebSearch",
    ],
    permission_mode="bypassPermissions",   # 무인 CI 실행. 툴이 화이트리스트로 제한되어 안전
    max_turns=40,
)
```

- `allowed_tools` 화이트리스트에 **`Bash`·`Write`·`Edit`를 포함하지 않는다.** 에이전트는 읽기만 한다. 파일 쓰기는 호출 측 Python 코드가 담당한다. `disallowed_tools` 로 한 번 더 못박는다.
- `max_turns=25`로 탐색 폭주를 막는다. 아래 선행 로딩 덕분에 40턴은 필요 없다 (실측 13턴).
- `max_budget_usd=3.0` 으로 비용 하드 상한을 건다.
- `output_format` 에 `ResearchPayload` 의 json_schema 를 넣어 결과 구조를 강제한다.
- `setting_sources=[]` — 사용자·프로젝트 설정을 상속하지 않는다. CI 와 로컬이 같게 돌아야 한다.

**선행 로딩 (starter kit)**: 에이전트를 빈손으로 시작시키지 않는다. 호출 전에 Python이 미리 가져온 다음을 **최초 프롬프트에 실어 보낸다.**

- README 전문
- 최상위 디렉터리 트리
- 최근 릴리스 3건
- 레포 메타데이터 (스타, Δ1d, 언어, 라이선스, 생성일)

에이전트가 "README 좀 줘 → 트리 좀 줘 → 릴리스 좀 줘"로 소비하던 5~8턴이 0턴이 된다. 같은 정보를 얻는 데 왕복이 줄어드는 만큼 비용도 줄어든다. 에이전트는 첫 턴부터 **"이 중 무엇을 더 파고들 것인가"** 라는 판단에 들어간다.
- `permission_mode="bypassPermissions"`는 위 화이트리스트 제한이 전제될 때만 성립한다. 툴 목록을 넓히려면 이 값을 함께 재검토해야 한다.

### 5.3 커스텀 MCP 툴

`create_sdk_mcp_server()`로 인프로세스 제공한다. 외부 프로세스가 없으므로 CI에서 추가 설치가 필요 없다.

| 툴 | 입력 | 하는 일 |
|---|---|---|
| `get_readme` | `repo` | README 원문 반환 (200KB 상한, 초과 시 잘라내고 그 사실을 명시) |
| `list_repo_tree` | `repo`, `path?`, `depth?` | 디렉터리 트리 (파일 수 300개 상한) |
| `get_file` | `repo`, `path` | 파일 내용 (100KB 상한, 바이너리 거부) |
| `recent_issues` | `repo`, `state?`, `limit?` | 최근 이슈 제목·본문 요약 (기본 20건) |
| `recent_releases` | `repo`, `limit?` | 최근 릴리스 노트 (기본 5건) |
| `contributor_stats` | `repo` | 컨트리뷰터 수, 상위 기여자, 커밋 빈도 |

모든 툴은 GitHub REST API를 `GITHUB_TOKEN`으로 호출하며, 레이트리밋 잔량이 100 미만이면 대기한다.

### 5.4 산출물 스키마 (`data/research/{date}-{slug}.json`)

```json
{
  "repo": "owner/repo",
  "researched_at": "2026-08-23T11:00:00+09:00",
  "one_liner": "한 문장 정의",
  "problem": { "text": "...", "evidence": ["README.md#L1-20"] },
  "why_now": { "text": "...", "evidence": ["releases/v2.0.0", "issues/1234"] },
  "key_features": [
    { "title": "...", "text": "...", "evidence": ["src/core.py"] }
  ],
  "architecture": { "text": "...", "evidence": ["src/"] },
  "quickstart": { "commands": ["pip install x", "x run"], "evidence": ["README.md"] },
  "differentiators": { "text": "...", "evidence": [] },
  "limitations": { "text": "...", "evidence": ["issues/998"] },
  "meta": { "license": "MIT", "language": "Python", "stars": 12034, "delta_1d": 820 }
}
```

### 5.5 환각 방지 규칙

**모든 서술 필드는 `evidence` 배열을 동반해야 한다.** `evidence`는 실제로 조회한 파일 경로, 이슈 번호, 릴리스 태그, 또는 URL이어야 한다.

- 시스템 프롬프트에 "근거를 찾지 못한 주장은 쓰지 말고 해당 필드를 비워라"를 명시한다.
- 검증기가 `evidence`가 빈 필드를 발견하면 **경고를 남기고 해당 카드를 원고 단계에서 제외**한다. 빈 근거를 그럴듯한 문장으로 채우는 것이 이 시스템의 가장 흔한 실패 모드이기 때문이다.
- `key_features`가 2개 미만이면 조사 실패로 간주하고 backup 레포로 넘어간다.

---

## 6. 원고 생성 명세 (`compose`)

### 6.1 카드 10장 구성 (고정)

**독자는 코딩·AI에 이제 막 발을 들인 사람이다.** 깃허브를 들어봤지만 클론해본 적은
없고, ChatGPT는 써봤지만 API나 에이전트는 모르는 수준. 한 장이라도 "무슨 말인지
모르겠네"가 되면 거기서 이탈한다.

| # | 역할 | 담기는 것 | 출처 필드 |
|---|---|---|---|
| 1 | 커버 | 레포명, **한 줄 훅(70자 이내)**, ⭐ 증가량. 이미지 없음 | `one_liner`, `meta` |
| 2 | **한마디로 뭐냐면** | 이게 뭔지 딱 한 문장. 비유 허용 | `one_liner` |
| 3 | 문제 정의 | "이런 적 있죠?" 공감으로 열기 | `problem` |
| 4 | 왜 지금 뜨나 | 급증 수치 + 계기, 감이 오게 | `why_now` |
| 5 | 이런 걸 해요 ① | | `key_features[0]` |
| 6 | 이런 걸 해요 ② | | `key_features[1]` |
| 7 | 이런 걸 해요 ③ | | `key_features[2]` |
| 8 | 직접 써보려면 | 설치·실행 커맨드 + 어디에 입력하는지 | `quickstart` |
| 9 | 나한테 맞을까 | 적합·부적합 케이스 | `differentiators`, `limitations` |
| 10 | 마무리 | **출처 → 라이선스 → 마무리 한마디.** 이미지 없음 | `meta` (자동) |

**`architecture` 카드는 뺐다.** 입문자에게 "어떻게 동작하나"를 90자로 설명하면
정확하거나 쉽거나 둘 중 하나를 반드시 포기하게 된다. 그 자리에 **"한마디로 뭐냐면"**
을 앞쪽에 넣었다 — 여기서 못 알아들으면 나머지 아홉 장을 안 읽기 때문이다.
조사한 아키텍처 내용은 버리지 않고 프롬프트 맥락으로 계속 넘긴다.

### 6.1.1 난이도 게이트

프롬프트로 "쉽게 써라"만 하면 **조사 자료의 파일명이 그대로 새어 나온다.** 실제로
첫 버전 캡션에 `curator.py 87KB`, `learning_graph`, `Mini Shai-Hulud 웜 사건`이
실렸다. 그래서 검증기가 잡는다:

| 잡는 것 | 예 |
|---|---|
| 파일명·모듈명 | `curator.py`, `pyproject.toml` |
| snake_case / camelCase 식별자 | `learning_graph`, `memoryManager` |
| 풀어 쓰지 않은 영문 약어 | `MCP`, `IPC` |

카드당 1개, 캡션 4개를 넘으면 위반으로 되돌려준다. 예외는 둘이다 —
`quickstart` 의 `code` 블록(명령어는 영문일 수밖에 없다)과 `outro`(레포 주소와
라이선스가 정보의 본체다). `AI`·`MIT`·`URL` 같은 자명한 약어는 허용 목록으로 뺐고,
**괄호로 뜻을 풀어 쓴 약어는 통과시킨다** — 그게 우리가 원하는 형태다.

조사(`research`)는 손대지 않았다. 깊게 파는 것과 쉽게 쓰는 것은 별개이고,
얕게 조사하면 쉬운 게 아니라 부실해진다. 번역은 `compose` 에서만 한다.

카드 상단 이미지는 `posts/{date}-{slug}/images/{index:02d}.{jpg|png}` 에서 읽는다.
`compose` 는 이미지를 다루지 않고 `render` 가 있으면 붙인다. 이미지 소스 확보는
아직 미결(15절)이다.

`key_features`가 2개뿐이면 카드 6을 생략하고 9장으로 발행한다. 캐러셀은 2~10장이면 유효하다.

### 6.2 분량 제약

| 요소 | 상한 | 근거 |
|---|---|---|
| 카드 제목 | 24자 | 1080px 폭에서 2줄 이내 |
| 카드 본문 | **85~150자** | 2~3문장. 하한이 없으면 한 문장으로 끝나 카드가 휑해진다 |
| 커버·마무리 본문 | **70자 이내** | 이 둘은 간결해야 한다. 하한을 적용하지 않는다 |
| 코드 블록(카드 8) | 3줄, 줄당 52자 | 27px 모노스페이스 실측. 42자는 과하게 좁아 모델이 URL 을 잘랐다 |
| 캡션 전체 | 2,200자 | 인스타그램 캡션 제한 |
| 해시태그 | 30개 | 인스타그램 제한 |

이 상한은 **생성 시점에 프롬프트로 지시하고, 검증 시점에 다시 강제한다.** 렌더링 단계에서 넘치는 것을 발견하면 이미 늦다.

**본문은 문장 단위로 줄을 나눠 렌더한다.** 한 줄에 두 문장이 걸치면 뒤 문장이
줄 끝에서 시작해 어정쩡하게 잘린다("…들어갔어요. 웹 검색은" 에서 줄바꿈). 문장마다
블록을 주면 긴 문장은 자기 블록 안에서 자연스럽게 감기고 다음 문장은 항상 새 줄에서
시작한다. 분리 시 **앞 글자가 숫자면 자르지 않는다** — `v0.20.4`, `23.4만` 의
소수점에서 끊기면 숫자가 갈라진다.

**code 의 각 줄은 그 자체로 완전한 명령이어야 한다.** 카드는 줄마다 `$` 프롬프트를
붙이므로, 길이 때문에 한 명령을 쪼개면 별개의 명령 두 개처럼 읽힌다 — 이는 미관이 아니라
내용 오류다. 실제 첫 렌더에서 `curl` 원라이너가 URL 중간에서 갈라져 나왔다. 검증기가
조각으로 보이는 줄(`.`, `|`, `&&`, `-`, 도메인으로 시작)을 잡아 재생성시킨다.

### 6.3 캡션 구성

```
{한 줄 훅}

{2~3줄 요약}

🔗 github.com/{owner}/{repo}
⭐ {현재 스타} (+{Δ1d} today)
📄 {라이선스}

#깃허브 #개발자 #오픈소스 #{language} ...
```

### 6.4 검증 및 재시도

스키마 자체는 `ClaudeAgentOptions.output_format` 의 `json_schema` 로 강제한다. 모든 객체에
`additionalProperties: false` 와 전체 `required` 를 붙여, 모델이 어려운 필드를 조용히
빠뜨리지 못하게 한다.

그 위의 제약 검사는 **성격에 따라 두 갈래로 나눈다.**

| 종류 | 예 | 처리 |
|---|---|---|
| **자동 보정** — 뜻이 바뀌지 않음 | 캡션 끝 해시태그 블록, `#` 접두, 중복 태그, 개수 초과 | 코드가 고치고 로그만 남긴다 |
| **재생성** — 고치면 뜻이 바뀜 | 글자수 초과, 카드 수·순서 불일치, 빈 제목 | 위반 내용을 구체적으로 되돌려주며 최대 2회 재생성, 이후 중단 |

글자수 초과를 잘라내면 문장이 끊긴다. 반면 해시태그 위치는 옮겨도 뜻이 같다. 후자까지
파이프라인 실패로 처리하면 재생성 비용만 쓰고 결과는 같다 — 실제로 초기 구현이 이 때문에
3회 재시도 후 실패했다.

캡션 본문의 `#` 을 일괄 금지하지 않는다. `C#`, `이슈 #42` 같은 정당한 쓰임이 있으므로,
**줄 끝 해시태그 블록만** 패턴으로 잡아 옮긴다.

---

## 7. 렌더링 명세 (`render`)

### 7.1 방식 — 피그마를 디자인 원본으로, 렌더링은 HTML/Playwright로

디자인은 **피그마에서 만들고**, 매일의 카드 생산은 **HTML/CSS + Playwright**가 담당한다. 피그마를 "찍어내는 기계"가 아니라 "금형을 만드는 곳"으로 쓴다.

**왜 피그마에서 직접 찍어내지 않는가**

피그마로 매일 카드를 자동 생성하려면 텍스트를 채워 넣고 이미지를 내보내는 쓰기 작업이 무인으로 가능해야 한다. 다음을 검토한 결과 v1 요건(매일 무인 실행)을 만족하는 경로가 없다.

| 경로 | 자동 채우기 | 무인 CI 실행 | 판정 |
|---|---|---|---|
| Figma REST API로 텍스트 주입 후 export | 불가 — REST API는 **노드 생성·수정을 지원하지 않음**(파일/노드 엔드포인트는 읽기 전용) | — | 불가 |
| Variables REST API에 문자열 변수 바인딩 | 가능 | 불가 — **Enterprise 플랜 전용** (`file_variables:write`). 현재 계정은 student 티어 | 불가 |
| Figma Plugin API로 인스턴스 복제·텍스트 주입 | 가능 | 불가 — 플러그인은 피그마 앱 안에서만 실행됨. 헤드리스 러너 없음 | 반자동 |
| Figma MCP로 에이전트가 캔버스에 작성 | 가능 | 불안정 — 대화형 인증 기반이라 cron/헤드리스 실행에서 보장되지 않음 | 초안 작업용 |
| **HTML/CSS + Playwright** | 가능 | 가능 | **채택** |

즉 "피그마 템플릿으로 매일 찍어내기"는 어느 경로로 가든 **사람이 피그마를 여는 단계**가 끼어든다. 이는 이 시스템의 핵심 목표(1.3)인 무인 실행과 정면으로 충돌한다.

**대신 얻는 것**

피그마를 디자인 원본으로 두면 자동화를 포기하지 않으면서도 원하던 이점은 그대로 가져온다.

- 카드 디자인을 **눈으로 보며 피그마에서 수정**하고, 코드는 토큰만 다시 뽑아 반영한다. 색·간격·타이포를 CSS에 하드코딩하고 매번 손으로 고치는 상황을 피한다.
- 피그마 프레임이 **시각적 정답지**가 되어, 렌더 결과와 비교하는 회귀 테스트의 기준이 생긴다.
- 카드 역할별(커버/기능/코드/마무리) 템플릿을 피그마 컴포넌트로 관리해 일관성을 유지한다.

### 7.2 디자인 동기화 (`design-sync`)

피그마 → 코드 반영은 **별도 서브커맨드**로 분리한다. 일일 파이프라인(2.1)에 포함되지 않으며, 디자인이 바뀔 때만 사람이 수동 실행한다.

```
python -m agent design-sync
```

**추출과 생성을 `design/tokens.json` 경계로 나눈다.**

```
피그마 ──[MCP 또는 REST]──▶ design/tokens.json ──[design-sync]──▶ templates/tokens.css
          (자격증명 필요, 플랜 제약)      (Git 커밋)          (순수 함수, 항상 동작)
```

| 단계 | 수단 | 산출물 | 자격증명 |
|---|---|---|---|
| 1. 토큰 추출 | Figma MCP 로 로컬 변수 덤프 | `design/tokens.json` | MCP 연결 |
| 2. CSS 변수 생성 | `design-sync` (네트워크 없음) | `templates/tokens.css` | 불필요 |
| 3. 레이아웃 참조 | Figma MCP `get_design_context` | 참조 코드 (사람이 `card.html.j2`에 반영) | MCP 연결 |
| 4. 기준 스냅샷 | Figma MCP `get_screenshot` | `design/baseline/{role}.png` | MCP 연결 |

**초안의 오류 정정**: 초안은 1단계를 "Variables REST API"로 적었으나, **Variables REST API는 쓰기(POST)뿐 아니라 읽기(GET)도 Enterprise 전용**이다. 현재 계정은 student 티어라 403이 난다. 실제로 값을 읽을 수 있었던 것은 MCP 경로다. MCP는 Claude 세션에 붙어 있어 헤드리스 CLI가 직접 호출할 수 없으므로, 추출 결과를 커밋된 JSON으로 떨어뜨리고 **생성 단계만 CLI로 만든다.** 이렇게 하면 2단계는 자격증명도 네트워크도 없이 결정적으로 돌아가 테스트가 쉽고, CI에서도 안전하다.

**피그마 파일 구조 규약**

- 카드 역할별 프레임을 1080×1350으로 만들고 프레임명을 `card/cover`, `card/feature`, `card/code`, `card/outro` 등으로 고정한다. 이 이름이 코드의 템플릿 키와 1:1 대응한다.
- 색·폰트크기·간격은 반드시 **피그마 변수(Variable)로 정의**한다. 변수로 잡히지 않은 값은 1단계에서 추출되지 않아 코드와 어긋난다.
- **템플릿(`card.html.j2`)에도 색을 하드코딩하지 않는다.** 파생색이 필요하면 `color-mix(in srgb, var(--accent) 14%, transparent)` 처럼 토큰에서 만든다. 실제로 배지 배경과 상단 광원이 하드코딩돼 있어, 피그마에서 accent를 바꿔도 그 둘만 옛 색으로 남았다. 테스트가 색 리터럴을 잡아낸다.
- **피그마에는 Pretendard가 없다.** 목업은 Noto Sans KR로, 렌더러는 번들한 Pretendard로 그린다. 따라서 시각 회귀는 픽셀 일치가 아니라 유사도(0.90) 기준이어야 한다 — 폰트가 다르므로 애초에 일치할 수 없다.
- 텍스트 레이어에는 최대 글자수를 가정한 더미 텍스트를 넣어, 6.2의 글자수 상한이 실제로 안 넘치는지 디자인 단계에서 확인한다.

**드리프트 방지**: `design/tokens.json`을 Git에 커밋한다. `design-sync` 실행 시 diff가 나면 디자인이 바뀐 것이므로 리뷰 대상이 된다. 3단계(레이아웃)는 자동 반영이 아니라 사람이 판단해 옮긴다 — 피그마의 절대 좌표 레이아웃을 그대로 코드에 밀어 넣으면 한글 줄바꿈에 대응하지 못하는 경직된 CSS가 나오기 때문이다.

**시각 회귀 테스트**: `render` 실행 후 결과 JPEG와 `design/baseline/{role}.png`를 구조적 유사도로 비교한다. 임계값을 넘게 벌어지면 PR에 경고를 남긴다. 픽셀 완전 일치는 목표가 아니다 — 피그마와 브라우저의 한글 줄바꿈·자간 처리는 원래 다르다.

### 7.3 캔버스 규격

| 항목 | 값 |
|---|---|
| 해상도 | 1080 × 1350 (4:5) |
| `deviceScaleFactor` | 1 |
| 최종 포맷 | **JPEG** (품질 90, 알파 제거) |

**전 장의 비율이 반드시 동일해야 한다.** 인스타그램 캐러셀은 첫 번째 이미지의 비율을 기준으로 나머지를 크롭하므로, 한 장이라도 비율이 다르면 잘려서 발행된다.

**JPEG 변환은 선택이 아니라 필수다.** Meta의 콘텐츠 게시 문서상 이미지 게시물은 JPEG만 지원한다. Playwright는 PNG로 캡처하므로 Pillow로 RGB 변환 후 JPEG로 저장한다.

**브라우저는 한 번만 띄운다.** Chromium 기동은 수 초가 걸리므로 카드마다 새로 띄우면 10배로 낭비된다. 브라우저와 페이지를 한 번 열어 `set_content` → 스크린샷을 10회 반복하고 마지막에 닫는다. CI에서는 Playwright 브라우저 바이너리를 캐시해 매 실행마다 내려받지 않게 한다.

### 7.4 폰트

- 한글 폰트(Pretendard 등 OFL 라이선스)를 `assets/fonts/`에 **번들**하고 CSS `@font-face`에서 로컬 파일로 참조한다.
- 이유: CI 컨테이너에는 한글 폰트가 없어 전부 두부(□)로 렌더링되며, 외부 CDN 참조는 네트워크 실패 시 조용히 폴백 폰트로 깨진다.
- 번들 폰트의 라이선스 파일을 함께 커밋한다.

### 7.5 디자인 원칙

- **레포의 README 스크린샷·로고를 가져다 쓰지 않는다.** 저작권이 레포 소유자에게 있고, 라이선스가 코드만 다루는 경우가 많아 이미지 재배포는 별개 문제다.
- 카드 상단 그래픽은 **역할마다 다르게 그린다**(`illustrate` 단계). 같은 이미지를 반복하면 캐러셀이 밋밋하고, 무엇보다 각 카드가 하는 말과 그림이 따로 논다.

| 역할 | 그래픽 | 출처 |
|---|---|---|
| what_is_it | GitHub OG 이미지 | `opengraph.githubassets.com` |
| why_now | STARS TODAY 수치 + 일별 막대 | 스냅샷 이력 (실데이터) |
| quickstart | 터미널 창 | 카드의 실제 설치 명령 |
| feature | 큰 번호 01/02/03 | 카드 순서 |
| problem | 큰 물음표 (라벨 없음) | — |
| fit | 이모지 세 단계 (👍 잘 맞아요 / 🤔 고민된다면 / 👎 아직 일러요) | 고정 문구 |
| cover, outro | 없음 | 간결해야 하는 카드 |

- **이미지 생성 모델을 쓰지 않는다.** 스타 증가량·설치 명령 같은 실제 데이터를 갖고 있고, 그걸 그리는 편이 생성 일러스트보다 정확하고 덜 상투적이다. 렌더링은 카드와 같은 Playwright 파이프라인을 재사용하므로 디자인 토큰이 자동으로 공유된다.
- **그래픽 라벨이 카드 eyebrow 와 같으면 안 된다.** 그래픽 바로 아래에 eyebrow 가 오므로 같은 문구가 위아래로 두 번 보인다. `problem` 에서 "왜 필요할까"가 그렇게 겹쳤다. `feature` 의 "핵심 기능"만 남기는데, 벌거벗은 번호에 의미를 주는 유일한 라벨이고 eyebrow("이런 걸 해요")와 다른 말이기 때문이다. 테스트가 이 규칙을 강제한다.
- **그래픽에 카드 제목을 넣지 않는다.** 넣어봤더니 같은 문장이 위아래로 두 번 나왔다. 그림은 리듬만 주고 내용은 카드가 말한다. 터미널 그래픽이 있으면 카드 본문의 코드 블록도 숨긴다 — 같은 명령이 두 번 보이기 때문이다.
- 이모지는 렌더러의 시스템 이모지 폰트에 의존한다. 로컬(Windows)에서는 Segoe UI Emoji 로 렌더되며, 카드 배지의 ⭐ 도 같은 경로를 쓴다. CI 로 옮길 일이 생기면 이모지 폰트를 함께 설치해야 두부가 되지 않는다.
- **없는 데이터를 그럴듯한 모양으로 채우지 않는다.** 스타 증가량을 모르면 수치 카드를 만들지 않고, 스냅샷이 3일치 미만이면 막대를 아예 그리지 않는다. 그건 그래프가 아니라 장식이다.
- 코드 스니펫은 설치 커맨드 수준의 짧은 인용으로 제한하고, 카드 10에 라이선스를 표기한다.
- **페이지 인디케이터는 쓰지 않는다.** 인스타그램이 캐러셀 위치를 자체 UI 로 이미 보여주므로 중복이고, 카드 하단 공간만 잡아먹는다.
- **카드 상단 470px 은 이미지 영역이다.** 커버는 예외로 이미지를 쓰지 않는다(제목과 배지가 이미 시선을 잡는다). 이미지 아래는 배경색 그라디언트로 녹여 본문과 잇는다.
- **커버와 마무리는 이미지를 쓰지 않는다.** 커버는 제목과 배지가 이미 시선을 잡고, 마무리는 출처·라이선스·한마디만 남는 카드다. 둘 다 여백이 정보다.
- **마무리 카드의 출처·라이선스는 조사 데이터에서 자동으로 채운다.** 주소는 한 글자만 틀려도 잘못된 정보이고, 그건 문장 생성에 맡길 일이 아니다. 모델은 마무리 한마디만 쓴다.
- **이미지가 없으면 그 영역을 아예 그리지 않는다.** 피그마 템플릿의 "여기에 이미지가 삽입됩니다" 점선 박스는 어디에 무엇이 들어가는지 알려주는 설명이지 렌더 결과물이 아니다. 자리표시자가 그대로 발행되면 안 된다.

### 7.6 오버플로 처리

렌더 후 각 텍스트 블록의 `scrollHeight`가 컨테이너를 넘는지 DOM에서 측정한다.

1. 넘치면 폰트 크기를 2단계까지 자동 축소한다.
2. 그래도 넘치면 **해당 카드를 실패로 표시하고 PR 본문에 경고를 남긴다.** 임의로 잘라내서 문장이 끊긴 채 발행되는 것을 막는다.

### 7.7 출력

```
posts/{date}-{slug}/
├── content.json
├── caption.md
├── cards/01.jpg … 10.jpg
└── meta.json          # 렌더 결과, 경고, 이미지 URL 예정 경로
```

---

## 8. 발행 명세 — 수동

**API 자동 발행을 하지 않는다.** 렌더된 카드와 캡션을 사람이 확인하고 직접 올린다.

### 8.1 왜 자동 발행을 접었는가

Meta Content Publishing API 는 기술적으로 동작하지만, 하루 1건을 올리자고 지불하는 비용이 과했다.

| 자동 발행이 요구하는 것 | 비용 |
|---|---|
| Meta 개발자 앱 등록 + 비즈니스 유형 | 설정 |
| `instagram_business_content_publish` 권한 | **앱 심사 2~4주 가능성** |
| 60일마다 토큰 갱신 | **한 번 놓치면 영구 만료 → 재발급** |
| 공개 이미지 URL (Meta 가 직접 fetch) | **GitHub Pages 배포 파이프라인 전체** |
| 컨테이너 생성 → 폴링 → 발행 시퀀스 | 구현 + 부분 실패 처리 |

얻는 것은 "하루 한 번 사진 10장을 올리는 손동작"뿐이다. 반면 잃는 것 중에는
**게시 직전 사람의 눈**도 있는데, 이 시스템의 최대 리스크가 "그럴듯하지만 틀린
기술 설명"(13.4)임을 생각하면 그 검토를 없애는 방향은 애초에 이상하다.

### 8.2 발행 절차

`agent run` 이 끝나면 아래가 준비되어 있다.

```
posts/{date}-{slug}/
├── cards/01.jpg … 10.jpg   ← 순서대로 인스타그램에 업로드
├── caption.md              ← 본문 + 해시태그. 그대로 복사
├── content.json            ← 고칠 게 있으면 여기를 수정하고 render 재실행
└── meta.json               ← 렌더 경고(오버플로 등)
```

1. `meta.json` 의 `warnings` 를 먼저 본다. 비어 있지 않으면 원고를 줄이고 다시 렌더한다.
2. 카드 10장을 눈으로 확인한다. 특히 **근거 없는 주장이 섞이지 않았는지** 본다.
3. 인스타그램 앱에서 캐러셀로 10장을 순서대로 올리고 `caption.md` 를 붙여넣는다.
4. 발행했으면 `data/published.json` 에 기록한다 — 이 파일이 재발행 차단(4.1)의 기준이다.

### 8.3 여전히 지켜야 하는 제약

수동으로 올려도 매체 제약은 그대로다. 렌더 단계가 이미 보장한다.

| 제약 | 값 |
|---|---|
| 캐러셀 장수 | 2 ~ 10 |
| 전 장 동일 비율 | 첫 장 기준으로 크롭되므로 필수 |
| 캡션 | 2,200자 이하 |
| 해시태그 | 30개 이하 |

### 8.4 발행 이력 (`data/published.json`)

```json
{
  "posts": [
    { "repo": "owner/repo", "date": "2026-08-23", "card_count": 10,
      "permalink": "https://www.instagram.com/p/...", "published_at": "..." }
  ]
}
```

`permalink` 는 올린 뒤 사람이 채운다. 비어 있어도 재발행 차단은 `repo` 와 `date` 로
동작한다.


## 9. 오케스트레이션 및 사람 검토 게이트

### 9.0 어디까지 무인으로 할 것인가

**수집·선정까지만 GitHub Actions 에서 돌린다.** 조사·원고·이미지·렌더는 로컬에서 사람이 실행한다.

`research`/`compose` 는 LLM 호출이라 CI 무인 실행에는 API 키가 필요하다. 무료 대안을 전수 조사한 결과:

| 후보 | 판정 | 사유 |
|---|---|---|
| GitHub Models | ❌ | **2026-07-30 완전 종료.** 기존 사용자 포함 엔드포인트 자체가 없다 |
| Groq 무료 | ❌ | TPM 8,000 이 **단일 요청**에 걸린다. 15K 짜리 compose 호출이 첫 시도부터 429 |
| Actions 내 로컬 추론 | ❌ | 100K 컨텍스트 KV 캐시만 ~12.8GB, 8B 가중치 포함 17.7GB > 러너 16GB. 한국어가 되려면 8B 이상인데 8B 이상이 안 올라간다 |
| Together AI / Cerebras / HuggingFace | ❌ | 무료 티어 폐지 · 30일 체험 · 크레딧 부족 |
| Gemini 무료 티어 | ⚠️ | 유일하게 동작하지만 **한국어 리더보드에 등장하지 않는다.** 무료 티어는 입력을 학습에 쓴다 |

한편 **로컬은 Claude Code 구독으로 이미 무과금**이다. 정리하면 CI 무인화의 대가는 "발행물의 한국어 품질을 근거 없는 모델에 맡기는 것"이고, 얻는 것은 "매일 명령 하나를 안 치는 것"이다. 후자가 더 싸므로 무인화를 포기했다.

이 판단은 뒤집을 수 있다. `ANTHROPIC_API_KEY` 를 시크릿에 넣으면 지금 코드 그대로 CI 에서 돌아간다(월 약 $33). 전환 작업은 없다.


### 9.1 워크플로 A — 일일 수집·선정 (cron)

| 항목 | 값 |
|---|---|
| 트리거 | `schedule` (KST 오전 10시) + `workflow_dispatch` |
| 실행 | `collect` → `select` → `research` → `compose` → `render` |
| 결과 | 브랜치 `draft/{date}-{slug}` 생성 → PR 자동 생성 |
| 시크릿 | `ANTHROPIC_API_KEY`, `GITHUB_TOKEN` |

PR 본문에는 다음을 포함한다.

- 선정 레포와 **선정 근거 점수 분해**
- 카드 10장 이미지 임베드 (검토자가 클릭 없이 스크롤로 확인)
- 캡션 전문
- 렌더 경고 목록(오버플로 등)
- 근거 없는 필드로 제외된 카드 목록

### 9.2 사람의 역할 (유일한 개입 지점)

1. PR에서 카드 이미지를 눈으로 확인한다.
2. 문구 수정이 필요하면 `content.json`을 고쳐 커밋한다 → 렌더 워크플로가 자동 재실행되어 이미지가 갱신된다.
3. 레포 자체가 부적합하면 PR을 닫는다 → 해당 레포는 `data/blocklist.json`에 추가된다.
4. 괜찮으면 머지한다. **머지가 곧 발행 승인이다.**

### 9.3 워크플로 B·C 는 두지 않는다

발행이 수동이므로 **머지 트리거 발행 워크플로(B)도, 토큰 갱신 워크플로(C)도 필요 없다.**
GitHub Pages 배포 역시 Meta 가 이미지를 fetch 해야 해서 필요했던 것이므로 함께 사라진다.

남는 자동화는 **워크플로 A(일일 수집·선정)** 하나뿐이고, `GITHUB_TOKEN` 만 쓰므로
시크릿 등록이 필요 없다.


## 10. 설정 및 시크릿

| 값 | 용도 | 어디에 | 획득 방법 |
|---|---|---|---|
| `GITHUB_TOKEN` | GitHub API 조회 | 로컬 `.env` | PAT (public repo 읽기). Actions 는 기본 제공분을 쓴다 |
| `ANTHROPIC_API_KEY` | **비워둔다** | — | 채우면 구독 대신 API 과금으로 전환된다 (9.0) |

**필요한 자격증명이 사실상 하나뿐이다.** 수동 발행으로 바꾸면서 `IG_USER_ID`,
`IG_ACCESS_TOKEN`, `SECRETS_ADMIN_PAT` 이 모두 사라졌다.

로컬 실행 시 `.env`로 주입하며 `.gitignore`에 반드시 포함한다.

---

## 11. 오류 처리 및 폴백

| 단계 | 실패 상황 | 동작 |
|---|---|---|
| collect | Trending HTML 파싱 실패 | 경고 로그 후 소스 A+C로 계속 진행 |
| collect | Search API 레이트리밋 | 지수 백오프 재시도 3회, 이후 전일 후보 재사용 |
| collect | Search API 완전 실패 | 스냅샷 기반 후보만으로 진행. 스냅샷도 없으면 당일 스킵 |
| select | 필터 통과 후보 0개 | **당일 스킵 + 알림.** 빈 PR을 만들지 않는다 |
| research | Anthropic API 오류 | 재시도 2회, 이후 backup 레포로 전환 |
| research | `key_features` 2개 미만 | 조사 실패 처리 → backup 레포로 전환 |
| research | `max_turns` 소진 | 그 시점까지의 결과로 검증 시도, 미달이면 backup 전환 |
| compose | 스키마·글자수 검증 실패 | 위반 내용 피드백하며 2회 재생성, 이후 중단 |
| render | Playwright 실행 실패 | 재시도 1회, 이후 중단 (부분 카드 발행 금지) |
| render | 텍스트 오버플로 | 폰트 2단계 축소 → 그래도 넘치면 카드 실패 표시 + PR 경고 |
| render | 기준 스냅샷과 유사도 미달 | 발행은 막지 않되 PR에 경고 + 차이 이미지 첨부 (디자인 drift 신호) |
| render | `templates/tokens.css` 없음 | 기본 팔레트로 렌더하고 "design-sync 미실행" 경고. 일일 실행이 피그마에 의존하지 않게 한다 |
| design-sync | Figma MCP 접근 실패 | 수동 실행 명령이므로 즉시 실패시키고 사람에게 알림 (일일 파이프라인에 영향 없음) |
| illustrate | GitHub OG·아바타 조회 실패 | 둘 다 실패할 때만 중단. `run` 은 경고만 남기고 이미지 없이 계속한다 |

**공통 원칙**: 어떤 실패도 "적당히 채운 결과"로 이어지지 않는다. 애매하면 멈추고 사람에게 알린다.

---

## 12. 비용 및 운영 지표

### 12.1 비용 추정 (1건 기준)

`research` 단계가 비용의 대부분을 차지한다. 40턴 상한, README + 파일 5~10개 + 이슈 20건 조회를 가정한다.

| 항목 | 추정치 | 단가 (`claude-opus-5`) | 비용 |
|---|---|---|---|
**2026-08-23 실측** (`NousResearch/hermes-agent`, 첫 실 운영 케이스):

| 단계 | 턴 | 실측 비용 |
|---|---|---|
| research | 13턴 (상한 25) | **$0.9056** |
| compose | 2턴 (1회 통과) | **$0.1984** |
| **합계** | | **$1.10 / 건** |

월 30건 기준 **약 $33**. 선행 로딩(5.2) 덕분에 research 가 상한의 절반만 쓰고 끝났다.

`ResultMessage.total_cost_usd` 가 실측 비용을 주므로 추정에 의존하지 않는다. 더불어
`ClaudeAgentOptions.max_budget_usd` 로 **하드 상한**을 건다 — research $3.0, compose $1.0.
상한을 넘으면 `error_max_budget_usd` 로 중단되므로 폭주해도 청구서가 터지지 않는다.

**비용 방어선 세 개**: ① `select`가 부적합 레포를 `research` 이전에 걸러낸다(4.1) ② 선행 로딩이 탐색 턴을 줄인다(5.2) ③ `research`와 `compose`를 분리해, 문구만 고칠 때 조사 비용을 다시 내지 않는다.

### 12.2 로깅 항목

각 실행마다 `data/logs/{date}.json`에 기록한다.

- 후보 풀 크기, 소스별 기여 건수
- 선정 레포와 점수 분해
- Agent 턴 수, 입력/출력 토큰, 호출한 툴 목록
- 단계별 소요 시간
- 렌더 경고 수
- 발행 성공 여부, permalink, 쿼터 잔량

### 12.3 품질 관찰 지표 (수동 검토)

- PR이 수정 없이 머지된 비율 (높을수록 원고 품질이 좋음)
- 근거 부족으로 제외된 카드 비율
- 캐러셀 완주율·저장수 (인스타그램 인사이트)

---

## 13. 준수 사항 및 리스크

### 13.1 인스타그램

- 사람이 직접 올리므로 API 정책 이슈가 없다. 자동화 도구가 계정을 조작하지 않는다.
- 자동 팔로우·좋아요·댓글·DM은 **영구히 범위 밖**이다. 정책 위반이며 계정 정지 사유다.
- AI가 생성한 콘텐츠임을 캡션에 명시할지는 운영 정책 사항으로 남긴다(15절).

### 13.2 GitHub

- Search API는 공식 인터페이스이며 레이트리밋을 준수한다.
- Trending 스크래핑은 비공식이다. 저빈도(일 6회 이하)·ETag 캐시·식별 가능한 User-Agent·즉시 폴백 원칙을 지킨다.

### 13.3 저작권

- 레포 로고·스크린샷·아이콘을 카드에 사용하지 않는다(7.4).
- 코드 인용은 설치 커맨드 수준으로 최소화하고 출처와 라이선스를 카드 10에 표기한다.
- 번들 폰트는 OFL 등 재배포 허용 라이선스만 사용하고 라이선스 파일을 동봉한다.

### 13.4 정확성

- 이 시스템의 가장 큰 리스크는 **그럴듯하지만 틀린 기술 설명**이다. 5.5의 근거 강제와 9.2의 사람 검토가 이에 대한 두 겹의 방어선이다.
- 오류가 발견된 게시물은 인스타그램에서 캡션 수정만 가능하고 이미지 교체는 불가하다. 잘못 나가면 삭제 후 재발행해야 하므로 **검토 게이트를 우회하는 옵션을 만들지 않는다.**

---

## 14. 마일스톤

| # | 범위 | 완료 기준 |
|---|---|---|
| **M1** | `collect` + `select` | 매일 스냅샷이 쌓이고 후보 상위 50개와 선정 결과가 나온다 |
| **M2** | `research` + `compose` | 실제 레포 1개에 대해 근거가 붙은 원고 JSON이 나온다 (렌더링 없이 품질 검증) |
| **M3** | 피그마 템플릿 + `design-sync` + `render` | 피그마에 역할별 카드 템플릿이 서고, 토큰이 CSS로 추출되며, 카드 10장 JPEG가 로컬에서 생성되고 오버플로·시각 회귀 검출이 동작한다 |
| **M4** | 워크플로 A | 매일 초안 PR이 자동 생성된다 |
| ~~M5~~ | ~~`publish` + 워크플로 B/C~~ | **취소.** 발행은 수동으로 한다 (8.1) |

**M1을 가장 먼저 하는 이유**: star velocity는 스냅샷이 최소 7일 쌓여야 Δ7d가 의미를 갖는다. 다른 단계를 개발하는 동안 데이터가 축적되도록 수집부터 띄운다.

---

## 15. 미결 사항 (구현 전 확정 필요)

M1~M3은 아래 항목 없이도 착수 가능하다. 대부분 M4 이후에 필요하다.

| # | 항목 | 필요 시점 | 비고 |
|---|---|---|---|
| ~~1~~ | ~~관심 토픽 필터~~ → **확정: AI/LLM/에이전트 중심** (`["ai", "llm", "agent", "machine-learning"]`) | — | 해결됨 |
| 2 | 카드 디자인 톤 (다크/라이트, 브랜드 컬러, 폰트 선택) | M3 | 피그마에서 시안을 몇 개 만들어 고르는 편이 빠름 |
| 2-1 | 피그마 파일 생성 및 `figma_file_key` / 역할별 `node_id` 확정 | M3 | 프레임 이름 규약(7.2)을 지켜야 `design-sync`가 동작함 |
| ~~3~~ | ~~GitHub 원격 저장소 + Pages~~ | — | **완료.** Pages 는 수동 발행 전환으로 불필요해짐 |
| 4 | 발행 시각 및 주 몇 회 운영할지 | M4 | cron 표현식에 반영 |
| ~~5~~ | ~~인스타그램 프로페셔널 계정·Meta 앱 등록~~ | — | **불필요.** 수동 발행으로 전환 (8.1) |
| ~~7~~ | ~~카드 상단 이미지 소스~~ | — | **확정.** GitHub OG·아바타 (`illustrate`) |
| 6 | 캡션에 AI 생성 사실을 명시할지 | 발행 전 | 운영 정책. 수동 발행이라 그때그때 정해도 된다 |

---

## 부록 A. 참고 자료

- [티스토리 Open API 서비스 종료 안내](https://tistory.github.io/document-tistory-apis/)
- [Meta — Publish Content using the Instagram Platform](https://developers.facebook.com/docs/instagram-platform/content-publishing/)
- [Claude Agent SDK (Python)](https://code.claude.com/docs/en/agent-sdk/python)
- [GitHub REST API — Search repositories](https://docs.github.com/en/rest/search/search)
- [Figma REST API — File endpoints (읽기 전용)](https://developers.figma.com/docs/rest-api/file-endpoints/)
- [Figma REST API — Variables (POST는 Enterprise 전용)](https://developers.figma.com/docs/rest-api/variables)
- [Figma Plugin API Reference (노드 수정은 플러그인에서만 가능)](https://developers.figma.com/docs/plugins/api/api-reference/)
