"""카드 상단 이미지 생성. SPEC.md 7.4.

**GitHub 이 제공하는 자산만 쓴다.** 레포의 README 스크린샷이나 로고를 가져다
쓰지 않는다 — 저작권이 레포 소유자에게 있고 라이선스가 코드만 다룰 수 있다.

쓰는 것:
- OG 이미지 (`opengraph.githubassets.com`) — GitHub 이 임베드용으로 만들어 제공
- 소유자 아바타 (`github.com/{owner}.png`)

같은 OG 이미지를 8장에 반복하면 캐러셀이 단조로워지므로 역할별로 다르게 쓴다:
- outro : OG 이미지 그대로 (레포 카드 자체가 마무리에 어울린다)
- 그 외 : 아바타를 크게 흐린 색면 + 선명한 아바타를 얹은 조합.
          카드 번호에 따라 그라디언트 방향을 바꿔 같은 그림이 반복되지 않게 한다.
- cover : 이미지를 쓰지 않는다 (제목과 배지가 이미 시선을 잡는다)
"""

from __future__ import annotations

import logging
from io import BytesIO
from pathlib import Path

import httpx
from PIL import Image, ImageDraw, ImageFilter

from . import paths
from .models import CardDeckFile

log = logging.getLogger(__name__)

W, H = 1080, 470
TIMEOUT = 30.0
USER_AGENT = "gh-cardnews-agent (+https://github.com/)"

OG_URL = "https://opengraph.githubassets.com/1/{repo}"
AVATAR_URL = "https://github.com/{owner}.png?size=460"

# 이미지를 쓰지 않는 역할
NO_IMAGE = {"cover"}
# OG 이미지를 쓰는 역할
OG_ROLES = {"outro"}


class ImageryError(RuntimeError):
    pass


def run(deck: CardDeckFile, post_dir: Path, dry_run: bool = False) -> dict[str, object]:
    owner = deck.repo.split("/")[0]
    out_dir = post_dir / "images"

    og = _fetch_image(OG_URL.format(repo=deck.repo))
    avatar = _fetch_image(AVATAR_URL.format(owner=owner))

    if og is None and avatar is None:
        raise ImageryError(
            "GitHub 에서 OG 이미지와 아바타를 모두 가져오지 못했습니다. "
            "이미지 없이 렌더하려면 이 단계를 건너뛰세요."
        )

    if not dry_run:
        out_dir.mkdir(parents=True, exist_ok=True)

    written: list[dict[str, object]] = []
    for card in deck.payload.cards:
        if card.role in NO_IMAGE:
            continue

        if card.role in OG_ROLES and og is not None:
            image, source = _from_og(og), "og"
        elif avatar is not None:
            image, source = _from_avatar(avatar, card.index), "avatar"
        elif og is not None:
            image, source = _from_og(og), "og"
        else:
            continue

        target = out_dir / f"{card.index:02d}.jpg"
        if not dry_run:
            image.save(target, format="JPEG", quality=88, optimize=True)
        written.append({"index": card.index, "role": card.role, "source": source})

    return {
        "repo": deck.repo,
        "og_available": og is not None,
        "avatar_available": avatar is not None,
        "images": written,
        "dir": str(out_dir),
    }


# ------------------------------------------------------------------ 조합


BG = (11, 13, 16)  # color/bg — 이미지를 카드 배경 쪽으로 눌러 통일한다


def _from_og(og: Image.Image) -> Image.Image:
    """OG 이미지를 카드 비율로 잘라 배경 쪽으로 눌러 넣는다.

    GitHub 의 OG 이미지는 흰 배경이라 다크 카드 위에 그대로 얹으면 위아래가
    서로 다른 디자인처럼 따로 논다. 배경색과 섞어 한 장으로 읽히게 만든다.
    글자는 장식 수준으로만 남는데, 어차피 같은 정보를 본문이 다시 말한다.
    """
    fitted = _cover_crop(og, W, H)
    damped = Image.blend(fitted, Image.new("RGB", (W, H), BG), 0.62)
    return _fade_bottom(damped)


def _from_avatar(avatar: Image.Image, index: int) -> Image.Image:
    """아바타에서 색면을 만들고 그 위에 선명한 아바타를 얹는다.

    아바타 하나로 여러 장을 만들면 똑같아지므로, 카드 번호로 그라디언트 방향과
    확대 위치를 바꿔 장마다 다르게 보이도록 한다.
    """
    # 배경: 크게 확대해 흐린 색면
    blurred = _cover_crop(avatar, int(W * 1.6), int(H * 1.6))
    blurred = blurred.filter(ImageFilter.GaussianBlur(48))
    # 카드 번호에 따라 잘라내는 위치를 옮긴다
    shift = (index * 97) % max(1, blurred.width - W)
    shift_y = (index * 53) % max(1, blurred.height - H)
    canvas = blurred.crop((shift, shift_y, shift + W, shift_y + H))

    # 어둡게 눌러 텍스트 대비를 확보한다
    canvas = Image.blend(canvas, Image.new("RGB", (W, H), BG), 0.55)

    # 방향이 도는 그라디언트를 덧씌운다.
    # 잘라내는 위치만 옮기는 방식은 아바타가 단색이면 아무 차이가 없다 —
    # 흑백 로고를 쓰는 레포가 흔해서 실제로 8장이 똑같아졌다.
    canvas = _angled_shade(canvas, index)

    # 전경: 선명한 아바타를 원형으로
    size = 220
    face = avatar.resize((size, size), Image.LANCZOS)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size, size), fill=255)
    canvas.paste(face, ((W - size) // 2, (H - size) // 2 - 20), mask)

    return _fade_bottom(canvas)


def _angled_shade(img: Image.Image, index: int) -> Image.Image:
    """카드 번호마다 방향이 도는 음영을 얹어 장마다 다르게 보이게 한다.

    소스 이미지가 단색이어도 결과가 달라진다는 점이 핵심이다.
    """
    angle = (index * 47) % 360
    gradient = Image.linear_gradient("L").rotate(angle, expand=True, resample=Image.BILINEAR)
    # 회전으로 생긴 빈 모서리를 피해 가운데를 잘라 쓴다
    side = min(gradient.size)
    left = (gradient.width - side) // 2
    top = (gradient.height - side) // 2
    mask = gradient.crop((left, top, left + side, top + side)).resize((W, H), Image.BILINEAR)
    # 음영 세기를 절반으로 눌러 과하지 않게
    mask = mask.point(lambda v: int(v * 0.45))
    return Image.composite(Image.new("RGB", (W, H), BG), img, mask)


def _cover_crop(img: Image.Image, w: int, h: int) -> Image.Image:
    """비율을 유지하며 채우고 중앙을 잘라낸다 (CSS background-size: cover)."""
    src = img.convert("RGB")
    scale = max(w / src.width, h / src.height)
    resized = src.resize((max(1, round(src.width * scale)), max(1, round(src.height * scale))), Image.LANCZOS)
    left = (resized.width - w) // 2
    top = (resized.height - h) // 2
    return resized.crop((left, top, left + w, top + h))


def _fade_bottom(img: Image.Image) -> Image.Image:
    """아래쪽을 카드 배경색으로 녹인다. HTML 쪽 그라디언트와 이중으로 겹쳐도
    자연스럽고, 이미지가 밝을 때 본문 첫 줄이 묻히는 것을 막는다."""
    out = img.copy()
    fade_h = int(H * 0.34)
    overlay = Image.new("RGB", (W, fade_h), BG)
    mask = Image.new("L", (W, fade_h))
    draw = ImageDraw.Draw(mask)
    for y in range(fade_h):
        draw.line([(0, y), (W, y)], fill=int(255 * (y / fade_h) ** 1.4))
    out.paste(overlay, (0, H - fade_h), mask)
    return out


# ------------------------------------------------------------------ 수집


def _fetch_image(url: str) -> Image.Image | None:
    """실패해도 예외를 던지지 않는다. 이미지가 없으면 그 카드는 텍스트만으로 간다."""
    try:
        with httpx.Client(
            headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT, follow_redirects=True
        ) as client:
            response = client.get(url)
        if response.status_code != 200:
            log.warning("이미지 요청 실패 (%s): %s", response.status_code, url)
            return None
        return Image.open(BytesIO(response.content)).convert("RGB")
    except (httpx.HTTPError, OSError) as exc:
        log.warning("이미지를 열 수 없습니다 (%s): %s", type(exc).__name__, url)
        return None


def load_deck(run_date: str) -> tuple[CardDeckFile, Path]:
    matches = sorted(paths.POSTS.glob(f"{run_date}-*/content.json"))
    if not matches:
        raise FileNotFoundError(
            f"원고가 없습니다: {paths.POSTS}/{run_date}-*/content.json. "
            "먼저 compose 를 실행하세요."
        )
    return CardDeckFile.model_validate(paths.read_json(matches[0])), matches[0].parent
