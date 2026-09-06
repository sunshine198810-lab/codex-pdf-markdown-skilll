"""exports：Markdown / JSON / JSONL 写盘与研究包入口模板（对应设计方案 §15）。

P0 实现：JSON/JSONL 写盘、00-阅读入口.md 模板。
P0 未实现：正文 MD 渲染、HTML table、CSV、复核页（需引擎产物）。
"""

from __future__ import annotations

import json
import pathlib


def write_json(path, data, *, indent: int = 2, sort_keys: bool = False) -> pathlib.Path:
    """以 UTF-8、保留中文、不转义非 ASCII 的方式写 JSON。"""
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=indent, sort_keys=sort_keys)
        fh.write("\n")
    return path


def write_jsonl(path, rows) -> pathlib.Path:
    """写 JSONL（facts/candidates/evidence/review_queue 等逐行同构）。"""
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False))
            fh.write("\n")


def grid_to_html(grid: dict, *, extra_attrs: dict = None) -> str:
    """把表格网格渲染为 HTML table（保留 rowspan/colspan，供人工复核阅读）。

    grid: {"rows","cols","cells":[{row,col,rowspan,colspan,text}]}
    extra_attrs: 渲染到 <table> 的属性（如 data-fragment-id、data-physical-page）。
    """
    if not grid or not grid.get("cells"):
        return "<table><caption>（空表/未解析）</caption></table>"
    matrix = {}
    for cell in grid["cells"]:
        r, c = cell["row"], cell["col"]
        for rr in range(r, r + cell.get("rowspan", 1)):
            for cc in range(c, c + cell.get("colspan", 1)):
                matrix[(rr, cc)] = cell
    rows = grid.get("rows", 0)
    cols = grid.get("cols", 0)
    attr = ""
    if extra_attrs:
        attr = " " + " ".join(f'{k}="{_esc(str(v))}"' for k, v in extra_attrs.items())
    parts = [f"<table{attr}>"]
    for r in range(rows):
        parts.append("<tr>")
        c = 0
        while c < cols:
            cell = matrix.get((r, c))
            if cell is None:
                c += 1
                continue
            # 只在左上角输出该合并单元格
            if r == cell["row"] and c == cell["col"]:
                cls = ""
                kind = cell.get("kind", "")
                if kind == "row_group":
                    cls = ' class="grp"'
                elif kind == "column_header":
                    cls = ' class="hd"'
                td = "<td" + cls
                if cell.get("rowspan", 1) > 1:
                    td += f' rowspan="{cell["rowspan"]}"'
                if cell.get("colspan", 1) > 1:
                    td += f' colspan="{cell["colspan"]}"'
                td += ">" + _esc(cell.get("text", "")).replace("\n", "<br>") + "</td>"
                parts.append(td)
            c += cell.get("colspan", 1)
        parts.append("</tr>")
    parts.append("</table>")
    return "\n".join(parts)


def _esc(s: str) -> str:
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def table_document_html(table_id: str, doc: dict) -> str:
    """组装一张表的独立 HTML 复核文档：表题/单位/正文网格/表注/来源链接。"""
    frag = doc.get("fragment", {})
    ev = doc.get("evidence", {})
    lines = [
        "<!DOCTYPE html>",
        "<html lang=\"zh\"><head><meta charset=\"utf-8\">",
        f"<title>{_esc(table_id)}</title>",
        "<style>table{border-collapse:collapse;margin:12px 0}",
        "td,th{border:1px solid #999;padding:4px 8px;font-size:13px;vertical-align:top}",
        "caption{font-weight:bold;text-align:left;padding:6px 0}</style></head><body>",
        f"<h2>{_esc(table_id)} · 表格结构候选（未人工核对）</h2>",
    ]
    # 表题候选
    caps = frag.get("caption_candidates") or []
    if caps:
        lines.append("<p><b>表题候选（可能过度捕获）：</b></p>")
        for c in caps:
            lines.append(f"<p>{_esc(c)}</p>")
    # 网格
    lines.append(grid_to_html(frag.get("grid", {}), extra_attrs={"data-fragment-id": table_id}))
    # 表注候选
    fns = frag.get("footnote_candidates") or []
    if fns:
        lines.append("<p><b>表注候选：</b></p>")
        for f in fns:
            lines.append(f"<p>{_esc(f)}</p>")
    # 来源
    loc = ev.get("locate", {})
    lines.append(
        "<hr><p style='color:#666;font-size:12px'>来源：物理页 "
        + str(loc.get("physical_page", "?"))
        + " · 引擎 "
        + _esc(str(ev.get("engine", "?")))
        + " · 证据 "
        + _esc(str(ev.get("evidence_id", "?")))
        + " · bbox_norm "
        + _esc(str(loc.get("bbox_norm", "")))
        + "</p>"
    )
    lines.append("</body></html>")
    return "\n".join(lines)
    return path


def entry_00_markdown(package_root, manifest: dict, run: dict) -> str:
    """生成 00-阅读入口.md 模板（P0 骨架：如实标注未解析状态）。"""
    fi = manifest.get("source_pdf", {})
    identity = manifest.get("identity", {})
    iss = identity.get("issuer_identity", {}) or {}
    rep = identity.get("report_identity", {}) or {}
    overall = manifest.get("overall_state", "unknown")
    page_count = fi.get("page_count")
    page_count_txt = str(page_count) if page_count is not None else "未探测"
    lines = [
        "# 阅读入口",
        "",
        f"> 由 `{run.get('program', {}).get('skill_version', '?')}` 生成 · 状态：**{overall}**",
        "",
        "## 报告身份（待核对）",
        "",
        "- 原 PDF：" + str(fi.get("path", "?")) ,
        "- SHA-256：`" + str(fi.get("sha256", "?")) + "`",
        "- 页数：" + page_count_txt,
        "- 公司名（来自文件名，须由封面核对）：" + str(iss.get("company_name_as_reported", "—")),
        "- 报告类型：" + str(rep.get("report_type", "—")),
        "",
        "> ⚠️ 文件名只是线索。正式解析前须用封面、重要提示、报表日期与编制基础核对身份。",
        "",
        "## 状态声明",
        "",
        "当前为 **P0 骨架**：解析引擎尚未接线，本包**不含**正文、表格、图表或财务数据。",
        "以下目录已建立占位；在引擎可用并通过验收回归前，不得把本包当作已解析研究资料使用。",
        "",
        "## 目录与入口",
        "",
        "- 正文/（章节阅读语料）：尚未生成",
        "- 表格/（index.json + table-*.json/.html）：尚未生成",
        "- 图表/（index.json + figure-*.png/.json）：尚未生成",
        "- 数据/（facts.jsonl + candidates.jsonl）：尚未生成",
        "- 索引/manifest.json：见 `索引/manifest.json`",
        "- 索引/document_map.json（章节地图）：尚未生成",
        "- 索引/evidence.jsonl：尚未生成",
        "- 索引/quality.json：见 `索引/quality.json`",
        "- 索引/review_queue.jsonl：见 `索引/review_queue.jsonl`",
        "- 复核/index.html：尚未生成",
        "",
        "## 未解决问题",
        "",
        "- 解析引擎选型与接线（P0 → V0.2）",
        "- 真实解析与验收回归（见 references/acceptance.md）",
        "",
        "## 原 PDF 与复核",
        "",
        "- 原 PDF：" + str(fi.get("path", "?")) + "（只读来源）",
    ]
    return "\n".join(lines) + "\n"
