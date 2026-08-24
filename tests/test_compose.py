from datetime import datetime, timezone

import pytest

from agent import compose as compose_mod
from agent.models import (
    AudioPick,
    Card,
    CardDeckPayload,
    Claim,
    Feature,
    Quickstart,
    ResearchFile,
    ResearchMeta,
    ResearchPayload,
)
from agent.schema import json_schema_for

MAX_TITLE, MAX_BODY, MAX_TAGS = 24, 90, 30


def _audio(
    mood="차분한 배경음",
    search=("lo-fi beat", "chill study"),
    why="담담한 도구 소개라 배경음이 맞습니다",
) -> AudioPick:
    return AudioPick(mood=mood, search=list(search), why=why)


def _three_audio() -> list[AudioPick]:
    return [
        _audio("차분한 배경음"),
        _audio("가볍고 통통 튀는", ("bright synth", "upbeat pop")),
        _audio("담백한 신스", ("minimal synth", "ambient loop")),
    ]


def _claim(text="본문", evidence=("README.md",)) -> Claim:
    return Claim(text=text, evidence=list(evidence))


def _research(**overrides) -> ResearchFile:
    payload = ResearchPayload(
        one_liner="한 줄",
        problem=_claim(),
        why_now=_claim(),
        key_features=[
            Feature(title=f"기능{i}", text="설명", evidence=["src/a.py"])
            for i in range(3)
        ],
        architecture=_claim(),
        quickstart=Quickstart(commands=["pip install x"], evidence=["README.md"]),
        differentiators=_claim(),
        limitations=_claim(),
    )
    for key, value in overrides.items():
        setattr(payload, key, value)
    return ResearchFile(
        repo="o/r",
        researched_at=datetime.now(timezone.utc),
        payload=payload,
        meta=ResearchMeta(stars=1000),
    )


class TestPlanRoles:
    def test_full_deck_is_ten_cards(self):
        roles, dropped = compose_mod._plan_roles(_research())
        assert len(roles) == 10
        assert dropped == []
        assert roles[0] == "cover" and roles[-1] == "outro"
        assert roles.count("feature") == 3

    def test_what_is_it_comes_second(self):
        """입문자가 여기서 못 알아들으면 나머지 아홉 장을 안 읽는다."""
        roles, _ = compose_mod._plan_roles(_research())
        assert roles[1] == "what_is_it"

    def test_architecture_is_not_a_card(self):
        """90자로 '어떻게 동작하나'를 쓰면 정확하거나 쉽거나 하나를 포기하게 된다."""
        roles, _ = compose_mod._plan_roles(_research())
        assert "architecture" not in roles

    def test_two_features_yields_nine_cards(self):
        research = _research()
        research.payload.key_features = research.payload.key_features[:2]
        roles, _ = compose_mod._plan_roles(research)
        assert len(roles) == 9
        assert roles.count("feature") == 2

    def test_ungrounded_field_is_dropped_not_invented(self):
        """근거 없는 필드는 카드로 만들지 않는다. 지어내지 않기 위해서다."""
        research = _research(quickstart=Quickstart(commands=[], evidence=[]))
        roles, dropped = compose_mod._plan_roles(research)
        assert "quickstart" in dropped
        assert "quickstart" not in roles

    def test_empty_text_counts_as_ungrounded(self):
        research = _research(problem=Claim(text="   ", evidence=["README.md"]))
        _, dropped = compose_mod._plan_roles(research)
        assert "problem" in dropped

    def test_fit_survives_if_either_half_is_grounded(self):
        research = _research(limitations=Claim(text="", evidence=[]))
        roles, dropped = compose_mod._plan_roles(research)
        assert "fit" in roles and "fit" not in dropped


class TestValidate:
    ROLES = ["cover", "feature", "quickstart", "outro"]

    def _deck(self, **overrides) -> CardDeckPayload:
        cards = [
            Card(index=i, role=role, title=f"제목{i}", body="본문")
            for i, role in enumerate(self.ROLES, 1)
        ]
        cards[2].code = ["pip install x"]
        deck = CardDeckPayload(
            cards=cards, caption="요약", hashtags=["깃허브"], audio=_three_audio()
        )
        for key, value in overrides.items():
            setattr(deck, key, value)
        return deck

    def _check(self, deck):
        return compose_mod._validate(deck, self.ROLES, MAX_TITLE, MAX_BODY, MAX_TAGS)

    def test_valid_deck_passes(self):
        assert self._check(self._deck()) == []

    def test_long_title_is_reported_with_the_offending_text(self):
        deck = self._deck()
        deck.cards[0].title = "가" * (MAX_TITLE + 1)
        problems = self._check(deck)
        assert len(problems) == 1
        # 모델에 그대로 돌려주므로 무엇이 문제인지 문장에 담겨야 한다
        assert str(MAX_TITLE + 1) in problems[0] and "가" in problems[0]

    def test_long_body_is_reported(self):
        deck = self._deck()
        deck.cards[1].body = "나" * (MAX_BODY + 1)
        assert any("body" in p for p in self._check(deck))

    def test_wrong_card_count(self):
        deck = self._deck()
        deck.cards = deck.cards[:2]
        assert any("정확히" in p for p in self._check(deck))

    def test_role_order_must_match(self):
        deck = self._deck()
        deck.cards[1].role = "outro"
        assert any("role" in p for p in self._check(deck))

    def test_code_outside_quickstart_is_rejected(self):
        deck = self._deck()
        deck.cards[1].code = ["some code"]
        assert any("quickstart" in p for p in self._check(deck))

    def test_long_code_line_is_rejected(self):
        deck = self._deck()
        deck.cards[2].code = ["x" * 60]
        assert any("52자" in p for p in self._check(deck))

    def test_too_many_code_lines(self):
        deck = self._deck()
        deck.cards[2].code = ["echo a", "echo b", "echo c", "echo d"]
        assert any("줄입니다" in p for p in self._check(deck))

    def test_too_many_hashtags(self):
        deck = self._deck(hashtags=[f"t{i}" for i in range(MAX_TAGS + 1)])
        assert any("해시태그" in p for p in self._check(deck))

    def test_hash_in_caption_prose_is_allowed(self):
        """'C#', '이슈 #42' 는 정당한 쓰임이다. 모두 막으면 오탐이 된다."""
        deck = self._deck(caption="C# 지원과 이슈 #42 수정이 핵심입니다.")
        assert self._check(deck) == []

    def test_empty_caption_is_rejected(self):
        deck = self._deck(caption="   ")
        assert any("caption" in p for p in self._check(deck))

    def test_caption_over_limit(self):
        deck = self._deck(caption="가" * (compose_mod.CAPTION_LIMIT + 1))
        assert any(str(compose_mod.CAPTION_LIMIT) in p for p in self._check(deck))


class TestContinuationDetection:
    """한 명령을 쪼갠 조각은 내용 오류다.

    카드는 code 줄마다 '$' 프롬프트를 붙이므로, URL 중간에서 끊긴 조각이 들어가면
    별개의 명령 두 개처럼 읽힌다. 실제로 첫 렌더에서 이 문제가 나왔다.
    """

    @pytest.mark.parametrize(
        "line",
        [
            ".nousresearch.com/install.sh | bash",  # 실제로 발생한 사례
            "| bash",
            "&& hermes doctor",
            "--verbose --model opus",
            "/usr/local/bin/tool",
            "example.com/install.sh",
        ],
    )
    def test_fragments_are_detected(self, line):
        assert compose_mod._looks_like_continuation(line) is True

    @pytest.mark.parametrize(
        "line",
        [
            "pip install hermes-agent",
            "curl -fsSL https://x.sh | bash",  # 완전한 한 줄이면 파이프도 정상
            "hermes && hermes model",
            "npx create-app my-app",
            "docker run -it ubuntu",
            "uv pip install -e '.[all]'",
        ],
    )
    def test_complete_commands_pass(self, line):
        assert compose_mod._looks_like_continuation(line) is False

    def test_fragment_is_reported_as_violation(self):
        deck = CardDeckPayload(
            cards=[
                Card(index=1, role="quickstart", title="설치", body="",
                     code=["curl -fsSL https://hermes-agent",
                           ".nousresearch.com/install.sh | bash"]),
            ],
            caption="요약",
            hashtags=[],
        )
        problems = compose_mod._validate(
            deck, ["quickstart"], MAX_TITLE, MAX_BODY, MAX_TAGS
        )
        assert any("완전한 명령" in p for p in problems)



class TestRepair:
    """의미를 바꾸지 않는 위반은 실패시키지 말고 고친다."""

    def _deck(self, **kw) -> CardDeckPayload:
        base = dict(cards=[], caption="요약", hashtags=["깃허브"])
        base.update(kw)
        return CardDeckPayload(**base)

    def test_trailing_hashtag_block_moves_to_array(self):
        deck = self._deck(caption="요약 문장입니다.\n\n#깃허브 #오픈소스", hashtags=[])
        notes = compose_mod.repair(deck, MAX_TAGS)
        assert deck.caption == "요약 문장입니다."
        assert deck.hashtags == ["깃허브", "오픈소스"]
        assert notes

    def test_inline_hash_in_prose_is_left_alone(self):
        deck = self._deck(caption="C# 과 이슈 #42 를 다룹니다.", hashtags=["깃허브"])
        compose_mod.repair(deck, MAX_TAGS)
        assert deck.caption == "C# 과 이슈 #42 를 다룹니다."
        assert deck.hashtags == ["깃허브"]

    def test_hash_prefix_is_stripped(self):
        deck = self._deck(hashtags=["#깃허브", "오픈소스"])
        compose_mod.repair(deck, MAX_TAGS)
        assert deck.hashtags == ["깃허브", "오픈소스"]

    def test_duplicates_are_removed_case_insensitively(self):
        deck = self._deck(hashtags=["AI", "ai", "#AI", "LLM"])
        compose_mod.repair(deck, MAX_TAGS)
        assert deck.hashtags == ["AI", "LLM"]

    def test_excess_tags_are_trimmed(self):
        deck = self._deck(hashtags=[f"t{i}" for i in range(MAX_TAGS + 5)])
        compose_mod.repair(deck, MAX_TAGS)
        assert len(deck.hashtags) == MAX_TAGS

    def test_repaired_deck_passes_validation(self):
        """보정 후에는 검증을 통과해야 한다 — 이게 재시도를 아끼는 지점이다."""
        deck = CardDeckPayload(
            cards=[Card(index=1, role="cover", title="제목", body="본문")],
            caption="요약입니다.\n\n#깃허브 #ai #AI",
            hashtags=[],
            audio=_three_audio(),
        )
        compose_mod.repair(deck, MAX_TAGS)
        assert compose_mod._validate(deck, ["cover"], MAX_TITLE, MAX_BODY, MAX_TAGS) == []

    def test_clean_deck_needs_no_repair(self):
        deck = self._deck(caption="요약", hashtags=["깃허브", "오픈소스"])
        assert compose_mod.repair(deck, MAX_TAGS) == []


class TestRenderCaption:
    def test_hashtags_are_appended_with_hash(self):
        deck = CardDeckPayload(cards=[], caption="요약", hashtags=["깃허브", "오픈소스"])
        assert compose_mod._render_caption(deck) == "요약\n\n#깃허브 #오픈소스\n"


class TestAudioInCaption:
    """오디오 메모는 캡션 파일에 들어가지만 캡션의 일부는 아니다."""

    def _rendered(self):
        deck = CardDeckPayload(
            cards=[], caption="요약", hashtags=["깃허브"], audio=_three_audio()
        )
        return compose_mod._render_caption(deck)

    def test_old_posts_without_audio_stay_fully_pasteable(self):
        """audio 가 없던 시절의 content.json 은 파일 전체가 캡션이었다."""
        deck = CardDeckPayload(cards=[], caption="요약", hashtags=["깃허브"])
        assert compose_mod.CAPTION_CUT not in compose_mod._render_caption(deck)

    def test_audio_sits_below_the_cut_line(self):
        # 구분선 위만 붙여넣으면 캡션만 발행된다. 이게 이 기능의 안전장치다.
        caption, _, memo = self._rendered().partition(compose_mod.CAPTION_CUT)
        assert caption.strip() == "요약\n\n#깃허브"
        assert "추천 오디오" in memo

    def test_cut_line_warns_in_words_too(self):
        """구분선만으로는 통째로 복사하는 것을 못 막는다."""
        assert "붙여넣지 마세요" in self._rendered()

    def test_every_pick_is_listed_with_terms_and_reason(self):
        memo = self._rendered()
        for pick in _three_audio():
            assert pick.mood in memo
            assert pick.why in memo
            for term in pick.search:
                assert term in memo


class TestAudioValidation:
    """확인할 수 없는 것은 쓰지 않는다 — 인스타 오디오 목록이 그렇다."""

    def test_three_well_formed_picks_pass(self):
        assert compose_mod._audio_problems(_three_audio()) == []

    def test_wrong_count_is_caught(self):
        problems = compose_mod._audio_problems(_three_audio()[:2])
        assert any("2건" in p for p in problems)

    @pytest.mark.parametrize(
        "term",
        [
            "가수 - 노래제목",       # 곡명 - 아티스트
            "someone – title",     # 엔 대시
            "someone feat. other",  # 피처링 표기
        ],
    )
    def test_specific_tracks_are_rejected(self, term):
        """오디오 목록을 조회할 수 없으니, 특정 곡 지목은 없는 사실을 만드는 것이다."""
        picks = _three_audio()
        picks[0] = _audio(search=(term, "lo-fi beat"))
        assert any("특정 곡" in p for p in compose_mod._audio_problems(picks))

    def test_plain_genre_terms_are_allowed(self):
        picks = _three_audio()
        picks[0] = _audio(search=("lo-fi hip hop", "차분한 브이로그"))
        assert compose_mod._audio_problems(picks) == []

    def test_duplicate_moods_are_caught(self):
        picks = _three_audio()
        picks[1] = _audio(picks[0].mood, ("bright synth", "upbeat pop"))
        assert any("겹칩니다" in p for p in compose_mod._audio_problems(picks))

    def test_too_few_search_terms_is_caught(self):
        picks = _three_audio()
        picks[0] = _audio(search=("lo-fi beat",))
        assert any("search" in p for p in compose_mod._audio_problems(picks))

    def test_empty_reason_is_caught(self):
        picks = _three_audio()
        picks[0] = _audio(why="  ")
        assert any("why" in p for p in compose_mod._audio_problems(picks))

    def test_audio_is_required_by_the_schema(self):
        """스키마가 강제해야 모델이 조용히 빼먹지 않는다."""
        schema = json_schema_for(CardDeckPayload)
        assert "audio" in schema["required"]
        assert set(schema["$defs"]["AudioPick"]["required"]) == {"mood", "search", "why"}


class TestSchema:
    """output_format 에 넘길 스키마는 모델이 필드를 빠뜨릴 여지를 없애야 한다."""

    def test_all_objects_are_closed_and_required(self):
        schema = json_schema_for(ResearchPayload)

        def walk(node):
            if isinstance(node, dict):
                if node.get("type") == "object" and "properties" in node:
                    assert node["additionalProperties"] is False
                    assert set(node["required"]) == set(node["properties"])
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for item in node:
                    walk(item)

        walk(schema)

    def test_optional_fields_are_still_required(self):
        """Optional 이라고 빠뜨리면 카드가 비므로, 명시적 null 을 요구한다."""
        schema = json_schema_for(CardDeckPayload)
        card = schema["$defs"]["Card"]
        assert "footnote" in card["required"]


class TestJargonGate:
    """타겟 독자는 '코딩·AI에 이제 발을 들인 사람' 이다.

    프롬프트로 "쉽게 써라"만 하면 조사 자료의 파일명이 그대로 새어 나온다.
    실제로 첫 버전 캡션에 `curator.py 87KB`, `learning_graph` 가 실렸다.
    """

    @pytest.mark.parametrize(
        "text,expected",
        [
            ("learning_graph.py가 SKILL.md를 읽는다", {"learning_graph", "SKILL"}),
            ("MCP 서버와 IPC 브리지", {"MCP", "IPC"}),
            ("memoryManager 가 처리한다", {"memoryManager"}),
            ("pyproject.toml 에 고정돼 있다", {"pyproject.toml"}),
        ],
    )
    def test_detects_developer_speak(self, text, expected):
        assert set(compose_mod.jargon_hits(text)) >= expected

    @pytest.mark.parametrize(
        "text",
        [
            "터미널(검은 화면에 글자로 명령하는 창)에서 도는 AI 비서예요",
            "별 23만 개를 받은 오픈소스 프로젝트예요",
            "한 번 알려준 요령을 적어두고 다음에 다시 꺼내 씁니다",
            "무료로 쓸 수 있고 PC 에도 설치됩니다",
        ],
    )
    def test_plain_korean_passes(self, text):
        assert compose_mod.jargon_hits(text) == []

    def test_common_acronyms_are_allowed(self):
        """AI, MIT, URL 까지 잡으면 쓸 수 있는 문장이 없다."""
        assert compose_mod.jargon_hits("AI 도구이고 MIT 라이선스예요") == []

    def test_explained_acronym_passes(self):
        """괄호로 풀어 썼으면 통과시킨다 — 그게 우리가 원하는 형태다."""
        assert compose_mod.jargon_hits("API(프로그램끼리 대화하는 통로)로 연결해요") == []

    def test_hard_card_is_reported_as_violation(self):
        deck = CardDeckPayload(
            cards=[
                Card(index=1, role="what_is_it", title="학습 루프",
                     body="learning_graph.py가 SKILL.md와 MCP 서버를 연결한다"),
            ],
            caption="요약", hashtags=[],
        )
        problems = compose_mod._validate(
            deck, ["what_is_it"], MAX_TITLE, MAX_BODY, MAX_TAGS
        )
        assert any("입문자에게 어렵습니다" in p for p in problems)

    def test_outro_is_exempt(self):
        """레포 주소와 라이선스는 영문일 수밖에 없고 그게 정보의 본체다."""
        deck = CardDeckPayload(
            cards=[Card(index=1, role="outro", title="MIT · Python",
                        body="github.com/NousResearch/hermes-agent")],
            caption="요약", hashtags=[],
        )
        problems = compose_mod._validate(deck, ["outro"], MAX_TITLE, MAX_BODY, MAX_TAGS)
        assert not any("입문자" in p for p in problems)
