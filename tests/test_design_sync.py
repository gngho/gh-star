"""design-sync 검증.

피그마에서 값을 꺼내오는 단계는 자격증명이 필요해 여기서 다루지 않는다.
대신 그 경계(design/tokens.json)부터 tokens.css 까지의 생성 단계를 검증한다.
이 단계는 순수 함수라 네트워크 없이 결정적으로 돌아간다.
"""

import json
import re

import pytest

from agent import design_sync as ds
from agent import paths

TEMPLATE = paths.ROOT / "templates" / "card.html.j2"


def _tokens(**extra):
    base = {
        "file_url": "https://figma.com/design/abc",
        "exported_at": "2026-01-01T00:00:00Z",
        "tokens": {
            "color/bg": {"type": "COLOR", "value": "#0b0d10"},
            "size/title": {"type": "FLOAT", "value": 64},
            "space/gap": {"type": "FLOAT", "value": 32},
        },
    }
    base.update(extra)
    return base


class TestNaming:
    """생성된 변수명이 템플릿이 쓰는 이름과 정확히 맞아야 한다."""

    @pytest.mark.parametrize(
        "token,expected",
        [
            ("color/bg", "--bg"),
            ("color/fg-muted", "--fg-muted"),
            ("color/accent-2", "--accent-2"),
            ("size/title", "--size-title"),
            ("size/title-cover", "--size-title-cover"),
            ("space/pad-x", "--pad-x"),
            ("space/gap", "--gap"),
        ],
    )
    def test_maps_to_css_custom_property(self, token, expected):
        assert ds.css_var_name(token) == expected

    def test_unknown_namespace_survives(self):
        assert ds.css_var_name("radius/card") == "--radius-card"


class TestValueFormatting:
    def test_color_passes_through(self):
        assert ds.format_value({"type": "COLOR", "value": "#7c5cff"}) == "#7c5cff"

    def test_integer_float_has_no_decimal(self):
        """88.0px 이 아니라 88px 로 나와야 한다."""
        assert ds.format_value({"type": "FLOAT", "value": 88}) == "88px"
        assert ds.format_value({"type": "FLOAT", "value": 88.0}) == "88px"

    def test_fractional_float_keeps_decimal(self):
        assert ds.format_value({"type": "FLOAT", "value": 1.5}) == "1.5px"


class TestRenderCss:
    def test_emits_every_token(self):
        css = ds.render_css(_tokens())
        assert "--bg: #0b0d10;" in css
        assert "--size-title: 64px;" in css
        assert "--gap: 32px;" in css

    def test_includes_manual_tokens_not_in_figma(self):
        """폰트 스택과 행간은 피그마에서 오지 않지만 템플릿이 필요로 한다."""
        css = ds.render_css(_tokens())
        assert "--font-sans:" in css
        assert "--leading-body:" in css

    def test_records_provenance(self):
        css = ds.render_css(_tokens())
        assert "figma.com/design/abc" in css
        assert "2026-01-01" in css

    def test_warns_not_to_hand_edit(self):
        assert "자동 생성" in ds.render_css(_tokens())

    def test_empty_tokens_is_an_error(self):
        with pytest.raises(ds.DesignSyncError):
            ds.render_css({"tokens": {}})

    def test_is_deterministic(self):
        """같은 입력이면 같은 출력. 아니면 매번 diff 가 생겨 drift 감지가 무의미해진다."""
        assert ds.render_css(_tokens()) == ds.render_css(_tokens())


class TestRun:
    def test_writes_and_reports_change(self, tmp_path):
        tokens = tmp_path / "tokens.json"
        tokens.write_text(json.dumps(_tokens()), encoding="utf-8")
        css = tmp_path / "tokens.css"

        first = ds.run(tokens_path=tokens, css_path=css)
        assert first["changed"] is True
        assert first["tokens"] == 3
        assert css.exists()

        second = ds.run(tokens_path=tokens, css_path=css)
        assert second["changed"] is False  # 멱등

    def test_dry_run_writes_nothing(self, tmp_path):
        tokens = tmp_path / "tokens.json"
        tokens.write_text(json.dumps(_tokens()), encoding="utf-8")
        css = tmp_path / "tokens.css"

        result = ds.run(tokens_path=tokens, css_path=css, dry_run=True)
        assert result["changed"] is True
        assert not css.exists()

    def test_missing_tokens_file_explains_how_to_fix(self, tmp_path):
        with pytest.raises(ds.DesignSyncError) as exc:
            ds.run(tokens_path=tmp_path / "nope.json")
        assert "추출" in str(exc.value)


class TestTemplateUsesTokensOnly:
    """색을 템플릿에 하드코딩하면 design-sync 가 무력해진다.

    실제로 배지 배경과 상단 광원이 하드코딩돼 있어, 피그마에서 accent 를 바꿔도
    그 둘만 옛 색으로 남았다. 다시 생기지 않게 막는다.
    """

    def test_no_hardcoded_color_literals(self):
        source = TEMPLATE.read_text(encoding="utf-8")
        # 주석은 제외하고 검사한다
        source = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
        source = re.sub(r"\{#.*?#\}", "", source, flags=re.DOTALL)

        offenders = re.findall(r"#[0-9a-fA-F]{3,8}\b|rgba?\([\d\s.,%]+\)", source)
        assert not offenders, f"토큰 대신 하드코딩된 색: {offenders}"

    def test_real_tokens_css_covers_every_variable_used(self):
        """템플릿이 쓰는 var(--x) 가 전부 tokens.css 에 정의돼 있어야 한다."""
        template = TEMPLATE.read_text(encoding="utf-8")
        css = (paths.ROOT / "templates" / "tokens.css").read_text(encoding="utf-8")

        used = set(re.findall(r"var\((--[a-z0-9-]+)\)", template))
        defined = set(re.findall(r"^\s*(--[a-z0-9-]+)\s*:", css, flags=re.MULTILINE))
        assert used <= defined, f"tokens.css 에 없는 변수: {sorted(used - defined)}"
