"""카드 원고 생성 단계. SPEC.md 6.

렌더링 단계에서 글자수 초과를 발견하면 이미 늦다. 생성 시점에 프롬프트로 지시하고
검증 시점에 다시 강제한다. 위반하면 무엇을 어겼는지 구체적으로 돌려주며 재생성한다.

research 와 분리된 이유는 비용이다. 문구만 고칠 때 조사 비용을 다시 내지 않는다.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone

from . import paths
from .config import Config
from .llm import AgentRunError, run_structured
from .models import (
    AudioPick,
    CardDeckFile,
    CardDeckPayload,
    ResearchFile,
)

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
CAPTION_LIMIT = 2200

# 오디오 추천. 셋이면 고를 여지가 생기고, 그 이상은 캡션 파일이 지저분해진다.
AUDIO_PICKS = 3
AUDIO_TERM_LIMIT = 24
AUDIO_MOOD_LIMIT = 30
AUDIO_WHY_LIMIT = 70

# 카드 한 장에 허용할 전문용어 개수. 0 은 비현실적이라(고유명사·언어명이 걸린다)
# 소량은 통과시키되 나열 수준이면 잡는다.
MAX_JARGON_PER_CARD = 1
MAX_JARGON_IN_CAPTION = 4

# 커버와 마무리는 간결하게 간다. 커버는 훅이라 짧아야 훅이고,
# 마무리는 주소·라이선스를 렌더가 데이터에서 자동으로 채우므로
# 모델이 쓸 것은 마무리 한마디뿐이다.
SHORT_ROLES = {"cover", "outro"}
SHORT_BODY_MAX = 70

SYSTEM_PROMPT = """\
너는 인스타그램 카드뉴스의 원고를 쓴다.

## 독자가 누구인지 잊지 마라

**코딩과 AI에 이제 막 발을 들인 사람**이다. 구체적으로:

- 깃허브가 뭔지는 들어봤지만 레포를 클론해본 적은 없다
- 파이썬을 조금 배웠거나, 아직 안 배웠다
- ChatGPT는 써봤지만 API나 에이전트는 모른다
- "터미널"이라는 말에 아직 긴장한다

이 사람이 **끝까지 읽고 "오 이거 재밌겠다"고 느끼게** 만드는 게 목표다.
한 장이라도 "무슨 말인지 모르겠네"가 되면 거기서 이탈한다.

## 쉽게 쓰는 규칙

1. **전문용어를 쓰면 그 자리에서 괄호로 풀어라.**
   나쁨: "CLI 에이전트다"
   좋음: "터미널(검은 화면에 글자로 명령하는 창)에서 돌아가는 AI 비서다"

2. **파일명·클래스명·함수명을 카드에 쓰지 마라.**
   조사 자료에는 `curator.py`, `learning_graph` 같은 게 잔뜩 있다. 그건 근거지
   독자에게 보여줄 내용이 아니다. **무엇을 하는지**로 바꿔 써라.
   나쁨: "learning_graph.py가 SKILL.md를 읽어 노드를 만든다"
   좋음: "한 번 알려준 요령을 파일로 적어두고 다음에 다시 꺼내 쓴다"

3. **비유를 최소 한 장에는 넣어라.** 익숙한 것에 빗대면 바로 이해된다.

4. **숫자는 감이 오게 써라.**
   나쁨: "스타 234,523개"
   좋음: "별 23만 개 — 웬만한 유명 앱보다 많이 받았다"

5. **영어 약어는 처음 나올 때 풀어라.** MIT 라이선스, API, MCP 전부 해당된다.

6. **말투는 친구에게 설명하듯.** "~한다" 보다 "~해요/~합니다" 가 낫고,
   질문을 던져도 좋다. 다만 호들갑은 떨지 마라.

7. **본문은 2~3문장으로 채워라.** 한 문장만 쓰면 카드가 휑하고 설명도 부족하다.
   보통 "무엇인지" 한 문장, "그래서 뭐가 좋은지/왜 그런지" 한두 문장이면 된다.
   다만 채우려고 같은 말을 반복하거나 없는 사실을 만들지는 마라.

## 쉽게 쓰되 틀리게 쓰지 마라

이게 제일 중요하다. 쉽게 만들려고 **사실을 바꾸거나 지어내면 안 된다.**
조사 자료에 없는 내용을 추가하지 마라. 어떤 내용이 도저히 쉽게 설명이 안 되면
그 카드는 짧게 쓰거나 다른 이야기를 해라. 정확성이 먼저다.

## 카드 구성

각 카드는 role 로 역할이 정해져 있다. 주어진 role 순서를 그대로 지켜라.

- cover: 레포명과 **한 줄 훅**. 궁금하게 만들되 낚시하지 마라.
  **여기만 짧게 쓴다 — 한 문장, 70자 이내.** 훅은 짧아야 훅이다.
  별 개수는 카드가 배지로 따로 보여주니 본문에 또 쓰지 마라.
- what_is_it: **"한마디로 뭐냐면"** — 이게 뭔지 딱 한 문장으로. 비유를 써도 좋다.
  독자가 이 카드에서 못 알아들으면 나머지는 안 읽는다. 가장 공들여라.
- problem: 이게 없으면 뭐가 불편한지. "이런 적 있죠?" 처럼 공감으로 열어라.
- why_now: 왜 지금 뜨는지. 숫자와 계기를 함께, 감이 오게.
- feature: 기능 하나씩. title 에 기능명(쉬운 말로), body 에 그래서 뭐가 좋은지.
- quickstart: 직접 써보려면 뭘 하는지. code 배열에 명령을 넣고 body 는 짧게.
  **code 의 각 줄은 그 자체로 완전한 명령이어야 한다.** 한 명령을 길이 때문에
  여러 줄로 쪼개지 마라 — 카드에는 줄마다 `$` 프롬프트가 붙어서, 쪼개면 별개의
  명령 두 개처럼 읽힌다. 길면 더 짧은 대표 명령을 골라라.
  body 에 이게 어디에 입력하는 건지 한마디 덧붙여라.
- fit: 어떤 사람에게 맞고 어떤 사람에겐 아직 이른지. 단점을 숨기지 마라.
- outro: **마무리 한마디만 쓴다 — 한 문장, 70자 이내.**
  레포 주소·라이선스·언어는 카드가 조사 데이터에서 자동으로 채운다.
  본문에 주소나 라이선스를 다시 쓰지 마라. 부담 없는 마무리면 충분하다
  (예: "오늘은 구경만 해도 충분해요").

## 매체 제약을 절대 어기지 마라

카드는 1080×1350 이미지다. 글자가 넘치면 잘려서 발행된다. 글자수 상한은 권고가
아니라 물리적 한계다. 상한을 넘기느니 내용을 덜어내라.

## 캡션

카드보다 조금 더 자세히 써도 되지만 **독자는 똑같다.** 여기서도 파일명을 나열하지
마라. 요약 3~4줄 + 레포 링크 + 별 개수 + 라이선스.
해시태그는 hashtags 배열에 따로 넣어라 (# 없이 단어만).
캡션 본문에는 해시태그를 넣지 마라.

## 추천 오디오

게시물에 얹을 오디오를 3건 제안한다 (audio 배열).

**곡명과 아티스트를 절대 쓰지 마라.** 인스타그램 오디오 목록은 지역과 계정
유형에 따라 다르고 너는 그 목록을 볼 수 없다. 특정 곡을 지목하면 있는지 없는지
모르는 것을 있다고 단정하는 셈이다. 대신 앱 검색창에 넣을 검색어를 줘라.

- mood: 어떤 분위기여야 하는지 한마디 (예: "차분한 배경음", "가볍고 통통 튀는")
- search: 인스타 오디오 검색창에 넣을 검색어 2~3개. 장르·악기·템포 같은
  일반 명사만 쓴다 (예: "lo-fi beat", "minimal synth", "차분한 브이로그").
  사람 이름, 곡 제목, 앨범명, 밴드명은 넣지 마라.
- why: 이 게시물의 내용·톤과 왜 맞는지 한 문장

셋은 서로 달라야 한다. 다 비슷하면 고를 이유가 없다.
카드가 담담한 도구 소개라면 훅이 센 음악은 내용과 어긋난다 — 내용에 맞춰라.
"""


def run(config: Config, run_date: str, dry_run: bool = False) -> CardDeckFile:
    return asyncio.run(_run_async(config, run_date, dry_run))


async def _run_async(config: Config, run_date: str, dry_run: bool) -> CardDeckFile:
    research = _load_research(run_date)
    cfg = config.section("compose")

    max_title = int(cfg.get("max_title_chars", 24))
    min_body = int(cfg.get("min_body_chars", 85))
    max_body = int(cfg.get("max_body_chars", 150))
    max_tags = int(cfg.get("max_hashtags", 30))
    max_code_lines = int(cfg.get("max_code_lines", 3))
    max_code_chars = int(cfg.get("max_code_line_chars", 52))
    tone = cfg.get("tone", "")

    roles, dropped = _plan_roles(research)
    log.info("카드 구성: %d장 (%s)", len(roles), ", ".join(roles))
    if dropped:
        log.warning("근거 부족으로 제외된 카드: %s", ", ".join(dropped))

    base_prompt = _build_prompt(
        research, roles, max_title, min_body, max_body, max_tags,
        max_code_lines, max_code_chars, tone
    )

    feedback = ""
    total_cost = 0.0
    for attempt in range(1, MAX_ATTEMPTS + 1):
        run_result = await run_structured(
            prompt=base_prompt + feedback,
            system_prompt=SYSTEM_PROMPT,
            schema_model=CardDeckPayload,
            max_turns=3,
            max_budget_usd=1.0,
            effort="medium",
        )
        total_cost += run_result.cost_usd or 0.0
        payload: CardDeckPayload = run_result.payload  # type: ignore[assignment]

        for note in repair(payload, max_tags):
            log.info("자동 보정: %s", note)

        violations = _validate(
            payload, roles, max_title, max_body, max_tags,
            max_code_lines, max_code_chars, min_body
        )
        if not violations:
            log.info("원고 검증 통과 (시도 %d회, $%.4f)", attempt, total_cost)
            break

        log.warning("시도 %d: 위반 %d건", attempt, len(violations))
        for v in violations[:5]:
            log.warning("  - %s", v)
        if attempt == MAX_ATTEMPTS:
            raise AgentRunError(
                f"{MAX_ATTEMPTS}회 시도했으나 원고가 제약을 만족하지 못했습니다:\n"
                + "\n".join(f"  - {v}" for v in violations)
            )
        feedback = (
            "\n\n## 직전 시도의 위반 사항 — 반드시 고쳐라\n"
            + "\n".join(f"- {v}" for v in violations)
        )

    result = CardDeckFile(
        repo=research.repo,
        date=run_date,
        generated_at=datetime.now(timezone.utc),
        payload=payload,
        dropped_roles=dropped,
        cost_usd=round(total_cost, 6),
        attempts=attempt,
    )

    if dry_run:
        log.info("[dry-run] 파일을 쓰지 않습니다.")
        return result

    post_dir = paths.POSTS / f"{run_date}-{_slug(research.repo)}"
    paths.write_json(post_dir / "content.json", result.model_dump(mode="json"))
    (post_dir / "caption.md").write_text(
        _render_caption(payload), encoding="utf-8"
    )
    return result


# ------------------------------------------------------------ 카드 구성


def _plan_roles(research: ResearchFile) -> tuple[list[str], list[str]]:
    """근거 있는 필드만으로 카드 순서를 짠다. 근거 없는 카드는 만들지 않는다.

    `architecture` 는 카드에서 뺐다. 입문자에게 "어떻게 동작하나"를 90자로
    설명하면 정확하거나 쉽거나 둘 중 하나를 반드시 포기하게 된다. 대신 그 자리에
    **"한마디로 뭐냐면"(what_is_it)** 을 앞쪽에 넣었다 — 여기서 못 알아들으면
    나머지 아홉 장을 안 읽기 때문이다.
    조사한 아키텍처 내용은 버리지 않고 프롬프트 맥락으로 계속 넘긴다.
    """
    p = research.payload
    roles = ["cover", "what_is_it"]  # one_liner 는 필수 필드라 항상 있다
    dropped: list[str] = []

    for role, claim in (("problem", p.problem), ("why_now", p.why_now)):
        (roles if claim.is_grounded else dropped).append(role)

    roles.extend("feature" for _ in p.key_features[:3])

    if p.quickstart.is_grounded:
        roles.append("quickstart")
    else:
        dropped.append("quickstart")

    if p.differentiators.is_grounded or p.limitations.is_grounded:
        roles.append("fit")
    else:
        dropped.append("fit")

    roles.append("outro")
    return roles, dropped


def _build_prompt(
    research: ResearchFile,
    roles: list[str],
    max_title: int,
    min_body: int,
    max_body: int,
    max_tags: int,
    max_code_lines: int,
    max_code_chars: int,
    tone: str,
) -> str:
    short_max = SHORT_BODY_MAX
    p = research.payload
    m = research.meta
    delta = f"+{m.delta_1d} (오늘)" if m.delta_1d is not None else "(미상)"

    features = "\n".join(
        f"- {f.title}: {f.text}  [근거: {', '.join(f.evidence)}]"
        for f in p.key_features
    )
    role_lines = "\n".join(f"{i}. {r}" for i, r in enumerate(roles, 1))

    return f"""\
아래 조사 자료로 카드 {len(roles)}장의 원고를 써라.

## 저장소
{research.repo} — {m.html_url}
스타 {m.stars:,} / 증가 {delta} / 언어 {m.language or "미상"} / 라이선스 {m.license or "미상"}

## 조사 자료
한 줄 정의: {p.one_liner}

문제: {p.problem.text}
왜 지금: {p.why_now.text}

핵심 기능:
{features}

동작 원리: {p.architecture.text}
설치: {" && ".join(p.quickstart.commands) or "(없음)"}
차별점: {p.differentiators.text}
한계: {p.limitations.text}

## 카드 순서 (index 1부터, 이 role 순서를 그대로)
{role_lines}

## 하드 제약
- title: {max_title}자 이하 (공백 포함)
- body: **{min_body}~{max_body}자** (공백 포함). 2~3문장으로 채운다.
  한 문장으로 끝내지 마라 — 카드가 휑해 보이고 설명도 부족하다.
  **단 cover 와 outro 는 예외다. 한 문장, {short_max}자 이내로 간결하게.**
- code 는 quickstart 카드에만. {max_code_lines}줄 이하, 줄당 {max_code_chars}자 이하
  각 줄은 그 자체로 완전한 명령이어야 한다. 한 명령을 쪼개지 마라.
- caption: {CAPTION_LIMIT}자 이하, 해시태그 미포함
- hashtags: {max_tags}개 이하, '#' 없이 단어만
- audio: {AUDIO_PICKS}건. 각 건의 search 는 2~3개, 검색어 하나는 {AUDIO_TERM_LIMIT}자 이하.
  mood 는 {AUDIO_MOOD_LIMIT}자 이하, why 는 {AUDIO_WHY_LIMIT}자 이하.
  곡명·아티스트명 금지 (장르·악기·템포 같은 일반 명사만)
- 톤: {tone}
"""


# -------------------------------------------------------------- 자동 보정


# 캡션 끝에 붙은 해시태그 블록 (마지막 줄들이 #토큰 위주인 경우)
_TRAILING_TAGS = re.compile(r"(?:^|\n)[ \t]*(?:#[^\s#]+[ \t]*)+$")
_TAG_TOKEN = re.compile(r"#([^\s#]+)")


def repair(payload: CardDeckPayload, max_tags: int) -> list[str]:
    """의미를 바꾸지 않고 기계적으로 고칠 수 있는 것만 고친다.

    글자수 초과는 여기서 다루지 않는다. 잘라내면 문장이 끊기므로 재생성해야 한다.
    반면 해시태그 위치나 '#' 접두는 뜻이 그대로이므로 파이프라인을 실패시킬 이유가 없다.
    """
    fixed: list[str] = []

    # 캡션 끝의 해시태그 블록은 hashtags 로 옮긴다. 인스타 캡션에서는 자연스러운
    # 작법이라 모델이 반복해서 넣는다. 막을 게 아니라 받아서 정리하는 편이 맞다.
    match = _TRAILING_TAGS.search(payload.caption)
    if match:
        moved = _TAG_TOKEN.findall(match.group(0))
        payload.caption = payload.caption[: match.start()].rstrip()
        payload.hashtags.extend(moved)
        fixed.append(f"캡션 끝 해시태그 {len(moved)}개를 hashtags 로 이동")

    cleaned = [t.lstrip("#").strip() for t in payload.hashtags]
    cleaned = [t for t in cleaned if t]

    deduped: list[str] = []
    seen: set[str] = set()
    for tag in cleaned:
        key = tag.lower()
        if key not in seen:
            seen.add(key)
            deduped.append(tag)
    if len(deduped) != len(payload.hashtags):
        fixed.append(f"해시태그 정리: {len(payload.hashtags)}개 → {len(deduped)}개")

    if len(deduped) > max_tags:
        fixed.append(f"해시태그 {len(deduped)}개를 상한 {max_tags}개로 자름")
        deduped = deduped[:max_tags]

    payload.hashtags = deduped
    return fixed


# ------------------------------------------------------- 난이도 검사


# 파일명·모듈명 (`curator.py`, `learning_graph.py`, `pyproject.toml`)
_IDENTIFIER = re.compile(r"\b[a-zA-Z_][a-zA-Z0-9_]*\.(py|js|ts|toml|json|yaml|yml|md|sh|cfg)\b")
# snake_case / camelCase 식별자 (`learning_graph`, `memoryManager`)
_SNAKE_OR_CAMEL = re.compile(r"\b[a-z]+_[a-z_]+\b|\b[a-z]+[A-Z][a-zA-Z]*\b")
# 풀어 쓰지 않은 영문 약어 (2~5글자 대문자). 괄호 설명이 붙어 있으면 통과시킨다.
_ACRONYM = re.compile(r"\b[A-Z]{2,5}\b(?!\s*\()")

# 설명 없이 써도 되는 것들 — 독자가 이미 알거나, 고유명사이거나, 뜻이 자명하다
_ALLOWED = {
    "AI", "PC", "OS", "URL", "MIT", "APT", "GPU", "CPU", "UI", "IT",
    "GPT", "OK", "TV", "USB", "PDF", "ID",
}


def jargon_hits(text: str) -> list[str]:
    """입문자가 막힐 만한 토큰을 뽑는다.

    프롬프트로 "쉽게 써라"라고만 하면 조사 자료의 파일명이 그대로 새어 나온다.
    실제로 첫 버전 캡션에 `curator.py 87KB`, `learning_graph` 가 그대로 실렸다.
    """
    found = [m.group(0) for m in _IDENTIFIER.finditer(text)]
    found += [m.group(0) for m in _SNAKE_OR_CAMEL.finditer(text)]
    found += [
        m.group(0) for m in _ACRONYM.finditer(text) if m.group(0) not in _ALLOWED
    ]
    return sorted(set(found))


# -------------------------------------------------------------- 검증


# 한 명령을 길이 때문에 쪼갠 흔적. 카드는 줄마다 '$' 를 붙이므로 이런 조각이
# 들어가면 별개의 명령 두 개처럼 읽힌다 — 내용 오류다.
_CONTINUATION = re.compile(
    r"""^\s*(
        [./]              # URL·경로 조각으로 시작: ".nousresearch.com/..."
      | \|                # 파이프로 시작
      | &&                # 연결자로 시작
      | -{1,2}[a-zA-Z]    # 옵션 플래그로 시작
      | [a-z0-9-]+\.(com|org|net|io|sh|dev)/   # 도메인 조각으로 시작
    )""",
    re.VERBOSE,
)


def _looks_like_continuation(line: str) -> bool:
    return bool(line.strip()) and bool(_CONTINUATION.match(line))


# "곡명 - 아티스트" 나 "Artist – Title" 꼴. 모델이 구체적인 곡을 적으려 할 때
# 가장 흔히 나오는 모양이라, 이것만 잡아도 대부분 걸러진다.
_TRACK_LIKE = re.compile(r"\s[-–—]\s|\bfeat\.?\b|\bft\.\s", re.IGNORECASE)


def _audio_problems(picks: list[AudioPick]) -> list[str]:
    """오디오 추천의 형식과 '없는 곡을 지목하지 않았는가'를 본다.

    검색어는 앱에 넣는 지시라 틀릴 수 없지만, 곡명은 있는지 없는지 확인할 방법이
    없다. 확인 못 하는 것은 애초에 쓰지 않는다 — 이 프로젝트가 근거 없는 필드를
    버리는 것과 같은 이유다.
    """
    problems: list[str] = []

    if len(picks) != AUDIO_PICKS:
        problems.append(f"audio 가 {len(picks)}건입니다. 정확히 {AUDIO_PICKS}건이어야 합니다.")

    for i, pick in enumerate(picks, 1):
        if not pick.mood.strip():
            problems.append(f"audio {i}: mood 가 비어 있습니다.")
        elif len(pick.mood) > AUDIO_MOOD_LIMIT:
            problems.append(
                f"audio {i}: mood 가 {len(pick.mood)}자입니다 ({AUDIO_MOOD_LIMIT}자 이하)."
            )

        if not pick.why.strip():
            problems.append(f"audio {i}: why 가 비어 있습니다.")
        elif len(pick.why) > AUDIO_WHY_LIMIT:
            problems.append(
                f"audio {i}: why 가 {len(pick.why)}자입니다 ({AUDIO_WHY_LIMIT}자 이하)."
            )

        if not 2 <= len(pick.search) <= 3:
            problems.append(f"audio {i}: search 가 {len(pick.search)}개입니다 (2~3개).")
        for term in pick.search:
            if len(term) > AUDIO_TERM_LIMIT:
                problems.append(
                    f"audio {i}: 검색어 '{term}' 이 {len(term)}자입니다 "
                    f"({AUDIO_TERM_LIMIT}자 이하)."
                )
            if _TRACK_LIKE.search(term):
                problems.append(
                    f"audio {i}: 검색어 '{term}' 이 특정 곡·아티스트처럼 보입니다. "
                    "장르·악기·템포 같은 일반 명사로 바꾸세요."
                )

    moods = [p.mood.strip() for p in picks if p.mood.strip()]
    if len(set(moods)) < len(moods):
        problems.append("audio 의 mood 가 겹칩니다. 셋은 서로 다른 분위기여야 합니다.")

    return problems


def _validate(
    payload: CardDeckPayload,
    roles: list[str],
    max_title: int,
    max_body: int,
    max_tags: int,
    max_code_lines: int = 3,
    max_code_line_chars: int = 52,
    min_body: int = 0,
) -> list[str]:
    """위반을 사람이 읽을 수 있는 문장으로 모은다. 그대로 모델에 되돌려준다."""
    problems: list[str] = []

    if len(payload.cards) != len(roles):
        problems.append(
            f"카드가 {len(payload.cards)}장입니다. 정확히 {len(roles)}장이어야 합니다."
        )

    for i, (card, expected_role) in enumerate(zip(payload.cards, roles), 1):
        where = f"카드 {i}({expected_role})"
        if card.role != expected_role:
            problems.append(f"{where}: role 이 '{card.role}' 입니다. '{expected_role}' 이어야 합니다.")
        if len(card.title) > max_title:
            problems.append(
                f"{where} title 이 {len(card.title)}자입니다 ({max_title}자 이하로 줄이세요): \"{card.title}\""
            )
        if expected_role in SHORT_ROLES:
            # 간결해야 하는 카드. 하한은 적용하지 않는다.
            if len(card.body) > SHORT_BODY_MAX:
                problems.append(
                    f"{where} body 가 {len(card.body)}자입니다. 이 카드는 간결해야 하니 "
                    f"한 문장, {SHORT_BODY_MAX}자 이내로 줄이세요."
                )
        elif len(card.body) > max_body:
            problems.append(
                f"{where} body 가 {len(card.body)}자입니다 ({max_body}자 이하로 줄이세요)."
            )
        elif min_body and 0 < len(card.body) < min_body:
            problems.append(
                f"{where} body 가 {len(card.body)}자로 너무 짧습니다 "
                f"({min_body}자 이상, 2~3문장). 한 문장으로 끝내지 마세요."
            )
        if not card.title.strip():
            problems.append(f"{where} title 이 비어 있습니다.")
        if card.code and expected_role != "quickstart":
            problems.append(f"{where} 에 code 가 있습니다. quickstart 카드에만 넣으세요.")
        if len(card.code) > max_code_lines:
            problems.append(
                f"{where} code 가 {len(card.code)}줄입니다 ({max_code_lines}줄 이하)."
            )
        for line in card.code:
            if len(line) > max_code_line_chars:
                problems.append(
                    f"{where} code 줄이 {len(line)}자입니다 "
                    f"({max_code_line_chars}자 이하). 쪼개지 말고 더 짧은 명령을 쓰세요: \"{line}\""
                )
            if _looks_like_continuation(line):
                problems.append(
                    f"{where} code 줄이 이어붙인 조각으로 보입니다. 각 줄은 그 자체로 "
                    f"실행 가능한 완전한 명령이어야 합니다: \"{line}\""
                )

    if len(payload.caption) > CAPTION_LIMIT:
        problems.append(
            f"caption 이 {len(payload.caption)}자입니다 ({CAPTION_LIMIT}자 이하)."
        )
    if len(payload.hashtags) > max_tags:
        problems.append(
            f"해시태그가 {len(payload.hashtags)}개입니다 ({max_tags}개 이하)."
        )
    if not payload.caption.strip():
        problems.append("caption 이 비어 있습니다.")

    problems.extend(_jargon_problems(payload, roles))

    # 해시태그 위치와 '#' 접두는 repair() 가 처리하므로 여기서 보지 않는다.
    # 캡션 안의 '#' 은 "C#", "이슈 #42" 처럼 정당한 쓰임이 있다.
    return problems


def _jargon_problems(payload: CardDeckPayload, roles: list[str]) -> list[str]:
    """입문자가 막힐 만한 표현을 잡아 되돌려준다.

    quickstart 의 code 와 outro 는 검사에서 뺀다 — 명령어와 레포 주소는
    영문일 수밖에 없고, 그게 정보의 본체다.
    """
    problems: list[str] = []
    for card, role in zip(payload.cards, roles):
        if role == "outro":
            continue
        text = f"{card.title} {card.body}"  # code 는 제외
        hits = jargon_hits(text)
        if len(hits) > MAX_JARGON_PER_CARD:
            problems.append(
                f"카드 {card.index}({role}) 가 입문자에게 어렵습니다. "
                f"파일명·식별자·풀지 않은 약어: {', '.join(hits)} — "
                "무엇을 하는지 쉬운 말로 바꾸거나 괄호로 뜻을 풀어주세요."
            )

    problems += _audio_problems(payload.audio)

    caption_hits = jargon_hits(payload.caption)
    if len(caption_hits) > MAX_JARGON_IN_CAPTION:
        problems.append(
            f"캡션이 입문자에게 어렵습니다. 파일명·식별자·풀지 않은 약어 "
            f"{len(caption_hits)}개: {', '.join(caption_hits[:8])} — "
            "무엇을 하는지로 바꿔 쓰세요."
        )
    return problems


# ------------------------------------------------------------------ 출력


# 이 줄 위까지가 인스타에 붙여넣는 캡션이다. 아래는 발행할 때 보는 메모다.
# 파일 하나에 둘을 같이 두면 통째로 복사해서 오디오 메모까지 발행될 수 있다.
# 그래서 구분선을 눈에 띄게 만들고 문구로도 한 번 더 막는다.
CAPTION_CUT = "=" * 60


def _render_caption(payload: CardDeckPayload) -> str:
    tags = " ".join(f"#{t.lstrip('#')}" for t in payload.hashtags)
    body = f"{payload.caption.rstrip()}\n\n{tags}\n"
    if not payload.audio:
        # 옛 게시물에는 audio 가 없다. 그때는 파일 전체가 캡션이던 옛 모양을 지킨다.
        return body
    return body + _render_audio(payload.audio)


def _render_audio(picks: list[AudioPick]) -> str:
    lines = [
        "",
        CAPTION_CUT,
        "↑ 여기까지가 캡션입니다. 아래는 붙여넣지 마세요 (발행할 때 보는 메모)",
        CAPTION_CUT,
        "",
        "## 추천 오디오",
        "",
        "인스타그램 앱 > 오디오 검색창에 검색어를 넣어 고르세요.",
        "곡을 지정하지 않는 이유: 오디오 목록은 지역과 계정 유형(비즈니스 계정은",
        "상업용 음원이 상당 부분 막힙니다)에 따라 달라서, 앱에서 직접 확인해야",
        "합니다. 아래는 '무엇을 찾을지'까지만 좁혀둔 것입니다.",
        "",
    ]
    for i, pick in enumerate(picks, 1):
        lines += [
            f"{i}. {pick.mood}",
            f"   검색어: {' / '.join(pick.search)}",
            f"   이유: {pick.why}",
            "",
        ]
    return "\n".join(lines)


def _load_research(run_date: str) -> ResearchFile:
    matches = sorted(paths.RESEARCH.glob(f"{run_date}-*.json"))
    if not matches:
        raise FileNotFoundError(
            f"조사 파일이 없습니다: {paths.RESEARCH}/{run_date}-*.json. 먼저 research 를 실행하세요."
        )
    return ResearchFile.model_validate(paths.read_json(matches[0]))


def _slug(full_name: str) -> str:
    return full_name.replace("/", "__").lower()
