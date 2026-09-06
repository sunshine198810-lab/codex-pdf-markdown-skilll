#!/usr/bin/env python3
"""目检复核页生成器：把 run_tableslice 的切片转成“原页图 + 阅读顺序候选 + 表格结构候选”HTML。

用法:
  build_eyeball.py --pdf <report.pdf> --slice <切片目录> [--out <目检输出目录>]

说明:
  - 用 pypdfium2（已装）渲染切片涉及的原 PDF 物理页为 PNG（可选；无 pypdfium2 则缺图继续）。
  - 产物为单个自包含入口 HTML（图片相对路径引用），供人工目检与后续“人工核对/复核队列”使用。
  - 内容诚实标注：表格为结构候选（extracted、未核对、不可计算）。
"""
from __future__ import annotations

import argparse
import html as _html
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from pipeline import exports  # noqa: E402


def _esc(s):
    return _html.escape(str(s if s is not None else ""))


def _render_page_image(pdf_path: str, physical_page: int, out_png: pathlib.Path, scale: float = 2.0):
    """用 pypdfium2 渲染物理页为 PNG。physical_page 1 起始。"""
    import pypdfium2 as pdfium  # type: ignore

    pdf = pdfium.PdfDocument(pdf_path)
    try:
        page = pdf[physical_page - 1]
        bitmap = page.render(scale=scale)
        pil = bitmap.to_pil()
        out_png.parent.mkdir(parents=True, exist_ok=True)
        pil.save(out_png)
        return True
    finally:
        pdf.close()


def _load_tables(slice_dir: pathlib.Path):
    import json

    index_path = slice_dir / "表格" / "index.json"
    if not index_path.is_file():
        return {}
    index = json.loads(index_path.read_text(encoding="utf-8"))
    tables_by_page = {}
    for item in index.get("tables", []):
        tid = item["logical_table_id"]
        jp = slice_dir / "表格" / f"{tid}.json"
        if not jp.is_file():
            continue
        doc = json.loads(jp.read_text(encoding="utf-8"))
        pg = doc.get("physical_page")
        tables_by_page.setdefault(pg, []).append((tid, doc, item))
    return tables_by_page


def _load_reading_lines(slice_dir: pathlib.Path, physical_page: int):
    p = slice_dir / "正文" / f"reading-order-p{physical_page:04d}.json"
    if not p.is_file():
        return None
    import json

    return json.loads(p.read_text(encoding="utf-8"))


def build(pdf_path, slice_dir, out_dir):
    slice_dir = pathlib.Path(slice_dir)
    out_dir = pathlib.Path(out_dir) if out_dir else slice_dir / "目检"
    pdf_path = pathlib.Path(pdf_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    images_dir = out_dir / "images"

    tables_by_page = _load_tables(slice_dir)
    pages = sorted(tables_by_page)
    page_labels = []
    for pg in pages:
        img = images_dir / f"p{pg:04d}.png"
        try:
            ok = _render_page_image(str(pdf_path), pg, img)
            if not ok:
                img = None
        except Exception as exc:  # noqa: BLE001
            img = None
            print("warn: 渲染物理页", pg, "失败:", exc)
        page_labels.append((pg, img))

    import json as _json

    # 表归属标题：优先“人工核对标注”（目检/table_titles.json），否则回落 MinerU 表题候选
    overrides = {}
    titles_path = out_dir / "table_titles.json"
    if titles_path.is_file():
        try:
            _raw = _json.loads(titles_path.read_text(encoding="utf-8"))
            # 兼容两种形态：{table_id: {...}} 或 {..., "tables": {table_id: {...}}}
            overrides = _raw.get("tables", _raw) if isinstance(_raw, dict) else {}
            if not isinstance(overrides, dict):
                overrides = {}
        except Exception as exc:  # noqa: BLE001
            print("warn: 读取 table_titles.json 失败:", exc)

    def _resolve_title(tid, doc):
        ov = overrides.get(tid)
        if isinstance(ov, dict) and (ov.get("title") or "").strip():
            return ov["title"].strip(), ov.get("source") or "人工核对", "ok"
        caps = doc.get("caption_candidates") or []
        if caps:
            return caps[-1], "MinerU 表题候选（待核对）", "cand"
        return None, "待人工命名", "todo"

    # 表头/分组行人工版面层（目检/table_layout.json）：修正 MinerU 对复杂合并表头的拍平/错位
    layout_ov = {}
    layout_path = out_dir / "table_layout.json"
    if layout_path.is_file():
        try:
            _raw_l = _json.loads(layout_path.read_text(encoding="utf-8"))
            layout_ov = _raw_l.get("tables", _raw_l) if isinstance(_raw_l, dict) else {}
            if not isinstance(layout_ov, dict):
                layout_ov = {}
        except Exception as exc:  # noqa: BLE001
            print("warn: 读取 table_layout.json 失败:", exc)

    # 数值核对状态（由 scripts/verify_tables.py 生成）
    check = {}
    check_path = out_dir / "table_check.json"
    if check_path.is_file():
        try:
            _raw_c = _json.loads(check_path.read_text(encoding="utf-8"))
            check = _raw_c.get("tables", _raw_c) if isinstance(_raw_c, dict) else {}
            if not isinstance(check, dict):
                check = {}
        except Exception as exc:  # noqa: BLE001
            print("warn: 读取 table_check.json 失败:", exc)

    def _layout_grid_html(raw_grid, ov):
        """重建显示网格。表头：col0 空（行标签列），其余放两行式年份列头；单位单独 meta 行显示。
        正文优先用 ov['rows']（人工逐行核对：字符串=分组行加粗，列表=[标签,2025,2024,2023]），
        否则回退 skip_body_rows+body_prefix 旧逻辑。"""
        headers = list(ov.get("headers") or [])
        raw_cols = raw_grid.get("cols", 0)
        ncols = max(len(headers) + 1, raw_cols, 2)
        new = []
        unit_hdr = ov.get("unit", "")
        for c in range(ncols):
            if c == 0:
                t = unit_hdr
            else:
                t = headers[c - 1] if (c - 1) < len(headers) else ""
            new.append({"row": 0, "col": c, "rowspan": 1, "colspan": 1, "text": t, "kind": "column_header"})

        def _blanks(row_i, cols_start):
            for c in range(cols_start, ncols):
                new.append({"row": row_i, "col": c, "rowspan": 1, "colspan": 1, "text": "", "kind": "blank"})

        rows_ov = ov.get("rows")
        if rows_ov is not None:
            for i, row in enumerate(rows_ov, start=1):
                if isinstance(row, str):
                    new.append({"row": i, "col": 0, "rowspan": 1, "colspan": 1, "text": row, "kind": "row_group"})
                    _blanks(i, 1)
                else:
                    items = list(row)
                    for c in range(ncols):
                        v = items[c] if c < len(items) and items[c] not in (None, "") else ""
                        new.append({"row": i, "col": c, "rowspan": 1, "colspan": 1,
                                    "text": str(v), "kind": "data"})
            total_rows = len(rows_ov) + 1
        else:
            try:
                skip = int(ov.get("skip_body_rows", 0))
            except (TypeError, ValueError):
                skip = 0
            prefix = list(ov.get("body_prefix") or [])
            raw_cells = raw_grid.get("cells", [])
            raw_rows = raw_grid.get("rows", 0)
            n_header = 1 + len(prefix)
            for i, lab in enumerate(prefix, start=1):
                new.append({"row": i, "col": 0, "rowspan": 1, "colspan": 1, "text": lab, "kind": "row_group"})
                _blanks(i, 1)
            for cell in raw_cells:
                if int(cell.get("row", 0)) < skip:
                    continue
                nc = dict(cell)
                nc["row"] = n_header + (int(cell["row"]) - skip)
                new.append(nc)
            total_rows = n_header + max(0, raw_rows - skip)
        return exports.grid_to_html({"rows": total_rows, "cols": ncols, "cells": new})

    # 组装文档
    parts = [
        "<!DOCTYPE html><html lang=\"zh\"><head><meta charset=\"utf-8\">",
        "<title>目检 · 表格结构候选</title>",
        "<style>",
        "body{font-family:-apple-system,'PingFang SC','Microsoft YaHei',sans-serif;margin:24px;color:#222}",
        "h1{font-size:20px}h2{font-size:17px;border-bottom:2px solid #ccc;padding-bottom:4px;margin-top:28px}",
        "h3{font-size:14px;margin:14px 0 4px}h4{font-size:13px;margin:10px 0 4px}",
        ".warn{background:#fff8e1;border-left:4px solid #f0ad4e;padding:8px 12px;margin:10px 0}",
        ".cols{display:flex;gap:16px;flex-wrap:wrap}.cols>div{flex:1 1 46%;min-width:340px}",
        "table{border-collapse:collapse;margin:6px 0;width:100%}",
        "td,th{border:1px solid #bbb;padding:4px 7px;font-size:12.5px;vertical-align:top}",
        "td.blank{background:#f4f4f4}",
        "td.grp{font-weight:700;background:#eef4fb}",
        "td.hd{font-weight:600;background:#f7f9fb}",
        "ol.lines{font-size:13px;line-height:1.6;color:#333}",
        ".meta{color:#777;font-size:12px}.cap{color:#0b5cad;font-weight:600}",
        "img.page{max-width:100%;border:1px solid #ccc}",
        ".tbl-title{font-size:15px;font-weight:700;margin:16px 0 2px;padding:6px 10px;",
        "border-left:4px solid #0b5cad;background:#eef4fb;border-radius:0 4px 4px 0}",
        ".tbl-title .ok{color:#177245}.tbl-title .cand{color:#b06d00}",
        ".tbl-title .todo{color:#999;font-weight:400}",
        ".ttag{font-size:11px;font-weight:400;color:#888;margin-left:8px}",
        "</style></head><body>",
        "<h1>目检 · 表格结构候选</h1>",
        "<p class='meta'>源 PDF：%s</p>" % _esc(pdf_path.resolve()),
        "<div class='warn'><b>状态：partial</b> —— 表格为<b>结构候选</b>（extracted、未人工核对、"
        "eligible_for_calculation=false）。每表<b>归属标题</b>优先取 目检/table_titles.json 的人工核对值（✓），"
        "否则回落 MinerU 表题候选（⚠）或‘待人工命名’。请对照左侧原页图目检；增改标题可编辑 table_titles.json 后重新生成。</div>",
    ]

    for pg, img in page_labels:
        parts.append(f"<h2>物理页 {pg}</h2>")
        lines = _load_reading_lines(slice_dir, pg)
        parts.append("<div class='cols'>")
        # 左：原页图
        parts.append("<div>")
        if img is not None:
            parts.append(
                f"<img class='page' src='images/{img.name}' alt='原页 {pg}' loading='lazy'>"
            )
        else:
            parts.append("<p class='meta'>(原页图不可用)</p>")
        parts.append("</div>")
        # 右：阅读顺序候选 + 表格候选
        parts.append("<div>")
        if lines is not None:
            parts.append("<h3>阅读顺序候选（词级分行，未合并段落）</h3>")
            parts.append("<ol class='lines'>")
            for ln in lines.get("reading_lines", []):
                t = ln.get("text", "")
                if not t.strip():
                    continue
                parts.append(f"<li>{_esc(t)}</li>")
            parts.append("</ol>")
        else:
            parts.append("<p class='meta'>无阅读顺序候选</p>")
        parts.append("</div>")
        parts.append("</div>")

        # 表格候选
        for tid, doc, item in tables_by_page.get(pg, []):
            grid = doc.get("grid", {})
            lay = layout_ov.get(tid)
            title, source, cls = _resolve_title(tid, doc)
            cells = grid.get("cells", [])
            if lay and lay.get("rows") is not None:
                disp_rows = len(lay["rows"]) + 1
                disp_cols = max(len(lay.get("headers") or []) + 1, grid.get("cols", 0))
                rows_meta = f"{disp_rows} 行 × {disp_cols} 列（人工重建）"
            else:
                rows_meta = f"{grid.get('rows',0)} 行 × {grid.get('cols',0)} 列 · {len(cells)} 单元格"
            shown = title if title else "（待人工命名）"
            parts.append(
                "<div class='tbl-title'><span class='" + cls + "'>" + _esc(tid)
                + " · " + _esc(shown) + "</span><span class='ttag'>[" + _esc(source) + "]</span></div>"
            )
            parts.append("<p class='meta'>" + rows_meta + "</p>")
            ck = check.get(tid)
            if ck:
                total = ck.get("rows_total", 0)
                ok = ck.get("rows_ok", 0)
                if ck.get("verdict") == "ok":
                    parts.append(
                        "<p class='meta' style='color:#177245'>数值核对 ✓："
                        + str(ok) + "/" + str(total) + " 数据行一致（对照原页文字层）</p>"
                    )
                else:
                    mm = ck.get("mismatch_labels") or []
                    parts.append(
                        "<p class='meta' style='color:#b00020'>数值核对 ✗："
                        + str(ok) + "/" + str(total) + " 数据行一致；不一致行："
                        + _esc("，".join(mm)) + "（详见 索引/table_verify.md）</p>"
                    )
            caps = doc.get("caption_candidates") or []
            if title is None or cls != "ok":
                hint = " / ".join(_esc(c["text"]) for c in cells[:2] if (c.get("text") or "").strip())
                parts.append("<p class='meta'>首行提示：" + hint + "</p>")
            if caps and (title is None or title not in caps):
                parts.append("<p class='cap'>MinerU 表题候选：" + " ｜ ".join(_esc(c) for c in caps) + "</p>")
            if lay:
                parts.append(_layout_grid_html(grid, lay))
                if lay.get("note"):
                    parts.append("<p class='meta' style='color:#b06d00'>" + _esc(lay["note"]) + "</p>")
            else:
                parts.append(exports.grid_to_html(grid))
            fns = doc.get("footnote_candidates", [])
            if fns:
                parts.append("<p class='meta'>表注候选：" + " ｜ ".join(_esc(f) for f in fns) + "</p>")
            parts.append(
                "<p class='meta'>证据：%s · 物理页 %s · bbox_norm %s · json %s · html %s</p>"
                % (
                    _esc(doc.get("evidence_ref", "?")),
                    pg,
                    _esc(str(item.get("evidence", "?"))),
                    _esc(f"表格/{tid}.json"),
                    _esc(f"表格/{tid}.html"),
                )
            )

    parts.append("</body></html>")
    index_html = out_dir / "index.html"
    index_html.write_text("\n".join(parts), encoding="utf-8")
    return index_html, out_dir


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--slice", required=True, help="run_tableslice 输出目录")
    ap.add_argument("--out", default=None, help="目检输出目录（默认 <slice>/目检）")
    args = ap.parse_args(argv)
    try:
        idx, out = build(args.pdf, args.slice, args.out)
    except Exception as exc:  # noqa: BLE001
        print("ERROR:", exc)
        return 2
    print("目检页已生成:", idx.resolve())
    print("请在浏览器打开上述 HTML（图片相对引用）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
