"""compare_dual：MinerU vs PP-StructureV3 双通道逐行/逐格对比（D3，probe，无人工）。

用法：
    python3 scripts/compare_dual.py \
        --content-list tests/_research_packages/_D3_mineru_main/auto/<...>_content_list.json \
        --first-page 113 \
        --pp-dir tests/_research_packages/_D3_paddle/out \
        --pages 113,114,116,118,120,124 \
        --out tests/_research_packages/_D3_paddle/dual_report.md

对每物理页：把 MinerU 该页各表行 与 PP 该页各表行 合并为两个行序列，
按“行首标签”匹配后：
  - 标签级：matched / only_mineru / only_pp
  - 值级（匹配行）：去掉空格的“显著格集合”全等 → equal；否则 diff 并列出两版细胞
产出 md 报告 + 同目录 dual_matrix.json（matrix 供后续 gate）。

纯标准库；MinerU 侧解析复用 route_ocr_main_statements / gen_ocr_truth_kit 的函数。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gen_ocr_truth_kit import _rows_of_html  # noqa: E402
from route_ocr_main_statements import content_list_tables  # noqa: E402


def _clean(s: str) -> str:
    s = re.sub(r"[\s\u3000,，]+", "", s or "")
    return s.replace("(", "(").replace("（", "(").replace("）", ")").replace(")", ")").strip()


def _label(row: list[str]) -> str:
    for c in row[:2]:
        cc = _clean(c)
        if cc:
            return cc
    return ""


def _significant(row: list[str]) -> list[str]:
    """去行首标签外的有效单元格（去空白/逗号），用于整行值比对。"""
    return [_clean(c) for c in row[1:] if _clean(c)]


def _rows_to_json(rows: list[list[str]]) -> list[dict]:
    return [{"label": _label(r), "cells": r} for r in rows]


def compare_page(mineru_rows, pp_rows) -> dict:
    def index(rows):
        m: dict[str, list[int]] = {}
        for i, r in enumerate(rows):
            m.setdefault(_label(r), []).append(i)
        return m

    mi, pi = index(mineru_rows), index(pp_rows)
    used_p: set[int] = set()
    matched, equal, diff_rows = [], 0, []
    only_mineru, only_pp = [], []
    for lab, idxs in mi.items():
        if not lab:
            continue
        # 尽量一一配对（重复标签按序）
        pool = [j for j in pi.get(lab, []) if j not in used_p]
        for i in idxs:
            if pool:
                j = pool.pop(0)
                used_p.add(j)
                m_row, p_row = mineru_rows[i], pp_rows[j]
                ms, ps = _significant(m_row), _significant(p_row)
                is_eq = ms == ps and _label(m_row) == _label(p_row)
                matched.append((i, j))
                if is_eq:
                    equal += 1
                else:
                    diff_rows.append({"label": lab, "mineru": m_row, "pp": p_row,
                                      "m_sig": ms, "p_sig": ps})
            else:
                only_mineru.append(mineru_rows[i])
    # only_pp：PP 有但 MinerU 标签对不上
    for j, r in enumerate(pp_rows):
        if j not in used_p and _label(r):
            only_pp.append(r)
    return {"n_mineru": len(mineru_rows), "n_pp": len(pp_rows),
            "matched": len(matched), "equal": equal, "row_diff": len(diff_rows),
            "only_mineru": len(only_mineru), "only_pp": len(only_pp),
            "diff_rows": diff_rows,
            "only_mineru_rows": only_mineru, "only_pp_rows": only_pp}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--content-list", required=True)
    ap.add_argument("--first-page", type=int, default=113)
    ap.add_argument("--pp-dir", required=True)
    ap.add_argument("--pages", default="113,114,116,118,120,124")
    ap.add_argument("--out", default="tests/_research_packages/_D3_paddle/dual_report.md")
    args = ap.parse_args()
    pages = [int(x) for x in args.pages.split(",") if x.strip()]
    pp_dir = Path(args.pp_dir)

    mineru_by_page: dict[int, list] = {}
    for page, html in content_list_tables(args.content_list, args.first_page):
        if page in pages:
            mineru_by_page.setdefault(page, []).extend(_rows_of_html(html))

    matrix = {"engine_a": "MinerU", "engine_b": "PP-StructureV3",
              "first_page": args.first_page, "pages": {}}
    L = []
    L.append("# MinerU vs PP-StructureV3 双通道对比报告（无人工）")
    L.append("")
    L.append(f"- 物理页：{pages}")
    L.append(f"- MinerU 侧来源：`{args.content_list}`；PP 侧来源：`{args.pp_dir}/pp_p*.json`")
    L.append("")
    grand = {"n_mineru": 0, "n_pp": 0, "matched": 0, "equal": 0, "row_diff": 0,
             "only_mineru": 0, "only_pp": 0}
    for page in pages:
        pp_path = pp_dir / f"pp_p{page}.json"
        if not pp_path.exists():
            L.append(f"## 物理页 {page}\n\n（PP 输出缺失，跳过）\n")
            continue
        pp_data = json.loads(pp_path.read_text(encoding="utf-8"))
        pp_rows = [c for t in pp_data["tables"] for c in t["rows"]]
        m_rows = mineru_by_page.get(page, [])
        s = compare_page(m_rows, pp_rows)
        for k in grand:
            grand[k] += s[k]
        matrix["pages"][str(page)] = {
            "n_mineru": s["n_mineru"], "n_pp": s["n_pp"], "matched": s["matched"],
            "equal": s["equal"], "row_diff": s["row_diff"],
            "only_mineru": s["only_mineru"], "only_pp": s["only_pp"],
            "diff_examples": [{"label": d["label"], "mineru": d["mineru"], "pp": d["pp"]}
                              for d in s["diff_rows"][:8]],
        }
        L.append(f"## 物理页 {page}")
        L.append("")
        L.append(f"- MinerU 行数 {s['n_mineru']} ｜ PP 行数 {s['n_pp']} ｜ 标签匹配 {s['matched']} ｜ "
                 f"值全等 {s['equal']} ｜ 值不一致 {s['row_diff']} ｜ 仅MinerU {s['only_mineru']} ｜ 仅PP {s['only_pp']}")
        if s["diff_rows"]:
            L.append("")
            L.append("### 值不一致（示例，最多 8 条）")
            L.append("")
            for d in s["diff_rows"][:8]:
                L.append(f"- `{d['label']}`\n  - MinerU：{' / '.join(d['mineru'])}\n  - PP　　：{' / '.join(d['pp'])}")
        if s["only_mineru_rows"]:
            L.append("")
            L.append(f"### 仅 MinerU 有（{s['only_mineru']}）")
            L.append("")
            for r in s["only_mineru_rows"][:10]:
                L.append(f"- {_label(r)}　{' / '.join(r)}")
        if s["only_pp_rows"]:
            L.append("")
            L.append(f"### 仅 PP 有（{s['only_pp']}）")
            L.append("")
            for r in s["only_pp_rows"][:10]:
                L.append(f"- {_label(r)}　{' / '.join(r)}")
        L.append("")
    ok = grand["equal"]; matched = grand["matched"]
    L.append("---")
    L.append("## 汇总")
    L.append("")
    L.append(f"- 标签匹配行 {matched} 中：值全等 **{ok}**（{100*ok/max(1,matched):.1f}%），"
             f"值不一致 {grand['row_diff']}")
    L.append(f"- 仅 MinerU {grand['only_mineru']} 行，仅 PP {grand['only_pp']} 行（多为表头/空行/分段行差异）")
    L.append("")
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text("\n".join(L), encoding="utf-8")
    json_out = str(Path(args.out)).rsplit(".", 1)[0] + "_matrix.json"
    Path(json_out).write_text(json.dumps(matrix, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"报告 -> {args.out}")
    print(f"矩阵 -> {json_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
