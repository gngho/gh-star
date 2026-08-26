"""선정 단계. SPEC.md 4.

핵심 원칙: 검증을 전수로 하지 않는다.
1) 추가 API 호출 없이 가능한 필터를 먼저 전부 적용하고
2) 점수순으로 정렬한 뒤
3) 위에서부터 README 를 조회해 검증하고, primary + backup 이 차면 멈춘다.

이 순서는 API 호출 절약이자 비용 방어선이다. 부적합 레포를 research 에 넘겨
40턴을 태운 뒤에야 실패를 알아채는 상황을 막는다.
"""

from __future__ import annotations

import logging
import re
from datetime import date as date_cls
from datetime import datetime, timedelta, timezone

from . import paths
from .config import Config, github_token
from .github_api import GitHubClient
from .models import (
    CandidatesFile,
    RejectionSummary,
    RepoFacts,
    ScoredCandidate,
    SelectedRepo,
    SelectionFile,
)

log = logging.getLogger(__name__)

EXCLUDED_TOPICS = {
    "awesome",
    "awesome-list",
    "tutorial",
    "tutorials",
    "roadmap",
    "interview",
    "interview-questions",
    "course",
    "curriculum",
    "learning-resources",
    "cheatsheet",
    "books",
}

EXCLUDED_NAME_PATTERNS = (
    re.compile(r"^awesome[-_]", re.I),
    re.compile(r"[-_]awesome$", re.I),
    re.compile(r"(tutorial|roadmap|cheat[-_]?sheet|interview|курс)", re.I),
    re.compile(r"(free[-_]?)?(books|resources|papers)$", re.I),
)

# README 가 링크 목록 위주인지 판단하는 임계값
LINK_HEAVY_RATIO = 0.35


class NoCandidateError(RuntimeError):
    """필터를 통과한 후보가 없음. 당일 스킵 사유가 된다 (SPEC 11)."""


def run(config: Config, run_date: str, dry_run: bool = False) -> SelectionFile:
    select_cfg = config.select

    raw = paths.read_json(paths.candidates_path(run_date), default=None)
    if not raw:
        raise FileNotFoundError(
            f"후보 파일이 없습니다: {paths.candidates_path(run_date)}. 먼저 collect 를 실행하세요."
        )
    candidates_file = CandidatesFile.model_validate(raw)

    published = recently_published(int(select_cfg.get("republish_block_days", 90)))
    blocklist = set(paths.read_json(paths.BLOCKLIST, default={}).get("repos", []))

    rejected: list[RejectionSummary] = []
    survivors: list[ScoredCandidate] = []

    # --- 1단계: 추가 호출이 필요 없는 필터 ---------------------------------
    for candidate in candidates_file.candidates:
        rule = _cheap_reject(candidate.repo, select_cfg, published, blocklist)
        if rule:
            rejected.append(
                RejectionSummary(full_name=candidate.repo.full_name, rule=rule)
            )
            continue
        survivors.append(candidate)

    log.info(
        "1단계 필터: %d개 중 %d개 생존 (추가 API 호출 0회)",
        len(candidates_file.candidates),
        len(survivors),
    )

    # --- 2단계: 위에서부터 README 검증 -------------------------------------
    survivors.sort(key=lambda c: c.score.total, reverse=True)
    needed = 1 + int(select_cfg.get("backup_count", 2))
    max_calls = int(select_cfg.get("max_validation_calls", 15))
    min_readme = int(select_cfg.get("min_readme_chars", 500))

    picked: list[SelectedRepo] = []
    calls = 0

    with GitHubClient(github_token()) as client:
        for candidate in survivors:
            if len(picked) >= needed or calls >= max_calls:
                break

            calls += 1
            readme = client.get_readme_text(candidate.repo.full_name)
            rule = _readme_reject(readme, min_readme)
            if rule:
                rejected.append(
                    RejectionSummary(full_name=candidate.repo.full_name, rule=rule)
                )
                continue

            picked.append(
                SelectedRepo(
                    full_name=candidate.repo.full_name,
                    score=candidate.score.total,
                    reason=_reason(candidate),
                    flags=candidate.flags,
                    repo=candidate.repo,
                )
            )

    log.info("2단계 검증: %d회 호출로 %d개 확보", calls, len(picked))

    if not picked:
        raise NoCandidateError(
            f"{run_date}: 필터를 통과한 후보가 없습니다. 당일 스킵합니다."
        )

    result = SelectionFile(
        date=run_date,
        generated_at=datetime.now(timezone.utc),
        primary=picked[0],
        backups=picked[1:],
        validation_calls=calls,
        rejected=rejected,
    )

    if dry_run:
        log.info("[dry-run] 파일을 쓰지 않습니다.")
        return result

    paths.write_json(paths.selection_path(run_date), result.model_dump(mode="json"))
    return result


# --------------------------------------------------------- 1단계 필터


def _cheap_reject(
    repo: RepoFacts, select_cfg: dict, published: set[str], blocklist: set[str]
) -> str | None:
    """Search API 응답 필드만으로 판정한다. 추가 호출이 없다."""
    if repo.stars < int(select_cfg.get("min_stars", 500)):
        return "min_stars"
    if repo.archived:
        return "archived"
    if repo.fork:
        return "fork"
    if not repo.license_spdx:
        return "no_license"
    if repo.full_name in published:
        return "recently_published"
    if repo.full_name in blocklist:
        return "blocklisted"

    stale_days = int(select_cfg.get("stale_push_days", 90))
    if repo.pushed_at is not None:
        pushed = repo.pushed_at
        if pushed.tzinfo is None:
            pushed = pushed.replace(tzinfo=timezone.utc)
        if (datetime.now(timezone.utc) - pushed).days > stale_days:
            return "stale"

    if EXCLUDED_TOPICS & {t.lower() for t in repo.topics}:
        return "excluded_topic"
    if any(p.search(repo.name) for p in EXCLUDED_NAME_PATTERNS):
        return "excluded_name"
    return None


# --------------------------------------------------------- 2단계 검증


def _readme_reject(readme: str | None, min_chars: int) -> str | None:
    if readme is None:
        return "no_readme"
    if len(readme.strip()) < min_chars:
        return "readme_too_short"
    if _is_link_list(readme):
        return "link_list_readme"
    return None


def _is_link_list(readme: str) -> bool:
    """awesome-list 류 감지: 본문 줄 중 링크 항목 비율이 높으면 참."""
    lines = [ln.strip() for ln in readme.splitlines() if ln.strip()]
    if len(lines) < 20:
        return False
    link_lines = sum(
        1 for ln in lines if re.match(r"^[-*+]\s*\[.+\]\(.+\)", ln) or ln.startswith("|")
    )
    return link_lines / len(lines) >= LINK_HEAVY_RATIO


# ------------------------------------------------------------------ 유틸


def recently_published(block_days: int) -> set[str]:
    """최근 block_days 안에 발행한 레포 full_name 집합.

    select 뿐 아니라 research 도 쓴다. CI 가 만든 선정 파일이 낡은 이력으로
    골랐을 수 있어, 로컬 파이프라인이 조사 직전에 한 번 더 대조한다.
    """
    data = paths.read_json(paths.PUBLISHED, default={}) or {}
    cutoff = (date_cls.today() - timedelta(days=block_days)).isoformat()
    out: set[str] = set()
    for post in data.get("posts", []):
        if str(post.get("date", "")) >= cutoff:
            repo = post.get("repo")
            if repo:
                out.add(repo)
    return out


def _reason(candidate: ScoredCandidate) -> str:
    s = candidate.score
    bits = [f"score={s.total:.3f}"]
    if s.delta_1d is not None:
        bits.append(f"Δ1d={s.delta_1d:+d}({s.stars_today_source})")
    if s.delta_7d is not None:
        bits.append(f"Δ7d={s.delta_7d:+d}")
    if s.recency_bonus is not None:
        bits.append(f"recency={s.recency_bonus:.2f}")
    bits.append(f"topic={s.topic_score:.2f}" if s.topic_score is not None else "topic=–")
    return ", ".join(bits)
