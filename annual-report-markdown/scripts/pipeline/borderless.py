"""V0.3-B1 无框线主表融合：MinerU 定位结构，PDF 原生词回填证据。

MinerU 的 HTML/OCR 只用来判断“这是一张四列表”和表区域；最终网格从
pdfplumber 的原生词与 x/y 坐标重建。因此 OCR 串不会绕过单元格证据门。
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from . import adapters, naming


_STATEMENT_TITLES = {
    "合并资产负债表", "母公司资产负债表", "公司资产负债表",
    "合并利润表", "母公司利润表", "公司利润表",
    "合并现金流量表", "母公司现金流量表", "公司现金流量表",
}
_STOP_TITLES = {
    "合并所有者权益变动表", "母公司所有者权益变动表",
    "合并股东权益变动表", "公司股东权益变动表", "母公司股东权益变动表",
    "财务报表附注",
}


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def _bbox_union(words: list) -> list | None:
    if not words:
        return None
    return [min(w["x0"] for w in words), min(w["top"] for w in words),
            max(w["x1"] for w in words), max(w["bottom"] for w in words)]


def detect_collapsed_main_statement_pages(pages: list, *, max_pages: int = 32) -> dict:
    """查找只有坍缩表/无四列网格的法定主表页段。

    返回的 start/end 均为 1 起始物理页。只扩展到下一主表或权益表/附注边界，
    且总跨度不得超过 max_pages。
    """
    starts = []
    boundaries = []
    for page in pages:
        for line in page.get("lines", []):
            title, continuation, source_title = naming.canonical_statement_title(
                line.get("text", ""), _STATEMENT_TITLES | _STOP_TITLES
            )
            # 同一法定报表的“续”页属于当前切片，不构成下一张报表边界。
            if title and not continuation:
                item = {"title": title, "source_title": source_title,
                        "physical_page": page["physical_page"],
                        "top": float(line["bbox"][1])}
                boundaries.append(item)
                if title in _STATEMENT_TITLES:
                    starts.append(item)
    boundaries.sort(key=lambda x: (x["physical_page"], x["top"]))
    # 有些无框线主表在相邻续页重复完整标题，但不写“续”。
    # 相同法定标题在紧邻物理页重复时只作续页边界，不新建报表。
    deduped = []
    for item in boundaries:
        if (deduped and item["title"] == deduped[-1]["title"]
                and item["physical_page"] in {
                    deduped[-1]["physical_page"], deduped[-1]["physical_page"] + 1
                }):
            continue
        deduped.append(item)
    boundaries = deduped
    starts = [item for item in boundaries if item["title"] in _STATEMENT_TITLES]
    targets = set()
    collapsed = []
    for start in starts:
        pos = (start["physical_page"], start["top"])
        nxt = next((b for b in boundaries if (b["physical_page"], b["top"]) > pos), None)
        end_pos = (nxt["physical_page"], nxt["top"]) if nxt else (10**9, 0)
        found = []
        for page in pages:
            for table in page.get("tables", []):
                tpos = (page["physical_page"], float(table["bbox"][1]))
                if pos < tpos < end_pos:
                    found.append(table)
        if any(int(t.get("grid", {}).get("cols", 0)) in {3, 4} for t in found):
            continue
        last = (nxt["physical_page"] - 1) if nxt else start["physical_page"]
        last = max(start["physical_page"], last)
        targets.update(range(start["physical_page"], last + 1))
        collapsed.append({"title": start["title"], "start_page": start["physical_page"],
                          "end_page": last, "detected_tables": len(found),
                          "detected_columns": sorted({int(t.get('grid', {}).get('cols', 0)) for t in found})})
    if not targets:
        return {"needed": False, "pages": [], "collapsed_statements": []}
    first, last = min(targets), max(targets)
    if last - first + 1 > max_pages:
        raise RuntimeError(
            f"无框线自动切片跨度 {last-first+1} 页，超过上限 {max_pages}；拒绝无界扩大"
        )
    return {"needed": True, "pages": sorted(targets), "start_page": first, "end_page": last,
            "mineru_start_page0": first - 1, "mineru_end_page0": last - 1,
            "collapsed_statements": collapsed}


def run_mineru(pdf_path, output_dir, *, start_page: int, end_page: int, timeout: int = 600) -> dict:
    """有界运行本机 MinerU pipeline，返回 content_list 与日志。"""
    executable = shutil.which("mineru")
    if not executable:
        raise RuntimeError("MinerU CLI 不存在；无框线自动升级已跳过")
    if start_page < 1 or end_page < start_page or end_page - start_page + 1 > 32:
        raise ValueError("MinerU 自动切片必须为 1–32 个连续物理页")
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    command = [
        executable, "-p", str(Path(pdf_path).resolve()), "-o", str(out),
        "-b", "pipeline", "-m", "txt", "-s", str(start_page - 1),
        "-e", str(end_page - 1), "-l", "ch", "-f", "false", "-t", "true",
    ]
    completed = subprocess.run(command, text=True, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, timeout=timeout, check=False)
    if completed.returncode != 0:
        tail = "\n".join(completed.stdout.splitlines()[-20:])
        raise RuntimeError(f"MinerU 自动切片失败（exit={completed.returncode}）:\n{tail}")
    found = sorted(p for p in out.rglob("*_content_list.json") if not p.name.endswith("_content_list_v2.json"))
    if len(found) != 1:
        raise RuntimeError(f"MinerU 产物不唯一：content_list={len(found)}")
    return {"content_list": str(found[0]), "start_page": start_page, "end_page": end_page,
            "command": command, "log": completed.stdout}


def _point_bbox(bbox_norm, page: dict) -> list:
    if not bbox_norm or len(bbox_norm) != 4:
        return [0.0, 0.0, page["width_pt"], page["height_pt"]]
    return [float(bbox_norm[0]) * page["width_pt"] / 1000.0,
            float(bbox_norm[1]) * page["height_pt"] / 1000.0,
            float(bbox_norm[2]) * page["width_pt"] / 1000.0,
            float(bbox_norm[3]) * page["height_pt"] / 1000.0]


def _header_geometry(page: dict, table_bbox: list):
    """找‘项目/附注/两期间’原生表头，兼容年份和月日分成两行。"""
    candidates = []
    for line in page.get("lines", []):
        cy = (line["bbox"][1] + line["bbox"][3]) / 2
        text = _compact(line.get("text", ""))
        if table_bbox[1] - 8 <= cy <= table_bbox[3] and "项目" in text and "20" in text:
            candidates.append(line)
    split_period_header = False
    inferred_blank_label_header = False
    if candidates:
        header = candidates[0]
    else:
        candidates = []
        for line in page.get("lines", []):
            cy = (line["bbox"][1] + line["bbox"][3]) / 2
            text = _compact(line.get("text", ""))
            if (table_bbox[1] - 8 <= cy <= table_bbox[3]
                    and "项目" in text and "附注" in text
                    and len(re.findall(r"12月31日", text)) >= 2):
                candidates.append(line)
        if candidates:
            header = candidates[0]
            split_period_header = True
        else:
            # 保险/综合金融报表常把第一列表头留空，期间可跨 1–3 行。
            # 只在 MinerU 给定表区顶部找到同行两个明确年份时启用。
            candidates = []
            for line in page.get("lines", []):
                cy = (line["bbox"][1] + line["bbox"][3]) / 2
                text = _compact(line.get("text", ""))
                if (table_bbox[1] - 8 <= cy <= min(table_bbox[1] + 55, table_bbox[3])
                        and len(re.findall(r"20\d{2}", text)) >= 2):
                    candidates.append(line)
            if not candidates:
                return None, None, None, None, None
            header = candidates[0]
            inferred_blank_label_header = True
    words = sorted(
        [w for w in page.get("words", []) if abs(float(w["top"]) - float(header["bbox"][1])) <= 3.0],
        key=lambda w: w["x0"],
    )
    note = [w for w in words if "附注" in _compact(w.get("text", ""))]
    if inferred_blank_label_header:
        header_band_bottom = min(float(header["bbox"][1]) + 38.0, table_bbox[3])
        header_line_tops = {
            float(line["bbox"][1])
            for line in page.get("lines", [])
            if float(header["bbox"][1]) - 2 <= float(line["bbox"][1]) <= header_band_bottom
            and (
                re.search(r"20\d{2}", _compact(line.get("text", "")))
                or re.search(r"止\d+个月期间", _compact(line.get("text", "")))
                or re.search(r"(?:未经|经)审计", _compact(line.get("text", "")))
                or "附注" in _compact(line.get("text", ""))
            )
        }
        header_words = sorted(
            [w for w in page.get("words", [])
             if float(header["bbox"][1]) - 2 <= float(w["top"]) <= header_band_bottom
             and any(abs(float(w["top"]) - top) <= 3.0 for top in header_line_tops)
             and table_bbox[0] <= (float(w["x0"]) + float(w["x1"])) / 2 <= table_bbox[2]],
            key=lambda w: (float(w["top"]), float(w["x0"])),
        )
        seeds = sorted(
            [w for w in words if re.search(r"20\d{2}", _compact(w.get("text", "")))],
            key=lambda w: float(w["x0"]),
        )[:2]
        if len(seeds) < 2:
            return None, None, None, None, None
        period_split = (float(seeds[0]["x1"]) + float(seeds[1]["x0"])) / 2
        note = [w for w in header_words if "附注" in _compact(w.get("text", ""))]
        current = [w for w in header_words
                   if float(w["x0"]) >= float(seeds[0]["x0"]) - 6
                   and (float(w["x0"]) + float(w["x1"])) / 2 < period_split]
        comparative = [w for w in header_words
                       if (float(w["x0"]) + float(w["x1"])) / 2 >= period_split]
        if not current or not comparative:
            return None, None, None, None, None
        current_box, comparative_box = _bbox_union(current), _bbox_union(comparative)
        note_box = _bbox_union(note)
        label_note_cut = ((float(note_box[0]) - 8) if note_box
                          else float(current_box[0]) - 60)
        note_current_cut = ((float(note_box[2]) + float(current_box[0])) / 2 if note_box
                            else float(current_box[0]) - 8)
        current_comparative_cut = (
            (float(current_box[0]) + float(current_box[2])) / 2
            + (float(comparative_box[0]) + float(comparative_box[2])) / 2
        ) / 2
        if not (table_bbox[0] < label_note_cut < note_current_cut
                < current_comparative_cut < table_bbox[2]):
            return None, None, None, None, None
        bounds = [[table_bbox[0], label_note_cut], [label_note_cut, note_current_cut],
                  [note_current_cut, current_comparative_cut],
                  [current_comparative_cut, table_bbox[2]]]
        header_bottom = max(float(w["bottom"]) for w in current + comparative + note)
        header_texts = ["项目", "附注"] + [
            _compact("".join(str(w.get("text", "")) for w in group))
            for group in (current, comparative)
        ]
        header_boxes = [
            [table_bbox[0], float(header["bbox"][1]), label_note_cut, header_bottom],
            note_box,
            current_box,
            comparative_box,
        ]
        return header, bounds, header_texts, header_boxes, header_bottom
    if split_period_header:
        period_words = sorted(
            [w for w in page.get("words", [])
             if header["bbox"][1] - 25 <= float(w["top"]) < header["bbox"][1] - 1
             and table_bbox[0] <= float(w["x0"]) <= table_bbox[2]
             and re.fullmatch(r"20\d{2}年", _compact(w.get("text", "")))],
            key=lambda w: w["x0"],
        )
        date_words = sorted(
            [w for w in words if re.fullmatch(r"12月31日", _compact(w.get("text", "")))],
            key=lambda w: w["x0"],
        )
    else:
        period_words = [w for w in words if re.search(r"20\d{2}", w.get("text", ""))]
        date_words = []
    if not note or len(period_words) < 2 or (split_period_header and len(date_words) < 2):
        return None, None, None, None, None
    # 每个期间均以年份 token 开始；日期的月/日 token 由后续 x 分组包含。
    starts = sorted(period_words, key=lambda w: w["x0"])[:2]
    split = (starts[0]["x1"] + starts[1]["x0"]) / 2
    if split_period_header:
        current = [period_words[0], date_words[0]]
        comparative = [period_words[1], date_words[1]]
    else:
        current = [w for w in words if w["x0"] >= starts[0]["x0"] and w["x0"] < split]
        comparative = [w for w in words if w["x0"] >= starts[1]["x0"]]
    label = [w for w in words if w["x0"] < note[0]["x0"]]
    if not all((label, note, current, comparative)):
        return None, None, None, None, None
    centers = [
        sum((w["x0"] + w["x1"]) / 2 for w in label) / len(label),
        sum((w["x0"] + w["x1"]) / 2 for w in note) / len(note),
        sum((w["x0"] + w["x1"]) / 2 for w in current) / len(current),
        sum((w["x0"] + w["x1"]) / 2 for w in comparative) / len(comparative),
    ]
    cuts = [(centers[i] + centers[i + 1]) / 2 for i in range(3)]
    bounds = [[table_bbox[0], cuts[0]], [cuts[0], cuts[1]],
              [cuts[1], cuts[2]], [cuts[2], table_bbox[2]]]
    if not split_period_header:
        return header, bounds, None, None, float(header["bbox"][3])
    period_groups = [[period_words[i], date_words[i]] for i in range(2)]
    header_texts = ["项目", "附注"] + [
        _compact("".join(str(w.get("text", "")) for w in group)) for group in period_groups
    ]
    header_boxes = [_bbox_union(label), _bbox_union(note)] + [_bbox_union(group) for group in period_groups]
    return header, bounds, header_texts, header_boxes, float(header["bbox"][3])


def _native_grid(page: dict, bbox_norm) -> dict | None:
    table_bbox = _point_bbox(bbox_norm, page)
    header, bounds, header_texts, header_boxes, header_bottom = _header_geometry(page, table_bbox)
    if not header or not bounds:
        return None
    rows = []
    for line in page.get("lines", []):
        cy = (line["bbox"][1] + line["bbox"][3]) / 2
        if cy < header["bbox"][1] - 2 or cy > table_bbox[3] + 2:
            continue
        if line is not header and float(line["bbox"][1]) <= float(header_bottom) + 2:
            continue
        line_words = sorted(
            [w for w in page.get("words", []) if abs(float(w["top"]) - float(line["bbox"][1])) <= 3.0],
            key=lambda w: w["x0"],
        )
        groups = [[] for _ in range(4)]
        for word in line_words:
            center = (word["x0"] + word["x1"]) / 2
            col = 0 if center < bounds[0][1] else (1 if center < bounds[1][1] else (2 if center < bounds[2][1] else 3))
            groups[col].append(word)
        texts = [_compact("".join(str(w.get("text", "")) for w in group)) for group in groups]
        if header_texts is not None and line is header:
            texts = header_texts
            boxes = header_boxes
        else:
            boxes = [_bbox_union(group) for group in groups]
        if any(texts):
            rows.append((texts, boxes))
    if not rows or "项目" not in rows[0][0][0]:
        return None
    cells = []
    for ri, (texts, boxes) in enumerate(rows):
        for ci, (text, bbox) in enumerate(zip(texts, boxes)):
            cells.append({
                "cell_id": f"cell_r{ri}_c{ci}", "row": ri, "col": ci,
                "rowspan": 1, "colspan": 1, "text": text, "raw_value": text,
                "kind": "blank" if not text else ("column_header" if ri == 0 else "other"),
                "bbox": bbox, "evidence_refs": [],
            })
    return {"rows": len(rows), "cols": 4, "cells": cells,
            "bbox": table_bbox, "column_bounds": bounds}


def load_candidates(content_list_path, start_page0: int, pages: list) -> list:
    """MinerU content_list + 原生页对象 → 无框线物理表候选。"""
    ingested = adapters.mineru_tables(content_list_path, start_page0=start_page0)
    page_by_number = {p["physical_page"]: p for p in pages}
    out = []
    for item in ingested["tables"]:
        page = page_by_number.get(item["physical_page"])
        # MinerU 只负责给出有界表区域；合并头/多级头可能把逻辑四列折叠或展开为 3–7 列。
        # 少数续页会保留 table bbox、却返回空 table_body（cols=0）。这种候选仍可进入
        # 原生词重建，但必须再次通过明确双期间表头与严格四列证据门。
        # 最终结构从不采用空 HTML，也不把 OCR 内容当作数值证据。
        mineru_cols = int(item.get("grid", {}).get("cols", 0))
        # HTML 的 rowspan/colspan 展开后可能膨胀成十余列；列数不能在原生重建
        # 之前作为拒绝条件。真正的安全门是下方双期间表头与严格四列原生几何。
        if not page or mineru_cols < 0 or mineru_cols > 64:
            continue
        grid = _native_grid(page, item.get("bbox_norm"))
        if not grid:
            continue
        out.append({
            "local_number": len(out) + 1,
            "physical_page": item["physical_page"],
            "bbox": grid.pop("bbox"),
            "bbox_norm": item.get("bbox_norm"),
            "grid": grid,
            "engine": "mineru-region+pdfplumber-native-grid",
            "engine_version": ingested.get("engine_version"),
            "structure_source": "mineru_borderless_region",
            "column_bounds": grid["column_bounds"],
        })
    return out


capabilities = {
    "engine_wired": True,
    "implemented": ["原生三列主表跳过无谓升级", "MinerU 无框线四列区域", "PDF 原生词四列重建", "空 HTML 续页的有界原生重建", "单元格坐标证据"],
    "pending": ["多级表头", "原生词缺失时 OCR 证据", "非四列附注表"],
}
