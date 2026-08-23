"""프로젝트 경로 규약. SPEC.md 2.2 / 2.3 의 파일 경로를 한 곳에서 관리한다."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent

DATA = ROOT / "data"
SNAPSHOTS = DATA / "snapshots"
CANDIDATES = DATA / "candidates"
SELECTION = DATA / "selection"
RESEARCH = DATA / "research"
LOGS = DATA / "logs"
CACHE = DATA / "cache"

WATCHLIST = DATA / "watchlist.json"
PUBLISHED = DATA / "published.json"
BLOCKLIST = DATA / "blocklist.json"

POSTS = ROOT / "posts"
CONFIG = ROOT / "config.yaml"


def snapshot_path(date: str) -> Path:
    return SNAPSHOTS / f"{date}.json"


def candidates_path(date: str) -> Path:
    return CANDIDATES / f"{date}.json"


def selection_path(date: str) -> Path:
    return SELECTION / f"{date}.json"


def log_path(date: str) -> Path:
    return LOGS / f"{date}.json"


def ensure_dirs() -> None:
    for d in (SNAPSHOTS, CANDIDATES, SELECTION, RESEARCH, LOGS, CACHE, POSTS):
        d.mkdir(parents=True, exist_ok=True)


def read_json(path: Path, default: Any = None) -> Any:
    """없거나 깨진 파일은 default 로 처리한다. 파이프라인을 중단시키지 않는다."""
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return default


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )
