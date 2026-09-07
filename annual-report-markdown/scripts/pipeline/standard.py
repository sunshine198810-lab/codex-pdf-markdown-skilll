"""M2 标准解析：原生文字财报的完整、保守研究包闭环。

完整登记每个物理页、文字行、表格候选与嵌入图片，并绑定可回查证据。
M2 仅对通过边界、几何、语义与勾稽规则的法定主表单元格开放计算资格。
"""

from __future__ import annotations

import datetime
import html
import json
import pathlib
import re
import shutil
import tempfile
import time

from . import borderless, equity, evidence_chain, exports, financials, general_tables, intake, layout, movement, sections, tables, validation, vector_evidence
from .version import PIPELINE_VERSION, SCHEMA_VERSION, SKILL_NAME


def _now() -> str:
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def _bbox(obj) -> list:
    return [float(obj["x0"]), float(obj["top"]), float(obj["x1"]), float(obj["bottom"])]


def _inside(inner: list, outer: list, slack: float = 2.0) -> bool:
    cx = (inner[0] + inner[2]) / 2
    cy = (inner[1] + inner[3]) / 2
    return outer[0] - slack <= cx <= outer[2] + slack and outer[1] - slack <= cy <= outer[3] + slack


def _extract_pages(pdf_path: pathlib.Path) -> tuple[list, str]:
    import pdfplumber  # type: ignore

    pages = []
    version = getattr(pdfplumber, "__version__", "unknown")
    with pdfplumber.open(str(pdf_path)) as pdf:
        for number, page in enumerate(pdf.pages, 1):
            page_data = {
                "physical_page": number,
                "width_pt": float(page.width),
                "height_pt": float(page.height),
                "rotation": int(page.rotation or 0),
                "words": [],
                "lines": [],
                "tables": [],
                "figures": [],
                "error": None,
            }
            try:
                page_data["words"] = [
                    {
                        "text": str(w.get("text", "")),
                        "x0": float(w["x0"]),
                        "x1": float(w["x1"]),
                        "top": float(w["top"]),
                        "bottom": float(w["bottom"]),
                    }
                    for w in page.extract_words(x_tolerance=2, y_tolerance=3)
                ]
                page_data["native_digit_count"] = sum(
                    ch.isdigit() for word in page_data["words"] for ch in word.get("text", "")
                )
                page_data["vector_curve_count"] = len(page.curves)
                page_data["lines"] = layout.words_to_lines(page_data["words"])
                for table_number, found in enumerate(page.find_tables(), 1):
                    try:
                        raw_rows = found.extract(x_tolerance=2, y_tolerance=3) or []
                    except TypeError:
                        raw_rows = found.extract() or []
                    grid = tables.extracted_rows_to_grid(raw_rows)
                    # pdfplumber 的 TableRow.cells 与抽取矩阵一一对应；把物理单元格框
                    # 写回候选网格，供 M2 建立逐格证据。合并空位可能为 None。
                    row_geometries = getattr(found, "rows", [])
                    for cell in grid["cells"]:
                        ri, ci = cell["row"], cell["col"]
                        if ri < len(row_geometries):
                            boxes = getattr(row_geometries[ri], "cells", [])
                            if ci < len(boxes) and boxes[ci] is not None:
                                cell["bbox"] = [float(v) for v in boxes[ci]]
                    # 单格“表”通常是边框文本框，不当作结构化表格候选。
                    nonempty = sum(1 for c in grid["cells"] if c.get("text"))
                    if grid["rows"] < 2 or grid["cols"] < 2 or nonempty < 3:
                        continue
                    page_data["tables"].append(
                        {
                            "local_number": table_number,
                            "bbox": [float(v) for v in found.bbox],
                            "grid": grid,
                        }
                    )
                for image_number, image in enumerate(page.images, 1):
                    if not all(k in image for k in ("x0", "x1", "top", "bottom")):
                        continue
                    box = _bbox(image)
                    if (box[2] - box[0]) * (box[3] - box[1]) < 400:
                        continue
                    page_data["figures"].append(
                        {"local_number": image_number, "bbox": box, "source_type": "embedded_raster"}
                    )
            except Exception as exc:  # noqa: BLE001
                page_data["error"] = f"{type(exc).__name__}: {exc}"
            pages.append(page_data)
    return pages, version


def _render_figure_clips(pdf_path: pathlib.Path, figures: list, root: pathlib.Path) -> list[str]:
    """按 PDF 可见页裁图；失败不影响文字主流程，错误返回给 review queue。"""
    errors = []
    if not figures:
        return errors
    try:
        import pypdfium2 as pdfium  # type: ignore
    except Exception as exc:  # noqa: BLE001
        return [f"pypdfium2 unavailable: {exc}"]
    doc = pdfium.PdfDocument(str(pdf_path))
    scale = 2.0
    by_page = {}
    for fig in figures:
        by_page.setdefault(fig["physical_page"], []).append(fig)
    for page_no, specs in by_page.items():
        try:
            bitmap = doc[page_no - 1].render(scale=scale)
            image = bitmap.to_pil()
            for spec in specs:
                x0, y0, x1, y1 = spec["bbox"]
                crop = image.crop((int(x0 * scale), int(y0 * scale), int(x1 * scale), int(y1 * scale)))
                target = root / spec["image_ref"]
                target.parent.mkdir(parents=True, exist_ok=True)
                crop.save(target, format="PNG")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"p{page_no}: {type(exc).__name__}: {exc}")
    return errors


def _write_review_html(root: pathlib.Path, queue: list) -> None:
    rows = []
    for item in queue:
        rows.append(
            "<tr><td>{}</td><td>{}</td><td><code>{}</code></td><td>{}</td><td>{}</td></tr>".format(
                item["review_id"], item["priority"], html.escape(item["object_ref"]),
                html.escape(item["reason"]), html.escape(item.get("next_step") or ""),
            )
        )
    body = "\n".join(rows) or "<tr><td colspan='5'>无待复核项</td></tr>"
    (root / "复核/index.html").write_text(
        "<!doctype html><html lang='zh-CN'><meta charset='utf-8'><title>复核队列</title>"
        "<style>body{font:14px system-ui;margin:32px}table{border-collapse:collapse;width:100%}"
        "td,th{border:1px solid #bbb;padding:7px;vertical-align:top}</style><body>"
        "<h1>复核队列</h1><p>优先级数字越小越重要。M2 仅允许通过规则的法定主表单元格计算，其余保持候选。</p>"
        "<table><thead><tr><th>ID</th><th>优先级</th><th>对象</th><th>原因</th><th>下一步</th>"
        f"</tr></thead><tbody>{body}</tbody></table></body></html>",
        encoding="utf-8",
    )


def _entry_markdown(manifest: dict, table_items: list, figure_items: list, review_count: int,
                    financial_result: dict, vector_result: dict) -> str:
    src = manifest["source_pdf"]
    identity = manifest.get("identity", {})
    issuer = identity.get("issuer_identity", {})
    return "\n".join(
        [
            "# 年报研究包阅读入口",
            "",
            f"> 状态：**{manifest['overall_state']}** · 解析管线 `{PIPELINE_VERSION}`",
            "",
            "## 报告身份",
            "",
            f"- 公司：{issuer.get('company_name_as_reported', '待核对')}",
            f"- 原 PDF：`{src['path']}`",
            f"- 物理页数：{src.get('page_count')}",
            f"- SHA-256：`{src['sha256']}`",
            "- 身份状态：当前仅依据文件名登记，尚未完成封面、重要提示、报表日期和版本关系的内容核验",
            "",
            "## 可用内容",
            "",
            "- [逐页正文与物理页锚点](正文/index.md)",
            "- [章节地图](索引/document_map.json)",
            f"- [表格索引](表格/index.json)：{len(table_items)} 个已检出并形成网格的物理片段；"
            f"{len(financial_result['accepted_fragments'])} 个片段通过法定主表规则；"
            f"{len(financial_result['logical_tables'])} 张逻辑报表",
            f"- `数据/facts.jsonl`：{len(financial_result['facts'])} 条通过完整证据门与适用勾稽规则的观察值",
            f"- `数据/candidates.jsonl`：{len(financial_result['candidates'])} 条未完全通过的候选",
            f"- [图像对象索引](图表/index.json)：{len(figure_items)} 个嵌入图片对象",
            f"- [矢量轮廓数字复核](复核/vector-outline-index.html)："
            f"{len(vector_result.get('statements', []))} 张法定主表候选；仅供人工核对，不可引用或计算",
            f"- [质量报告](索引/quality.json)；[复核入口](复核/index.html)：{review_count} 项",
            "",
            "## 使用边界",
            "",
            "正文可阅读、可按物理页回查。V0.3-C3 对六类法定主表绑定语义与逐格证据；权益变动表仅放行通过完整证据门及期初+增减=期末校验的关键单元格；",
            "仅 `数据/facts.jsonl` 中 `quality.eligible_for_calculation=true` 的观察值可用于机械计算。",
            "这不等于披露真实、审计正确或整份报告财务结构化完整；其他表格仍是不可计算候选。",
            "",
            "## 目录",
            "",
            "- `正文/page-####.md`：逐页文字，含 `source_page` 注释和 `page-N` 锚点",
            "- `表格/table-####.json/.html`：表格结构候选及人工复核视图",
            "- `图表/figure-####.json/.png`：嵌入图片对象及页面裁图（若渲染成功）",
            "- `_internal/pages/page-####.json`：页面体检、坐标和对象引用",
            "- `_internal/objects.jsonl`、`索引/evidence.jsonl`：对象账目与证据账目",
            "",
        ]
    )


def parse_native_text_package(pdf_path, output_dir, *, mineru_content=None, mineru_start=0,
                              auto_borderless=False) -> dict:
    """运行 V0.3-C4.4 标准解析。目标目录必须为空或不存在。"""
    started = time.time()
    pdf_path = pathlib.Path(pdf_path).resolve()
    if not pdf_path.is_file():
        raise FileNotFoundError(f"不是可读取的 PDF: {pdf_path}")
    if auto_borderless and mineru_content:
        raise ValueError("--auto-borderless 与 --mineru-content 不能同时使用")
    root = pathlib.Path(output_dir).resolve()
    if root.exists() and any(root.iterdir()):
        raise FileExistsError(f"目标研究包已存在且非空，拒绝覆盖: {root}")
    for sub in ("正文", "表格", "图表", "数据", "索引", "复核", "_internal/pages"):
        (root / sub).mkdir(parents=True, exist_ok=True)

    sha_start = intake.sha256_of(pdf_path)
    pages, pdfplumber_version = _extract_pages(pdf_path)
    borderless_tables = []
    vector_result = {"suspected_pages": [], "fragments": [], "statements": []}
    mineru_content_ref = None
    mineru_content_sha256 = None
    auto_result = {"requested": bool(auto_borderless), "status": "not_requested"}
    auto_temp = None
    if auto_borderless:
        try:
            detection = borderless.detect_collapsed_main_statement_pages(pages)
            auto_result = {"requested": True, "status": "not_needed", "detection": detection}
            if detection["needed"]:
                auto_temp = tempfile.TemporaryDirectory(prefix="annual-report-mineru-")
                executed = borderless.run_mineru(
                    pdf_path, auto_temp.name,
                    start_page=detection["start_page"], end_page=detection["end_page"],
                )
                mineru_content = executed["content_list"]
                mineru_start = detection["mineru_start_page0"]
                auto_result = {"requested": True, "status": "completed", "detection": detection,
                               "start_page": executed["start_page"], "end_page": executed["end_page"],
                               "command": executed["command"], "log": executed["log"]}
        except Exception as exc:  # 有界升级失败不得伪造成功，但应回退 M2 候选包。
            auto_result = {"requested": True, "status": "failed", "error": f"{type(exc).__name__}: {exc}"}
    if mineru_content:
        mineru_source = pathlib.Path(mineru_content).resolve()
        if not mineru_source.is_file():
            raise FileNotFoundError(f"MinerU content_list 不存在: {mineru_source}")
        mineru_content_ref = "_internal/engines/mineru/content_list.json"
        mineru_saved = root / mineru_content_ref
        mineru_saved.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(mineru_source, mineru_saved)
        mineru_content_sha256 = intake.sha256_of(mineru_saved)
        borderless_tables = borderless.load_candidates(mineru_saved, int(mineru_start), pages)
        vector_result = vector_evidence.inspect(
            mineru_saved, int(mineru_start), pages, recovered=borderless_tables
        )
        by_page = {p["physical_page"]: p for p in pages}
        for candidate in borderless_tables:
            page = by_page[candidate["physical_page"]]
            # MinerU 区域覆盖的坍缩 2列 pdfplumber 伪表不再进入主表拼接；
            # 原文仍保留在逐页 Markdown 与词级证据中。
            page["tables"] = [
                t for t in page["tables"]
                if not (int(t["grid"].get("cols", 0)) < 4 and _inside(t["bbox"], candidate["bbox"], slack=8.0))
            ]
            page["tables"].append(candidate)
            page["tables"].sort(key=lambda t: (t["bbox"][1], t["bbox"][0]))
    if auto_borderless:
        engine_dir = root / "_internal/engines/mineru"
        engine_dir.mkdir(parents=True, exist_ok=True)
        if auto_result.get("log"):
            (engine_dir / "auto-run.log").write_text(auto_result.pop("log"), encoding="utf-8")
            auto_result["log_ref"] = "_internal/engines/mineru/auto-run.log"
        exports.write_json(engine_dir / "auto-detection.json", auto_result)
    if auto_temp is not None:
        auto_temp.cleanup()
    marginalia = layout.repeated_marginalia(pages)
    page_count = len(pages)
    identity = intake.identity_from_filename(pdf_path)
    identity["file_identity"]["page_count"] = page_count

    objects = []
    evidence = []
    headings = []
    table_items = []
    figure_items = []
    review = []
    page_status = []
    page_last_object = {}
    prior_object = None
    table_no = figure_no = review_no = object_no = evidence_no = 0

    index_lines = ["# 逐页正文", "", "> 物理页与原 PDF 一一对应；表格文字同时保留在逐页正文中，结构候选见 `../表格/`。", ""]
    for page in pages:
        pno = page["physical_page"]
        status = "failed" if page["error"] else "processed"
        table_boxes = [t["bbox"] for t in page["tables"]]
        page_object_ids = []
        md_lines = [f"<!-- source_page: {pno} -->", f'<a id="page-{pno}"></a>', "", f"# 物理页 {pno}", ""]

        for line_index, line in enumerate(page["lines"], 1):
            norm = layout.normalize_marginal_text(line["text"])
            top, bottom = line["bbox"][1], line["bbox"][3]
            is_edge = top <= page["height_pt"] * 0.09 or bottom >= page["height_pt"] * 0.85
            line["is_marginalia"] = bool(is_edge and norm in marginalia)
            heading = sections.heading_from_line(line["text"])
            line["is_heading"] = bool(heading)
            in_table = any(_inside(line["bbox"], box) for box in table_boxes)
            object_no += 1
            oid = f"obj-{object_no:06d}"
            evidence_no += 1
            evid = f"ev-{evidence_no:06d}"
            destination = "into_marginalia" if line["is_marginalia"] else ("duplicate_retained" if in_table else "into_prose")
            obj = {
                "object_id": oid,
                "object_type": "page_header" if line["is_marginalia"] and top < page["height_pt"] / 2 else (
                    "page_footer" if line["is_marginalia"] else ("heading" if heading else "text_block")
                ),
                "page_region": {"physical_page": pno, "bbox": line["bbox"], "region_id": f"p{pno:04d}-line-{line_index:03d}"},
                "original_text": line["text"],
                "method": "pdf_text_words_y_cluster",
                "engine": "pdfplumber",
                "engine_version": pdfplumber_version,
                "confidence": None,
                "candidate_status": "accepted_by_rules" if not in_table else "needs_review",
                "manually_reviewed": False,
                "relations": {"reading_before": prior_object, "derived_from": [evid]},
                "destination": destination,
                "quality_flags": ["table_text_duplicate"] if in_table else [],
                "notes": "页边缘重复项" if line["is_marginalia"] else "",
            }
            objects.append(obj)
            evidence.append(
                {
                    "evidence_id": evid,
                    "source_kind": "text_span",
                    "granularity": "span",
                    "locate": {"physical_page": pno, "printed_page_label": None, "bbox": line["bbox"], "object_id": oid, "pdf_reference": f"page={pno}"},
                    "context": {"full_sentence": line["text"]},
                    "footnote_refs": [],
                    "engine": "pdfplumber",
                    "engine_version": pdfplumber_version,
                    "processing": [],
                    "candidate_reason": "native PDF text layer",
                    "confidence": None,
                }
            )
            if heading:
                headings.append(
                    {"title": heading, "object_id": oid, "physical_page": pno, "order": line_index,
                     "previous_object_id": prior_object, "confidence": "body_rule_high"}
                )
                md_lines.extend([f"## {heading}", ""])
            elif not line["is_marginalia"]:
                # 保留 PDF 物理换行但不制造空段；Markdown 渲染时会自然折为连续语句。
                md_lines.append(line["text"])
            page_object_ids.append(oid)
            prior_object = oid

        for table in page["tables"]:
            table_no += 1
            tid = f"table-{table_no:04d}"
            object_no += 1
            oid = f"obj-{object_no:06d}"
            evidence_no += 1
            evid = f"ev-{evidence_no:06d}"
            grid = table["grid"]
            for cell in grid["cells"]:
                cell["evidence_refs"] = [evid]
                cell["eligible_for_calculation"] = False
            fragment = {
                "table_fragment_id": tid,
                "belongs_to_logical_table": None,
                "physical_page": pno,
                "caption": None,
                "units_declaration": None,
                "has_repeated_header": False,
                "header_rows": 0,
                "row_count": grid["rows"],
                "column_count": grid["cols"],
                "grid": grid,
                "cells": grid["cells"],
                "candidate_status": "needs_review",
                "eligible_for_calculation": False,
                "bbox": table["bbox"],
                "evidence_ref": evid,
                "structure_source": table.get("structure_source", "pdfplumber_ruled_table"),
                "column_bounds": table.get("column_bounds") or grid.get("column_bounds"),
            }
            exports.write_json(root / "表格" / f"{tid}.json", fragment)
            (root / "表格" / f"{tid}.html").write_text(
                "<!doctype html><html lang='zh-CN'><meta charset='utf-8'><title>" + tid + "</title>"
                "<style>table{border-collapse:collapse}td{border:1px solid #999;padding:4px}</style><body>"
                f"<h1>{tid} · 物理页 {pno}</h1><p><b>结构候选，未获计算资格。</b></p>"
                + exports.grid_to_html(grid, extra_attrs={"data-page": pno}) + "</body></html>", encoding="utf-8"
            )
            table_engine = table.get("engine", "pdfplumber")
            table_engine_version = table.get("engine_version", pdfplumber_version)
            table_method = "mineru_region_pdfplumber_native_grid" if table.get("structure_source") else "pdfplumber_find_tables"
            objects.append({"object_id": oid, "object_type": "table_fragment", "page_region": {"physical_page": pno, "bbox": table["bbox"], "region_id": tid}, "original_text": None, "method": table_method, "engine": table_engine, "engine_version": table_engine_version, "confidence": None, "candidate_status": "needs_review", "manually_reviewed": False, "relations": {"reading_before": prior_object, "derived_from": [evid]}, "destination": "into_table", "quality_flags": ["not_semantically_validated", "not_computable"], "notes": "M2/M3 结构候选"})
            evidence.append({"evidence_id": evid, "source_kind": "table_cell", "granularity": "table", "locate": {"physical_page": pno, "printed_page_label": None, "bbox": table["bbox"], "object_id": oid, "pdf_reference": f"page={pno}"}, "context": {}, "footnote_refs": [], "engine": table_engine, "engine_version": table_engine_version, "processing": [], "candidate_reason": "detected table geometry", "confidence": None})
            table_items.append({"table_fragment_id": tid, "physical_page": pno, "bbox": table["bbox"], "rows": grid["rows"], "columns": grid["cols"], "json": f"表格/{tid}.json", "html": f"表格/{tid}.html", "candidate_status": "needs_review", "eligible_for_calculation": False, "structure_source": fragment["structure_source"]})
            review_no += 1
            priority = 1 if 24 <= pno <= 40 else 2
            review.append({"review_id": f"rev-{review_no:05d}", "priority": priority, "object_ref": f"表格/{tid}.json", "reason": "表格结构候选尚未绑定表头路径、主体、期间和单位，不具备计算资格", "evidence_clip": None, "candidates": [], "methods_tried": [table_method], "next_step": "对照原 PDF 核对结构；法定主表再进入语义与勾稽规则", "status": "open", "created_at": _now()})
            page_object_ids.append(oid)
            prior_object = oid

        for figure in page["figures"]:
            figure_no += 1
            fid = f"figure-{figure_no:04d}"
            object_no += 1
            oid = f"obj-{object_no:06d}"
            evidence_no += 1
            evid = f"ev-{evidence_no:06d}"
            image_ref = f"图表/{fid}.png"
            fig_doc = {"figure_id": fid, "physical_page": pno, "bbox": figure["bbox"], "source_type": figure["source_type"], "candidate_status": "needs_review", "note_level": "raw_retained", "image_ref": image_ref, "evidence_ref": evid}
            exports.write_json(root / "图表" / f"{fid}.json", fig_doc)
            figure_items.append({**fig_doc, "json": f"图表/{fid}.json"})
            objects.append({"object_id": oid, "object_type": "figure", "page_region": {"physical_page": pno, "bbox": figure["bbox"], "region_id": fid}, "original_text": None, "method": "pdf_embedded_image_inventory", "engine": "pdfplumber", "engine_version": pdfplumber_version, "confidence": None, "candidate_status": "needs_review", "manually_reviewed": False, "relations": {"reading_before": prior_object, "derived_from": [evid]}, "destination": "into_figure", "quality_flags": ["labels_not_interpreted"], "notes": "仅保留与定位，未作图表理解"})
            evidence.append({"evidence_id": evid, "source_kind": "image_region", "granularity": "region", "locate": {"physical_page": pno, "printed_page_label": None, "bbox": figure["bbox"], "object_id": oid, "pdf_reference": f"page={pno}"}, "context": {}, "footnote_refs": [], "engine": "pdfplumber+pypdfium2", "engine_version": pdfplumber_version, "processing": [], "candidate_reason": "embedded raster object", "confidence": None})
            review_no += 1
            review.append({"review_id": f"rev-{review_no:05d}", "priority": 3, "object_ref": f"图表/{fid}.json", "reason": "图像对象已留存，但标题、图例、轴、单位和脚注未解释", "evidence_clip": image_ref, "candidates": [], "methods_tried": ["embedded_image_inventory"], "next_step": "需要时进行局部视觉复核", "status": "open", "created_at": _now()})
            page_object_ids.append(oid)
            prior_object = oid

        char_count = sum(len(line["text"]) for line in page["lines"] if not line.get("is_marginalia"))
        if page["error"] or char_count < 40:
            status = "failed" if page["error"] else "unresolved"
            review_no += 1
            review.append({"review_id": f"rev-{review_no:05d}", "priority": 1, "object_ref": f"_internal/pages/page-{pno:04d}.json", "reason": page["error"] or f"可提取正文仅 {char_count} 字，可能为封面、图片页、扫描页或复杂版面", "evidence_clip": None, "candidates": [], "methods_tried": ["native_text_pdfplumber"], "next_step": "查看原 PDF；必要时对该页启用 OCR/视觉引擎", "status": "open", "created_at": _now()})
            md_lines.extend(["> [解析警告] 本页原生文字过少或解析失败，请回查原 PDF。", ""])

        page_record = {
            "physical_page": pno,
            "printed_page_label": None,
            "geometry": {"width_pt": page["width_pt"], "height_pt": page["height_pt"], "rotation": page["rotation"]},
            "transform": {"origin": "top_left", "unit": "point", "rotation_deg": page["rotation"], "to_pixels": {"dpi": 144, "scale_x": 2, "scale_y": 2}, "from_engine_bbox": None},
            "region_inspection": [
                {"region_id": f"p{pno:04d}-native", "kind": "native_text" if char_count else "unclassified", "bbox": [0, 0, page["width_pt"], page["height_pt"]], "text_char_count": char_count, "note": page["error"] or ""}
            ] + [
                {"region_id": f"p{pno:04d}-table-{i:02d}", "kind": "image_table", "bbox": t["bbox"], "text_char_count": sum(len(c.get("text", "")) for c in t["grid"]["cells"]), "note": "结构候选"}
                for i, t in enumerate(page["tables"], 1)
            ],
            "contains_two_printed_pages": False,
            "native_digit_count": int(page.get("native_digit_count", 0)),
            "vector_curve_count": int(page.get("vector_curve_count", 0)),
            "vector_outline_numeric_suspected": pno in vector_result.get("suspected_pages", []),
            "status": status,
            "evidence": [e["evidence_id"] for e in evidence if e["locate"]["physical_page"] == pno],
            "object_refs": page_object_ids,
        }
        exports.write_json(root / "_internal/pages" / f"page-{pno:04d}.json", page_record)
        (root / "正文" / f"page-{pno:04d}.md").write_text("\n".join(md_lines).rstrip() + "\n", encoding="utf-8")
        index_lines.append(f"- [物理页 {pno}](page-{pno:04d}.md) · {status} · {char_count} 字 · {len(page['tables'])} 表格候选")
        page_status.append({"physical_page": pno, "status": status})
        if page_object_ids:
            page_last_object[str(pno)] = page_object_ids[-1]

    vector_render_errors = vector_evidence.write_bundle(root, pdf_path, vector_result)
    signoff_records = []
    for statement in vector_result.get("statements", []):
        review_no += 1
        object_ref = "复核/vector-outline-index.json#" + statement["statement_id"]
        review.append({
            "review_id": f"rev-{review_no:05d}",
            "priority": 1,
            "object_ref": object_ref,
            "reason": statement["reason"],
            "evidence_clip": statement.get("first_clip_ref"),
            "candidates": [
                {"fragment_ref": fragment_id, "candidate_status": "needs_review",
                 "eligible_for_calculation": False}
                for fragment_id in statement["fragments"]
            ],
            "methods_tried": ["native_text_digit_audit", "pdf_vector_curve_inventory", "mineru_candidate"],
            "next_step": "逐格一致性为 single_channel；接入第二独立识别通道完成交叉验证后，"
                         "用 run_signoff.py record ... --decision accepted --eligible 记录人工签核方可引用",
            "status": "open",
            "created_at": _now(),
        })
        signoff_records.append(evidence_chain.new_signoff(
            object_ref, reviewer=None, decision="pending",
            note="矢量轮廓数字：单通道（MinerU）候选，待第二通道交叉验证与人工签核",
            eligible_for_calculation=False,
            run_id=None,
        ))
    if signoff_records:
        evidence_chain.write_signoffs(root / "复核/signoffs.jsonl", signoff_records)

    financial_result = financials.build_main_statements(
        root, pages, table_items, evidence, review, identity, pdfplumber_version
    )
    equity_result = equity.build_equity_statements(
        root, pages, table_items, evidence, review, pdfplumber_version,
        identity=identity,
        start_index=len(financial_result["logical_tables"]) + 1,
    )
    financial_result["facts"].extend(equity_result["facts"])
    financial_result["candidates"].extend(equity_result["candidates"])
    financial_result["validations"].extend(equity_result["validations"])
    all_logical_tables = financial_result["logical_tables"] + equity_result["logical_tables"]
    result_for_entry = dict(financial_result)
    result_for_entry["logical_tables"] = all_logical_tables

    # 主表/权益表已消费片段（六类法定主表 + 权益变动表）
    claimed_main = {
        frag
        for logical in all_logical_tables
        for frag in (logical.get("fragments") or [])
    }
    # D2a 资产变动表族（账面原值/折旧(摊销)/减值/账面价值 × 类别列 × 阶段行）：
    # 仅期初+增−减=期末 且证据/上下文齐全的单元格进入 facts；其余退回候选。
    movement_result = movement.build_movement_facts(
        root, pages, table_items, evidence, review, identity, pdfplumber_version,
        claimed_fragments=claimed_main,
    )
    movement_processed = set(movement_result["processed_fragments"])
    # D2b 行向减值/跌价准备变动表（存货跌价、坏账、各类减值等）：期初+Σ增−Σ减=期末。
    provision_result = movement.build_provision_movement_facts(
        root, pages, table_items, evidence, review, identity, pdfplumber_version,
        claimed_fragments=claimed_main | movement_processed,
    )
    movement_result["facts"].extend(provision_result["facts"])
    movement_result["candidates"].extend(provision_result["candidates"])
    movement_result["processed_fragments"] = sorted(
        movement_processed | set(provision_result["processed_fragments"])
    )
    movement_result["provision_tables"] = provision_result["provision_tables"]
    financial_result["facts"].extend(movement_result["facts"])
    financial_result["candidates"].extend(movement_result["candidates"])
    movement_processed = set(movement_result["processed_fragments"])

    # D1 非主表广度：只处理未被主表/权益表/变动表族消费的片段，
    # 生成的是不可计算候选（eligible=false），不触碰任何 facts。
    claimed_fragments = claimed_main | movement_processed
    general_result = general_tables.build_general_candidates(
        root, pages, table_items, evidence, review, identity, pdfplumber_version,
        claimed_fragments=claimed_fragments, headings=headings,
    )
    financial_result["candidates"].extend(general_result["candidates"])
    general_candidate_ids = set(general_result["candidate_fragments"])
    general_text_ids = set(general_result["text_fragments"])

    # 同步物理表、对象账目、证据账目与页记录，避免同一对象出现互相矛盾的状态。
    accepted_fragments = set(financial_result["accepted_fragments"])
    accepted_equity_structure = set(equity_result["accepted_structure_fragments"])
    equity_fact_fragments = set(equity_result["fact_fragments"])
    for obj in objects:
        region_id = obj.get("page_region", {}).get("region_id")
        if obj.get("object_type") == "table_fragment" and region_id in accepted_fragments:
            obj["candidate_status"] = "accepted_by_rules"
            obj["quality_flags"] = []
            obj["notes"] = "M2 法定主表片段；仅带逐格证据且通过规则的数值单元格可计算"
        elif obj.get("object_type") == "table_fragment" and region_id in accepted_equity_structure:
            obj["structure_status"] = "accepted_by_rules"
            if region_id in equity_fact_fragments:
                obj["notes"] = "C3 权益变动表结构已验收；仅 facts 中通过逐列勾稽的期初/增减/期末关键单元格可引用计算"
            else:
                obj["notes"] = "C3 权益变动表结构已验收；本片段没有通过完整证据与逐列勾稽门的可计算单元格"
        elif obj.get("object_type") == "table_fragment" and region_id in general_candidate_ids:
            obj["candidate_status"] = "candidate_generated"
            obj["quality_flags"] = ["not_semantically_validated", "not_computable"]
            obj["notes"] = "D1 非主表数值候选已生成（eligible=false）；族标签与单位/期间/主体待核验"
        elif obj.get("object_type") == "table_fragment" and region_id in general_text_ids:
            obj["candidate_status"] = "classified_text_table"
            obj["quality_flags"] = ["non_financial_text_table"]
            obj["notes"] = "D1 纯文本表分类留存，无数值候选"
    evidence_by_page = {}
    for ev in evidence:
        page_no = ev.get("locate", {}).get("physical_page")
        if page_no:
            evidence_by_page.setdefault(page_no, []).append(ev["evidence_id"])
    for pno, evidence_ids in evidence_by_page.items():
        page_path = root / "_internal/pages" / f"page-{pno:04d}.json"
        page_doc = json.loads(page_path.read_text(encoding="utf-8"))
        page_doc["evidence"] = evidence_ids
        exports.write_json(page_path, page_doc)

    exports.write_jsonl(root / "_internal/objects.jsonl", objects)
    exports.write_jsonl(root / "索引/evidence.jsonl", evidence)
    (root / "正文/index.md").write_text("\n".join(index_lines) + "\n", encoding="utf-8")
    section_map = sections.build_section_tree({"headings": headings, "page_last_object": page_last_object, "page_count": page_count})
    exports.write_json(root / "索引/document_map.json", {"sections": section_map, "unresolved_references": [], "notes": "M1 依据正文中的第X节标题建立；对象边界优先于页码近似。目录/书签交叉核验留待 M2。"})
    logical_index = [
        {
            "logical_table_id": item["logical_table_id"],
            "caption": item["caption"],
            "statement_type": item["statement_type"],
            "entity_scope": item["entity_scope"],
            "fragments": item["fragments"],
            "eligible_for_calculation": item["eligible_for_calculation"],
            "json": item["json_path"],
            "html": item["html_path"],
        }
        for item in all_logical_tables
    ]
    exports.write_json(
        root / "表格/index.json",
        {"tables": table_items, "logical_tables": logical_index, "state": "v03_c3_equity_validated_key_cells_partial"},
    )
    render_errors = _render_figure_clips(pdf_path, figure_items, root) + vector_render_errors
    exports.write_json(root / "图表/index.json", {"figures": figure_items, "state": "m1_raw_retained_labels_pending", "render_errors": render_errors})
    exports.write_jsonl(root / "数据/candidates.jsonl", financial_result["candidates"])
    exports.write_jsonl(root / "数据/facts.jsonl", financial_result["facts"])
    financials.write_facts_csv(root / "数据/facts.csv", financial_result["facts"])
    exports.write_jsonl(root / "索引/review_queue.jsonl", review)
    _write_review_html(root, review)

    failed_pages = [p for p in page_status if p["status"] == "failed"]
    unresolved_pages = [p for p in page_status if p["status"] == "unresolved"]
    sha_end = intake.sha256_of(pdf_path)
    if sha_end != sha_start:
        raise RuntimeError("解析期间源 PDF 哈希发生变化，拒绝生成混合版本研究包")
    run_id = datetime.datetime.now().strftime("%Y%m%dT%H%M%S")
    open_reviews = [item for item in review if item.get("status") == "open"]
    # complete 表示当前声明范围内没有失败、未解决页或开放复核项；不能因“存在表格”本身降级。
    overall = "partial" if failed_pages or unresolved_pages or open_reviews else "complete"
    run = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "mode": "standard_parse",
        "source_pdf": {"path": str(pdf_path), "sha256_start": sha_start, "sha256_end": sha_end,
                       "size_bytes": intake.file_size(pdf_path)},
        "identity": identity,
        "program": {"skill_version": SKILL_NAME, "pipeline_version": PIPELINE_VERSION,
                    "command_line": "run_parse.py run"},
        "engines": {"renderer": "pypdfium2(optional)", "ocr": None,
                    "layout": f"pdfplumber {pdfplumber_version}+deterministic_rules",
                    "table": f"pdfplumber {pdfplumber_version}+m2-financial-rules"
                    + ("+mineru-borderless-region" if mineru_content else "")},
        "config": {"remote_services_off_by_default": True,
                   "computable_scope": "accepted_statutory_main_statement_cells_and_validated_equity_key_cells_only",
                   "mineru_content_ref": mineru_content_ref,
                   "mineru_content_sha256": mineru_content_sha256,
                   "mineru_start_page0": int(mineru_start) if mineru_content else None,
                   "auto_borderless": auto_result},
        "timing": {"started_at": datetime.datetime.fromtimestamp(started).astimezone().isoformat(timespec="seconds"),
                   "finished_at": _now(), "elapsed_seconds": round(time.time() - started, 3),
                   "peak_memory_mb": None, "pages_processed": page_count - len(failed_pages), "model_calls": 0},
        "failures": [f"p{x['physical_page']}: failed" for x in failed_pages] + render_errors,
        "page_status": page_status, "output_dir": str(root), "overall_state": overall,
    }
    open_issues = [
        "六类法定主表可在完整证据门下进入 facts；权益变动表仅期初、增减、期末且通过逐列勾稽的关键单元格可计算，其余仍不可计算",
        "D1 已将非主表按族分类并生成不可计算候选（candidates.jsonl）；族标签与单位/期间/主体待人工或后续深度语义核验，不进入 facts",
        "D2 资产变动表族（账面原值/折旧(摊销)/减值/账面价值变动）仅放行通过期初+增−减=期末且证据/上下文齐全的单元格；其余版式与明细行仍为候选",
        "扫描、复杂多栏、超出已回归样式的行坍缩宽表、重述列和未回归的保险业版式尚未进入支持矩阵；港股繁英按当前范围暂缓",
        "仅登记 PDF 嵌入图片；矢量图表识别与图表标签理解未完成",
    ]
    if unresolved_pages:
        open_issues.append(f"{len(unresolved_pages)} 个稀疏/未解决页面待 OCR 或视觉复核")
    if failed_pages:
        open_issues.append(f"{len(failed_pages)} 页解析失败")
    if auto_result.get("status") == "failed":
        open_issues.append("无框线自动升级失败，已回退到保守候选: " + auto_result.get("error", "unknown"))
    if vector_result.get("statements"):
        open_issues.append(
            f"{len(vector_result['statements'])} 张法定主表的数字疑似为矢量轮廓；"
            "已生成源页裁图与 MinerU 复核候选（single_channel），并登记 pending 人工签核；"
            "完整事实门要求第二独立通道交叉验证与人工签核（复核/signoffs.jsonl）"
        )
    manifest = {
        "schema_version": SCHEMA_VERSION, "package_root": str(root),
        "source_pdf": {"path": str(pdf_path), "sha256": sha_start, "page_count": page_count,
                       "copied_into_package": False},
        "identity": identity, "run": {"run_id": run_id, "pipeline_version": PIPELINE_VERSION},
        "generated_at": _now(),
        "artifacts": {
            "prose_files": [f"正文/page-{p:04d}.md" for p in range(1, page_count + 1)],
            "tables": len(table_items), "logical_tables": len(all_logical_tables),
            "structured_tables": len(table_items),
            "table_status": {
                "detected_fragments": len(table_items),
                "grids_extracted": len(table_items),
                "structure_validated_fragments": len(accepted_fragments | accepted_equity_structure),
                "calculation_validated_fragments": len(financial_result["accepted_fragments"]),
                "fragments_with_computable_cells": len(accepted_fragments | equity_fact_fragments),
                "computable_cells": len(financial_result["facts"]),
            },
            "logical_equity_statements": len(equity_result["logical_tables"]),
            "equity_structure_accepted": sum(
                item.get("structure_complete") is True for item in equity_result["logical_tables"]
            ),
            "equity_structure_needs_review": sum(
                item.get("structure_complete") is not True for item in equity_result["logical_tables"]
            ),
            "equity_native_reconstructed_fragments": len(equity_result["native_reconstructed_fragments"]),
            "equity_key_fact_fragments": len(equity_fact_fragments),
            "equity_key_fact_cells": len(equity_result["facts"]),
            "borderless_native_grid_fragments": len(borderless_tables),
            "vector_outline_numeric_pages": len(vector_result.get("suspected_pages", [])),
            "vector_outline_review_fragments": len(vector_result.get("fragments", [])),
            "visual_candidate_statements": len(vector_result.get("statements", [])),
            "vector_outline_review_ref": "复核/vector-outline-index.json" if vector_result.get("fragments") else None,
            "computable_table_cells": len(financial_result["facts"]),
            "figures": len(figure_items), "figure_labeled": 0,
            "facts": len(financial_result["facts"]), "candidates": len(financial_result["candidates"]),
            "general_table_fragments": general_result["fragments_classified"],
            "general_families": general_result["families"],
            "general_candidate_tables": general_result["candidate_tables"],
            "general_text_tables": general_result["text_tables"],
            "general_candidates": len(general_result["candidates"]),
            "movement_tables": len(movement_result["movement_tables"]),
            "movement_processed_fragments": len(movement_result["processed_fragments"]),
            "movement_facts": len(movement_result["facts"]),
            "movement_candidates": len(movement_result["candidates"]),
            "provision_tables": len(movement_result.get("provision_tables", [])),
        },
        "overall_state": overall, "open_issues": open_issues,
        "quality_ref": "索引/quality.json", "review_entry_ref": "索引/review_queue.jsonl",
        "entry_00_readme": "00-阅读入口.md",
    }
    total_financial = len(financial_result["facts"]) + len(financial_result["candidates"])
    accounted_objects = sum(bool(item.get("destination")) for item in objects)
    review_burden = {
        "open_total": len(open_reviews),
        "resolved_total": sum(item.get("status") == "resolved" for item in review),
        "open_high_priority": sum(
            item.get("status") == "open" and int(item.get("priority", 99)) == 1 for item in review
        ),
        "open_tables": sum(
            item.get("status") == "open" and str(item.get("object_ref", "")).startswith("表格/")
            for item in review
        ),
        "open_vector_statements": sum(
            item.get("status") == "open"
            and str(item.get("object_ref", "")).startswith("复核/vector-outline-index.json#")
            for item in review
        ),
        "open_vector_signoffs": sum(
            r.get("decision") == "pending" for r in signoff_records
        ),
        "open_figures": sum(
            item.get("status") == "open" and str(item.get("object_ref", "")).startswith("图表/")
            for item in review
        ),
        "open_pages": sum(
            item.get("status") == "open" and str(item.get("object_ref", "")).startswith("_internal/pages/")
            for item in review
        ),
        "rate": None,
        "rate_reason": "缺少人工标注的应处理对象分母；不得用全部内部对象数稀释人工复核负担",
    }
    quality = {
        "schema_version": SCHEMA_VERSION, "report_generated_at": _now(),
        "coverage": {
            "content_retention": None,
            "structure_recovery": None,
            "computable": None,
            "review_rate": None,
            "page_processing_rate": round((page_count - len(failed_pages)) / page_count, 6) if page_count else 0,
            "detected_object_accounting_rate": round(accounted_objects / max(1, len(objects)), 6),
            "content_retention_reason": "尚未用图像侧标注核对所有可见正文、表格、图表和脚注，不能报告内容留存率",
            "by_language_industry_scan": None,
        },
        "five_layers": {
            "run_and_files": {"ok": sha_start == sha_end, "pages_registered": page_count,
                              "pages_expected": page_count},
            "content_coverage": {"processed": page_count - len(failed_pages),
                                 "unresolved": len(unresolved_pages), "failed": len(failed_pages),
                                 "page_processing_rate": round((page_count - len(failed_pages)) / page_count, 6) if page_count else 0,
                                 "detected_objects": len(objects), "detected_objects_accounted": accounted_objects,
                                 "visual_region_coverage_measured": False},
            "structure": {"sections": len(section_map), "table_fragments": len(table_items),
                          "logical_main_statements": len(financial_result["logical_tables"]),
                          "logical_equity_statements": len(equity_result["logical_tables"]),
                          "accepted_main_fragments": len(financial_result["accepted_fragments"]),
                          "accepted_equity_structure_fragments": len(accepted_equity_structure),
                          "equity_native_reconstructed_fragments": len(equity_result["native_reconstructed_fragments"]),
                          "borderless_native_grid_fragments": len(borderless_tables),
                          "vector_outline_numeric_pages": len(vector_result.get("suspected_pages", [])),
                          "vector_outline_review_fragments": len(vector_result.get("fragments", [])),
                          "visual_candidate_statements": len(vector_result.get("statements", [])),
                          "table_status": {
                              "detected_fragments": len(table_items),
                              "grids_extracted": len(table_items),
                              "structure_validated_fragments": len(accepted_fragments | accepted_equity_structure),
                              "calculation_validated_fragments": len(financial_result["accepted_fragments"]),
                              "fragments_with_computable_cells": len(accepted_fragments | equity_fact_fragments),
                              "computable_cells": len(financial_result["facts"]),
                          },
                          "equity_key_fact_fragments": len(equity_fact_fragments),
                          "equity_key_fact_cells": len(equity_result["facts"]),
                          "general_tables": {
                              "classified_fragments": general_result["fragments_classified"],
                              "families": general_result["families"],
                              "candidate_tables": general_result["candidate_tables"],
                              "text_tables": general_result["text_tables"],
                              "candidates_emitted": len(general_result["candidates"]),
                          },
                          "movement_tables": {
                              "processed_fragments": len(movement_result["processed_fragments"]),
                              "facts_emitted": len(movement_result["facts"]),
                              "candidates_emitted": len(movement_result["candidates"]),
                              "provision_tables": len(movement_result.get("provision_tables", [])),
                              "method": "d2-movement-v1 + d2-provision-v1 (期初+Σ增−Σ减=期末/账面价值交叉门 + 证据/上下文门)",
                          },
                          "note": "六类法定主表已做语义验收；权益变动表仅通过完整证据门与逐列期初+增减=期末校验的关键单元格可计算"},
            "text_semantics": {"status": "m2_main_statements", "facts": len(financial_result["facts"]),
                               "candidates": len(financial_result["candidates"]),
                               "vector_outline_numeric_pages": len(vector_result.get("suspected_pages", [])),
                               "ocr_only_cells_promoted_to_facts": 0,
                               "accepted_share_within_detected_main_statements": round(
                                   len(financial_result["facts"]) / max(1, total_financial), 6
                               )},
            "financial_consistency": {"status": "m2_applicable_checks",
                                      "results": financial_result["validations"]},
        },
        "object_status_summary": {"total": len(objects),
                                  "needs_review": sum(1 for o in objects if o["candidate_status"] == "needs_review"),
                                  "accepted_by_rules": sum(1 for o in objects if o["candidate_status"] == "accepted_by_rules")},
        "usage_eligibility_summary": {
            "readable": sum(item.get("object_type") in {"text_block", "heading"} for item in objects),
            "traceable": len(objects),
            "citable": len(financial_result["facts"]),
            "computable": len(financial_result["facts"]),
            "unit_note": "readable/traceable 计对象；citable/computable 仅计通过完整证据门的事实",
            "ordinary_prose_citable": 0,
        },
        "review_burden": review_burden,
        "validation_results": [],
        "sample_and_uncertainty": {"scope": "原生文字简体中文 A 股披露样本；六类通用主表与已回归银行、保险样本可产出事实。矢量轮廓数字仅形成不可计算复核候选；不外推到扫描、繁体或英文报告"},
        "open_issues_count": len(open_issues), "overall_state": overall,
    }
    exports.write_json(root / "_internal/run.json", run)
    exports.write_json(root / "索引/manifest.json", manifest)
    exports.write_json(root / "索引/quality.json", quality)
    (root / "00-阅读入口.md").write_text(
        _entry_markdown(manifest, table_items, figure_items, len(review), result_for_entry, vector_result), encoding="utf-8"
    )
    # 最后校验，结果回填质量报告。
    results = validation.validate_package(root)
    quality["validation_results"] = results
    quality["schema_check"] = {"tool": "jsonschema", "available": validation._jsonschema_available(), "checked_files": len(results), "failed": [r for r in results if r.get("valid") is False]}
    exports.write_json(root / "索引/quality.json", quality)
    return manifest


capabilities = {
    "engine_wired": True,
    "implemented": ["完整物理页登记", "逐页 Markdown", "对象与证据账目", "表格候选 JSON/HTML", "六类主表事实", "权益变动表多级表头结构与关键单元格事实", "矢量轮廓数字复核候选", "嵌入图片留存", "章节地图", "质量报告与复核队列", "非主表分族与数值候选（D1，不可计算）"],
    "pending": ["扫描/复杂多栏", "矢量轮廓数字独立交叉识别与签核", "权益变动表明细行语义", "非主表深度语义（族内勾稽门/列级单位期间）", "繁英及行业专用主表", "矢量图表理解"],
}
