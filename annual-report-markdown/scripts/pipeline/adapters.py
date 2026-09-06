"""adapters：第三方引擎到统一结构的转换（对应设计方案 §14、§7.1）。

只登记候选与规则，不安装、不导入引擎。接线后每个 adapter 须把引擎原始输出转换为
统一对象结构（页、块、单元格、图片、坐标、来源、候选状态），并记录引擎/模型版本。
"""

from __future__ import annotations

from .version import ENGINE_CANDIDATES

_ADAPTERS = {}


def list_candidates() -> dict:
    """返回候选引擎登记（含职责/许可/是否选中）。"""
    return ENGINE_CANDIDATES


def register(name: str, adapter) -> None:
    """注册 adapter。名称与 ENGINE_CANDIDATES 解耦（候选登记≠已接线 adapter）。"""
    if not name or not callable(adapter):
        raise ValueError("register 需要非空 name 与可调用 adapter")
    _ADAPTERS[name] = adapter


def list_registered() -> list:
    return sorted(_ADAPTERS)


def run_adapter(name: str, *args, **kwargs):
    """运行某引擎 adapter。已接线：pdfplumber_evidence、mineru_tables。"""
    if name in _ADAPTERS:
        return _ADAPTERS[name](*args, **kwargs)
    raise NotImplementedError(
        f"引擎 adapter '{name}' 尚未接线。当前已接线: {sorted(_ADAPTERS)}。"
    )


# ---------------------------------------------------------------------------
# 已接线 adapter（候选/证据层，见 references/engines-probe.md）
# ---------------------------------------------------------------------------
import json  # noqa: E402
import pathlib  # noqa: E402
from importlib import metadata as _md  # noqa: E402

from . import tables as _tables  # noqa: E402


def _engine_version(pkg: str) -> str:
    try:
        return _md.version(pkg)
    except Exception:
        return "unknown"


def pdfplumber_evidence(pdf_path, pages=None):
    """pdfplumber 词级证据 adapter。

    返回 {"engine":"pdfplumber","engine_version":...,"page_count":N,
          "pages":[{physical_page,width_pt,height_pt,words:[{text,x0,x1,top,bottom}]}]}
    坐标=工作坐标（左上角原点，point）。pages 为 {1,2,...} 过滤器（None=全部）。
    """
    import pdfplumber  # type: ignore

    pdf_path = str(pdf_path)
    out_pages = []
    page_count = 0
    with pdfplumber.open(pdf_path) as pdf:
        page_count = len(pdf.pages)
        for i, page in enumerate(pdf.pages, start=1):
            if pages and i not in pages:
                continue
            words = []
            for w in page.extract_words():
                words.append(
                    {
                        "text": w["text"],
                        "x0": float(w["x0"]),
                        "x1": float(w["x1"]),
                        "top": float(w["top"]),
                        "bottom": float(w["bottom"]),
                    }
                )
            out_pages.append(
                {
                    "physical_page": i,
                    "width_pt": float(page.width),
                    "height_pt": float(page.height),
                    "words": words,
                }
            )
    return {
        "engine": "pdfplumber",
        "engine_version": _engine_version("pdfplumber"),
        "page_count": page_count,
        "pages": out_pages,
    }


def _text_of(value) -> str:
    """从 caption/footnote（可能是 str / list[dict] / list[str]）递归取纯文本。"""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for v in value:
            t = _text_of(v)
            if t:
                parts.append(t)
        return "".join(parts)
    if isinstance(value, dict):
        for k in ("text", "content", "body"):
            if k in value:
                return _text_of(value[k])
        return ""
    return str(value)


def mineru_tables(content_list_path, start_page0: int = 0):
    """MinerU `content_list.json` → 统一“表格结构候选”列表。

    处理设计 §7.1 的两个 MinerU 口径：
      - page_idx 从 0 起始且相对解析切片起点 → physical_page = start_page0 + page_idx + 1；
      - bbox 为引擎归一化坐标（0–1000 示例），按原样保存为 bbox_norm，不作伪精确换算。

    返回 {"engine":"mineru","engine_version":..., "tables":[{...}]}，每个表：
      fragment_id / physical_page / caption_candidates / footnote_candidates /
      bbox_norm / img_path / grid(rows,cols,cells) / candidate_status:"extracted"
    caption 是候选（MinerU 会“过度捕获”前文行），candidate_status 不给已核实资格。
    """
    p = pathlib.Path(content_list_path)
    blocks = json.loads(p.read_text(encoding="utf-8"))
    tables = []
    for n, b in enumerate(blocks):
        if not isinstance(b, dict) or b.get("type") != "table":
            continue
        page_idx = int(b.get("page_idx", 0))
        html = b.get("table_body", "")
        grid = _tables.html_to_grid(html) if isinstance(html, str) else {"rows": 0, "cols": 0, "cells": []}
        frag_id = f"frag-{start_page0 + page_idx + 1}-t{n:03d}"
        tables.append(
            {
                "fragment_id": frag_id,
                "physical_page": start_page0 + page_idx + 1,
                "page_idx": page_idx,
                "caption_candidates": [_text_of(c) for c in (b.get("table_caption") or []) if _text_of(c)],
                "footnote_candidates": [_text_of(c) for c in (b.get("table_footnote") or []) if _text_of(c)],
                "bbox_norm": b.get("bbox"),
                "img_path": b.get("img_path"),
                "grid": {"rows": grid.get("rows", 0), "cols": grid.get("cols", 0), "cells": grid.get("cells", [])},
                "candidate_status": "extracted",
            }
        )
    return {
        "engine": "mineru",
        "engine_version": _engine_version("mineru"),
        "table_count": len(tables),
        "tables": tables,
    }


register("pdfplumber_evidence", pdfplumber_evidence)
register("mineru_tables", mineru_tables)


capabilities = {
    "engine_wired": True,
    "implemented": [
        "引擎候选登记、adapter 注册表",
        "pdfplumber_evidence：词级坐标证据（工作坐标 point）",
        "mineru_tables：content_list → 表格结构候选（0 起始 page_idx 偏移换算、rowspan/colspan、caption/footnote）",
    ],
    "pending": [
        "Docling / PP-StructureV3 适配",
        "MinerU bbox 归一化→point 的精确换算（需与渲染页尺寸标定）",
        "跨页逻辑表拼接与语义绑定（V0.2 后段）",
    ],
}
