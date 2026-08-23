"""카드 상단 이미지 생성 검증.

네트워크는 타지 않는다 — 수집 함수를 갈아끼우고 조합 로직만 본다.
여기서 지키려는 것은 두 가지다:
1. GitHub 이 주는 자산만 쓴다 (저작권, SPEC 13.3)
2. 같은 그림이 캐러셀 내내 반복되지 않는다
"""

from datetime import datetime, timezone

import pytest

from agent.models import Card, CardDeckFile, CardDeckPayload

Image = pytest.importorskip("PIL.Image")

from agent import imagery  # noqa: E402


def _deck() -> CardDeckFile:
    roles = [
        "cover", "problem", "why_now", "feature", "feature",
        "feature", "architecture", "quickstart", "fit", "outro",
    ]
    return CardDeckFile(
        repo="acme/widget",
        date="2099-01-01",
        generated_at=datetime.now(timezone.utc),
        payload=CardDeckPayload(
            cards=[Card(index=i, role=r, title=f"제목{i}") for i, r in enumerate(roles, 1)],
            caption="요약",
            hashtags=["테스트"],
        ),
    )


@pytest.fixture
def fake_sources(monkeypatch):
    """OG 는 가로로 긴 흰 이미지, 아바타는 정사각 컬러 이미지로 흉내낸다."""
    og = Image.new("RGB", (1280, 640), (250, 250, 250))
    avatar = Image.new("RGB", (460, 460), (40, 120, 200))

    def fetch(url: str):
        if "opengraph" in url:
            return og
        if "github.com" in url:
            return avatar
        return None

    monkeypatch.setattr(imagery, "_fetch_image", fetch)
    return og, avatar


class TestComposition:
    def test_cover_crop_hits_exact_size(self):
        src = Image.new("RGB", (300, 900))
        out = imagery._cover_crop(src, 1080, 470)
        assert out.size == (1080, 470)

    def test_og_output_is_card_sized(self, fake_sources):
        og, _ = fake_sources
        assert imagery._from_og(og).size == (imagery.W, imagery.H)

    def test_avatar_output_is_card_sized(self, fake_sources):
        _, avatar = fake_sources
        assert imagery._from_avatar(avatar, 4).size == (imagery.W, imagery.H)

    def test_bright_source_is_dimmed_to_fit_dark_theme(self, fake_sources):
        """흰 OG 이미지를 그대로 얹으면 위아래가 다른 디자인처럼 따로 논다."""
        og, _ = fake_sources
        top_pixel = imagery._from_og(og).getpixel((540, 40))
        assert max(top_pixel) < 190, f"충분히 눌리지 않았습니다: {top_pixel}"

    def test_bottom_fades_to_background(self, fake_sources):
        """아래쪽은 카드 배경색이어야 본문과 이어진다."""
        og, _ = fake_sources
        bottom = imagery._from_og(og).getpixel((540, imagery.H - 2))
        assert max(abs(a - b) for a, b in zip(bottom, imagery.BG)) <= 6

    def test_same_avatar_yields_different_images_per_card(self, fake_sources):
        """아바타 하나로 8장을 만들면 똑같아진다. 카드 번호로 변주를 준다."""
        _, avatar = fake_sources
        renders = [imagery._from_avatar(avatar, i).tobytes() for i in range(2, 10)]
        assert len(set(renders)) == len(renders), "장마다 달라야 합니다"

    def test_variation_works_even_for_a_flat_avatar(self):
        """흑백 로고를 쓰는 레포가 흔하다. 위치만 옮기는 변주는 단색에서 무력하다."""
        flat = Image.new("RGB", (460, 460), (128, 128, 128))
        a = imagery._from_avatar(flat, 2).tobytes()
        b = imagery._from_avatar(flat, 7).tobytes()
        assert a != b


class TestRoleMapping:
    def test_cover_gets_no_image(self, fake_sources, tmp_path):
        result = imagery.run(_deck(), tmp_path)
        assert 1 not in [i["index"] for i in result["images"]]
        assert not (tmp_path / "images" / "01.jpg").exists()

    def test_outro_uses_the_og_card(self, fake_sources, tmp_path):
        result = imagery.run(_deck(), tmp_path)
        outro = next(i for i in result["images"] if i["role"] == "outro")
        assert outro["source"] == "og"

    def test_other_roles_use_the_avatar(self, fake_sources, tmp_path):
        result = imagery.run(_deck(), tmp_path)
        others = [i for i in result["images"] if i["role"] not in ("outro", "cover")]
        assert others and all(i["source"] == "avatar" for i in others)

    def test_writes_one_file_per_card_except_cover(self, fake_sources, tmp_path):
        imagery.run(_deck(), tmp_path)
        written = sorted(p.name for p in (tmp_path / "images").glob("*.jpg"))
        assert written == [f"{i:02d}.jpg" for i in range(2, 11)]

    def test_dry_run_writes_nothing(self, fake_sources, tmp_path):
        result = imagery.run(_deck(), tmp_path, dry_run=True)
        assert result["images"]
        assert not (tmp_path / "images").exists()


class TestFallbacks:
    def test_avatar_missing_falls_back_to_og(self, monkeypatch, tmp_path):
        og = Image.new("RGB", (1280, 640), (250, 250, 250))
        monkeypatch.setattr(
            imagery, "_fetch_image", lambda url: og if "opengraph" in url else None
        )
        result = imagery.run(_deck(), tmp_path)
        assert all(i["source"] == "og" for i in result["images"])

    def test_both_missing_raises_rather_than_shipping_blanks(self, monkeypatch, tmp_path):
        """빈 이미지를 만들어 붙이느니 실패시킨다. render 가 이미지 없이 진행한다."""
        monkeypatch.setattr(imagery, "_fetch_image", lambda url: None)
        with pytest.raises(imagery.ImageryError):
            imagery.run(_deck(), tmp_path)


class TestSourcesArePermitted:
    """레포 README 스크린샷·로고는 저작권이 소유자에게 있어 쓰지 않는다."""

    def test_only_github_hosted_endpoints(self):
        for url in (imagery.OG_URL, imagery.AVATAR_URL):
            assert "githubassets.com" in url or "github.com" in url
