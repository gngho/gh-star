"""에이전트가 저장소를 직접 읽게 해주는 인프로세스 MCP 툴. SPEC.md 5.3.

설계 원칙:
- 전부 읽기 전용이다. 에이전트에는 Bash/Write/Edit 를 주지 않는다.
- 각 툴은 출력 크기 상한을 갖는다. 잘라낸 경우 그 사실을 본문에 명시해
  에이전트가 "이게 전부"라고 오해하지 않게 한다.
- 동기 HTTP 클라이언트를 asyncio.to_thread 로 감싼다. SDK 의 stdio 펌프가
  같은 이벤트 루프에서 돌기 때문에 블로킹하면 세션이 멈춘다.
"""

from __future__ import annotations

import asyncio
import base64
from typing import Annotated, Any

from claude_agent_sdk import create_sdk_mcp_server, tool

from ..github_api import API_ROOT, GitHubClient

SERVER_NAME = "gh"

README_LIMIT = 200_000
FILE_LIMIT = 100_000
TREE_LIMIT = 300
ISSUE_BODY_LIMIT = 1_200

TOOL_NAMES = [
    f"mcp__{SERVER_NAME}__get_readme",
    f"mcp__{SERVER_NAME}__list_repo_tree",
    f"mcp__{SERVER_NAME}__get_file",
    f"mcp__{SERVER_NAME}__recent_issues",
    f"mcp__{SERVER_NAME}__recent_releases",
    f"mcp__{SERVER_NAME}__contributor_stats",
]

Repo = Annotated[str, "대상 저장소 (owner/repo 형식)"]


def _text(body: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": body}]}


def _truncate(body: str, limit: int, label: str) -> str:
    if len(body) <= limit:
        return body
    return (
        body[:limit]
        + f"\n\n[...{label} 이 {limit:,}자에서 잘렸습니다. 전체가 아닙니다.]"
    )


def build_github_server(client: GitHubClient) -> Any:
    """주어진 클라이언트를 공유하는 MCP 서버를 만든다.

    클라이언트를 주입받는 이유는 레이트리밋 상태와 커넥션 풀을 파이프라인
    전체가 공유하기 위해서다.
    """

    async def _get(path: str, **kwargs: Any) -> Any:
        return await asyncio.to_thread(
            client._request, "GET", f"{API_ROOT}{path}", **kwargs
        )

    @tool(
        "get_readme",
        "저장소의 README 원문을 가져온다. 프로젝트를 파악하는 출발점.",
        {"repo": Repo},
    )
    async def get_readme(args: dict[str, Any]) -> dict[str, Any]:
        repo = args["repo"]
        body = await asyncio.to_thread(client.get_readme_text, repo)
        if body is None:
            return _text(f"{repo} 에는 README 가 없습니다.")
        return _text(_truncate(body, README_LIMIT, "README"))

    @tool(
        "list_repo_tree",
        "저장소의 디렉터리 트리를 나열한다. 어떤 파일을 읽을지 정하는 데 쓴다.",
        {
            "repo": Repo,
            "path": Annotated[str, "하위 경로. 비우면 최상위"],
        },
    )
    async def list_repo_tree(args: dict[str, Any]) -> dict[str, Any]:
        repo = args["repo"]
        path = (args.get("path") or "").strip("/")
        response = await _get(f"/repos/{repo}/contents/{path}")
        if response.status_code != 200:
            return _text(f"트리 조회 실패 ({response.status_code}): {repo}/{path}")

        payload = response.json()
        if isinstance(payload, dict):
            return _text(f"{path} 는 디렉터리가 아니라 파일입니다. get_file 을 쓰세요.")

        entries = []
        for entry in payload:
            is_dir = entry.get("type") == "dir"
            size = "" if is_dir else f"  ({entry.get('size', 0):,}B)"
            entries.append(f"{'d' if is_dir else 'f'} {entry.get('path')}{size}")

        note = ""
        if len(entries) > TREE_LIMIT:
            note = f"\n[...{len(entries) - TREE_LIMIT}개 더 있음. 잘렸습니다.]"
            entries = entries[:TREE_LIMIT]
        return _text("\n".join(entries) + note)

    @tool(
        "get_file",
        "저장소의 특정 파일 내용을 읽는다. 진입점 코드나 설정 파일 확인용.",
        {"repo": Repo, "path": Annotated[str, "파일 경로"]},
    )
    async def get_file(args: dict[str, Any]) -> dict[str, Any]:
        repo, path = args["repo"], args["path"]
        response = await _get(f"/repos/{repo}/contents/{path}")
        if response.status_code != 200:
            return _text(f"파일 조회 실패 ({response.status_code}): {repo}/{path}")

        payload = response.json()
        if isinstance(payload, list):
            return _text(f"{path} 는 디렉터리입니다. list_repo_tree 를 쓰세요.")
        if payload.get("encoding") != "base64":
            return _text(f"{path} 는 읽을 수 없는 인코딩입니다.")

        try:
            body = base64.b64decode(payload["content"]).decode("utf-8")
        except (UnicodeDecodeError, ValueError):
            return _text(f"{path} 는 바이너리 파일로 보입니다.")
        return _text(_truncate(body, FILE_LIMIT, path))

    @tool(
        "recent_issues",
        "최근 이슈 목록. 사용자들이 실제로 무엇을 겪는지 파악하는 데 쓴다.",
        {
            "repo": Repo,
            "state": Annotated[str, "open | closed | all (기본 open)"],
            "limit": Annotated[int, "가져올 개수 (기본 20, 최대 50)"],
        },
    )
    async def recent_issues(args: dict[str, Any]) -> dict[str, Any]:
        repo = args["repo"]
        limit = min(int(args.get("limit") or 20), 50)
        response = await _get(
            f"/repos/{repo}/issues",
            params={
                "state": args.get("state") or "open",
                "per_page": limit,
                "sort": "created",
                "direction": "desc",
            },
        )
        if response.status_code != 200:
            return _text(f"이슈 조회 실패 ({response.status_code}): {repo}")

        blocks = []
        for item in response.json():
            if "pull_request" in item:  # 이슈 엔드포인트는 PR 도 같이 준다
                continue
            body = (item.get("body") or "").strip()
            blocks.append(
                f"#{item['number']} [{item['state']}] {item['title']}\n"
                f"{body[:ISSUE_BODY_LIMIT]}"
            )
        if not blocks:
            return _text(f"{repo} 에 해당 조건의 이슈가 없습니다.")
        return _text("\n\n---\n\n".join(blocks))

    @tool(
        "recent_releases",
        "최근 릴리스 노트. '왜 지금 뜨는가'의 계기를 찾는 데 가장 유용하다.",
        {"repo": Repo, "limit": Annotated[int, "가져올 개수 (기본 5, 최대 15)"]},
    )
    async def recent_releases(args: dict[str, Any]) -> dict[str, Any]:
        repo = args["repo"]
        limit = min(int(args.get("limit") or 5), 15)
        response = await _get(f"/repos/{repo}/releases", params={"per_page": limit})
        if response.status_code != 200:
            return _text(f"릴리스 조회 실패 ({response.status_code}): {repo}")

        items = response.json()
        if not items:
            return _text(f"{repo} 에는 릴리스가 없습니다.")
        blocks = [
            f"{it.get('tag_name')} ({it.get('published_at')})\n"
            f"{(it.get('body') or '').strip()[:2000]}"
            for it in items
        ]
        return _text("\n\n---\n\n".join(blocks))

    @tool(
        "contributor_stats",
        "컨트리뷰터 수와 상위 기여자. 1인 프로젝트인지 팀 프로젝트인지 판단용.",
        {"repo": Repo},
    )
    async def contributor_stats(args: dict[str, Any]) -> dict[str, Any]:
        repo = args["repo"]
        response = await _get(
            f"/repos/{repo}/contributors", params={"per_page": 30, "anon": "false"}
        )
        if response.status_code != 200:
            return _text(f"컨트리뷰터 조회 실패 ({response.status_code}): {repo}")

        items = response.json()
        if not items:
            return _text(f"{repo} 의 컨트리뷰터 정보를 가져오지 못했습니다.")
        top = ", ".join(
            f"{c.get('login')}({c.get('contributions')})" for c in items[:10]
        )
        more = " (상위 30명까지만 조회)" if len(items) >= 30 else ""
        return _text(f"컨트리뷰터 {len(items)}명{more}\n상위: {top}")

    return create_sdk_mcp_server(
        name=SERVER_NAME,
        version="1.0.0",
        tools=[
            get_readme,
            list_repo_tree,
            get_file,
            recent_issues,
            recent_releases,
            contributor_stats,
        ],
    )
