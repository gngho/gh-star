"""Claude Agent SDK 래퍼.

호출 측이 신경 쓸 것을 줄이는 게 목적이다:
- 구조적 출력을 Pydantic 모델로 검증해서 돌려준다
- 실측 비용(total_cost_usd)과 턴 수를 함께 반환한다
- max_budget_usd 로 하드 상한을 건다 (SPEC 12.1 의 비용 방어선)
"""

from __future__ import annotations

import logging
import os
import platform
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any, TypeVar

from claude_agent_sdk import (
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
)
from pydantic import BaseModel, ValidationError

from .schema import output_format_for

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

MODEL = "claude-opus-5"


class AgentRunError(RuntimeError):
    """에이전트가 사용 가능한 결과를 내지 못했다."""


@dataclass
class AgentRun:
    payload: BaseModel
    cost_usd: float | None
    num_turns: int
    raw: Any


@cache
def _cli_path() -> str | None:
    """Windows 에서 쓸 네이티브 claude.exe 경로. 못 찾으면 None.

    SDK 는 PATH 에서 먼저 잡히는 claude 를 쓰는데, npm 전역 설치는 셸 심
    (`claude.cmd`)을 PATH 에 올린다. SDK 는 배치 스크립트 실행을 거부하므로
    (cmd.exe 인자 인젝션을 막을 방법이 없다) 그대로 두면 조사·원고 단계가
    CLIConnectionError 로 죽는다. PyPI 에 Windows 휠이 없어 SDK 에 exe 가
    번들되지도 않는다. 그래서 심 옆에 있는 진짜 exe 를 직접 가리킨다.

    None 을 돌려주면 SDK 기본 탐색에 맡긴다 (macOS·리눅스·Actions).
    """
    if platform.system() != "Windows":
        return None

    if override := os.environ.get("CLAUDE_CLI_PATH"):
        return override

    candidates = [Path.home() / ".local/bin/claude.exe"]  # 네이티브 설치본
    if appdata := os.environ.get("APPDATA"):
        npm = Path(appdata) / "npm/node_modules/@anthropic-ai"
        candidates += [
            npm / "claude-code/bin/claude.exe",
            npm / "claude-code/node_modules/@anthropic-ai/claude-code-win32-x64/claude.exe",
        ]

    for path in candidates:
        if path.is_file():
            log.debug("claude.exe: %s", path)
            return str(path)

    # 여기까지 왔으면 SDK 가 스스로 찾아보게 두고, 실패 시 SDK 쪽
    # 안내 메시지(설치 방법이 적혀 있다)를 그대로 보여주는 편이 낫다.
    log.warning(
        "네이티브 claude.exe 를 찾지 못했습니다. "
        "CLAUDE_CLI_PATH 에 경로를 지정하거나 Claude Code 를 네이티브로 설치하세요."
    )
    return None


def _note_auth_source() -> None:
    """어떤 인증으로 도는지 한 번 알려준다.

    API 키가 없는 것은 오류가 아니라 기본 상태다 — 이 단계는 로컬에서 Claude
    Code 구독 로그인으로 돌리기로 했다(SPEC 9.0). 키를 넣으면 API 과금으로
    바뀌고 CI 에서도 돌릴 수 있다.
    """
    if os.environ.get("ANTHROPIC_API_KEY"):
        log.info("인증: ANTHROPIC_API_KEY (API 과금)")
    else:
        log.info("인증: Claude Code 로그인 (구독 한도 사용, API 과금 없음)")


async def run_structured(
    *,
    prompt: str,
    system_prompt: str,
    schema_model: type[T],
    mcp_servers: dict[str, Any] | None = None,
    allowed_tools: list[str] | None = None,
    max_turns: int = 25,
    max_budget_usd: float | None = 3.0,
    effort: str = "high",
) -> AgentRun:
    """에이전트를 돌려 schema_model 로 검증된 결과를 받는다."""
    _note_auth_source()

    options = ClaudeAgentOptions(
        model=MODEL,
        cli_path=_cli_path(),
        system_prompt=system_prompt,
        mcp_servers=mcp_servers or {},
        allowed_tools=allowed_tools or [],
        # 에이전트는 읽기만 한다. 파일 쓰기는 호출 측 Python 이 담당한다 (SPEC 5.2).
        disallowed_tools=["Bash", "Write", "Edit", "NotebookEdit"],
        permission_mode="bypassPermissions",
        max_turns=max_turns,
        max_budget_usd=max_budget_usd,
        effort=effort,  # type: ignore[arg-type]
        output_format=output_format_for(schema_model),
        # 사용자/프로젝트 설정을 상속하지 않는다. CI 와 로컬이 같게 돌아야 한다.
        setting_sources=[],
    )

    result: ResultMessage | None = None
    async with ClaudeSDKClient(options=options) as client:
        await client.query(prompt)
        async for message in client.receive_response():
            if isinstance(message, ResultMessage):
                result = message

    if result is None:
        raise AgentRunError("에이전트가 결과 메시지를 반환하지 않았습니다.")

    if result.is_error:
        detail = result.subtype or "unknown"
        errors = "; ".join(result.errors or [])
        raise AgentRunError(f"에이전트 실행 실패 ({detail}) {errors}".strip())

    if result.structured_output is None:
        raise AgentRunError(
            "구조적 출력이 비어 있습니다. 스키마를 만족하지 못했을 가능성이 큽니다."
        )

    try:
        payload = schema_model.model_validate(result.structured_output)
    except ValidationError as exc:
        raise AgentRunError(f"구조적 출력이 스키마와 맞지 않습니다: {exc}") from exc

    # 구독 로그인으로 돌 때 total_cost_usd 는 실제 청구액이 아니라
    # "API 로 돌렸다면" 기준의 환산 비용이다. 구독 한도만 소진된다.
    log.info(
        "에이전트 완료: %d턴, 환산 $%.4f",
        result.num_turns,
        result.total_cost_usd or 0.0,
    )
    return AgentRun(
        payload=payload,
        cost_usd=result.total_cost_usd,
        num_turns=result.num_turns,
        raw=result.structured_output,
    )
