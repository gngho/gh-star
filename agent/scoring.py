"""스코어링. SPEC.md 3.5.

score = w_delta_1d·norm(Δ1d) + w_delta_7d·norm(Δ7d)
      + w_recency·recency_bonus + w_topic_match·topic_score

구성요소가 없으면(None) 그 항을 빼고 남은 가중치로 재정규화한다.
누락을 0 으로 취급하면 "급증하지 않았다"는 잘못된 신호가 되기 때문이다.
"""

from __future__ import annotations

from datetime import datetime, timezone

from .models import RepoFacts


def percentile_normalize(values: list[float | None]) -> list[float | None]:
    """후보 풀 내 백분위(0~1)로 정규화한다. 동점은 평균 순위를 공유한다.

    절대값을 그대로 쓰면 초대형 레포가 항상 이기므로 순위 기반으로 바꾼다.
    """
    present = [i for i, v in enumerate(values) if v is not None]
    out: list[float | None] = [None] * len(values)
    if not present:
        return out
    if len(present) == 1:
        out[present[0]] = 1.0
        return out

    order = sorted(present, key=lambda i: values[i])  # type: ignore[index,arg-type]
    n = len(order)
    i = 0
    while i < n:
        j = i
        while j + 1 < n and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg_rank = (i + j) / 2
        for k in range(i, j + 1):
            out[order[k]] = avg_rank / (n - 1)
        i = j + 1
    return out


def recency_bonus(
    created_at: datetime | None, full_days: int, zero_days: int, now: datetime | None = None
) -> float | None:
    """생성 후 full_days 이내면 1.0, zero_days 에서 0 으로 선형 감쇠."""
    if created_at is None:
        return None
    now = now or datetime.now(timezone.utc)
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    age_days = (now - created_at).total_seconds() / 86400
    if age_days <= full_days:
        return 1.0
    if age_days >= zero_days or zero_days <= full_days:
        return 0.0
    return 1.0 - (age_days - full_days) / (zero_days - full_days)


def topic_score(repo: RepoFacts, topic_filter: list[str]) -> float:
    """topic_filter 와의 일치도. 필터가 비어 있으면 0.5 고정 (SPEC 3.5)."""
    if not topic_filter:
        return 0.5

    wanted = {t.lower() for t in topic_filter}
    repo_topics = {t.lower() for t in repo.topics}
    matched = len(wanted & repo_topics)

    if matched == 0:
        # 토픽 태그를 안 단 레포도 많다. 설명/언어에서 약한 신호를 줍는다.
        haystack = f"{repo.description or ''} {repo.name}".lower()
        if any(w in haystack for w in wanted):
            return 0.4
        return 0.0
    return min(1.0, matched / 2)


MISSING_IMPUTE = 0.5
"""데이터가 없는 구성요소에 넣을 값 = 후보 풀의 중앙값 위치.

누락을 0 으로 두면 "급증하지 않았다"는 잘못된 신호가 되고,
반대로 그 항을 빼고 남은 가중치로 재정규화하면 "증거 없음"이 "완벽한 증거"와
같은 점수를 받아 데이터가 있는 후보를 오히려 밀어낸다. 둘 다 틀렸다.
모르는 값은 '평균적일 것'으로 두는 것이 맞다.
"""


def combine(
    components: dict[str, float | None], weights: dict[str, float]
) -> tuple[float, float]:
    """(점수, 실제 데이터로 뒷받침된 가중치 비율)을 반환한다.

    없는 구성요소는 MISSING_IMPUTE 로 대체하되 가중치는 그대로 적용한다.
    두 번째 반환값은 신뢰도이며, 동점 시 근거가 많은 쪽을 앞세우는 데 쓴다.
    """
    total_weight = sum(weights.get(k, 0.0) for k in components)
    if total_weight <= 0:
        return 0.0, 0.0

    acc = 0.0
    backed = 0.0
    for key, value in components.items():
        weight = weights.get(key, 0.0)
        if value is None:
            acc += weight * MISSING_IMPUTE
        else:
            acc += weight * value
            backed += weight
    return acc / total_weight, backed / total_weight
