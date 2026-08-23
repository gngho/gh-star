# 새 PC 에서 시작하기

노트북에서 쓰던 프로젝트를 다른 PC 로 옮길 때. **폴더를 복사하지 말고 GitHub 에서 클론한다.**

`.venv/`, `.env`, Playwright 브라우저는 Git 에 없다(있으면 안 된다). 복사해봐야
경로가 깨지므로 새 PC 에서 다시 만드는 편이 빠르고 확실하다.

## 0. 노트북에서 먼저 (5초)

```bash
git status          # 커밋 안 된 게 없는지
git push            # 원격과 동기화
```

여기서 `## main...origin/main` 만 보이면 됐다. 뒤에 `[ahead N]` 이 붙어 있으면
아직 안 올라간 커밋이 있다는 뜻이다.

## 1. PC 에 필요한 것

| 항목 | 확인 | 없으면 |
|---|---|---|
| Python 3.12 | `py -3.12 --version` | [python.org](https://www.python.org/downloads/) |
| Git | `git --version` | [git-scm.com](https://git-scm.com/) |
| Claude Code (로그인 상태) | `claude --version` | [설치 안내](https://code.claude.com/docs) 후 로그인 |

**Claude Code 로그인이 핵심이다.** `research`/`compose` 가 이 로그인으로 돌기
때문에, 로그인이 안 돼 있으면 그 두 단계가 실패한다. API 키는 필요 없다.

## 2. 클론과 설치

```bash
git clone https://github.com/gngho/gh-star.git
cd gh-star

py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -e .
.venv\Scripts\python.exe -m playwright install chromium
```

마지막 줄은 Chromium 을 내려받는다(약 150MB). 카드 렌더링에 쓰이며 한 번만 하면 된다.

## 3. `.env` 만들기

```bash
copy .env.example .env
```

`.env` 를 열어 `GITHUB_TOKEN` 한 줄만 채운다. 나머지는 비워둔다.

토큰은 [github.com/settings/tokens](https://github.com/settings/tokens) 에서
발급한다. 공개 저장소 조회만 하므로 권한은 최소로:
- classic → `public_repo`
- fine-grained → Public repositories 읽기

> ⚠️ `ANTHROPIC_API_KEY` 는 **비워둔다.** 채우면 구독 대신 API 과금으로 조용히
> 바뀐다. 비어 있는 게 정상 상태다.

## 4. 확인

```bash
chcp 65001                                   # 한글 깨짐 방지 (터미널 세션마다)
.venv\Scripts\python.exe -m agent status
```

이렇게 나오면 성공이다:

```
파이프라인 상태
  GITHUB_TOKEN : 설정됨
  스냅샷       : N일치 (2026-08-23 ~ ...)
  워치리스트   : 500개
```

`GITHUB_TOKEN : 없음` 이면 3번을 다시 본다. 축소 모드로도 돌지만 star velocity
정확도가 떨어진다.

## 5. 시험 삼아 한 번

```bash
.venv\Scripts\python.exe -m agent run --date 2026-08-23
```

이미 만들어둔 날짜로 돌려보면 환경이 제대로 잡혔는지 알 수 있다. 카드 10장이
다시 그려지면 끝이다.

---

## 매번 새 터미널에서

```bash
chcp 65001
cd gh-star
.venv\Scripts\python.exe -m agent run --open
```

`chcp 65001` 이 귀찮으면 PowerShell 프로필에 넣어두면 된다.
매번 `.venv\Scripts\python.exe` 를 치기 싫으면 `.venv\Scripts\activate` 로
가상환경을 켜고 그냥 `python` 을 써도 된다.

## 노트북과 PC 를 번갈아 쓴다면

`data/` 와 `posts/` 가 Git 에 들어 있으므로 **작업 전 `git pull`, 작업 후 `git push`**
만 지키면 된다. GitHub Actions 도 매일 `data/` 에 커밋하므로, pull 을 건너뛰면
충돌한다.
