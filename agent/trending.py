"""GitHub Trending 스크래핑 (비공식 인터페이스).

SPEC.md 3.3 의 원칙을 그대로 구현한다:
1. 셀렉터 실패는 예외가 아니라 빈 결과로 처리한다.
2. 0건이어도 파이프라인을 중단시키지 않는다.
3. ETag 캐시 + 요청 간 지연으로 예의 있게 접근한다.
4. User-Agent 에 프로젝트를 식별할 수 있게 한다.
"""

from __future__ import annotations

import logging
import re
import time

import httpx
from bs4 import BeautifulSoup

from . import paths

log = logging.getLogger(__name__)

TRENDING_URL = "https://github.com/trending"
REQUEST_SPACING_SEC = 3.0
USER_AGENT = "gh-cardnews-agent (+https://github.com/; daily trending fetch)"

_STARS_TODAY = re.compile(r"([\d,]+)\s+stars?\s+today")


def fetch_stars_today(
    languages: list[str], since: str = "daily", timeout: float = 20.0
) -> dict[str, int]:
    """언어별 트렌딩 페이지에서 {full_name: stars_today} 를 모아 반환한다.

    어떤 실패도 위로 전파하지 않는다. 최악의 경우 빈 dict 를 돌려준다.
    """
    cache_dir = paths.CACHE / "trending"
    cache_dir.mkdir(parents=True, exist_ok=True)

    merged: dict[str, int] = {}
    headers = {"User-Agent": USER_AGENT, "Accept": "text/html"}

    with httpx.Client(headers=headers, timeout=timeout, follow_redirects=True) as client:
        for i, language in enumerate(languages):
            if i:
                time.sleep(REQUEST_SPACING_SEC)

            key = language or "_all"
            cache_file = cache_dir / f"{since}-{key}.json"
            cached = paths.read_json(cache_file, default={}) or {}

            url = TRENDING_URL + (f"/{language}" if language else "")
            request_headers = {}
            if cached.get("etag"):
                request_headers["If-None-Match"] = cached["etag"]

            try:
                response = client.get(
                    url, params={"since": since}, headers=request_headers
                )
            except httpx.HTTPError as exc:
                log.warning("트렌딩 요청 실패 (%s): %s", key, exc)
                merged.update(cached.get("stars_today", {}))
                continue

            if response.status_code == 304:
                merged.update(cached.get("stars_today", {}))
                continue
            if response.status_code != 200:
                log.warning("트렌딩 응답 %s (%s)", response.status_code, key)
                merged.update(cached.get("stars_today", {}))
                continue

            parsed = _parse(response.text)
            if not parsed:
                # 파싱 0건은 HTML 구조 변경 신호다. 경고만 남기고 계속 간다.
                log.warning(
                    "트렌딩 파싱 0건 (%s). HTML 구조가 바뀌었을 수 있습니다.", key
                )

            merged.update(parsed)
            paths.write_json(
                cache_file,
                {"etag": response.headers.get("ETag"), "stars_today": parsed},
            )

    return merged


def _parse(html: str) -> dict[str, int]:
    """트렌딩 HTML 에서 {full_name: stars_today} 추출. 실패 시 빈 dict."""
    out: dict[str, int] = {}
    try:
        soup = BeautifulSoup(html, "html.parser")
        for row in soup.select("article.Box-row"):
            anchor = row.select_one("h2 a")
            if anchor is None:
                continue
            href = (anchor.get("href") or "").strip("/")
            if href.count("/") != 1:
                continue

            stars_today = 0
            for span in row.select("span"):
                match = _STARS_TODAY.search(span.get_text(" ", strip=True))
                if match:
                    stars_today = int(match.group(1).replace(",", ""))
                    break
            out[href] = stars_today
    except Exception as exc:  # noqa: BLE001 - 스크래핑 실패는 절대 전파하지 않는다
        log.warning("트렌딩 파싱 중 예외: %s", exc)
        return {}
    return out
