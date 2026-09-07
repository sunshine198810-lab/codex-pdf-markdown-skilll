"""route_ocr_combined：MinerU ∪ PP-StructureV3 双通道合并路由（D3-D4，probe）。

对物理页区间（默认 113–128 = 紫金六主表 16 页）逐页：
  - MinerU 侧：content_list 页级绑定 → route_statement
  - PP 侧：pp_p{page}.json（有则用）→ 汇总该页所有表单元格 → route_statement
合并规则（只用于 *candidate 路由*，不产生事实/数值）：
  - 两引擎同候选中某类 → final=该类，source=both
  - MinerU 无候选 & PP 有 → final=PP 候选，source=pp_supplied（补缺）
  - MinerU 有 & PP 有且不同 → 显示两者，source=conflict（不自动取舍）
  - 单引擎页 → final=该引擎，source=mineru_only / pp_only
  判定 unresolved = candidate 为 None（或 tied）。
输出：md 表格 + 每页 JSON；打印是否 16 页全路由。

纯标准库。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from route_ocr_main_statements import _cells_of_md_table, content_list_tables, route_statement  # noqa: E402

PAGES = list(range(113, 129))  # 113..128


def pp_cells(pp_dir: Path, page: int):
    f = pp_dir / f"pp_p{page}.json"
    if not f.exists():
        return None
    d = json.loads(f.read_text(encoding="utf-8"))
    cells = []
    for t in d.get("tables") or []:
        for row in t.get("rows") or []:
            for c in row:
                c = re.sub(r"\s+", "", c or "")
                if c:
                    cells.append(c)
    return cells


def fmt(r: dict) -> str:
    if not r:
        return "—"
    if r.get("candidate"):
        return r["candidate"]
    if r.get("tied_with"):
        return "tie:" + ",".join(r["tied_with"])
    return "unresolved"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--content-list", required=True)
    ap.add_argument("--pp-dir", required=True)
    ap.add_argument("--first-page", type=int, default=113)
    ap.add_argument("--pages", default=None, help="逗号分隔；默认 113..128")
    ap.add_argument("--out", default="tests/_research_packages/_D3_paddle/route_combined.md")
    args = ap.parse_args()
    pages = [int(x) for x in args.pages.split(",")] if args.pages else list(
        range(args.first_page, args.first_page + 16))
    pp_dir = Path(args.pp_dir)

    mineru_by_page = {}
    for page, html in content_list_tables(args.content_list, args.first_page):
        mineru_by_page.setdefault(page, []).extend(_cells_of_md_table(html))

    rows = []
    for p in pages:
        m_cells = mineru_by_page.get(p)
        pp_c = pp_cells(pp_dir, p)
        m = route_statement(m_cells) if m_cells else None
        pp = route_statement(pp_c) if pp_c else None
        m_cand = fmt(m) if m else None
        pp_cand = fmt(pp) if pp else None

        if m_cand and pp_cand:
            if m_cand == pp_cand:
                final, source = m_cand, "both"
            else:
                final, source = m_cand, "conflict"
        elif m_cand:
            final, source = m_cand, "mineru_only"
        elif pp_cand:
            final, source = pp_cand, "pp_supplied"
        else:
            final, source = "unresolved", "none"
        rows.append({
            "page": p,
            "mineru": {"cand": m_cand, "side": (m or {}).get("side"),
                       "scope": (m or {}).get("scope"), "conf": (m or {}).get("confidence"),
                       "hits": len((m or {}).get("matched") or [])} if m else None,
            "pp": {"cand": pp_cand, "side": (pp or {}).get("side"),
                   "scope": (pp or {}).get("scope"), "conf": (pp or {}).get("confidence"),
                   "hits": len((pp or {}).get("matched") or [])} if pp else None,
            "final": final, "source": source,
        })
    # 输出
    L = ["# MinerU ∪ PP-StructureV3 合并路由（16 页）", ""]
    L.append("| 页 | MinerU | side | PP | side | 最终 | 来源 |")
    L.append("|---|---|---|---|---|---|---|")
    n_resolved = 0
    for r in rows:
        m, pp = r["mineru"], r["pp"]
        ms = f'{m["cand"]}({m["conf"]})' if m else "—"
        mside = m["side"] if m else ""
        ps = f'{pp["cand"]}({pp["conf"]})' if pp else "—"
        pside = pp["side"] if pp else ""
        final = r["final"]
        if final != "unresolved":
            n_resolved += 1
        L.append(f"| {r['page']} | {ms} | {mside} | {ps} | {pside} | {final} | {r['source']} |")
    L.append("")
    L.append(f"- 已路由：{n_resolved}/{len(rows)} 页"
             + ("  ✅ 全路由" if n_resolved == len(rows) else "  ⚠️ 仍有缺口"))
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text("\n".join(L), encoding="utf-8")
    print("\n".join(L))
    return 0


if __name__ == "__main__":
    sys.exit(main())
