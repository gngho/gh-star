from datetime import datetime, timedelta, timezone

from agent import scoring
from agent.models import RepoFacts

WEIGHTS = {"delta_1d": 0.45, "delta_7d": 0.25, "recency": 0.15, "topic": 0.15}


def _repo(**kwargs) -> RepoFacts:
    base = dict(full_name="o/r", owner="o", name="r")
    base.update(kwargs)
    return RepoFacts(**base)


class TestPercentileNormalize:
    def test_ranks_to_zero_one(self):
        assert scoring.percentile_normalize([10.0, 20.0, 30.0]) == [0.0, 0.5, 1.0]

    def test_missing_stays_missing(self):
        out = scoring.percentile_normalize([10.0, None, 30.0])
        assert out == [0.0, None, 1.0]

    def test_ties_share_average_rank(self):
        out = scoring.percentile_normalize([5.0, 5.0, 9.0])
        assert out[0] == out[1] == 0.25
        assert out[2] == 1.0

    def test_single_value(self):
        assert scoring.percentile_normalize([None, 7.0]) == [None, 1.0]

    def test_all_missing(self):
        assert scoring.percentile_normalize([None, None]) == [None, None]

    def test_absolute_size_does_not_dominate(self):
        """거대 레포의 절대 증가량이 아니라 순위만 반영되어야 한다."""
        out = scoring.percentile_normalize([1.0, 2.0, 100000.0])
        assert out == [0.0, 0.5, 1.0]


class TestCombine:
    def test_missing_does_not_beat_full_evidence(self):
        """증거가 전혀 없는 후보가 최상위 점수를 받으면 안 된다.

        재정규화 방식의 회귀 방지: 예전 구현은 없는 항을 빼고 남은 가중치로
        나눠서, 데이터 없는 후보가 1.0 을 받아 실제 급등 레포를 밀어냈다.
        """
        no_data, no_conf = scoring.combine(
            {"delta_1d": None, "delta_7d": None, "recency": 1.0, "topic": 1.0}, WEIGHTS
        )
        strong, strong_conf = scoring.combine(
            {"delta_1d": 0.9, "delta_7d": 0.9, "recency": 1.0, "topic": 1.0}, WEIGHTS
        )
        assert strong > no_data
        assert strong_conf > no_conf

    def test_missing_is_imputed_at_median(self):
        score, confidence = scoring.combine(
            {"delta_1d": None, "delta_7d": None, "recency": 0.5, "topic": 0.5}, WEIGHTS
        )
        assert score == 0.5
        assert confidence == 0.30  # recency + topic 만 실측

    def test_confidence_is_one_when_all_present(self):
        _, confidence = scoring.combine(
            {"delta_1d": 0.1, "delta_7d": 0.2, "recency": 0.3, "topic": 0.4}, WEIGHTS
        )
        assert confidence == 1.0

    def test_missing_is_not_treated_as_zero(self):
        """누락을 0 으로 보면 '급증하지 않았다'는 잘못된 신호가 된다."""
        missing, _ = scoring.combine({"delta_1d": None}, {"delta_1d": 1.0})
        zero, _ = scoring.combine({"delta_1d": 0.0}, {"delta_1d": 1.0})
        assert missing > zero


class TestRecencyBonus:
    def test_new_repo_gets_full_bonus(self):
        created = datetime.now(timezone.utc) - timedelta(days=30)
        assert scoring.recency_bonus(created, 180, 1095) == 1.0

    def test_old_repo_gets_zero(self):
        created = datetime.now(timezone.utc) - timedelta(days=2000)
        assert scoring.recency_bonus(created, 180, 1095) == 0.0

    def test_decays_between(self):
        created = datetime.now(timezone.utc) - timedelta(days=600)
        value = scoring.recency_bonus(created, 180, 1095)
        assert 0.0 < value < 1.0

    def test_naive_datetime_is_handled(self):
        created = datetime.now() - timedelta(days=10)
        assert scoring.recency_bonus(created, 180, 1095) == 1.0

    def test_missing_created_at(self):
        assert scoring.recency_bonus(None, 180, 1095) is None


class TestTopicScore:
    FILTER = ["ai", "llm", "agent", "machine-learning"]

    def test_two_matches_is_full(self):
        repo = _repo(topics=["ai", "llm", "python"])
        assert scoring.topic_score(repo, self.FILTER) == 1.0

    def test_one_match_is_half(self):
        repo = _repo(topics=["llm"])
        assert scoring.topic_score(repo, self.FILTER) == 0.5

    def test_no_topics_falls_back_to_description(self):
        repo = _repo(topics=[], description="An LLM agent framework")
        assert scoring.topic_score(repo, self.FILTER) == 0.4

    def test_unrelated_repo_scores_zero(self):
        repo = _repo(topics=["css"], description="A stylesheet toolkit")
        assert scoring.topic_score(repo, self.FILTER) == 0.0

    def test_empty_filter_is_neutral(self):
        repo = _repo(topics=["css"])
        assert scoring.topic_score(repo, []) == 0.5

    def test_matching_is_case_insensitive(self):
        repo = _repo(topics=["AI", "LLM"])
        assert scoring.topic_score(repo, self.FILTER) == 1.0
