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
        assert render_mod.eyebrow_for("feature", "핵심 기능 셋") == ""
        assert render_mod.eyebrow_for("quickstart", "30초 시작하기") == ""

    def test_distinct_title_keeps_label(self):
        assert render_mod.eyebrow_for("feature", "실행 백엔드 7종") == "핵심 기능"

    def test_renamed_fit_label_no_longer_echoes_title(self):
        """'이럴 때 / 이럴 땐' 라벨이 '이럴 때 / 아닐 때' 제목과 겹쳐 나왔던 사례.

        라벨을 '도입 판단'으로 바꿔 근본 원인을 없앴고, 가드는 남은 안전망이다.
        """
        assert render_mod.eyebrow_for("fit", "이럴 때 / 아닐 때") == "도입 판단"

    def test_cover_and_outro_have_no_label(self):
        assert render_mod.eyebrow_for("cover", "hermes-agent") == ""
        assert render_mod.eyebrow_for("outro", "MIT · Python") == ""

    def test_unknown_role_has_no_label(self):
        assert render_mod.eyebrow_for("mystery", "제목") == ""
