"""Claude Agent SDK 래퍼.

호출 측이 신경 쓸 것을 줄이는 게 목적이다:
- 구조적 출력을 Pydantic 모델로 검증해서 돌려준다
- 실측 비용(total_cost_usd)과 턴 수를 함께 반환한다
- max_budget_usd 로 하드 상한을 건다 (SPEC 12.1 의 비용 방어선)
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
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
