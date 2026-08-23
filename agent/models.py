"""파이프라인 단계 간 파일 인터페이스의 Pydantic 스키마. SPEC.md 2.2 참조."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class RepoFacts(BaseModel):
    """Search API 응답만으로 채울 수 있는 사실들. 추가 호출이 필요 없다."""

    full_name: str
    owner: str
    name: str
    description: str | None = None
    language: str | None = None
    topics: list[str] = Field(default_factory=list)
    stars: int = 0
    forks: int = 0
    open_issues: int = 0
    archived: bool = False
    fork: bool = False
    license_spdx: str | None = None
    created_at: datetime | None = None
    pushed_at: datetime | None = None
    html_url: str = ""


class ScoreBreakdown(BaseModel):
    """선정 근거 추적용. 모든 구성요소를 남긴다."""

    delta_1d: int | None = None
    delta_7d: int | None = None
    delta_1d_norm: float | None = None
    delta_7d_norm: float | None = None
    recency_bonus: float | None = None
    topic_score: float | None = None
    stars_today_source: str = "snapshot"  # "trending" | "snapshot" | "none"
    confidence: float = 0.0  # 실제 데이터로 뒷받침된 가중치 비율 (0~1)
    total: float = 0.0


class ScoredCandidate(BaseModel):
    repo: RepoFacts
    score: ScoreBreakdown
    flags: list[str] = Field(default_factory=list)


class CandidatesFile(BaseModel):
    date: str
    generated_at: datetime
    sources: dict[str, int] = Field(default_factory=dict)
    watchlist_size: int = 0
    candidates: list[ScoredCandidate] = Field(default_factory=list)


class RepoStats(BaseModel):
    stars: int
    forks: int = 0


class SnapshotFile(BaseModel):
    captured_at: datetime
    repos: dict[str, RepoStats] = Field(default_factory=dict)


class WatchEntry(BaseModel):
    first_seen: str
    last_seen: str
    stars: int = 0


class WatchlistFile(BaseModel):
    updated_at: datetime | None = None
    entries: dict[str, WatchEntry] = Field(default_factory=dict)


class SelectedRepo(BaseModel):
    full_name: str
    score: float
    reason: str = ""
    flags: list[str] = Field(default_factory=list)
    repo: RepoFacts


class RejectionSummary(BaseModel):
    full_name: str
    rule: str


class SelectionFile(BaseModel):
    date: str
    generated_at: datetime
    primary: SelectedRepo | None = None
    backups: list[SelectedRepo] = Field(default_factory=list)
    validation_calls: int = 0
    rejected: list[RejectionSummary] = Field(default_factory=list)


class PublishedPost(BaseModel):
    repo: str
    date: str
    media_id: str | None = None
    permalink: str | None = None
    published_at: datetime | None = None
    card_count: int = 0


class PublishedFile(BaseModel):
    posts: list[PublishedPost] = Field(default_factory=list)
