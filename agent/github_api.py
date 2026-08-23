"""GitHub REST / GraphQL 클라이언트.

레이트리밋 대응 원칙 (SPEC.md 3.2):
- Search API 는 인증 시 분당 30요청이므로 요청 간 간격을 둔다.
- 워치리스트 스타 조회는 GraphQL 로 100개씩 배치한다 (REST 개별 조회 대비 100배 절약).
- 2차 레이트리밋(403/429)은 Retry-After / X-RateLimit-Reset 을 존중해 대기 후 재시도한다.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Iterable, Iterator

import httpx

from .models import RepoFacts, RepoStats

log = logging.getLogger(__name__)

API_ROOT = "https://api.github.com"
GRAPHQL_URL = f"{API_ROOT}/graphql"

SEARCH_SPACING_SEC = 2.0  # 분당 30요청 제한에 대한 여유
MAX_RETRIES = 3
GRAPHQL_BATCH = 100


class GitHubError(RuntimeError):
    pass


def _alias(index: int) -> str:
    return f"r{index}"


class GitHubClient:
    def __init__(self, token: str | None, timeout: float = 30.0) -> None:
        self.token = token
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "gh-cardnews-agent (+https://github.com/)",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._client = httpx.Client(headers=headers, timeout=timeout)
        self._last_search_at = 0.0

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "GitHubClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------ 공통

    def _sleep_for_rate_limit(self, response: httpx.Response, attempt: int) -> None:
        retry_after = response.headers.get("Retry-After")
        if retry_after and retry_after.isdigit():
            delay = min(int(retry_after), 60)
        else:
            reset = response.headers.get("X-RateLimit-Reset")
            delay = 0.0
            if reset and reset.isdigit():
                delay = max(0.0, int(reset) - time.time())
            delay = min(delay, 60.0) or (2.0**attempt)
        log.warning("레이트리밋 대기 %.1fs (시도 %d)", delay, attempt + 1)
        time.sleep(delay)

    def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        for attempt in range(MAX_RETRIES):
            response = self._client.request(method, url, **kwargs)
            if response.status_code in (403, 429):
                remaining = response.headers.get("X-RateLimit-Remaining")
                if remaining == "0" or "rate limit" in response.text.lower():
                    self._sleep_for_rate_limit(response, attempt)
                    continue
            if response.status_code >= 500:
                time.sleep(2.0**attempt)
                continue
            return response
        return response

    # ------------------------------------------------------------ Search API

    def search_repositories(
        self, query: str, max_pages: int = 2, per_page: int = 100
    ) -> list[RepoFacts]:
        """검색 결과를 RepoFacts 로 변환해 반환한다. 실패해도 예외를 던지지 않는다."""
        results: list[RepoFacts] = []
        for page in range(1, max_pages + 1):
            elapsed = time.monotonic() - self._last_search_at
            if elapsed < SEARCH_SPACING_SEC:
                time.sleep(SEARCH_SPACING_SEC - elapsed)
            self._last_search_at = time.monotonic()

            response = self._request(
                "GET",
                f"{API_ROOT}/search/repositories",
                params={
                    "q": query,
                    "sort": "stars",
                    "order": "desc",
                    "per_page": per_page,
                    "page": page,
                },
            )
            if response.status_code != 200:
                log.warning(
                    "검색 실패 (%s): %s | q=%s",
                    response.status_code,
                    response.text[:200],
                    query,
                )
                break

            items = response.json().get("items", [])
            results.extend(_facts_from_search_item(item) for item in items)
            if len(items) < per_page:
                break
        return results

    # --------------------------------------------------------------- GraphQL

    def batch_repo_stats(self, full_names: Iterable[str]) -> dict[str, RepoStats]:
        """워치리스트 전체의 현재 스타 수를 배치 조회한다.

        토큰이 없으면 GraphQL 을 쓸 수 없으므로 빈 dict 를 반환한다.
        호출 측이 이를 감지해 축소 모드로 진행한다.
        """
        names = list(full_names)
        if not names:
            return {}
        if not self.token:
            log.warning("GITHUB_TOKEN 이 없어 GraphQL 배치 조회를 건너뜁니다.")
            return {}

        stats: dict[str, RepoStats] = {}
        for chunk in _chunks(names, GRAPHQL_BATCH):
            stats.update(self._graphql_chunk(chunk))
        return stats

    def _graphql_chunk(self, chunk: list[str]) -> dict[str, RepoStats]:
        parts = []
        alias_map: dict[str, str] = {}
        for i, full_name in enumerate(chunk):
            if "/" not in full_name:
                continue
            owner, name = full_name.split("/", 1)
            alias = _alias(i)
            alias_map[alias] = full_name
            parts.append(
                f'{alias}: repository(owner: {_gql_str(owner)}, name: {_gql_str(name)}) '
                "{ nameWithOwner stargazerCount forkCount }"
            )
        if not parts:
            return {}

        query = "query {\n  " + "\n  ".join(parts) + "\n}"
        response = self._request("POST", GRAPHQL_URL, json={"query": query})
        if response.status_code != 200:
            log.warning("GraphQL 실패 (%s): %s", response.status_code, response.text[:200])
            return {}

        payload = response.json()
        # 삭제·비공개 전환된 레포는 해당 alias 만 null 로 오고 errors 에 기록된다.
        # 부분 실패이므로 전체를 버리지 않고 살아있는 것만 취한다.
        for err in payload.get("errors", []) or []:
            log.debug("GraphQL 부분 오류: %s", err.get("message"))

        out: dict[str, RepoStats] = {}
        for alias, node in (payload.get("data") or {}).items():
            if not node:
                continue
            full_name = node.get("nameWithOwner") or alias_map.get(alias)
            if not full_name:
                continue
            out[full_name] = RepoStats(
                stars=node.get("stargazerCount", 0),
                forks=node.get("forkCount", 0),
            )
        return out

    # ------------------------------------------------------------- REST 단건

    def get_readme_text(self, full_name: str) -> str | None:
        """README 원문. 없으면 None."""
        response = self._request(
            "GET",
            f"{API_ROOT}/repos/{full_name}/readme",
            headers={"Accept": "application/vnd.github.raw+json"},
        )
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            log.warning("README 조회 실패 (%s): %s", response.status_code, full_name)
            return None
        return response.text

    def get_repo(self, full_name: str) -> RepoFacts | None:
        response = self._request("GET", f"{API_ROOT}/repos/{full_name}")
        if response.status_code != 200:
            return None
        return _facts_from_search_item(response.json())

    def rate_limit_remaining(self) -> dict[str, int]:
        response = self._request("GET", f"{API_ROOT}/rate_limit")
        if response.status_code != 200:
            return {}
        resources = response.json().get("resources", {})
        return {k: v.get("remaining", 0) for k, v in resources.items()}


# ------------------------------------------------------------------ 헬퍼


def _gql_str(value: str) -> str:
    """GraphQL 문자열 리터럴로 안전하게 인용한다."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _chunks(items: list[str], size: int) -> Iterator[list[str]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _facts_from_search_item(item: dict[str, Any]) -> RepoFacts:
    owner = (item.get("owner") or {}).get("login", "")
    license_info = item.get("license") or {}
    spdx = license_info.get("spdx_id")
    if spdx in ("NOASSERTION", "", None):
        spdx = None
    return RepoFacts(
        full_name=item.get("full_name", ""),
        owner=owner,
        name=item.get("name", ""),
        description=item.get("description"),
        language=item.get("language"),
        topics=item.get("topics") or [],
        stars=item.get("stargazers_count", 0),
        forks=item.get("forks_count", 0),
        open_issues=item.get("open_issues_count", 0),
        archived=bool(item.get("archived")),
        fork=bool(item.get("fork")),
        license_spdx=spdx,
        created_at=item.get("created_at"),
        pushed_at=item.get("pushed_at"),
        html_url=item.get("html_url", ""),
    )
