"""intake：输入、哈希、身份与体检（对应设计方案 §6）。

P0 可实现：文件哈希、大小、可选页数探测、基于文件名的身份占位。
P0 未实现：基于封面/重要提示/报表日期/编制基础的身份核对、逐区文字层质量体检（需引擎）。
"""

from __future__ import annotations

import hashlib
import pathlib
import re
from typing import Optional

from .version import SCHEMA_VERSION

_PDF_STEM_RE = re.compile(
    r"^(?P<code>\d{6})[_\-\s]+(?P<company>.+?)[_\-\s]+(?P<report>.+)$"
)


def sha256_of(path) -> str:
    """计算文件 SHA-256。"""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def file_size(path) -> int:
    return pathlib.Path(path).stat().st_size


def _try_page_count(path) -> Optional[int]:
    """可选：若安装了 pypdf / pdfplumber 则返回页数；否则返回 None。

    注意：页数可用不代表内容可靠（见 SKILL.md 状态声明）。不安装任何引擎也不报错。
    """
    try:
        from pypdf import PdfReader  # type: ignore

        return len(PdfReader(str(path)).pages)
    except Exception:
        pass
    try:
        import pdfplumber  # type: ignore

        with pdfplumber.open(str(path)) as pdf:
            return len(pdf.pages)
    except Exception:
        return None


def file_identity_of(path, *, page_count=None) -> dict:
    """构建符合 identity.schema 的 file_identity 块（文件级客观事实，无需引擎）。

    文件不存在时（如组装引用已移动/仅测试用路径）降级：sha256=None、
    file_size_bytes=None，如实标注 origin_path。调用方若必须真实文件，
    应自行核对（组装前 sha 一致性已强制，见 assemble.py）。
    """
    path = pathlib.Path(path)
    exists = path.is_file()
    return {
        "filename": path.name,
        "original_path": str(path.resolve()),
        "sha256": sha256_of(path) if exists else None,
        "page_count": page_count,
        "file_size_bytes": file_size(path) if exists else None,
        "source_url": None,
    }


def identity_from_filename(path) -> dict:
    """基于文件名的身份占位。**文件名只是线索**：返回 verification='filename'，
    真实身份须由封面/重要提示/报表日期/编制基础核对后覆盖（见 identity.schema.json）。
    自带 file_identity 使该块随时满足 identity.schema 的 required 字段。"""
    path = pathlib.Path(path)
    stem = path.stem
    identity = {
        "document_id": stem,
        "file_identity": file_identity_of(path, page_count=_try_page_count(path)),
        "verification": {"resolved_from": "filename"},
    }
    m = _PDF_STEM_RE.match(stem)
    if m:
        code, company, report = m.group("code"), m.group("company"), m.group("report")
        identity["document_id"] = f"{code}_{company}"
        identity["issuer_identity"] = {
            "company_name_as_reported": company,
            "securities": [{"code": code}],
        }
        identity["report_identity"] = {"report_type": report or None}
    return identity


def inspect_pdf(path) -> dict:
    """来源登记与体检（P0 骨架版）。不接线引擎，仅登记可得的客观事实。"""
    path = pathlib.Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"不是可读取的文件: {path}")
    page_count = _try_page_count(path)
    identity = identity_from_filename(path)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "document_id": identity.get("document_id"),
        "file_identity": {
            "filename": path.name,
            "original_path": str(path.resolve()),
            "sha256": sha256_of(path),
            "page_count": page_count,  # None 表示未探测（无引擎/不可读）
            "file_size_bytes": file_size(path),
            "source_url": None,
        },
        "identity": identity,
        "page_count_probe": {
            "obtained": page_count is not None,
            "note": "来自可选 pypdf/pdfplumber；页数齐全不代表内容可靠",
        },
        "capabilities": {
            "parsing_engines_wired": False,
            "p0_skeleton": True,
        },
    }
    return payload
