"""렌더 통합 테스트.

Playwright/Chromium 이 필요하므로 없으면 건너뛴다. 여기서 검증하는 건
"카드가 예쁘게 나오는가"가 아니라 **안전장치가 실제로 동작하는가**다:
넘치는 원고를 잘라내지 않고 축소하는지, 그래도 넘치면 경고를 남기는지,
그리고 인스타그램이 받아들이는 형식(JPEG, 동일 비율)으로 나오는지.
"""

from datetime import datetime, timezone

import pytest

from agent import paths
from agent.config import load_config
from agent.models import Card, CardDeckFile, CardDeckPayload

playwright = pytest.importorskip("playwright.sync_api")
Image = pytest.importorskip("PIL.Image")

from agent import render as render_mod  # noqa: E402


def _deck(cards: list[Card]) -> CardDeckFile:
    return CardDeckFile(
        repo="acme/widget",
        date="2099-01-01",
        generated_at=datetime.now(timezone.utc),
        payload=CardDeckPayload(cards=cards, caption="요약", hashtags=["테스트"]),
    )


@pytest.fixture(scope="module")
def rendered(tmp_path_factory):
    """정상 카드 1장 + 의도적으로 넘치는 카드 1장을 렌더한다."""
    post_dir = tmp_path_factory.mktemp("posts") / "2099-01-01-acme__widget"
    post_dir.mkdir(parents=True)

    deck = _deck(
        [
            Card(index=1, role="cover", title="위젯", body="짧은 본문입니다."),
            Card(
                index=2,
                role="feature",
                # 상한을 한참 넘는 원고. 축소로도 감당이 안 되게 만든다.
                title="아주 긴 제목 " * 12,
                body="넘치는 본문 문장입니다. " * 60,
            ),
        ]
    )
    paths.write_json(post_dir / "content.json", deck.model_dump(mode="json"))

    original = paths.POSTS
    paths.POSTS = post_dir.parent
    try:
        meta = render_mod.run(load_config(), "2099-01-01")
    finally:
        paths.POSTS = original
    return meta, post_dir


class TestOverflowSafety:
    def test_normal_card_renders_at_full_scale(self, rendered):
        meta, _ = rendered
        card = meta["cards"][0]
        assert card["scale"] == 1.0
        assert card["overflow_px"] == 0

    def test_overlong_card_is_shrunk_not_truncated(self, rendered):
        """넘치면 폰트를 줄인다. 텍스트를 잘라 문장이 끊기게 두지 않는다."""
        meta, _ = rendered
        card = meta["cards"][1]
        assert card["scale"] < 1.0

    def test_unfixable_overflow_is_warned_not_silently_shipped(self, rendered):
        """축소로도 안 되면 조용히 내보내지 말고 경고를 남겨야 한다 (SPEC 7.6)."""
        meta, _ = rendered
        assert meta["warnings"], "축소 후에도 넘치는데 경고가 없습니다"
        assert "카드 2" in meta["warnings"][0]

    def test_overflowing_card_still_produces_a_file(self, rendered):
        """경고는 남기되 파일은 만든다. 사람이 PR 에서 눈으로 보고 판단한다."""
        _, post_dir = rendered
        assert (post_dir / "cards" / "02.jpg").exists()


class TestInstagramConstraints:
    def test_output_is_jpeg_without_alpha(self, rendered):
        """Instagram 은 이미지 게시물에 JPEG 만 받는다."""
        _, post_dir = rendered
        with Image.open(post_dir / "cards" / "01.jpg") as img:
            assert img.format == "JPEG"
            assert img.mode == "RGB"

    def test_all_cards_share_one_aspect_ratio(self, rendered):
        """캐러셀은 첫 장 비율로 나머지를 크롭한다. 하나라도 다르면 잘린다."""
        meta, post_dir = rendered
        sizes = set()
        for card in meta["cards"]:
            with Image.open(post_dir / "cards" / card["file"]) as img:
                sizes.add(img.size)
        assert len(sizes) == 1
        assert sizes.pop() == (meta["width"], meta["height"])

    def test_configured_four_five_ratio(self, rendered):
        meta, _ = rendered
        assert meta["width"] / meta["height"] == pytest.approx(4 / 5, rel=1e-3)


class TestMeta:
    def test_records_token_source(self, rendered):
        """피그마 토큰인지 기본값인지 남겨야 drift 를 추적할 수 있다."""
        meta, _ = rendered
        assert meta["tokens_source"] in ("figma", "default")

    def test_one_entry_per_card(self, rendered):
        meta, _ = rendered
        assert [c["index"] for c in meta["cards"]] == [1, 2]


class TestEyebrow:
    """라벨은 고정, 제목은 모델이 쓴다. 같은 말이 두 줄로 겹치면 안 된다."""

    def test_duplicate_label_is_dropped(self):
        """제목이 라벨을 거의 그대로 품으면 라벨을 뺀다."""
        assert render_mod.eyebrow_for("what_is_it", "한마디로 말하면") == ""
        assert render_mod.eyebrow_for("problem", "왜 필요할까요") == ""

    def test_distinct_title_keeps_label(self):
        assert render_mod.eyebrow_for("feature", "요령을 기억해요") == "이런 걸 해요"

    def test_labels_are_beginner_friendly(self):
        """타겟이 입문자로 바뀌면서 라벨도 대화체로 갈았다."""
        assert render_mod.eyebrow_for("what_is_it", "AI 비서예요") == "한마디로"
        assert render_mod.eyebrow_for("quickstart", "세 줄이면 끝") == "직접 써보려면"
        assert render_mod.eyebrow_for("fit", "누구에게 맞을까") == "나한테 맞을까"

    def test_cover_and_outro_have_no_label(self):
        assert render_mod.eyebrow_for("cover", "hermes-agent") == ""
        assert render_mod.eyebrow_for("outro", "MIT · Python") == ""

    def test_unknown_role_has_no_label(self):
        assert render_mod.eyebrow_for("mystery", "제목") == ""


class TestImageSlot:
    """카드 상단 이미지 영역. 이미지가 없으면 자리표시자를 그리지 않는다.

    피그마 템플릿의 점선 박스는 "여기에 무엇이 들어간다"는 설명이지 렌더 결과가
    아니다. 그대로 내보내면 '여기에 이미지가 삽입됩니다' 가 인스타그램에 발행된다.
    """

    def _cards(self):
        return [
            Card(index=1, role="cover", title="레포명", body="훅"),
            Card(index=2, role="feature", title="기능", body="설명"),
        ]

    def test_missing_image_leaves_slot_empty(self, tmp_path):
        cards = self._cards()
        render_mod._attach_images(cards, tmp_path)
        assert all(c.image is None for c in cards)

    def test_image_is_embedded_as_data_uri(self, tmp_path):
        images = tmp_path / "images"
        images.mkdir()
        # 최소 크기의 유효한 PNG
        Image.new("RGB", (8, 8), (10, 20, 30)).save(images / "02.png")

        cards = self._cards()
        render_mod._attach_images(cards, tmp_path)
        assert cards[1].image.startswith("data:image/png;base64,")

    def test_cover_never_gets_an_image(self, tmp_path):
        """커버는 이미지 없이 간다는 디자인 결정."""
        images = tmp_path / "images"
        images.mkdir()
        Image.new("RGB", (8, 8)).save(images / "01.png")

        cards = self._cards()
        render_mod._attach_images(cards, tmp_path)
        assert cards[0].image is None

    def test_jpg_is_also_picked_up(self, tmp_path):
        images = tmp_path / "images"
        images.mkdir()
        Image.new("RGB", (8, 8)).save(images / "02.jpg")

        cards = self._cards()
        render_mod._attach_images(cards, tmp_path)
        assert cards[1].image.startswith("data:image/jpeg;base64,")

    def test_meta_records_whether_each_card_had_an_image(self, rendered):
        meta, _ = rendered
        assert all("has_image" in c for c in meta["cards"])
        assert meta["cards"][0]["has_image"] is False


class TestNoPager:
    """페이지 인디케이터는 쓰지 않기로 했다."""

    def test_template_has_no_pager_markup(self):
        source = (paths.ROOT / "templates" / "card.html.j2").read_text(encoding="utf-8")
        assert "pager" not in source
        assert "class=\"dot" not in source


class TestBodyFlowsAsOneParagraph:
    """본문은 문장을 끊지 않고 이어서 흘린다.

    한때 문장마다 블록을 줘서 줄을 나눴는데, 카드가 목록처럼 읽혀서 되돌렸다.
    되돌아가지 않도록 마크업과 CSS 양쪽을 고정한다.
    """

    def test_template_has_no_per_sentence_markup(self):
        source = (paths.ROOT / "templates" / "card.html.j2").read_text(encoding="utf-8")
        assert "body_sentences" not in source
        assert ".body .s" not in source
        assert '<p class="body">{{ card.body }}</p>' in source

    def test_render_module_no_longer_splits(self):
        assert not hasattr(render_mod, "split_sentences")

    def test_body_renders_as_a_single_paragraph(self):
        card = Card(
            index=2,
            role="what_is_it",
            title="제목",
            body="첫 문장입니다. 둘째 문장입니다. 셋째 문장입니다.",
        )
        html = render_mod._environment().get_template("card.html.j2").render(
            card=card,
            repo="acme/widget",
            total=3,
            scale=1.0,
            width=1080,
            height=1350,
            tokens_css=":root {}",
            font_uri="",
            eyebrow="",
            star_badge="",
            outro={},
        )
        paragraph = html.split('<p class="body">')[1].split("</p>")[0]
        assert paragraph == card.body      # 문장이 통째로 한 문단이다
        assert "<span" not in paragraph    # 블록 경계가 남아 있지 않다
        assert html.count('<p class="body">') == 1


class TestConciseCards:
    """커버와 마무리는 간결해야 한다. 나머지 카드와 다른 규칙을 따른다."""

    def test_cover_and_outro_get_no_image(self, tmp_path):
        images = tmp_path / "images"
        images.mkdir()
        for name in ("01.png", "10.png", "04.png"):
            Image.new("RGB", (8, 8)).save(images / name)

        cards = [
            Card(index=1, role="cover", title="레포"),
            Card(index=4, role="feature", title="기능"),
            Card(index=10, role="outro", title="정리하면"),
        ]
        render_mod._attach_images(cards, tmp_path)
        assert cards[0].image is None
        assert cards[1].image is not None   # 일반 카드는 붙는다
        assert cards[2].image is None

    def test_no_image_roles_match_imagery(self):
        """두 모듈이 각자 목록을 들면 언젠가 어긋난다."""
        from agent import imagery
        assert render_mod.NO_IMAGE_ROLES == imagery.NO_IMAGE


class TestOutroFacts:
    """주소·라이선스는 한 글자만 틀려도 잘못된 정보다. 문장 생성에 맡기지 않는다."""

    def _deck(self):
        return _deck([Card(index=1, role="outro", title="정리하면")])

    def test_strips_scheme_from_url(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            render_mod, "_find_research",
            lambda d: {"meta": {"html_url": "https://github.com/acme/widget",
                                "license": "MIT", "language": "Python"}},
        )
        facts = render_mod._outro_facts(self._deck())
        assert facts["url"] == "github.com/acme/widget"
        assert facts["meta"] == "MIT · Python"

    def test_falls_back_to_repo_when_research_missing(self, monkeypatch):
        monkeypatch.setattr(render_mod, "_find_research", lambda d: None)
        facts = render_mod._outro_facts(self._deck())
        assert facts["url"] == "github.com/acme/widget"
        assert facts["meta"] == ""

    def test_omits_missing_license_or_language(self, monkeypatch):
        monkeypatch.setattr(
            render_mod, "_find_research",
            lambda d: {"meta": {"html_url": "https://github.com/acme/widget",
                                "license": None, "language": "Rust"}},
        )
        assert render_mod._outro_facts(self._deck())["meta"] == "Rust"
