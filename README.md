# GitHub 핫 레포 → 인스타그램 카드뉴스 에이전트

스타가 급증 중인 GitHub 레포를 매일 발굴하고, 1개를 심층 리뷰해 인스타그램 캐러셀 카드뉴스로 만드는 에이전트.

전체 설계는 [SPEC.md](SPEC.md) 참조.

## 현재 상태

**M1 (수집 + 선정) 구현 완료.** 나머지는 미구현.

| 마일스톤 | 단계 | 상태 |
|---|---|---|
| M1 | `collect`, `select` | ✅ 동작 |
| M2 | `research`, `compose` | 미착수 |
| M3 | `design-sync`, `render` | 미착수 |
| M4 | GitHub Actions 초안 PR | 미착수 |
| M5 | `publish`, 토큰 갱신 | 미착수 |

## 설치

```bash
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -e .
copy .env.example .env      # GITHUB_TOKEN 을 채운다
```

`GITHUB_TOKEN` 은 없어도 동작하지만 **축소 모드**가 된다. GraphQL 배치 조회를
쓸 수 없어 워치리스트 전체가 아니라 당일 검색 결과만 스냅샷되므로,
star velocity 정확도가 떨어진다. 토큰은 `public_repo` 읽기 권한이면 충분하다.

## 사용

```bash
python -m agent status      # 스냅샷 축적 현황
python -m agent collect     # 후보 수집 + 점수화
python -m agent select      # 심층 리뷰 대상 1개 + 예비 2개 선정
```

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
└── select.py       2단계 필터 (무호출 → README 검증)
```
