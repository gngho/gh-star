"""카드 상단 그래픽 생성. SPEC.md 7.4.

**역할마다 다른 그림을 그린다.** 같은 이미지를 반복하면 캐러셀이 밋밋하고,
무엇보다 각 카드가 하는 말과 그림이 따로 논다.

| 역할 | 그래픽 | 출처 |
|---|---|---|
| what_is_it | GitHub OG 이미지 | `opengraph.githubassets.com` |

| quickstart | 터미널 창 | 카드의 실제 설치 명령 |
| fit | 두 칸 머리글 | 고정 문구 |
| feature | 큰 번호 01/02/03 | 카드 순서 |
| problem | 큰 물음표 | — |
| cover, outro | 없음 | 간결해야 하는 카드 |

**이미지 생성 모델을 쓰지 않는다.** 우리는 스타 증가량·설치 명령·기능명 같은
실제 데이터를 갖고 있고, 그걸 그리는 편이 생성 일러스트보다 정확하고 덜
상투적이다. 렌더링은 카드와 같은 Playwright 파이프라인을 재사용한다 —
디자인 토큰이 자동으로 공유되므로 카드와 그래픽이 한 세트로 보인다.

레포의 README 스크린샷이나 로고는 여전히 쓰지 않는다 (SPEC 13.3).
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from io import BytesIO
from pathlib import Path
from typing import Any

import httpx
from PIL import Image
from playwright.sync_api import sync_playwright

from . import paths
from .models import CardDeckFile, ResearchFile

log = logging.getLogger(__name__)

W, H = 1080, 470
TIMEOUT = 30.0
USER_AGENT = "gh-cardnews-agent (+https://github.com/)"

OG_URL = "https://opengraph.githubassets.com/1/{repo}"

# 이미지를 쓰지 않는 역할 — 간결해야 하는 카드들
NO_IMAGE = {"cover", "outro"}
# GitHub OG 이미지를 쓰는 역할
OG_ROLES = {"what_is_it"}

BG = (11, 13, 16)  # color/bg


class ImageryError(RuntimeError):
    pass


def run(deck: CardDeckFile, post_dir: Path, dry_run: bool = False) -> dict[str, Any]:
    research = _load_research(deck)
    out_dir = post_dir / "images"
    if not dry_run:
        out_dir.mkdir(parents=True, exist_ok=True)

    plans = [
        (card, _plan(card, deck, research))
        for card in deck.payload.cards
        if card.role not in NO_IMAGE
    ]
    plans = [(c, p) for c, p in plans if p is not None]
    if not plans:
        raise ImageryError("그릴 카드가 없습니다.")

    written: list[dict[str, Any]] = []
    og_image = None
    if any(p["kind"] == "og" for _, p in plans):
        og_image = _fetch_og(deck.repo)

    from .render import _environment, _font_data_uri, _read_tokens  # 렌더러 재사용

    template = _environment().get_template("imagery.html.j2")
    tokens_css = _read_tokens()
    font_uri = _font_data_uri()

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": W, "height": H}, device_scale_factor=1)
            for card, plan in plans:
                if plan["kind"] == "og":
                    if og_image is None:
                        log.warning("OG 이미지를 못 가져와 카드 %d 를 건너뜁니다.", card.index)
                        continue
                    image = _from_og(og_image)
                    source = "og"
                else:
                    html = template.render(
                        w=W, h=H, seed=card.index,
                        tokens_css=tokens_css, font_uri=font_uri, **plan,
                    )
                    page.set_content(html, wait_until="load")
                    page.evaluate("() => document.fonts.ready")
                    image = Image.open(BytesIO(page.screenshot(type="png"))).convert("RGB")
                    source = plan["kind"]

                if not dry_run:
                    image.save(out_dir / f"{card.index:02d}.jpg", format="JPEG",
                               quality=88, optimize=True)
                written.append({"index": card.index, "role": card.role, "source": source})
        finally:
            browser.close()

    if not written:
        raise ImageryError("그래픽을 하나도 만들지 못했습니다.")

    return {
        "repo": deck.repo,
        "images": written,
        "dir": str(out_dir),
    }


# ------------------------------------------------------------------ 계획


def _plan(card: Any, deck: CardDeckFile, research: ResearchFile | None) -> dict[str, Any] | None:
    """카드 내용에서 그래픽에 넣을 값을 뽑는다."""
    role = card.role

    if role in OG_ROLES:
        return {"kind": "og"}

    if role == "why_now":
        meta = research.meta if research else None
        delta = (meta.delta_1d if meta else None) or 0
        stars = (meta.stars if meta else 0) or 0
        if delta > 0:
            return {"kind": "stat", "delta": delta, "stars": stars,
                    "bars": _star_bars(deck)}
        # 증가량을 모르면 수치 카드가 거짓말이 된다. 다른 그림으로 넘어간다.
        return {"kind": "mark", "mark": "↑", "label": "지금 뜨는 이유"}

    if role == "quickstart":
        lines = [l for l in card.code if l.strip()][:3]
        if lines:
            return {"kind": "terminal", "lines": lines}
        return {"kind": "mark", "mark": "$", "label": "직접 써보려면"}

    if role == "fit":
        # 조사 원문을 두 칸에 넣어봤으나 셋 다 틀렸다: 글자가 단어 중간에서
        # 잘리고, 조사 원문은 개발자 용어라 입문자 원칙을 어기며, 아래 본문이
        # 같은 내용을 쉬운 말로 다시 말해 중복이었다. 머리글만 남긴다.
        return {"kind": "split"}

    if role == "feature":
        return {"kind": "mark", "mark": f"{_feature_number(card, deck):02d}",
                "label": "핵심 기능"}

    if role == "problem":
        return {"kind": "mark", "mark": "?", "label": "왜 필요할까"}

    return {"kind": "mark", "mark": "*", "label": ""}


def _feature_number(card: Any, deck: CardDeckFile) -> int:
    features = [c for c in deck.payload.cards if c.role == "feature"]
    for i, c in enumerate(features, 1):
        if c.index == card.index:
            return i
    return 1


def _star_bars(deck: CardDeckFile) -> list[int]:
    """최근 스냅샷에서 일별 증가량을 뽑아 막대 높이(%)로 만든다.

    스냅샷이 며칠 없으면 막대를 아예 그리지 않는다. 없는 데이터를 그럴듯한
    모양으로 채우면 그건 그래프가 아니라 장식이다.
    """
    try:
        today = date.fromisoformat(deck.date)
    except ValueError:
        return []

    counts: list[int] = []
    for offset in range(6, -1, -1):
        snap = paths.read_json(paths.snapshot_path((today - timedelta(days=offset)).isoformat()))
        repos = (snap or {}).get("repos", {})
        entry = repos.get(deck.repo)
        counts.append(entry.get("stars", 0) if entry else 0)

    deltas = [
        b - a for a, b in zip(counts, counts[1:])
        if a > 0 and b > 0 and b >= a
    ]
    if len(deltas) < 3:
        return []

    peak = max(deltas) or 1
    return [max(12, round(d / peak * 100)) for d in deltas[-7:]]


# --------------------------------------------------------------- OG 이미지


def _from_og(og: Image.Image) -> Image.Image:
    """OG 이미지를 카드 비율로 잘라 배경 쪽으로 눌러 넣는다.

    GitHub 의 OG 이미지는 흰 배경이라 다크 카드 위에 그대로 얹으면 위아래가
    서로 다른 디자인처럼 따로 논다.
    """
    fitted = _cover_crop(og, W, H)
    damped = Image.blend(fitted, Image.new("RGB", (W, H), BG), 0.62)
    return _fade_bottom(damped)


def _cover_crop(img: Image.Image, w: int, h: int) -> Image.Image:
    src = img.convert("RGB")
    scale = max(w / src.width, h / src.height)
    resized = src.resize(
        (max(1, round(src.width * scale)), max(1, round(src.height * scale))), Image.LANCZOS
    )
    left = (resized.width - w) // 2
    top = (resized.height - h) // 2
    return resized.crop((left, top, left + w, top + h))


def _fade_bottom(img: Image.Image) -> Image.Image:
    from PIL import ImageDraw

    out = img.copy()
    fade_h = int(H * 0.34)
    overlay = Image.new("RGB", (W, fade_h), BG)
    mask = Image.new("L", (W, fade_h))
    draw = ImageDraw.Draw(mask)
    for y in range(fade_h):
        draw.line([(0, y), (W, y)], fill=int(255 * (y / fade_h) ** 1.4))
    out.paste(overlay, (0, H - fade_h), mask)
    return out


def _fetch_og(repo: str) -> Image.Image | None:
    """실패해도 예외를 던지지 않는다. 그 카드만 이미지 없이 간다."""
    url = OG_URL.format(repo=repo)
    try:
        with httpx.Client(
            headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT, follow_redirects=True
        ) as client:
            response = client.get(url)
        if response.status_code != 200:
            log.warning("OG 이미지 요청 실패 (%s)", response.status_code)
            return None
        return Image.open(BytesIO(response.content)).convert("RGB")
    except (httpx.HTTPError, OSError) as exc:
        log.warning("OG 이미지를 열 수 없습니다 (%s)", type(exc).__name__)
        return None


# ------------------------------------------------------------------ 로드


def _load_research(deck: CardDeckFile) -> ResearchFile | None:
    slug = deck.repo.replace("/", "__").lower()
    raw = paths.read_json(paths.RESEARCH / f"{deck.date}-{slug}.json", default=None)
    if not raw:
        return None
    try:
        return ResearchFile.model_validate(raw)
    except Exception:  # noqa: BLE001 - 조사 파일이 깨졌으면 없는 셈 친다
        return None


def load_deck(run_date: str) -> tuple[CardDeckFile, Path]:
    matches = sorted(paths.POSTS.glob(f"{run_date}-*/content.json"))
    if not matches:
        raise FileNotFoundError(
            f"원고가 없습니다: {paths.POSTS}/{run_date}-*/content.json. "
            "먼저 compose 를 실행하세요."
        )
    return CardDeckFile.model_validate(paths.read_json(matches[0])), matches[0].parent
