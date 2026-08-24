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


# --------------------------------------------------------------- research


class Claim(BaseModel):
    """모든 서술은 근거를 동반한다. SPEC 5.5.

    evidence 는 실제로 조회한 파일 경로, 이슈 번호, 릴리스 태그, URL 이어야 한다.
    빈 근거를 그럴듯한 문장으로 채우는 것이 이 시스템의 가장 흔한 실패 모드다.
    """

    text: str
    evidence: list[str] = Field(default_factory=list)

    @property
    def is_grounded(self) -> bool:
        return bool(self.text.strip()) and bool(self.evidence)


class Feature(BaseModel):
    title: str
    text: str
    evidence: list[str] = Field(default_factory=list)

    @property
    def is_grounded(self) -> bool:
        return bool(self.title.strip()) and bool(self.evidence)


class Quickstart(BaseModel):
    commands: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)

    @property
    def is_grounded(self) -> bool:
        return bool(self.commands) and bool(self.evidence)


class ResearchPayload(BaseModel):
    """에이전트가 채워야 하는 부분. output_format 의 json_schema 로 강제된다."""

    one_liner: str
    problem: Claim
    why_now: Claim
    key_features: list[Feature] = Field(default_factory=list)
    architecture: Claim
    quickstart: Quickstart
    differentiators: Claim
    limitations: Claim


class ResearchMeta(BaseModel):
    license: str | None = None
    language: str | None = None
    stars: int = 0
    delta_1d: int | None = None
    html_url: str = ""


class ResearchFile(BaseModel):
    repo: str
    researched_at: datetime
    payload: ResearchPayload
    meta: ResearchMeta
    ungrounded_fields: list[str] = Field(default_factory=list)
    cost_usd: float | None = None
    num_turns: int | None = None


# ---------------------------------------------------------------- compose


class Card(BaseModel):
    index: int
    role: str  # cover|problem|why_now|feature|architecture|quickstart|fit|outro
    title: str
    body: str = ""
    code: list[str] = Field(default_factory=list)
    footnote: str | None = None

    # 카드 상단 이미지. 렌더 단계에서 채운다 (compose 는 채우지 않는다).
    # 비어 있으면 이미지 영역을 그리지 않고 본문이 카드 전체를 쓴다 —
    # 자리표시자가 그대로 발행되는 것을 막기 위해서다. cover 는 항상 비운다.
    image: str | None = None


class AudioPick(BaseModel):
    """게시물에 얹을 오디오 추천 한 건.

    **곡명과 아티스트를 적지 않는다.** 인스타그램 오디오 라이브러리는 지역과
    계정 유형(비즈니스 계정은 상업용 음원이 상당 부분 막힌다)에 따라 목록이
    다르고, 에이전트는 그 목록을 조회할 방법이 없다. 그래서 "이 곡을 쓰라"는
    없는 사실을 만드는 일이 된다. 대신 앱 검색창에 넣을 검색어를 준다 —
    이건 지시라서 틀릴 여지가 없고, 고르는 건 사람이 한다.
    """

    mood: str = ""
    search: list[str] = Field(default_factory=list)
    why: str = ""


class CardDeckPayload(BaseModel):
    """에이전트가 생성하는 카드 원고."""

    cards: list[Card] = Field(default_factory=list)
    caption: str = ""
    hashtags: list[str] = Field(default_factory=list)
    # 기존 content.json 에는 없는 필드다. 기본값이 있어야 옛 게시물이 그대로 읽힌다.
    audio: list[AudioPick] = Field(default_factory=list)


class CardDeckFile(BaseModel):
    repo: str
    date: str
    generated_at: datetime
    payload: CardDeckPayload
    dropped_roles: list[str] = Field(default_factory=list)
    cost_usd: float | None = None
    attempts: int = 1


class PublishedPost(BaseModel):
    repo: str
    date: str
    media_id: str | None = None
    permalink: str | None = None
    published_at: datetime | None = None
    card_count: int = 0


class PublishedFile(BaseModel):
    posts: list[PublishedPost] = Field(default_factory=list)
