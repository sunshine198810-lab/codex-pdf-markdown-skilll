"""Schema 加载工具（§17：主要输入输出先形成版本化 Schema）。

一个模块不能绕过 Schema 直接修改另一个模块的结果。
"""

from __future__ import annotations

import json
import pathlib

_SCHEMAS_DIR = pathlib.Path(__file__).resolve().parent.parent.parent / "schemas"

_SCHEMA_NAMES = {
    "identity": "identity.schema.json",
    "page": "page.schema.json",
    "object": "object.schema.json",
    "table": "table.schema.json",
    "logical-table": "logical-table.schema.json",
    "observation": "observation.schema.json",
    "evidence": "evidence.schema.json",
    "document-map": "document-map.schema.json",
    "quality": "quality.schema.json",
    "review-queue": "review-queue.schema.json",
    "signoff": "signoff.schema.json",
    "manifest": "manifest.schema.json",
    "run": "run.schema.json",
}


def schema_path(name: str) -> pathlib.Path:
    """返回某 schema 文件路径。name 用短名（如 'manifest'）或文件名。"""
    if name.endswith(".json"):
        return _SCHEMAS_DIR / name
    if name not in _SCHEMA_NAMES:
        raise KeyError(f"未知 schema: {name!r}; 可用: {sorted(_SCHEMA_NAMES)}")
    return _SCHEMAS_DIR / _SCHEMA_NAMES[name]


def load_schema(name: str) -> dict:
    """加载并解析某 schema JSON。"""
    with open(schema_path(name), encoding="utf-8") as fh:
        return json.load(fh)


def list_schemas() -> list:
    """列出 schemas/ 目录下全部 *.json（相对文件名）。"""
    return sorted(p.name for p in _SCHEMAS_DIR.glob("*.json"))
