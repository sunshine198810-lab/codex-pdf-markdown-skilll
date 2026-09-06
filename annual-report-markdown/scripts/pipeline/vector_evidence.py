"""矢量轮廓数字的独立复核证据链。

部分 PDF 把中文标签保留为文字对象，却把报表数字画成 path/curve。此模块只把
MinerU 结果登记为视觉复核候选，并生成源页裁图；候选单元格永不进入 facts。
"""

from __future__ import annotations

import html
import json
import pathlib
import re

from . import adapters, evidence_chain, naming


_NUMBER = re.compile(r"(?:^|[^0-9])(?:\(?-?\d[\d,]*(?:\.\d+)?\)?)(?:$|[^0-9])")
_TYPE = {
    "资产负债表": "balance_sheet",
    "利润表": "income_statement",
    "现金流量表": "cash_flow_statement",
}


def _point_bbox(bbox_norm, page: dict) -> list:
    if not bbox_norm or len(bbox_norm) != 4:
        return [0.0, 0.0, float(page["width_pt"]), float(page["height_pt"])]
    return [
        float(bbox_norm[0]) * float(page["width_pt"]) / 1000.0,
        float(bbox_norm[1]) * float(page["height_pt"]) / 1000.0,
        float(bbox_norm[2]) * float(page["width_pt"]) / 1000.0,
        float(bbox_norm[3]) * float(page["height_pt"]) / 1000.0,
    ]


def _title_on_page(page: dict) -> tuple[str | None, bool]:
    for line in page.get("lines", []):
        title, continuation, _ = naming.canonical_statement_title(
            line.get("text", ""), naming.MAIN_STATEMENT_TITLES
        )
        if title:
            return title, continuation
    return None, False


def _statement_metadata(title: str) -> tuple[str, str]:
    statement_type = next(value for suffix, value in _TYPE.items() if title.endswith(suffix))
    scope = "consolidated" if title.startswith("合并") else "parent"
    return statement_type, scope


def _overlaps_recovered(item: dict, page: dict, recovered: list) -> bool:
    box = _point_bbox(item.get("bbox_norm"), page)
    for candidate in recovered:
        if candidate.get("physical_page") != item.get("physical_page"):
            continue
        other = candidate.get("bbox") or []
        if len(other) != 4:
            continue
        ix = max(0.0, min(box[2], other[2]) - max(box[0], other[0]))
        iy = max(0.0, min(box[3], other[3]) - max(box[1], other[1]))
        if ix * iy >= 0.5 * max(1.0, (box[2] - box[0]) * (box[3] - box[1])):
            return True
    return False


def inspect(content_list_path, start_page0: int, pages: list, recovered: list | None = None) -> dict:
    """登记原生数字缺失、矢量对象密集且 OCR 给出表格的法定主表候选。"""
    recovered = recovered or []
    ingested = adapters.mineru_tables(content_list_path, start_page0=start_page0)
    page_by_number = {p["physical_page"]: p for p in pages}
    fragments = []
    for item in ingested["tables"]:
        page = page_by_number.get(item["physical_page"])
        if not page or _overlaps_recovered(item, page, recovered):
            continue
        title, continuation = _title_on_page(page)
        if not title:
            continue
        native_digits = int(page.get("native_digit_count", 0))
        curves = int(page.get("vector_curve_count", 0))
        grid = item.get("grid", {})
        numeric_cells = sum(
            bool(_NUMBER.search(str(cell.get("text") or cell.get("raw_value") or "")))
            for cell in grid.get("cells", [])
        )
        # 文字层没有任何数字、页面又包含大量绘制路径，才认定为轮廓数字疑似页。
        # MinerU 至少要给出二维表与多个数值候选；它仍不构成事实证据。
        if native_digits or curves < 50 or int(grid.get("rows", 0)) < 2 \
                or int(grid.get("cols", 0)) < 2 or numeric_cells < 2:
            continue
        statement_type, scope = _statement_metadata(title)
        # 多通道逐格一致性判定：当前只有 MinerU 一个独立通道，因此命中格子的
        # 一致性判定为 single_channel，仍不可引用或计算（见 evidence_chain）。
        channel_id = "mineru"
        evidence_chain.register_channel(
            channel_id, ingested.get("engine") or "mineru",
            engine_version=ingested.get("engine_version") or "unknown",
            method="mineru_pipeline_ocr",
            note="矢量轮廓数字的第一独立通道；第二通道（图像 OCR / PP-StructureV3）接入前不可交叉验证",
        )
        consistency = evidence_chain.fragment_consistency(grid, channel_id)
        cells = []
        for cell in grid.get("cells", []):
            copied = dict(cell)
            copied["eligible_for_calculation"] = False
            copied["candidate_status"] = "needs_review"
            copied["quality_flags"] = ["ocr_only", "no_native_numeric_evidence"]
            cells.append(copied)
        fragment_id = f"vector-review-p{item['physical_page']:04d}-{len(fragments)+1:02d}"
        fragments.append({
            "fragment_id": fragment_id,
            "physical_page": item["physical_page"],
            "statement_title": title,
            "statement_type": statement_type,
            "entity_scope": scope,
            "continuation": continuation,
            "bbox": _point_bbox(item.get("bbox_norm"), page),
            "bbox_norm": item.get("bbox_norm"),
            "grid": {"rows": grid.get("rows", 0), "cols": grid.get("cols", 0), "cells": cells},
            "native_digit_count": native_digits,
            "vector_curve_count": curves,
            "ocr_numeric_candidate_cells": numeric_cells,
            "structure_source": "mineru_vector_outline_review_candidate",
            "candidate_status": "needs_review",
            "eligible_for_calculation": False,
            "quality_flags": ["vector_outline_numeric_suspected", "ocr_structure_unverified"],
            "reason": "页面可见数字疑似由矢量轮廓绘制；文字层无数字，MinerU 网格仅供人工复核",
            "clip_ref": f"复核/{fragment_id}.png",
            "evidence_chain": consistency,
        })

    groups = {}
    for fragment in fragments:
        key = (fragment["statement_title"], fragment["statement_type"], fragment["entity_scope"])
        groups.setdefault(key, []).append(fragment)
    statements = []
    for n, ((title, statement_type, scope), items) in enumerate(groups.items(), 1):
        statements.append({
            "statement_id": f"vector-statement-{n:02d}",
            "statement_title": title,
            "statement_type": statement_type,
            "entity_scope": scope,
            "physical_pages": sorted({item["physical_page"] for item in items}),
            "fragments": [item["fragment_id"] for item in items],
            "candidate_status": "needs_review",
            "eligible_for_calculation": False,
            "reason": "原生数值证据缺失；OCR 表格不能升格为事实",
            "first_clip_ref": items[0]["clip_ref"],
        })
    return {
        "method": "native_text_digit_absence+vector_curve_density+mineru_candidate",
        "engine": ingested.get("engine"),
        "engine_version": ingested.get("engine_version"),
        "candidate_status": "needs_review",
        "eligible_for_calculation": False,
        "suspected_pages": sorted({item["physical_page"] for item in fragments}),
        "fragments": fragments,
        "statements": statements,
        "channels": evidence_chain.list_channels(),
        "evidence_chain": {
            "verdict_scope": "per_cell_single_channel",
            "second_channel_present": False,
            "sign_off_required_before_computable": True,
            "note": "当前仅 MinerU 一个独立通道；接入第二通道并完成人工签核前，矢量数值不可引用或计算",
        },
    }


def _grid_html(grid: dict) -> str:
    cells = {(int(c.get("row", 0)), int(c.get("col", 0))): c for c in grid.get("cells", [])}
    rows = []
    for ri in range(int(grid.get("rows", 0))):
        cols = []
        for ci in range(int(grid.get("cols", 0))):
            value = cells.get((ri, ci), {}).get("text", "")
            cols.append(f"<td>{html.escape(str(value))}</td>")
        rows.append("<tr>" + "".join(cols) + "</tr>")
    return "<table>" + "".join(rows) + "</table>"


def write_bundle(root: pathlib.Path, pdf_path: pathlib.Path, result: dict) -> list[str]:
    """写复核 JSON/HTML 与源 PDF 裁图。渲染失败不改变候选状态。"""
    if not result.get("fragments"):
        return []
    review_dir = root / "复核"
    review_dir.mkdir(parents=True, exist_ok=True)
    errors = []
    try:
        import pypdfium2 as pdfium  # type: ignore
        doc = pdfium.PdfDocument(str(pdf_path))
        scale = 2.0
        for item in result["fragments"]:
            try:
                image = doc[item["physical_page"] - 1].render(scale=scale).to_pil()
                x0, y0, x1, y1 = item["bbox"]
                crop = image.crop((int(x0 * scale), int(y0 * scale), int(x1 * scale), int(y1 * scale)))
                crop.save(root / item["clip_ref"], format="PNG")
            except Exception as exc:  # noqa: BLE001
                errors.append(f"p{item['physical_page']}: {type(exc).__name__}: {exc}")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"pypdfium2 unavailable: {exc}")
    result["render_errors"] = errors
    (review_dir / "vector-outline-index.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    sections = []
    for item in result["fragments"]:
        sections.append(
            f"<section><h2>{html.escape(item['statement_title'])} · 物理页 {item['physical_page']}</h2>"
            "<p><b>仅供人工复核：OCR 数值没有原生文字证据，不可引用或计算。</b></p>"
            f"<img src='{html.escape(pathlib.Path(item['clip_ref']).name)}' style='max-width:100%'>"
            + _grid_html(item["grid"]) + "</section>"
        )
    (review_dir / "vector-outline-index.html").write_text(
        "<!doctype html><html lang='zh-CN'><meta charset='utf-8'><title>矢量轮廓数字复核</title>"
        "<style>body{font:14px system-ui;margin:28px}table{border-collapse:collapse;margin:12px 0 36px}"
        "td{border:1px solid #aaa;padding:4px}img{border:1px solid #ccc}</style><body>"
        "<h1>矢量轮廓数字复核</h1>" + "".join(sections) + "</body></html>", encoding="utf-8"
    )
    return errors


capabilities = {
    "implemented": ["矢量轮廓数字疑似页检测", "OCR 候选隔离", "源页裁图复核",
                    "多通道逐格一致性判定（骨架；当前单通道→single_channel）",
                    "人工签核记录（复核/signoffs.jsonl，run_signoff.py）"],
    "pending": ["第二独立 OCR 交叉验证", "轮廓字形直接解码", "人工签核后升格"],
}
