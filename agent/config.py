"""config.yaml 로드 및 환경변수 접근."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

from . import paths

load_dotenv(paths.ROOT / ".env")


@dataclass(frozen=True)
class Config:
    raw: dict[str, Any]

    def section(self, name: str) -> dict[str, Any]:
        value = self.raw.get(name)
        return value if isinstance(value, dict) else {}

    @property
    def collect(self) -> dict[str, Any]:
        return self.section("collect")

    @property
    def score(self) -> dict[str, Any]:
        return self.section("score")

    @property
    def select(self) -> dict[str, Any]:
        return self.section("select")


def load_config(path: Path | None = None) -> Config:
    target = path or paths.CONFIG
    if not target.exists():
        raise FileNotFoundError(f"설정 파일을 찾을 수 없습니다: {target}")
    data = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    return Config(raw=data)


def github_token() -> str | None:
    """GITHUB_TOKEN 우선, 없으면 GH_TOKEN 도 허용한다."""
    return os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or None
