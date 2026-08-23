"""카드 상단 그래픽 검증.

여기서 지키려는 것:
1. 역할마다 다른 그림이 나온다 (같은 그림이 반복되면 캐러셀이 밋밋하다)
2. **그래픽이 카드 본문을 반복하지 않는다** — 실제로 그렇게 만들었다가
   같은 제목이 위아래로 두 번 나왔다
3. 없는 데이터를 그럴듯한 모양으로 채우지 않는다
"""

from datetime import datetime, timezone

import pytest

from agent import imagery, paths
from agent.models import (
    Card, CardDeckFile, CardDeckPayload, Claim, Feature,
    Quickstart, ResearchFile, ResearchMeta, ResearchPayload,
)


def _deck(cards=None) -> CardDeckFile:
    if cards is None:
        roles = [
            "cover", "what_is_it", "problem", "why_now", "feature",
            "feature", "feature", "quickstart", "fit", "outro",
        ]
        cards = [Card(index=i, role=r, title=f"제목{i}") for i, r in enumerate(roles, 1)]
        cards[7].code = ["hermes", "hermes model"]
    return CardDeckFile(
        repo="acme/widget",
        date="2099-01-01",
        generated_at=datetime.now(timezone.utc),
        payload=CardDeckPayload(cards=cards, caption="요약", hashtags=["테스트"]),
    )


def _research(delta=443, stars=1000) -> ResearchFile:
    return ResearchFile(
        repo="acme/widget",
        researched_at=datetime.now(timezone.utc),
        payload=ResearchPayload(
            one_liner="한 줄",
            problem=Claim(text="문제", evidence=["README.md"]),
            why_now=Claim(text="이유", evidence=["README.md"]),
            key_features=[Feature(title="기능", text="설명", evidence=["a.py"])],
            architecture=Claim(text="구조", evidence=["src/"]),
            quickstart=Quickstart(commands=["pip install x"], evidence=["README.md"]),
            differentiators=Claim(text="차별점", evidence=["README.md"]),
            limitations=Claim(text="한계", evidence=["issues/1"]),
        ),
        meta=ResearchMeta(stars=stars, delta_1d=delta),
    )


def _plan(role, deck=None, research=None, **card_kw):
    deck = deck or _deck()
    card = next(c for c in deck.payload.cards if c.role == role)
    for k, v in card_kw.items():
        setattr(card, k, v)
    return imagery._plan(card, deck, research if research is not None else _research())


class TestRoleMapping:
    def test_what_is_it_uses_the_og_card(self):
        assert _plan("what_is_it")["kind"] == "og"

    def test_why_now_shows_real_numbers(self):
        plan = _plan("why_now")
        assert plan["kind"] == "stat"
        assert plan["delta"] == 443 and plan["stars"] == 1000

    def test_quickstart_shows_the_actual_commands(self):
        plan = _plan("quickstart")
        assert plan["kind"] == "terminal"
        assert plan["lines"] == ["hermes", "hermes model"]

    def test_features_are_numbered_in_order(self):
        deck = _deck()
        features = [c for c in deck.payload.cards if c.role == "feature"]
        marks = [imagery._plan(c, deck, _research())["mark"] for c in features]
        assert marks == ["01", "02", "03"]

    def test_fit_shows_only_fixed_headers(self):
        assert _plan("fit")["kind"] == "split"

    def test_problem_gets_a_question_mark(self):
        assert _plan("problem")["mark"] == "?"

    def test_every_role_gets_some_graphic(self):
        deck = _deck()
        for card in deck.payload.cards:
            if card.role in imagery.NO_IMAGE:
                continue
            assert imagery._plan(card, deck, _research()) is not None


class TestNoDuplication:
    """그래픽에 카드 제목을 넣으면 바로 아래에서 같은 말이 반복된다."""

    @pytest.mark.parametrize("role", ["problem", "feature", "fit", "quickstart"])
    def test_graphic_never_carries_the_card_title(self, role):
        plan = _plan(role, title="아주 특징적인 제목입니다")
        assert "아주 특징적인 제목입니다" not in str(plan.values())


class TestHonestData:
    """없는 데이터를 그럴듯한 모양으로 채우면 그건 그래프가 아니라 장식이다."""

    def test_unknown_delta_falls_back_instead_of_showing_zero(self):
        plan = _plan("why_now", research=_research(delta=None))
        assert plan["kind"] != "stat"

    def test_missing_research_falls_back(self):
        deck = _deck()
        card = next(c for c in deck.payload.cards if c.role == "why_now")
        assert imagery._plan(card, deck, None)["kind"] != "stat"

    def test_no_commands_means_no_terminal(self):
        plan = _plan("quickstart", code=[])
        assert plan["kind"] != "terminal"

    def test_sparse_snapshots_yield_no_bars(self, monkeypatch, tmp_path):
        """스냅샷이 며칠 없으면 막대를 아예 그리지 않는다."""
        monkeypatch.setattr(paths, "SNAPSHOTS", tmp_path)
        monkeypatch.setattr(paths, "snapshot_path", lambda d: tmp_path / f"{d}.json")
        assert imagery._star_bars(_deck()) == []

    def test_bars_appear_once_enough_history_exists(self, monkeypatch, tmp_path):
        from datetime import date, timedelta

        base = date(2099, 1, 1)
        stars = 1000
        for offset in range(6, -1, -1):
            stars += 50 + offset * 10
            paths.write_json(
                tmp_path / f"{(base - timedelta(days=offset)).isoformat()}.json",
                {"captured_at": "2099-01-01T00:00:00Z",
                 "repos": {"acme/widget": {"stars": stars, "forks": 1}}},
            )
        monkeypatch.setattr(paths, "snapshot_path", lambda d: tmp_path / f"{d}.json")

        bars = imagery._star_bars(_deck())
        assert len(bars) >= 3
        assert all(0 < b <= 100 for b in bars)


class TestSourcesArePermitted:
    """레포 README 스크린샷·로고는 저작권이 소유자에게 있어 쓰지 않는다."""

    def test_only_github_hosted_endpoint(self):
        assert "githubassets.com" in imagery.OG_URL

    def test_concise_cards_are_excluded(self):
        assert imagery.NO_IMAGE == {"cover", "outro"}


class TestMarkLabels:
    """그래픽 라벨이 카드 eyebrow 와 같으면 같은 문구가 위아래로 두 번 보인다."""

    def test_label_never_repeats_the_eyebrow(self):
        from agent.render import EYEBROWS

        deck = _deck()
        for card in deck.payload.cards:
            if card.role in imagery.NO_IMAGE:
                continue
            plan = imagery._plan(card, deck, _research())
            label = plan.get("label")
            if not label:
                continue
            assert label != EYEBROWS.get(card.role), (
                f"{card.role}: 그래픽 라벨과 eyebrow 가 '{label}' 로 겹칩니다"
            )

    def test_problem_mark_has_no_label(self):
        assert _plan("problem")["label"] == ""

    def test_feature_keeps_its_label(self):
        """벌거벗은 번호에 의미를 주는 유일한 라벨이라 남긴다."""
        assert _plan("feature")["label"] == "핵심 기능"
