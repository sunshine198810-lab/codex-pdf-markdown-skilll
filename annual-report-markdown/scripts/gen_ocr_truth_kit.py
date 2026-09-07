"""gen_ocr_truth_kit：为扫描 OCR 主表页生成“人工真值标注工作单”（D3，probe）。

用法：
    python3 gen_ocr_truth_kit.py <content_list.json> --first-page 113 \
        --pages 113,114,116,118,120,124 --out tests/_research_packages/_D3_truth_kit

对每页导出：
  - 页信息（物理页、语句 candidate、n_cells）
  - 逐行 OCR 预览（行号 | 首格标签 | 其余单元格，前若干格）
  - 真值填写区：语句类型/合并或母公司/左右半页/币种单位/表头期间/行标签逐行打钩或订正/数值是否逐格一致
供人工对照原 PDF 页面填写后交回，再由对照脚本算 OCR↔真值 行/值一致性。
纯标准库；不读取 PDF，不触碰 facts。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from route_ocr_main_statements import _cells_of_md_table, content_list_tables, route_statement  # noqa: E402


def _rows_of_html(html: str) -> list[list[str]]:
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", html, flags=re.S)
    out = []
    for r in rows:
        tds = re.findall(r"<td[^>]*>(.*?)</td>", r, flags=re.S)
        cells = []
        for t in tds:
            t = re.sub(r"<[^>]+>", "", t)
            t = re.sub(r"\s+", "", t)
            cells.append(t)
        out.append(cells)
    return out


def _md_escape(s: str) -> str:
    return (s or "").replace("|", "\\|").replace("\n", " ")


def render_page(page: int, html: str, route: dict) -> str:
    rows = _rows_of_html(html)
    L = []
    cand = route.get("candidate") or ("tie:" + ",".join(route.get("tied_with") or [])) or "unresolved"
    L.append(f"# 真值工作单 · 物理页 {page}")
    L.append("")
    L.append("- 路由候选（机器）：`%s`（conf=%s，side=%s，scope=%s）" % (
        cand, route.get("confidence"), route.get("side"), route.get("scope")))
    L.append("- 本页为扫描图页，**请对照原 PDF 第 %d 页** 填写下方真值。" % page)
    L.append("")
    L.append("## 0. 页面级真值（必填）")
    L.append("")
    L.append("| 项 | 真值 |")
    L.append("|---|---|")
    L.append("| 报表类型（资产负债表/利润表/现金流量表/权益变动表，或注明） |  |")
    L.append("| 合并 / 母公司 |  |")
    L.append("| 左右半页（资产侧/负债权益侧/整表/续页） |  |")
    L.append("| 币种 / 单位（如 人民币元 / 千元） |  |")
    L.append("| 列期间表头（如 2025年12月31日/2024年12月31日 或 2025年度/2024年度） |  |")
    L.append("| 行级一致性（逐行） | 见下方表 |")
    L.append("")
    L.append("## 1. 逐行 OCR 预览 + 人工订正")
    L.append("")
    L.append("> 填法：每行给 ①`OK`(该行标签与数值均与原文一致) ②`LBL`(仅标签错/漏) ③写下正确的值。")
    L.append("")
    L.append("| # | 行首标签(OCR) | OCR 其余单元格(前 6) | 人工 |")
    L.append("|---:|---|---|---|")
    for i, row in enumerate(rows[:80], 1):
        label = row[0] if row else ""
        rest = " / ".join(row[1:7])
        L.append(f"| {i} | {_md_escape(label)} | {_md_escape(rest)} |  |")
    if len(rows) > 80:
        L.append(f"| … | （共 {len(rows)} 行，其余同法续填） |  |  |")
    L.append("")
    L.append("## 2. 备注 / 不一致汇总")
    L.append("")
    L.append("- 逐格数值不一致的行号与正确值：")
    L.append("")
    L.append("- 其它（表题位置、跨页续段、单位位置等）：")
    L.append("")
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("content_list", help="MinerU *_content_list.json")
    ap.add_argument("--first-page", type=int, default=113)
    ap.add_argument("--pages", default="113,114,116,118,120,124",
                    help="要出工作单的物理页（逗号分隔）")
    ap.add_argument("--out", required=True, help="输出目录")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    want = {int(x) for x in args.pages.split(",") if x.strip()}
    tables = content_list_tables(args.content_list, args.first_page)
    (out / "README.md").write_text(
        "## 使用说明\n\n"
        "1. 每页一份 `pNNN-真值.md`；请对照原 PDF 该物理页逐项填写（页面级真值 + 逐行 OK/订正）。\n"
        "2. 行首标签即行名；`OCR 其余单元格` 为该行数值等。OCR 可能把跨列合并拆开或粘连，"
        "以原 PDF 为准订正。\n"
        "3. 填完保存即交回；系统随后计算 OCR↔真值 的 行/值一致性，并给出质量报告。\n"
        "4. 工作单只用于人工校验，OCR 数值在签核前保持 single_channel、不升 facts。\n",
        encoding="utf-8")
    made = []
    for page, html in tables:
        if page not in want:
            continue
        cells = _cells_of_md_table(html)
        route = route_statement(cells)
        md = render_page(page, html, route)
        p = out / f"p{page:03d}-真值.md"
        p.write_text(md, encoding="utf-8")
        made.append(p.name)
    print("已生成", len(made), "份工作单于", out)
    for m in made:
        print("  -", m)
    return 0


if __name__ == "__main__":
    sys.exit(main())
