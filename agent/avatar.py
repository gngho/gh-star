"""인스타그램 프로필 사진 생성 (Gemini 이미지 모델, 통칭 Nano Banana).

카드와 달리 프로필 사진은 매일 만드는 산출물이 아니다. 계정을 처음 만들 때
한 번, 브랜드를 바꿀 때 한 번 돌리는 도구다. 그래서 파이프라인(`run`)에는
넣지 않고 별도 명령으로 둔다.

색은 카드와 같은 출처(design/tokens.json)에서 읽는다. 프로필과 카드의 보라색이
어긋나면 피드에서 바로 티가 난다.

프로필 사진은 피드에서 지름 40px 원으로 잘린다. 그래서 프롬프트는 대부분
"작게 줄여도 읽히는가"를 강제하는 문장이다 — 글자 금지, 중앙 정렬, 여백 확보.
"""

from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

import httpx

from . import paths

log = logging.getLogger(__name__)

API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"

# "Nano Banana" = gemini-2.5-flash-image. Pro 는 gemini-3-pro-image.
# 이 키로 열려 있는 이미지 모델은 ListModels 로 확인했다 (2026-08 기준):
#   gemini-2.5-flash-image, gemini-3.1-flash-image, gemini-3.1-flash-lite-image,
#   gemini-3-pro-image (+ -preview). --model 로 아무거나 넣을 수 있다.
DEFAULT_MODEL = "gemini-2.5-flash-image"
PRO_MODEL = "gemini-3-pro-image"

OUT_DIR = paths.ROOT / "assets" / "profile"
CANVAS = 1080  # 인스타 프로필 원본 권장 상한. 표시는 320px 이하로 줄어든다.
TIMEOUT = 120.0


class AvatarError(RuntimeError):
    """프로필 사진 생성 실패. 파이프라인과 무관하므로 호출부가 그냥 보고한다."""


@dataclass(frozen=True)
class Concept:
    key: str
    label: str
    subject: str


# 셋을 한 번에 뽑아 눈으로 고르게 한다. 프로필 사진은 취향의 영역이라
# 한 장만 내밀면 결국 다시 돌리게 된다.
CONCEPTS: tuple[Concept, ...] = (
    Concept(
        key="star-surge",
        label="급상승 별",
        subject=(
            "a single bold five-pointed star, geometric with slightly rounded tips, "
            "tilted a few degrees, with two short motion streaks trailing down-left "
            "to suggest it is rocketing upward"
        ),
    ),
    Concept(
        key="terminal-star",
        label="터미널 별",
        subject=(
            "a minimal terminal window glyph: a rounded rectangle with a thin top bar, "
            "inside it a single chevron prompt mark and a small solid five-pointed star "
            "where the text cursor would blink"
        ),
    ),
    Concept(
        key="star-chart",
        label="별 그래프",
        subject=(
            "three ascending bars of increasing height, and above the tallest bar "
            "a single small five-pointed star, like a chart peaking into a star"
        ),
    ),
)


def _tokens() -> dict[str, str]:
    """카드와 같은 팔레트를 쓴다. 토큰이 없으면 생성 자체를 막는다 —
    임의의 보라색으로 만든 프로필은 카드와 나란히 놓였을 때 어긋난다."""
    data = paths.read_json(paths.ROOT / "design" / "tokens.json", default=None)
    if not data:
        raise AvatarError("design/tokens.json 이 없습니다. 색 기준 없이는 만들지 않습니다.")
    tokens = data.get("tokens", {})
    out: dict[str, str] = {}
    for name in ("color/bg", "color/accent", "color/accent-soft", "color/accent-2"):
        value = (tokens.get(name) or {}).get("value")
        if not value:
            raise AvatarError(f"design/tokens.json 에 {name} 이 없습니다.")
        out[name] = value
    return out


def build_prompt(concept: Concept, tokens: dict[str, str]) -> str:
    """아이콘 프롬프트. '프로필 사진'이 아니라 '앱 아이콘'이라고 못박는 게 핵심이다.

    프로필 사진이라고 하면 모델이 인물 사진 쪽으로 기운다.
    """
    bg = tokens["color/bg"]
    accent = tokens["color/accent"]
    accent_soft = tokens["color/accent-soft"]
    accent_2 = tokens["color/accent-2"]
    return (
        f"A flat vector app icon on a square canvas. Centered subject: {concept.subject}.\n"
        f"\n"
        f"Style: modern developer-tool branding, flat 2D vector illustration, crisp "
        f"geometric shapes, thick confident strokes, one subtle smooth gradient on the "
        f"main shape only. No photorealism, no 3D render, no bevel, no drop shadow, "
        f"no glossy highlight.\n"
        f"Palette, use these exact colors: background solid {bg}; main shape gradient "
        f"from {accent} to {accent_soft}; a single small accent detail in {accent_2}. "
        f"No other hues.\n"
        f"Composition: one subject only, perfectly centered, occupying about 60 percent "
        f"of the canvas with generous empty margin on all four sides so nothing is "
        f"clipped when the image is cropped into a circle. Flat solid background edge to "
        f"edge, no frame, no border, no rounded card behind the subject.\n"
        f"It must stay instantly readable when scaled down to 40 pixels: high contrast, "
        f"no thin lines, no fine detail, no texture, no pattern.\n"
        f"Absolutely no text, no letters, no numbers, no words, no lettering, no "
        f"watermark, no signature, no people, no faces, no hands."
    )


def _request_body(prompt: str, with_image_config: bool) -> dict[str, Any]:
    body: dict[str, Any] = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"responseModalities": ["IMAGE"]},
    }
    if with_image_config:
        # 모델/버전에 따라 없는 필드다. 400 이 나면 호출부가 빼고 재시도한다.
        body["generationConfig"]["imageConfig"] = {"aspectRatio": "1:1"}
    return body


def extract_image(payload: dict[str, Any]) -> bytes:
    """응답에서 첫 이미지 파트를 꺼낸다.

    빈 응답의 원인은 대부분 안전 필터라, 그 사유를 그대로 올려보낸다.
    조용히 실패하면 멀쩡한 프롬프트를 몇 번이고 다시 고치게 된다.
    """
    candidates = payload.get("candidates") or []
    if not candidates:
        feedback = payload.get("promptFeedback") or {}
        reason = feedback.get("blockReason") or "이유 미상"
        raise AvatarError(f"모델이 이미지를 반환하지 않았습니다 (차단: {reason}).")

    candidate = candidates[0]
    for part in (candidate.get("content") or {}).get("parts") or []:
        inline = part.get("inlineData") or part.get("inline_data")
        if inline and inline.get("data"):
            return base64.b64decode(inline["data"])

    finish = candidate.get("finishReason") or "이유 미상"
    raise AvatarError(f"응답에 이미지 파트가 없습니다 (finishReason: {finish}).")


def _quota_message(response: httpx.Response) -> str:
    """429 를 두 가지로 갈라 읽는다.

    이미지 모델은 무료 등급 쿼터가 `limit: 0` 이라, 결제를 켜지 않으면 첫 호출부터
    429 가 난다. 이걸 '잠시 뒤 재시도'로 안내하면 될 때까지 기다리게 된다 —
    영원히 안 된다. 재시도로 풀리는 진짜 레이트리밋과 반드시 구분해야 한다.
    """
    try:
        message = response.json().get("error", {}).get("message", "")
    except ValueError:
        message = response.text[:300]

    if "limit: 0" in message or "free_tier" in message:
        return (
            "무료 등급에서는 이미지 생성이 막혀 있습니다 (쿼터 limit: 0).\n"
            "  기다려도 풀리지 않습니다. 구글 클라우드 프로젝트에 결제를 연결해야 합니다:\n"
            "      https://aistudio.google.com/apikey → 해당 키의 프로젝트 → Set up Billing\n"
            "  참고: 이미지 1장당 약 $0.04 (컨셉 3종이면 약 $0.12)."
        )
    return f"Gemini 레이트리밋(429). 잠시 뒤 다시 실행하세요.\n  {message[:200]}"


def _call(client: httpx.Client, model: str, key: str, prompt: str) -> bytes:
    url = f"{API_BASE}/{model}:generateContent"
    headers = {"x-goog-api-key": key, "content-type": "application/json"}

    for with_image_config in (True, False):
        response = client.post(url, headers=headers, json=_request_body(prompt, with_image_config))
        if response.status_code == 400 and with_image_config:
            log.debug("imageConfig 미지원으로 보임, 빼고 재시도: %s", response.text[:200])
            continue
        if response.status_code == 429:
            raise AvatarError(_quota_message(response))
        if response.status_code in (401, 403):
            raise AvatarError(
                f"Gemini 인증 실패({response.status_code}). "
                f"GEMINI_API_KEY 를 확인하세요: {response.text[:200]}"
            )
        if response.status_code >= 400:
            raise AvatarError(f"Gemini 오류 {response.status_code}: {response.text[:300]}")
        return extract_image(response.json())

    raise AvatarError("Gemini 호출이 모두 실패했습니다.")  # 도달하지 않는다


def _to_square_jpeg(raw: bytes, size: int = CANVAS) -> bytes:
    """정사각 중앙 크롭 후 리사이즈. 인스타가 원형으로 다시 자르므로
    여기서 정사각을 보장해두지 않으면 좌우가 잘린다."""
    from PIL import Image

    image = Image.open(BytesIO(raw)).convert("RGB")
    side = min(image.size)
    left = (image.width - side) // 2
    top = (image.height - side) // 2
    image = image.crop((left, top, left + side, top + side))
    if image.width != size:
        image = image.resize((size, size), Image.LANCZOS)

    buffer = BytesIO()
    image.save(buffer, "JPEG", quality=92, optimize=True, subsampling=0)
    return buffer.getvalue()


def write_prompts(out_dir: Path = OUT_DIR) -> Path:
    """프롬프트를 파일로 떨군다.

    무료 등급에서는 API 로 이미지가 안 나오므로, AI Studio 웹에 붙여넣는 경로가
    현실적인 대안이다. 그때 프롬프트를 터미널에서 긁으면 줄바꿈이 깨진다.
    """
    tokens = _tokens()
    lines = [
        "# 프로필 사진 프롬프트",
        "",
        "https://aistudio.google.com 에서 이미지 모델을 고르고 아래를 그대로 붙여넣으세요.",
        "가로세로 비는 1:1 로 두고, 받은 파일은 이렇게 다듬습니다:",
        "",
        "    python -m agent avatar --import 내려받은파일.png",
        "",
        f"색 기준: design/tokens.json ({', '.join(f'{k}={v}' for k, v in tokens.items())})",
        "",
    ]
    for concept in CONCEPTS:
        lines += [
            f"## {concept.label} (`{concept.key}`)",
            "",
            "```",
            build_prompt(concept, tokens),
            "```",
            "",
        ]
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / "prompts.md"
    target.write_text("\n".join(lines), encoding="utf-8")
    return target


def import_files(sources: list[Path], out_dir: Path = OUT_DIR) -> list[dict[str, Any]]:
    """웹에서 받은 이미지를 인스타 프로필 규격으로 다듬는다.

    AI Studio 가 주는 파일은 비율도 크기도 제각각이다. 그대로 올리면 인스타가
    임의로 잘라서, 공들여 넣은 여백이 사라진다.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []

    for source in sources:
        if not source.exists():
            raise AvatarError(f"파일이 없습니다: {source}")
        # 원래 파일명을 유지한다. 임의로 번호를 붙이면 어떤 컨셉이었는지
        # 알 수 없게 된다 — 컨셉 키로 저장해두면 나중에 비교가 쉽다.
        target = out_dir / f"{source.stem}.jpg"
        target.write_bytes(_to_square_jpeg(source.read_bytes()))
        results.append({"source": str(source), "file": target.name})
    return results


def run(
    api_key: str,
    *,
    model: str = DEFAULT_MODEL,
    concepts: tuple[Concept, ...] = CONCEPTS,
    out_dir: Path = OUT_DIR,
    dry_run: bool = False,
) -> dict[str, Any]:
    tokens = _tokens()
    items: list[dict[str, Any]] = []

    if dry_run:
        # 키 없이 프롬프트만 확인할 수 있어야 한다. 이미지 한 장이 곧 요금이다.
        for concept in concepts:
            items.append(
                {
                    "key": concept.key,
                    "label": concept.label,
                    "prompt": build_prompt(concept, tokens),
                    "file": None,
                }
            )
        return {"model": model, "dir": str(out_dir), "images": items, "failures": [], "dry_run": True}

    out_dir.mkdir(parents=True, exist_ok=True)
    failures: list[dict[str, str]] = []

    with httpx.Client(timeout=TIMEOUT) as client:
        for concept in concepts:
            prompt = build_prompt(concept, tokens)
            try:
                raw = _call(client, model, api_key, prompt)
            except AvatarError as exc:
                # 한 컨셉이 막혀도 나머지는 뽑는다. 셋 다 실패해야 실패다.
                log.warning("%s 실패: %s", concept.key, exc)
                failures.append({"key": concept.key, "error": str(exc)})
                continue

            target = out_dir / f"{concept.key}.jpg"
            target.write_bytes(_to_square_jpeg(raw))
            items.append(
                {
                    "key": concept.key,
                    "label": concept.label,
                    "prompt": prompt,
                    "file": target.name,
                }
            )

    if not items:
        # 셋 다 같은 이유로 죽는 게 보통이다 (쿼터·인증). 같은 문단을 세 번
        # 찍으면 정작 읽어야 할 안내가 묻힌다.
        reasons = list(dict.fromkeys(f["error"] for f in failures))
        raise AvatarError("모든 컨셉이 실패했습니다.\n  " + "\n  ".join(reasons))

    meta = {
        "model": model,
        "size": CANVAS,
        "tokens_source": "design/tokens.json",
        "images": items,
        "failures": failures,
    }
    (out_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return {"model": model, "dir": str(out_dir), "images": items, "failures": failures}
