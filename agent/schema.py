"""Pydantic 모델 → 구조적 출력(json_schema)용 스키마 변환.

Agent SDK 의 output_format 은 Messages API 의 구조를 따른다:
    {"type": "json_schema", "schema": {...}}

Pydantic 이 만든 스키마는 additionalProperties 를 명시하지 않고 Optional 필드를
required 에서 빼기 때문에, 모델이 필드를 조용히 누락할 수 있다. 여기서 모든
객체에 additionalProperties: false 와 전체 required 를 강제한다.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


def _tighten(node: Any) -> None:
    if isinstance(node, list):
        for item in node:
            _tighten(item)
        return
    if not isinstance(node, dict):
        return

    if node.get("type") == "object" and "properties" in node:
        node["additionalProperties"] = False
        # 누락을 허용하면 모델이 어려운 필드를 조용히 빼먹는다. 전부 필수로 만든다.
        node["required"] = list(node["properties"].keys())

    for key, value in node.items():
        if key in ("properties", "$defs", "definitions") and isinstance(value, dict):
            for sub in value.values():
                _tighten(sub)
        elif key in ("items", "anyOf", "oneOf", "allOf", "prefixItems"):
            _tighten(value)


def json_schema_for(model: type[BaseModel]) -> dict[str, Any]:
    """모델의 엄격한 JSON Schema 를 반환한다."""
    schema = model.model_json_schema()
    _tighten(schema)
    return schema


def output_format_for(model: type[BaseModel]) -> dict[str, Any]:
    """ClaudeAgentOptions.output_format 에 그대로 넣을 값."""
    return {"type": "json_schema", "schema": json_schema_for(model)}
