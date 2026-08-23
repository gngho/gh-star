"""심층 조사 단계. SPEC.md 5.

핵심 두 가지:
1. 선행 로딩 — 에이전트를 빈손으로 시작시키지 않는다. README·트리·릴리스를
   미리 프롬프트에 실어 보내면 "그거 줘" 왕복 5~8턴이 0턴이 된다.
2. 근거 강제 — 모든 서술에 evidence 를 요구하고, 없는 필드는 표시해 원고
   단계에서 제외한다. 빈 근거를 그럴듯한 문장으로 채우는 게 최대 실패 모드다.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from . import paths
from .config import Config, github_token
from .github_api import API_ROOT, GitHubClient
from .llm import AgentRunError, run_structured
from .models import (
    ResearchFile,
    ResearchMeta,
    ResearchPayload,
    SelectedRepo,
    SelectionFile,
)
from .tools import TOOL_NAMES, build_github_server

log = logging.getLogger(__name__)

MIN_FEATURES = 2
STARTER_README_CHARS = 40_000

SYSTEM_PROMPT = """\
너는 오픈소스 저장소를 조사해 한국 개발자에게 소개할 근거 자료를 만드는 리서처다.

## 가장 중요한 규칙: 근거 없는 주장은 쓰지 않는다

모든 서술 필드에는 evidence 배열이 함께 간다. evidence 에는 네가 **실제로 조회한**
파일 경로, 이슈 번호(#123), 릴리스 태그(v2.0.0), 또는 URL 만 넣는다.

- 근거를 찾지 못했으면 text 를 비우고 evidence 도 비워라. 지어내지 마라.
- "아마도", "일반적으로 이런 프로젝트는" 같은 추측은 근거가 아니다.
- README 에 적힌 주장과 코드가 다르면 코드를 믿고 그 사실을 limitations 에 적어라.

## 조사 방법

프롬프트에 README·디렉터리 트리·최근 릴리스가 이미 실려 있다. 그것부터 읽고,
**부족한 부분만** 툴로 보충해라. 같은 정보를 다시 요청하지 마라.

특히 다음을 확인해라:
- 진입점 코드를 실제로 열어봐라. README 의 설명과 구현이 일치하는가?
- 최근 이슈에서 사용자들이 무엇에 막히는가? 그게 limitations 의 재료다.
- 릴리스 노트에 최근 스타 급증을 설명할 변화가 있는가? 그게 why_now 다.

## 톤

한국어로 쓴다. 과장하지 않는다. 마케팅 문구가 아니라 동료 개발자에게 설명하듯 쓴다.
key_features 는 3개를 목표로 하되, 근거를 갖춘 것만 넣어라. 2개뿐이면 2개만 넣는다.
"""


def run(config: Config, run_date: str, dry_run: bool = False) -> ResearchFile:
    return asyncio.run(_run_async(config, run_date, dry_run))


async def _run_async(config: Config, run_date: str, dry_run: bool) -> ResearchFile:
    raw = paths.read_json(paths.selection_path(run_date), default=None)
    if not raw:
        raise FileNotFoundError(
            f"선정 파일이 없습니다: {paths.selection_path(run_date)}. 먼저 select 를 실행하세요."
        )
    selection = SelectionFile.model_validate(raw)
    if selection.primary is None:
        raise RuntimeError(f"{run_date}: primary 가 없습니다.")

    targets = [selection.primary, *selection.backups]
    last_error: Exception | None = None

    with GitHubClient(github_token()) as client:
        server = build_github_server(client)
        for attempt, target in enumerate(targets, 1):
            label = "primary" if attempt == 1 else f"backup{attempt - 1}"
            log.info("[%s] 조사 시작: %s", label, target.full_name)
            try:
                result = await _research_one(client, server, target, run_date)
            except (AgentRunError, InsufficientResearch) as exc:
                log.warning("[%s] %s 실패: %s", label, target.full_name, exc)
                last_error = exc
                continue

            if not dry_run:
                paths.write_json(
                    paths.RESEARCH / f"{run_date}-{_slug(target.full_name)}.json",
                    result.model_dump(mode="json"),
                )
            return result

    raise RuntimeError(
        f"{run_date}: primary 와 backup 전부 조사에 실패했습니다. 마지막 오류: {last_error}"
    )


class InsufficientResearch(RuntimeError):
    """근거를 갖춘 핵심 기능이 부족해 카드로 만들 수 없다."""


async def _research_one(
    client: GitHubClient, server: object, target: SelectedRepo, run_date: str
) -> ResearchFile:
    starter = await _build_starter_kit(client, target)

    run = await run_structured(
        prompt=starter,
        system_prompt=SYSTEM_PROMPT,
        schema_model=ResearchPayload,
        mcp_servers={"gh": server},
        allowed_tools=[*TOOL_NAMES, "WebSearch"],
        max_turns=25,
        max_budget_usd=3.0,
        effort="high",
    )
    payload: ResearchPayload = run.payload  # type: ignore[assignment]

    ungrounded = _ungrounded_fields(payload)
    grounded_features = [f for f in payload.key_features if f.is_grounded]
    if len(grounded_features) < MIN_FEATURES:
        raise InsufficientResearch(
            f"근거 있는 key_features 가 {len(grounded_features)}개뿐입니다 "
            f"(최소 {MIN_FEATURES}개 필요)."
        )
    payload.key_features = grounded_features

    if ungrounded:
        log.warning("근거 없는 필드: %s", ", ".join(ungrounded))

    return ResearchFile(
        repo=target.full_name,
        researched_at=datetime.now(timezone.utc),
        payload=payload,
        meta=ResearchMeta(
            license=target.repo.license_spdx,
            language=target.repo.language,
            stars=target.repo.stars,
            delta_1d=_delta_from_reason(target.reason),
            html_url=target.repo.html_url,
        ),
        ungrounded_fields=ungrounded,
        cost_usd=run.cost_usd,
        num_turns=run.num_turns,
    )


async def _build_starter_kit(client: GitHubClient, target: SelectedRepo) -> str:
    """조사 시작 전에 확실히 필요한 것들을 미리 실어 보낸다 (SPEC 5.2)."""
    repo = target.full_name

    readme = await asyncio.to_thread(client.get_readme_text, repo) or "(README 없음)"
    if len(readme) > STARTER_README_CHARS:
        readme = (
            readme[:STARTER_README_CHARS]
            + f"\n\n[...README 가 {STARTER_README_CHARS:,}자에서 잘렸습니다. "
            "전체가 필요하면 get_readme 툴을 쓰세요.]"
        )

    tree = await _top_level_tree(client, repo)
    releases = await _recent_releases_text(client, repo)
    facts = target.repo

    return f"""\
아래 저장소를 조사해 카드뉴스 원고의 재료를 만들어라.

## 대상
- 저장소: {repo}
- URL: {facts.html_url}
- 설명: {facts.description or "(없음)"}
- 언어: {facts.language or "(미상)"} | 라이선스: {facts.license_spdx or "(없음)"}
- 스타: {facts.stars:,} | 토픽: {", ".join(facts.topics) or "(없음)"}
- 선정 근거: {target.reason}

## README (선행 로딩됨 — 다시 요청하지 마라)
```
{readme}
```

## 최상위 트리 (선행 로딩됨)
```
{tree}
```

## 최근 릴리스 (선행 로딩됨)
```
{releases}
```

## 할 일
위 자료로 부족한 부분만 툴로 보충한 뒤, 스키마에 맞춰 결과를 내라.
진입점 코드를 최소 하나는 실제로 열어보고, 최근 이슈도 확인해라.
"""


async def _top_level_tree(client: GitHubClient, repo: str) -> str:
    response = await asyncio.to_thread(
        client._request, "GET", f"{API_ROOT}/repos/{repo}/contents/"
    )
    if response.status_code != 200:
        return "(트리 조회 실패)"
    payload = response.json()
    if not isinstance(payload, list):
        return "(트리 없음)"
    return "\n".join(
        f"{'d' if e.get('type') == 'dir' else 'f'} {e.get('name')}" for e in payload[:80]
    )


async def _recent_releases_text(client: GitHubClient, repo: str) -> str:
    response = await asyncio.to_thread(
        client._request,
        "GET",
        f"{API_ROOT}/repos/{repo}/releases",
        params={"per_page": 3},
    )
    if response.status_code != 200:
        return "(릴리스 조회 실패)"
    items = response.json()
    if not items:
        return "(릴리스 없음)"
    return "\n\n---\n\n".join(
        f"{it.get('tag_name')} ({it.get('published_at')})\n"
        f"{(it.get('body') or '').strip()[:1500]}"
        for it in items
    )


def _ungrounded_fields(payload: ResearchPayload) -> list[str]:
    out = []
    for name in (
        "problem",
        "why_now",
        "architecture",
        "quickstart",
        "differentiators",
        "limitations",
    ):
        if not getattr(payload, name).is_grounded:
            out.append(name)
    return out


def _delta_from_reason(reason: str) -> int | None:
    import re

    match = re.search(r"Δ1d=([+-]?\d+)", reason)
    return int(match.group(1)) if match else None


def _slug(full_name: str) -> str:
    return full_name.replace("/", "__").lower()
