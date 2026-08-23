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
    CardDeckFile,
    CardDeckPayload,
    ResearchFile,
)

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
CAPTION_LIMIT = 2200

SYSTEM_PROMPT = """\
너는 개발자 대상 인스타그램 카드뉴스의 원고를 쓴다.

## 매체 제약을 절대 어기지 마라

카드는 1080×1350 이미지다. 글자가 넘치면 잘려서 발행된다. 글자수 상한은 권고가
아니라 물리적 한계다. 상한을 넘기느니 내용을 덜어내라.

## 카드 구성

각 카드는 role 로 역할이 정해져 있다. 주어진 role 순서를 그대로 지켜라.
- cover: 레포명과 한 줄 훅. 궁금하게 만들되 낚시하지 마라.
- problem: 이게 없으면 뭐가 불편한지.
- why_now: 왜 지금 뜨는지. 숫자와 계기를 함께.
- feature: 기능 하나씩. title 에 기능명, body 에 그게 왜 좋은지.
- architecture: 어떻게 동작하는지 3줄 요약.
- quickstart: 설치·실행 커맨드. code 배열에 넣고 body 는 짧게.
- fit: 이럴 때 쓰면 좋고 이럴 땐 아니다. 단점을 숨기지 마라.
- outro: 레포 URL 과 라이선스, 그리고 행동 유도.

## 톤

한국어. 실무 개발자에게 말하듯. 과장 금지. "혁신적인", "게임 체인저" 같은 표현 금지.
근거 자료에 없는 사실을 추가하지 마라. 자료에 없으면 그 카드는 짧게 써라.

## 캡션

요약 2~3줄 + 레포 링크 + 스타 수 + 라이선스. 해시태그는 hashtags 배열에 따로 넣어라
(# 없이 단어만). 캡션 본문에는 해시태그를 넣지 마라.
"""


def run(config: Config, run_date: str, dry_run: bool = False) -> CardDeckFile:
    return asyncio.run(_run_async(config, run_date, dry_run))


async def _run_async(config: Config, run_date: str, dry_run: bool) -> CardDeckFile:
    research = _load_research(run_date)
    cfg = config.section("compose")

    max_title = int(cfg.get("max_title_chars", 24))
    max_body = int(cfg.get("max_body_chars", 90))
    max_tags = int(cfg.get("max_hashtags", 30))
    tone = cfg.get("tone", "")

    roles, dropped = _plan_roles(research)
    log.info("카드 구성: %d장 (%s)", len(roles), ", ".join(roles))
    if dropped:
        log.warning("근거 부족으로 제외된 카드: %s", ", ".join(dropped))

    base_prompt = _build_prompt(research, roles, max_title, max_body, max_tags, tone)

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

        violations = _validate(payload, roles, max_title, max_body, max_tags)
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
    """근거 있는 필드만으로 카드 순서를 짠다. 근거 없는 카드는 만들지 않는다."""
    p = research.payload
    roles = ["cover"]
    dropped: list[str] = []

    for role, claim in (("problem", p.problem), ("why_now", p.why_now)):
        (roles if claim.is_grounded else dropped).append(role)

    roles.extend("feature" for _ in p.key_features[:3])

    if p.architecture.is_grounded:
        roles.append("architecture")
    else:
        dropped.append("architecture")

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
    max_body: int,
    max_tags: int,
    tone: str,
) -> str:
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
- body: {max_body}자 이하 (공백 포함)
- code 는 quickstart 카드에만. 3줄 이하, 줄당 42자 이하
- caption: {CAPTION_LIMIT}자 이하, 해시태그 미포함
- hashtags: {max_tags}개 이하, '#' 없이 단어만
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


# -------------------------------------------------------------- 검증


def _validate(
    payload: CardDeckPayload,
    roles: list[str],
    max_title: int,
    max_body: int,
    max_tags: int,
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
        if len(card.body) > max_body:
            problems.append(
                f"{where} body 가 {len(card.body)}자입니다 ({max_body}자 이하로 줄이세요)."
            )
        if not card.title.strip():
            problems.append(f"{where} title 이 비어 있습니다.")
        if card.code and expected_role != "quickstart":
            problems.append(f"{where} 에 code 가 있습니다. quickstart 카드에만 넣으세요.")
        for line in card.code:
            if len(line) > 42:
                problems.append(f"{where} code 줄이 {len(line)}자입니다 (42자 이하).")

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

    # 해시태그 위치와 '#' 접두는 repair() 가 처리하므로 여기서 보지 않는다.
    # 캡션 안의 '#' 은 "C#", "이슈 #42" 처럼 정당한 쓰임이 있다.
    return problems


# ------------------------------------------------------------------ 출력


def _render_caption(payload: CardDeckPayload) -> str:
    tags = " ".join(f"#{t.lstrip('#')}" for t in payload.hashtags)
    return f"{payload.caption.rstrip()}\n\n{tags}\n"


def _load_research(run_date: str) -> ResearchFile:
    matches = sorted(paths.RESEARCH.glob(f"{run_date}-*.json"))
    if not matches:
        raise FileNotFoundError(
            f"조사 파일이 없습니다: {paths.RESEARCH}/{run_date}-*.json. 먼저 research 를 실행하세요."
        )
    return ResearchFile.model_validate(paths.read_json(matches[0]))


def _slug(full_name: str) -> str:
    return full_name.replace("/", "__").lower()
