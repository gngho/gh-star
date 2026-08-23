"""수집 단계. SPEC.md 3.

소스 A (Search API) + 소스 B (Trending 스크래핑) + 소스 C (자체 스냅샷) 을 합쳐
후보를 점수화한다. 소스 B 는 실패해도 파이프라인을 멈추지 않는다.
"""

from __future__ import annotations

import logging
from datetime import date as date_cls
from datetime import datetime, timedelta, timezone

from . import paths, scoring, trending
from .config import Config, github_token
from .github_api import GitHubClient
from .models import (
    CandidatesFile,
    RepoFacts,
    RepoStats,
    ScoreBreakdown,
    ScoredCandidate,
    SnapshotFile,
    WatchEntry,
    WatchlistFile,
)

log = logging.getLogger(__name__)


def run(config: Config, run_date: str, dry_run: bool = False) -> CandidatesFile:
    collect_cfg = config.collect
    score_cfg = config.score
    topic_filter = config.select.get("topic_filter") or []

    with GitHubClient(github_token()) as client:
        # --- 소스 A: Search API -------------------------------------------
        found = _search_candidates(client, collect_cfg, topic_filter)
        log.info("소스 A (Search API): %d개", len(found))

        # --- 워치리스트 갱신 ----------------------------------------------
        watchlist = _update_watchlist(found, run_date, collect_cfg)
        log.info("워치리스트: %d개", len(watchlist.entries))

        # --- 소스 C: 스냅샷 ------------------------------------------------
        snapshot = _capture_snapshot(client, watchlist, found)
        log.info("스냅샷: %d개 기록", len(snapshot.repos))

    # --- 소스 B: 트렌딩 ---------------------------------------------------
    stars_today = trending.fetch_stars_today(
        collect_cfg.get("trending_languages") or [""],
        since=collect_cfg.get("trending_since", "daily"),
    )
    log.info("소스 B (Trending): %d개", len(stars_today))

    # --- 델타 계산 --------------------------------------------------------
    prev_1d = _load_snapshot(_shift(run_date, -1))
    prev_7d = _load_snapshot(_shift(run_date, -7))

    facts_by_name = {r.full_name: r for r in found}
    # 후보 집합은 검색 결과다. 트렌딩은 후보를 늘리는 소스가 아니라 Δ1d 를 채워주는
    # 소스로만 쓴다 — 토픽 필터를 통과할 레포라면 검색에도 이미 잡히기 때문이다.
    scored = _score_all(
        facts_by_name,
        snapshot=snapshot,
        prev_1d=prev_1d,
        prev_7d=prev_7d,
        stars_today=stars_today,
        score_cfg=score_cfg,
        topic_filter=topic_filter,
    )

    top_n = int(collect_cfg.get("candidates_top_n", 50))
    result = CandidatesFile(
        date=run_date,
        generated_at=datetime.now(timezone.utc),
        sources={
            "search": len(found),
            "trending": len(stars_today),
            "snapshot": len(snapshot.repos),
        },
        watchlist_size=len(watchlist.entries),
        candidates=scored[:top_n],
    )

    if dry_run:
        log.info("[dry-run] 파일을 쓰지 않습니다.")
        return result

    paths.write_json(paths.snapshot_path(run_date), snapshot.model_dump(mode="json"))
    paths.write_json(paths.WATCHLIST, watchlist.model_dump(mode="json"))
    paths.write_json(paths.candidates_path(run_date), result.model_dump(mode="json"))
    _prune_snapshots(int(collect_cfg.get("snapshot_retention_days", 180)), run_date)
    return result


# --------------------------------------------------------------- 소스 A


def _search_candidates(
    client: GitHubClient, collect_cfg: dict, topic_filter: list[str]
) -> list[RepoFacts]:
    min_stars = int(collect_cfg.get("search_min_stars", 200))
    within_days = int(collect_cfg.get("search_pushed_within_days", 14))
    max_pages = int(collect_cfg.get("search_max_pages", 2))
    pushed_since = (date_cls.today() - timedelta(days=within_days)).isoformat()

    base = f"stars:>{min_stars} pushed:>{pushed_since}"
    queries = [f"{base} topic:{t}" for t in topic_filter] or [base]

    merged: dict[str, RepoFacts] = {}
    for query in queries:
        for repo in client.search_repositories(query, max_pages=max_pages):
            if repo.full_name:
                merged[repo.full_name] = repo
    return list(merged.values())


# ---------------------------------------------------------- 워치리스트


def _update_watchlist(
    found: list[RepoFacts], run_date: str, collect_cfg: dict
) -> WatchlistFile:
    """오늘 발견분을 합치고, 오래 미등장한 항목을 정리한다.

    스냅샷 대상을 고정 집합으로 유지하는 것이 이 단계의 목적이다 (SPEC 3.4).
    """
    raw = paths.read_json(paths.WATCHLIST, default=None)
    watchlist = WatchlistFile.model_validate(raw) if raw else WatchlistFile()

    for repo in found:
        entry = watchlist.entries.get(repo.full_name)
        if entry is None:
            watchlist.entries[repo.full_name] = WatchEntry(
                first_seen=run_date, last_seen=run_date, stars=repo.stars
            )
        else:
            entry.last_seen = run_date
            entry.stars = repo.stars

    ttl_days = int(collect_cfg.get("watchlist_ttl_days", 30))
    cutoff = _shift(run_date, -ttl_days)
    watchlist.entries = {
        name: entry
        for name, entry in watchlist.entries.items()
        if entry.last_seen >= cutoff
    }

    max_size = int(collect_cfg.get("watchlist_max", 500))
    if len(watchlist.entries) > max_size:
        # 최근 등장 우선, 동률이면 스타 많은 순으로 남긴다.
        kept = sorted(
            watchlist.entries.items(),
            key=lambda kv: (kv[1].last_seen, kv[1].stars),
            reverse=True,
        )[:max_size]
        watchlist.entries = dict(kept)

    watchlist.updated_at = datetime.now(timezone.utc)
    return watchlist


# --------------------------------------------------------------- 소스 C


def _capture_snapshot(
    client: GitHubClient, watchlist: WatchlistFile, found: list[RepoFacts]
) -> SnapshotFile:
    """워치리스트 전체의 현재 스타 수를 기록한다.

    토큰이 없어 GraphQL 을 못 쓰면, 검색으로 이미 확보한 값만으로 축소 기록한다.
    velocity 정확도는 떨어지지만 파이프라인은 계속 돈다.
    """
    stats = client.batch_repo_stats(watchlist.entries.keys())

    if not stats:
        log.warning("GraphQL 조회 결과가 비어 축소 모드로 스냅샷합니다 (검색 결과만).")
        stats = {r.full_name: RepoStats(stars=r.stars, forks=r.forks) for r in found}
    else:
        # 검색으로 방금 받은 최신값이 있으면 그쪽을 신뢰한다.
        for repo in found:
            stats[repo.full_name] = RepoStats(stars=repo.stars, forks=repo.forks)

    return SnapshotFile(captured_at=datetime.now(timezone.utc), repos=stats)


def _load_snapshot(date_str: str) -> dict[str, RepoStats]:
    raw = paths.read_json(paths.snapshot_path(date_str), default=None)
    if not raw:
        return {}
    try:
        return SnapshotFile.model_validate(raw).repos
    except Exception:  # noqa: BLE001 - 깨진 과거 스냅샷은 없는 셈 친다
        return {}


def _prune_snapshots(retention_days: int, run_date: str) -> None:
    cutoff = _shift(run_date, -retention_days)
    for path in paths.SNAPSHOTS.glob("*.json"):
        if path.stem < cutoff:
            path.unlink(missing_ok=True)


# --------------------------------------------------------------- 스코어링


def _score_all(
    facts_by_name: dict[str, RepoFacts],
    *,
    snapshot: SnapshotFile,
    prev_1d: dict[str, RepoStats],
    prev_7d: dict[str, RepoStats],
    stars_today: dict[str, int],
    score_cfg: dict,
    topic_filter: list[str],
) -> list[ScoredCandidate]:
    names = list(facts_by_name.keys())

    deltas_1d: list[float | None] = []
    deltas_7d: list[float | None] = []
    sources: list[str] = []

    for name in names:
        current = snapshot.repos.get(name)
        current_stars = current.stars if current else facts_by_name[name].stars

        if name in stars_today:
            deltas_1d.append(float(stars_today[name]))
            sources.append("trending")
        elif name in prev_1d:
            deltas_1d.append(float(current_stars - prev_1d[name].stars))
            sources.append("snapshot")
        else:
            deltas_1d.append(None)
            sources.append("none")

        if name in prev_7d:
            deltas_7d.append(float(current_stars - prev_7d[name].stars))
        else:
            deltas_7d.append(None)

    norm_1d = scoring.percentile_normalize(deltas_1d)
    norm_7d = scoring.percentile_normalize(deltas_7d)

    weights = {
        "delta_1d": float(score_cfg.get("w_delta_1d", 0.45)),
        "delta_7d": float(score_cfg.get("w_delta_7d", 0.25)),
        "recency": float(score_cfg.get("w_recency", 0.15)),
        "topic": float(score_cfg.get("w_topic_match", 0.15)),
    }
    full_days = int(score_cfg.get("recency_full_days", 180))
    zero_days = int(score_cfg.get("recency_zero_days", 1095))

    out: list[ScoredCandidate] = []
    for i, name in enumerate(names):
        repo = facts_by_name[name]
        recency = scoring.recency_bonus(repo.created_at, full_days, zero_days)
        topic = scoring.topic_score(repo, topic_filter)

        total, confidence = scoring.combine(
            {
                "delta_1d": norm_1d[i],
                "delta_7d": norm_7d[i],
                "recency": recency,
                "topic": topic,
            },
            weights,
        )

        breakdown = ScoreBreakdown(
            delta_1d=int(deltas_1d[i]) if deltas_1d[i] is not None else None,
            delta_7d=int(deltas_7d[i]) if deltas_7d[i] is not None else None,
            delta_1d_norm=norm_1d[i],
            delta_7d_norm=norm_7d[i],
            recency_bonus=recency,
            topic_score=topic,
            stars_today_source=sources[i],
            confidence=round(confidence, 4),
            total=round(total, 6),
        )
        out.append(ScoredCandidate(repo=repo, score=breakdown, flags=_flags(repo)))

    # 동점이면 근거가 많은 쪽, 그다음 스타 많은 쪽. 정렬이 dict 순서에 좌우되지 않게 한다.
    out.sort(
        key=lambda c: (c.score.total, c.score.confidence, c.repo.stars), reverse=True
    )
    return out


def _flags(repo: RepoFacts) -> list[str]:
    """스팸성 급증 의심 신호 (SPEC 4.3). 탈락시키지 않고 표시만 한다."""
    flags: list[str] = []
    if repo.forks == 0 and repo.stars > 1000:
        flags.append("no_forks_despite_stars")
    elif repo.forks and repo.stars / repo.forks > 500:
        flags.append("star_fork_ratio_outlier")

    if repo.created_at is not None:
        created = repo.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        age_days = (datetime.now(timezone.utc) - created).days
        if age_days <= 7 and repo.stars > 2000:
            flags.append("very_new_high_stars")
    return flags


# ------------------------------------------------------------------ 유틸


def _shift(date_str: str, days: int) -> str:
    return (date_cls.fromisoformat(date_str) + timedelta(days=days)).isoformat()
