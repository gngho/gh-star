"""프로필 사진 생성의 네트워크 없는 부분만 검증한다.

이미지 생성 자체는 비결정적이고 요금이 붙으므로 테스트하지 않는다.
대신 프롬프트가 브랜드 토큰에서 나오는지, 응답 파싱이 조용히 실패하지
않는지를 잡는다 — 이 둘이 실제로 틀렸던 자리다.
"""

from __future__ import annotations

import base64

import pytest

from agent import avatar

TOKENS = {
    "color/bg": "#0b0d10",
    "color/accent": "#7c5cff",
    "color/accent-soft": "#a68dff",
    "color/accent-2": "#22d3ee",
}


def test_prompt_carries_brand_colors():
    prompt = avatar.build_prompt(avatar.CONCEPTS[0], TOKENS)
    for value in TOKENS.values():
        assert value in prompt


def test_prompt_forbids_text_and_faces():
    # 프로필 아이콘에 글자나 얼굴이 들어가면 40px 원에서 죽는다.
    prompt = avatar.build_prompt(avatar.CONCEPTS[0], TOKENS).lower()
    for banned in ("no text", "no letters", "no faces", "no watermark"):
        assert banned in prompt


def test_every_concept_builds():
    keys = {c.key for c in avatar.CONCEPTS}
    assert len(keys) == len(avatar.CONCEPTS)  # 키가 겹치면 파일이 덮어써진다
    for concept in avatar.CONCEPTS:
        assert concept.subject in avatar.build_prompt(concept, TOKENS)


def test_extract_image_decodes_inline_data():
    raw = b"\x89PNG\r\n\x1a\n fake"
    payload = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {"text": "여기 있습니다"},
                        {"inlineData": {"mimeType": "image/png",
                                        "data": base64.b64encode(raw).decode()}},
                    ]
                }
            }
        ]
    }
    assert avatar.extract_image(payload) == raw


def test_extract_image_accepts_snake_case_key():
    raw = b"bytes"
    payload = {
        "candidates": [
            {"content": {"parts": [{"inline_data": {"data": base64.b64encode(raw).decode()}}]}}
        ]
    }
    assert avatar.extract_image(payload) == raw


def test_blocked_prompt_surfaces_reason():
    payload = {"candidates": [], "promptFeedback": {"blockReason": "SAFETY"}}
    with pytest.raises(avatar.AvatarError, match="SAFETY"):
        avatar.extract_image(payload)


def test_text_only_response_surfaces_finish_reason():
    payload = {"candidates": [{"content": {"parts": [{"text": "못 만들겠습니다"}]},
                               "finishReason": "IMAGE_SAFETY"}]}
    with pytest.raises(avatar.AvatarError, match="IMAGE_SAFETY"):
        avatar.extract_image(payload)


def test_image_config_is_droppable():
    # 400 재시도 경로가 실제로 다른 바디를 보내야 의미가 있다.
    with_config = avatar._request_body("p", True)
    without = avatar._request_body("p", False)
    assert with_config["generationConfig"]["imageConfig"]["aspectRatio"] == "1:1"
    assert "imageConfig" not in without["generationConfig"]


def _response(status: int, body: dict) -> "httpx.Response":
    import httpx

    return httpx.Response(status, json=body)


def test_free_tier_quota_is_not_reported_as_retryable():
    # 무료 등급 이미지 쿼터는 limit: 0 이라 기다려도 안 풀린다.
    # 여기서 '잠시 뒤 재시도'라고 안내하면 영원히 기다리게 된다.
    message = avatar._quota_message(
        _response(429, {"error": {"message": "Quota exceeded ... limit: 0, model: x"}})
    )
    assert "잠시 뒤" not in message
    assert "결제" in message


def test_real_rate_limit_is_reported_as_retryable():
    message = avatar._quota_message(
        _response(429, {"error": {"message": "Resource exhausted, retry in 12s"}})
    )
    assert "잠시 뒤" in message


def test_dry_run_makes_no_request_and_writes_nothing(tmp_path):
    out = tmp_path / "profile"
    result = avatar.run("", out_dir=out, dry_run=True)
    assert len(result["images"]) == len(avatar.CONCEPTS)
    assert all(item["file"] is None for item in result["images"])
    assert not out.exists()


def test_prompts_sheet_contains_every_concept(tmp_path):
    target = avatar.write_prompts(out_dir=tmp_path)
    text = target.read_text(encoding="utf-8")
    for concept in avatar.CONCEPTS:
        assert concept.key in text
        assert concept.subject in text


def test_import_squares_a_downloaded_image(tmp_path):
    from io import BytesIO

    from PIL import Image

    source = tmp_path / "star-surge.png"
    Image.new("RGB", (1408, 768), "#7c5cff").save(source)

    out = tmp_path / "profile"
    results = avatar.import_files([source], out_dir=out)

    assert results[0]["file"] == "star-surge.jpg"
    # 인스타가 원형으로 다시 자르므로 정사각이 아니면 여백 설계가 무너진다.
    assert Image.open(out / "star-surge.jpg").size == (avatar.CANVAS, avatar.CANVAS)


def test_import_reports_missing_file(tmp_path):
    with pytest.raises(avatar.AvatarError, match="파일이 없습니다"):
        avatar.import_files([tmp_path / "없는파일.png"], out_dir=tmp_path)


def test_square_jpeg_crops_to_center_square():
    from io import BytesIO

    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", (1600, 900), "#7c5cff").save(buffer, "PNG")
    out = Image.open(BytesIO(avatar._to_square_jpeg(buffer.getvalue(), size=256)))
    assert out.size == (256, 256)
