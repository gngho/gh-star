"""피그마 디자인 → 코드 템플릿 동기화. SPEC.md 7.2.

일일 파이프라인에 포함되지 않는다. 디자인이 바뀔 때만 사람이 수동 실행한다.

## 추출과 생성을 분리한 이유

피그마에서 값을 꺼내오는 경로는 플랜에 따라 다르다:

- **Variables REST API 는 읽기(GET)도 Enterprise 전용이다.** 무료·student 플랜에서는
  403 이 난다. 명세 초안은 이 부분을 잘못 적었다.
- Figma MCP 를 통하면 플랜과 무관하게 값이 읽힌다. 다만 MCP 는 Claude 세션에
  붙어 있어서 헤드리스 CLI 가 직접 호출할 수 없다.

그래서 `design/tokens.json`(추출 결과, Git 커밋)을 경계로 둔다:

    피그마 ──[MCP 또는 REST]──▶ design/tokens.json ──[이 모듈]──▶ templates/tokens.css
              (자격증명 필요)         (커밋됨)           (순수 함수, 항상 동작)

생성 쪽은 네트워크도 토큰도 필요 없고 결정적이라 테스트가 쉽다. 커밋된 tokens.json
의 diff 가 곧 "디자인이 바뀌었다"는 신호가 된다.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import paths

log = logging.getLogger(__name__)

TOKENS_JSON = paths.ROOT / "design" / "tokens.json"
TOKENS_CSS = paths.ROOT / "templates" / "tokens.css"

# 피그마 변수 이름 → CSS 커스텀 프로퍼티 이름
PREFIX_MAP = {
    "color/": "--{name}",
    "size/": "--size-{name}",
    "space/": "--{name}",
}

# 피그마 변수로 표현하기 어려운 값들. 폰트 스택은 렌더러가 번들 폰트를 쓰고,
# 행간은 피그마의 PERCENT 단위와 CSS 의 unitless 가 1:1 로 대응하지 않는다.
# 여기서 관리하고 생성 시 항상 덧붙인다.
MANUAL_TOKENS = """\
  /* 아래는 피그마에서 오지 않는 값이다 (design_sync.py MANUAL_TOKENS 에서 관리).
     폰트 스택은 렌더러가 번들한 Pretendard 를 쓰고, 피그마 목업은 Noto Sans KR 을
     쓴다 — 피그마에 Pretendard 가 없기 때문이다. 그래서 시각 회귀 비교는
     픽셀 일치가 아니라 유사도 기준이어야 한다. */
  --font-sans: "Pretendard Variable", system-ui, -apple-system, sans-serif;
  --font-mono: ui-monospace, "Cascadia Mono", Consolas, monospace;

  --leading-title: 1.22;
  --leading-body: 1.62;
"""


class DesignSyncError(RuntimeError):
    pass


def css_var_name(token_name: str) -> str:
    """'color/fg-muted' → '--fg-muted', 'size/title' → '--size-title'."""
    for prefix, pattern in PREFIX_MAP.items():
        if token_name.startswith(prefix):
            return pattern.format(name=token_name[len(prefix) :])
    # 알 수 없는 네임스페이스는 슬래시만 하이픈으로 바꿔 그대로 살린다.
    return "--" + token_name.replace("/", "-")


def format_value(token: dict[str, Any]) -> str:
    kind = token.get("type")
    value = token.get("value")
    if kind == "COLOR":
        return str(value)
    if kind == "FLOAT":
        # 정수는 정수로 (88.0px 대신 88px)
        number = float(value)
        text = str(int(number)) if number.is_integer() else str(number)
        return f"{text}px"
    return str(value)


def render_css(data: dict[str, Any]) -> str:
    tokens = data.get("tokens") or {}
    if not tokens:
        raise DesignSyncError("tokens.json 에 토큰이 없습니다.")

    # 종류별로 묶어 사람이 읽기 좋게 배치한다.
    groups: dict[str, list[str]] = {"색": [], "타이포": [], "여백": [], "기타": []}
    for name in sorted(tokens):
        token = tokens[name]
        line = f"  {css_var_name(name)}: {format_value(token)};"
        if name.startswith("color/"):
            groups["색"].append(line)
        elif name.startswith("size/"):
            groups["타이포"].append(line)
        elif name.startswith("space/"):
            groups["여백"].append(line)
        else:
            groups["기타"].append(line)

    body = ""
    for label, lines in groups.items():
        if lines:
            body += f"\n  /* {label} */\n" + "\n".join(lines) + "\n"

    source = data.get("file_url") or data.get("file_key") or "(출처 미상)"
    exported = data.get("exported_at", "(시각 미상)")

    return f"""\
/* 이 파일은 자동 생성된다. 직접 고치지 마라.
 *
 *   생성: python -m agent design-sync
 *   원본: design/tokens.json  (피그마에서 추출)
 *   피그마: {source}
 *   추출 시각: {exported}
 *
 * 디자인을 바꾸려면 피그마에서 고친 뒤 토큰을 다시 추출하고 이 명령을 실행하라.
 */

:root {{
{body}
{MANUAL_TOKENS}}}
"""


def load_tokens(path: Path | None = None) -> dict[str, Any]:
    target = path or TOKENS_JSON
    data = paths.read_json(target, default=None)
    if data is None:
        raise DesignSyncError(
            f"토큰 파일이 없습니다: {target}\n"
            "피그마에서 토큰을 추출해 이 파일로 저장한 뒤 다시 실행하세요.\n"
            "(Figma MCP 로 로컬 변수를 덤프하거나, Enterprise 플랜이면 REST Variables API 사용)"
        )
    return data


def run(
    tokens_path: Path | None = None,
    css_path: Path | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    data = load_tokens(tokens_path)
    css = render_css(data)
    target = css_path or TOKENS_CSS

    previous = target.read_text(encoding="utf-8") if target.exists() else ""
    changed = previous != css

    if not dry_run:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(css, encoding="utf-8")

    return {
        "tokens": len(data.get("tokens") or {}),
        "css_path": str(target),
        "changed": changed,
        "source": data.get("file_url", ""),
        "synced_at": datetime.now(timezone.utc).isoformat(),
    }
