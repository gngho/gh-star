"""카드 렌더링 단계. SPEC.md 7.

Jinja2 → Playwright 스크린샷 → JPEG. HTML/CSS 를 쓰는 이유는 한글 줄바꿈·자간·
웹폰트 처리가 이미지 라이브러리보다 압도적으로 낫기 때문이다.

세 가지를 꼭 지킨다:
- 전 장의 비율이 동일해야 한다. 캐러셀은 첫 장 비율로 나머지를 크롭한다.
- JPEG 로 저장해야 한다. Instagram 은 이미지 게시물에 JPEG 만 받는다.
- 브라우저는 한 번만 띄운다. 카드마다 새로 띄우면 10배로 낭비된다.
"""

from __future__ import annotations

import base64
import io
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape
from PIL import Image
from playwright.sync_api import Page, sync_playwright

from . import paths
from .config import Config
from .models import Card, CardDeckFile

log = logging.getLogger(__name__)

TEMPLATES = paths.ROOT / "templates"
FONT = paths.ROOT / "assets" / "fonts" / "PretendardVariable.woff2"

# 오버플로 시 시도할 축소 배율. 3단계로 끝낸다 — 더 줄이면 읽을 수 없다.
SCALES = (1.0, 0.92, 0.85)

EYEBROWS = {
    "cover": "",
    "what_is_it": "한마디로",
    "problem": "왜 필요할까",
    "why_now": "지금 뜨는 이유",
    "feature": "이런 걸 해요",
    "quickstart": "직접 써보려면",
    "fit": "나한테 맞을까",
    "outro": "",
}

# 오버플로 판정: 내용 높이가 컨테이너를 넘는가
OVERFLOW_JS = """
() => {
  const el = document.getElementById('measure');
  if (!el) return {over: false, by: 0};
  const by = el.scrollHeight - el.clientHeight;
  return {over: by > 1, by};
}
"""


class RenderError(RuntimeError):
    pass


def eyebrow_for(role: str, title: str) -> str:
    """역할 라벨을 고르되, 제목과 같은 말을 반복하면 뺀다.

    라벨은 고정 문구고 제목은 모델이 쓰므로 겹칠 수 있다. 실제로 fit 카드에서
    "이럴 때 / 이럴 땐"(라벨)과 "이럴 때 / 아닐 때"(제목)가 겹쳐 나왔다.
    """
    label = EYEBROWS.get(role, "")
    if not label:
        return ""

    def norm(text: str) -> set[str]:
        return {ch for ch in text if ch.isalnum()}

    label_chars, title_chars = norm(label), norm(title)
    if not label_chars:
        return ""
    overlap = len(label_chars & title_chars) / len(label_chars)
    return "" if overlap >= 0.7 else label


def run(config: Config, run_date: str, dry_run: bool = False) -> dict[str, Any]:
    render_cfg = config.section("render")
    width = int(render_cfg.get("width", 1080))
    height = int(render_cfg.get("height", 1350))
    quality = int(render_cfg.get("jpeg_quality", 90))

    deck, post_dir = _load_deck(run_date)
    cards = deck.payload.cards
    if not cards:
        raise RenderError("카드가 없습니다. 먼저 compose 를 실행하세요.")

    html_template = _environment().get_template("card.html.j2")
    tokens_css = _read_tokens()
    font_uri = _font_data_uri()

    star_badge = _star_badge(deck)
    _attach_images(cards, post_dir)
    cards_dir = post_dir / "cards"
    warnings: list[str] = []
    results: list[dict[str, Any]] = []

    if not dry_run:
        cards_dir.mkdir(parents=True, exist_ok=True)

    # 브라우저는 한 번만 띄운다.
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            page = browser.new_page(
                viewport={"width": width, "height": height},
                device_scale_factor=1,
            )
            for card in cards:
                png, scale, overflow_by = _render_card(
                    page,
                    html_template,
                    card=card,
                    deck=deck,
                    tokens_css=tokens_css,
                    font_uri=font_uri,
                    width=width,
                    height=height,
                    total=len(cards),
                    star_badge=star_badge,
                )
                if overflow_by > 0:
                    msg = (
                        f"카드 {card.index}({card.role}): 축소 후에도 "
                        f"{overflow_by}px 넘칩니다. 원고를 줄여야 합니다."
                    )
                    warnings.append(msg)
                    log.warning(msg)
                elif scale < 1.0:
                    log.info("카드 %d: %.0f%% 로 축소해 맞췄습니다.", card.index, scale * 100)

                out = cards_dir / f"{card.index:02d}.jpg"
                if not dry_run:
                    _save_jpeg(png, out, quality)
                results.append(
                    {
                        "index": card.index,
                        "role": card.role,
                        "file": out.name,
                        "scale": scale,
                        "overflow_px": overflow_by,
                        "has_image": bool(card.image),
                    }
                )
        finally:
            browser.close()

    meta = {
        "repo": deck.repo,
        "date": deck.date,
        "rendered_at": datetime.now(timezone.utc).isoformat(),
        "width": width,
        "height": height,
        "format": "jpeg",
        "tokens_source": "figma" if _tokens_from_figma() else "default",
        "cards": results,
        "warnings": warnings,
    }
    if not dry_run:
        paths.write_json(post_dir / "meta.json", meta)
    return meta


# ------------------------------------------------------------------ 렌더


def _render_card(
    page: Page,
    template: Any,
    *,
    card: Card,
    deck: CardDeckFile,
    tokens_css: str,
    font_uri: str,
    width: int,
    height: int,
    total: int,
    star_badge: str,
) -> tuple[bytes, float, int]:
    """오버플로가 없어질 때까지 축소하며 렌더한다. (PNG, 배율, 남은초과px)"""
    last_over = 0
    for scale in SCALES:
        html = template.render(
            card=card,
            repo=deck.repo,
            total=total,
            scale=scale,
            width=width,
            height=height,
            tokens_css=tokens_css,
            font_uri=font_uri,
            eyebrow=eyebrow_for(card.role, card.title),
            star_badge=star_badge,
        )
        page.set_content(html, wait_until="load")
        page.evaluate("() => document.fonts.ready")

        state = page.evaluate(OVERFLOW_JS)
        last_over = int(state["by"])
        if not state["over"]:
            return page.screenshot(type="png"), scale, 0

    # 마지막 배율의 결과라도 내보낸다. 잘라내지 않고 경고를 남긴다.
    return page.screenshot(type="png"), SCALES[-1], max(last_over, 1)


def _save_jpeg(png: bytes, path: Path, quality: int) -> None:
    """PNG → JPEG. Instagram 이 JPEG 만 받으므로 선택이 아니라 필수다."""
    with Image.open(io.BytesIO(png)) as img:
        rgb = img.convert("RGB")  # 알파 채널 제거
        rgb.save(path, format="JPEG", quality=quality, optimize=True, subsampling=0)


# ------------------------------------------------------------------ 자원


def _environment() -> Environment:
    return Environment(
        loader=FileSystemLoader(TEMPLATES),
        autoescape=select_autoescape(["html", "j2"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )


def _read_tokens() -> str:
    path = TEMPLATES / "tokens.css"
    if not path.exists():
        log.warning(
            "templates/tokens.css 가 없습니다. 렌더는 진행하지만 디자인이 기본값입니다. "
            "design-sync 를 실행하세요."
        )
        return ":root {}"
    return path.read_text(encoding="utf-8")


def _tokens_from_figma() -> bool:
    """토큰이 피그마에서 온 것인지 기본값인지 표시한다."""
    return (paths.ROOT / "design" / "tokens.json").exists()


IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp")
IMAGE_MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}


def _attach_images(cards: list[Card], post_dir: Path) -> None:
    """카드 상단 이미지를 `posts/{...}/images/{index:02d}.{ext}` 에서 찾아 붙인다.

    없으면 그대로 비워 둔다. 이미지가 없는데 자리표시자를 그리면
    "여기에 이미지가 삽입됩니다"가 인스타그램에 그대로 발행된다.
    피그마 템플릿의 점선 박스는 어디에 무엇이 들어가는지 알려주는 설명이지,
    렌더 결과물이 아니다.
    """
    images_dir = post_dir / "images"
    for card in cards:
        if card.role == "cover":
            card.image = None  # 커버는 이미지 없이 간다 (디자인 결정)
            continue
        for suffix in IMAGE_SUFFIXES:
            candidate = images_dir / f"{card.index:02d}{suffix}"
            if candidate.exists():
                card.image = _data_uri(candidate)
                break


def _data_uri(path: Path) -> str:
    mime = IMAGE_MIME.get(path.suffix.lower(), "application/octet-stream")
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def _font_data_uri() -> str:
    """폰트를 data URI 로 임베드한다.

    file:// 참조는 Chromium 의 폰트 CORS 규칙에 걸릴 수 있고, CDN 참조는
    네트워크가 끊기면 조용히 폴백 폰트로 깨진다. 임베드가 가장 확실하다.
    """
    if not FONT.exists():
        raise RenderError(
            f"한글 폰트가 없습니다: {FONT}\n"
            "폰트 없이 렌더하면 모든 글자가 두부(□)로 나옵니다."
        )
    encoded = base64.b64encode(FONT.read_bytes()).decode("ascii")
    return f"data:font/woff2;base64,{encoded}"


def _load_deck(run_date: str) -> tuple[CardDeckFile, Path]:
    matches = sorted(paths.POSTS.glob(f"{run_date}-*/content.json"))
    if not matches:
        raise FileNotFoundError(
            f"원고가 없습니다: {paths.POSTS}/{run_date}-*/content.json. "
            "먼저 compose 를 실행하세요."
        )
    path = matches[0]
    return CardDeckFile.model_validate(paths.read_json(path)), path.parent


def _star_badge(deck: CardDeckFile) -> str:
    """커버 카드의 스타 증가량 배지 문구."""
    research = _find_research(deck)
    if research is None:
        return ""
    stars = research.get("meta", {}).get("stars")
    delta = research.get("meta", {}).get("delta_1d")
    if stars is None:
        return ""
    text = f"{stars:,}"
    if delta:
        text += f"  ↑{delta:,} today"
    return text


def _find_research(deck: CardDeckFile) -> dict[str, Any] | None:
    slug = deck.repo.replace("/", "__").lower()
    path = paths.RESEARCH / f"{deck.date}-{slug}.json"
    return paths.read_json(path, default=None)
