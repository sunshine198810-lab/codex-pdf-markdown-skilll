#!/usr/bin/env python3
"""表体数值核对脚本（固化的“文字层 + x 坐标”核对法）。

对 run_tableslice 切片中的每张表，逐行把“显示网格”（优先取人工重建 table_layout.json 的 rows，
否则取 MinerU 候选网格正文行）与源 PDF 同页的 **pdfplumber 精确文字层** 比对：
  - 行标签须在该页某文字行中出现（按页内自上而下、跨表共享游标，处理同页重复标签如“营业收入”）；
  - 该行数值词（x 升序）应以前缀方式匹配该行数值（列序 = 左→右 = x 升序）。

产出：
  <slice>/索引/table_verify.json   结构化核对结果（每表逐行）
  <slice>/索引/table_verify.md     人读核对报告
  <slice>/目检/table_check.json    目检页状态（eyeball 读取显示 ✓/✗）

诚实说明：本核对用文字层原文作为真值来源，只做“单元格内容 vs 原页文字层”一致性判定；
不做语义/期间/单位归属判定，也不代替人工最终核对。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from pipeline import adapters, exports  # noqa: E402

_NUM = re.compile(r"^[\(（]?[-\u2212]?\d[\d,]*(?:\.\d+)?[\)）]?%?$")
# MinerU 常在逗号后插入空格（如 "13, 788, 280, 669.65"）；比对前统一剥离 空格/逗号/百分/括号/减号
_NUM_STRIP = re.compile(r"[\s,，%％（）()\u2212]")


def _clean_num(t: str) -> str:
    return _NUM_STRIP.sub("", t or "")


def _numkey(t: str) -> str:
    c = _clean_num(t)
    # 仅保留 数字/点/负号 的比较键
    return re.sub(r"[^\d.\-]", "", c) or c


def _is_num(t: str) -> bool:
    c = _clean_num(t.strip())
    return bool(re.fullmatch(r"[-+]?\d+(?:\.\d+)?", c))


_PUNCT = re.compile(r"[\s，。、；：？！“”‘’\"'（）()\[\]{}<>《》·—–-−_~.,;:!?…]+")
# MinerU 常把括号注里的减号 OCR 成 一/二（如“损失以‘一’号填列” vs 原文“－”）：
# 仅在“以…号填列”语境统一去掉该标记字符，避免误删“（一）（二）”等合法序号。
_MARKER = re.compile(r"以[“”\"'（(]?[－—–-一二_＿][“”\"'）)]?号填列")


def _norm_label(s: str) -> str:
    """标签归一：先把括号注减号标记归一，再去空白与常见标点/括号/引号。

    MinerU 常用全角（）“”而 PDF 用半角或相反；"-" 号也可能被 OCR 成“一/二”，
    这类字符级差异导致子串匹配失败 → 归一后比较更稳。
    """
    s = s or ""
    s = _MARKER.sub("以号填列", s)
    return _PUNCT.sub("", s)


def _page_lines(words, tol: float = 3.0):
    """把 pdfplumber 词按 top 分组为行，行内词按 x 升序。返回行列表（top 升序）。"""
    ws = sorted(words, key=lambda w: (w["top"], w["x0"]))
    lines = []
    for w in ws:
        if not lines or abs(w["top"] - lines[-1]["top"]) > tol:
            lines.append({"top": w["top"], "tokens": [w["text"]], "x0": w["x0"]})
        else:
            lines[-1]["tokens"].append(w["text"])
    for ln in lines:
        ln["text"] = "".join(ln["tokens"])
    return lines


def _load_layout(slice_dir):
    p = slice_dir / "目检" / "table_layout.json"
    if not p.is_file():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}
    ov = raw.get("tables", raw) if isinstance(raw, dict) else {}
    return ov if isinstance(ov, dict) else {}


def _display_body_rows(tid, doc, layout_ov):
    """返回该表“应核对”的正文行：[(label, values...), ...]；表头/空行剔除。"""
    ov = layout_ov.get(tid) or {}
    if ov.get("rows") is not None:
        out = []
        for row in ov["rows"]:
            if isinstance(row, str):
                out.append((row, []))  # 分组行：无数值，仅占位
            else:
                items = [str(x) for x in row]
                out.append((items[0], items[1:]))
        return out
    grid = doc.get("grid", {})
    cells = grid.get("cells", [])
    rows_n = grid.get("rows", 0)
    cols_n = grid.get("cols", 0)
    mat = {}
    for c in cells:
        for rr in range(c["row"], c["row"] + c.get("rowspan", 1)):
            for cc in range(c["col"], c["col"] + c.get("colspan", 1)):
                mat[(rr, cc)] = c
    out = []
    for r in range(1, rows_n):  # 跳过表头行 row0
        if r == 0:
            continue
        label = ""
        vals = []
        for cc in range(cols_n):
            c = mat.get((r, cc))
            if c is None or not (r == c["row"] and cc == c["col"]):
                continue
            t = (c.get("text") or "").strip()
            if cc == 0:
                label = t
            else:
                vals.append(t)
        if label:
            out.append((label, vals))
    return out


def verify_slice(pdf_path, slice_dir, *, check_dir=None):
    pdf_path = str(pdf_path)
    slice_dir = pathlib.Path(slice_dir)
    check_dir = pathlib.Path(check_dir) if check_dir else (slice_dir / "目检")
    index = json.loads((slice_dir / "表格" / "index.json").read_text(encoding="utf-8"))
    layout_ov = _load_layout(slice_dir)

    # 缓存每页权威文字行
    word_cache = {}
    lines_cache = {}

    def page_lines(page):
        if page not in lines_cache:
            if page not in word_cache:
                ev = adapters.pdfplumber_evidence(pdf_path, pages={page})
                word_cache[page] = ev["pages"][0]["words"]
            lines_cache[page] = _page_lines(word_cache[page])
        return lines_cache[page]

    per_page = {}
    for item in index.get("tables", []):
        tid = item["logical_table_id"]
        doc = json.loads((slice_dir / "表格" / f"{tid}.json").read_text(encoding="utf-8"))
        per_page.setdefault(doc.get("physical_page"), []).append((tid, doc, item))

    tables_out = {}
    for page in sorted(per_page):
        # 页内表按版面顺序（用 MinerU bbox 上边 y 归一近似；无则按表号）
        items = per_page[page]

        def _ykey(it):
            b = it[2].get("bbox_norm") or it[1].get("bbox_norm")
            try:
                return float(b[1]) if b and len(b) > 1 else 0.0
            except (TypeError, ValueError):
                return 0.0

        items.sort(key=_ykey)
        lines = page_lines(page)
        ptr = 0  # 跨表共享游标（页内自上而下），处理同页重复标签
        for tid, doc, item in items:
            body = _display_body_rows(tid, doc, layout_ov)
            row_results = []
            matched = 0
            for row_i, (label, vals) in enumerate(body, start=1):
                if not vals:  # 分组行
                    row_results.append({"row": row_i, "label": label, "kind": "group"})
                    continue
                want = [v for v in vals if _is_num(v)]
                wantk = [_numkey(v) for v in want]
                label_n = _norm_label(label)
                # 第 1 道：整词归一 == 标签，或该词是标签的前缀（≥MINPREF 字）
                # ——pdfplumber 原生文字 run 常在列边界把标签切碎/截断（如“所有者权益（或股东权”）
                found_idx = None
                span_end = None
                for j in range(ptr, len(lines)):
                    for t in lines[j]["tokens"]:
                        tn = _norm_label(t)
                        if not tn:
                            continue
                        if tn == label_n:
                            found_idx = j
                            break
                        if len(tn) >= 5 and label_n.startswith(tn):
                            found_idx = j
                            break
                    if found_idx is not None:
                        break
                # 第 2 道：单行整行归一文本包含标签
                if found_idx is None and label_n:
                    for j in range(ptr, len(lines)):
                        if label_n in _norm_label(lines[j]["text"]):
                            found_idx = j
                            break
                # 第 3 道：标签跨相邻视觉行折行（如“负债和所有者权益/（或股东权益）总计”），
                # 把连续 ≤MAXWRAP 行连读找命中；数值行取命中区末行或其下一数字行
                if found_idx is None and label_n:
                    maxwrap = 4
                    for j in range(ptr, len(lines)):
                        joined = ""
                        for m in range(min(maxwrap, len(lines) - j)):
                            joined += _norm_label(lines[j + m]["text"])
                            if label_n in joined:
                                found_idx = j
                                span_end = j + m
                                break
                        if found_idx is not None:
                            break
                if found_idx is None:
                    row_results.append({
                        "row": row_i, "label": label, "kind": "data",
                        "ok": False, "issue": "label_not_found_in_text_layer",
                        "values_in_grid": vals,
                    })
                    continue
                # 确定数值所在行
                if span_end is not None:
                    e = span_end
                    lnum = e
                    if not any(_is_num(t) for t in lines[e]["tokens"]) and e + 1 < len(lines):
                        lnum = e + 1
                else:
                    lnum = found_idx
                # 数值行前视：标签被切成“纯标签行”时，数字在其后 1–2 行（本行应有数值才前视）
                if wantk:
                    if not any(_is_num(t) for t in lines[lnum]["tokens"]):
                        for k in range(lnum + 1, min(len(lines), lnum + 3)):
                            if any(_is_num(t) for t in lines[k]["tokens"]):
                                lnum = k
                                break
                ptr = lnum + 1
                found_line = lines[lnum]
                line_nums = [t for t in found_line["tokens"] if _is_num(t)]
                got = [_numkey(t) for t in line_nums]
                prefix_ok = len(wantk) <= len(got) and got[: len(wantk)] == wantk
                count_ok = len(wantk) == len(got) and set(wantk) == set(got)
                ok = prefix_ok or count_ok
                if ok:
                    matched += 1
                row_results.append({
                    "row": row_i, "label": label, "kind": "data",
                    "ok": ok,
                    "issue": None if ok else "values_mismatch",
                    "values_in_grid": vals,
                    "line_numeric_tokens": line_nums if not ok else None,
                })
            total = sum(1 for r in row_results if r["kind"] == "data")
            tables_out[tid] = {
                "physical_page": page,
                "title_item": item,
                "rows_total": total,
                "rows_ok": matched,
                "mismatch_labels": [r["label"] for r in row_results
                                    if r["kind"] == "data" and not r["ok"]],
                "row_results": row_results,
                "verdict": "ok" if (total == 0 or matched == total) else "mismatch",
            }

    # 汇总
    ok_tables = [t for t in tables_out if tables_out[t]["verdict"] == "ok"]
    result = {
        "source_pdf": pdf_path,
        "schema_version": "2026-09-06.1",
        "method": "pdfplumber_text_layer + x_order prefix check (vs display grid)",
        "tables_total": len(tables_out),
        "tables_ok": len(ok_tables),
        "tables": tables_out,
    }
    # 写入 json / md / eyeball check
    (slice_dir / "索引").mkdir(parents=True, exist_ok=True)
    exports.write_json(slice_dir / "索引/table_verify.json", result)
    md = _render_md(result)
    (slice_dir / "索引/table_verify.md").write_text(md, encoding="utf-8")
    check_dir.mkdir(parents=True, exist_ok=True)
    exports.write_json(check_dir / "table_check.json", {
        "schema_version": "2026-09-06.1",
        "tables": {tid: {"verdict": t["verdict"], "rows_ok": t["rows_ok"],
                          "rows_total": t["rows_total"],
                          "mismatch_labels": t["mismatch_labels"]}
                   for tid, t in tables_out.items()},
    })
    return result


def _render_md(result) -> str:
    lines = [
        "# 表体数值核对报告",
        "",
        f"- 源 PDF：`{result['source_pdf']}`",
        f"- 方法：pdfplumber 精确文字层 + 词级 x 坐标前缀比对（对照显示网格）",
        f"- 结论：{result['tables_ok']}/{result['tables_total']} 张表数值一致",
        "",
    ]
    for tid in sorted(result["tables"]):
        t = result["tables"][tid]
        st = "✅ 一致" if t["verdict"] == "ok" else "❌ 不一致"
        lines.append(f"## {tid} · {st}（{t['rows_ok']}/{t['rows_total']} 数据行）")
        if t["mismatch_labels"]:
            lines.append("不一致行：")
            for r in t["row_results"]:
                if r["kind"] == "data" and not r["ok"]:
                    lines.append(
                        f"- 行 {r['row']} {r['label']}：网格值 {r['values_in_grid']}"
                        + (f"；该行文字层数值词 {r['line_numeric_tokens']}" if r.get("line_numeric_tokens") else "")
                        + (f"；{r['issue']}" if r.get("issue") else "")
                    )
        lines.append("")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--slice", required=True)
    args = ap.parse_args(argv)
    try:
        result = verify_slice(args.pdf, args.slice)
    except Exception as exc:  # noqa: BLE001
        print("ERROR:", exc)
        return 2
    print(f"核对完成: {result['tables_ok']}/{result['tables_total']} 张表数值一致")
    for tid in sorted(result["tables"]):
        t = result["tables"][tid]
        mark = "OK " if t["verdict"] == "ok" else "MISMATCH"
        print(f"  [{mark}] {tid} p{t['physical_page']} {t['rows_ok']}/{t['rows_total']} 数据行"
              + (("  不一致: " + ", ".join(t["mismatch_labels"])) if t["mismatch_labels"] else ""))
    print("报告: <slice>/索引/table_verify.json/.md ; 目检状态: <slice>/目检/table_check.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
