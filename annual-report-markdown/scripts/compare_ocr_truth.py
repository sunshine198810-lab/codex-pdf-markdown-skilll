"""compare_ocr_truth：OCR 行/值 ↔ 人工真值 一致性统计（D3，probe）。

输入两种：
  1) 目录：gen_ocr_truth_kit.py 生成的 *-真值.md（人工已填“人工”列：OK/LBL/订正值）
  2) JSON 文件：gen_ocr_truth_html.py 生成的标注页里点“下载真值 JSON”导出的 *_truths.json
      （每行含 verdict: OK/LBL/FIX + corrected）

输出（每页 + 汇总）：
  rows_total / ok / label_only / corrected / unresolved
  page-level 是否已填（报表类型、合并母公司、单位、期间）
给扫描 OCR 质量做“先有真实答案”的行/值一致性近似；逐格数值精确率需按需扩展。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


def _tally(rows, page_fields) -> dict:
    stats = {"rows_total": 0, "ok": 0, "label_only": 0, "corrected": 0,
             "unresolved": 0, "page_fields_filled": 0, "page_fields_total": 5}
    for r in rows:
        stats["rows_total"] += 1
        verdict = (r.get("verdict") or "").strip().upper()
        corr = (r.get("corrected") or "").strip()
        if verdict == "OK":
            stats["ok"] += 1
        elif verdict in ("LBL", "LABEL"):
            stats["label_only"] += 1
        elif verdict == "FIX" or verdict in ("订正",) or corr:
            stats["corrected"] += 1
        else:
            stats["unresolved"] += 1
    stats["page_fields_filled"] = sum(1 for v in page_fields.values() if str(v).strip())
    return stats


def parse_worksheet(path: Path) -> dict:
    lines = path.read_text(encoding="utf-8").splitlines()
    in_rows = False
    page_fields = {}
    rows = []
    for ln in lines:
        if ln.startswith("| # |"):
            in_rows = True
            continue
        if not in_rows:
            if ln.startswith("| ") and "|" in ln[2:]:
                cells = [c.strip() for c in ln.strip().strip("|").split("|")]
                if len(cells) == 2 and cells[0] in (
                        "报表类型（资产负债表/利润表/现金流量表/权益变动表，或注明）",
                        "合并 / 母公司", "左右半页（资产侧/负债权益侧/整表/续页）",
                        "币种 / 单位（如 人民币元 / 千元）",
                        "列期间表头（如 2025年12月31日/2024年12月31日 或 2025年度/2024年度）"):
                    page_fields[cells[0]] = cells[1]
            continue
        # 行表
        if not ln.startswith("|"):
            in_rows = False
            continue
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        if len(cells) < 4 or not cells[0].lstrip("#").strip().isdigit():
            continue
        rows.append({"verdict": cells[3] if len(cells) > 3 else "", "corrected": ""})
    return _tally(rows, page_fields)


def parse_truths_json(path: Path) -> dict:
    import json
    data = json.loads(path.read_text(encoding="utf-8"))
    out = {}
    for page, p in (data.get("pages") or {}).items():
        page_fields = p.get("page_level") or {}
        out[str(page)] = _tally(p.get("rows") or [], page_fields)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("path", help="含 *-真值.md 的目录，或 HTML 导出的 *_truths.json 文件")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    p = Path(args.path)
    if p.is_dir():
        files = sorted(p.glob("*-真值.md"))
        rows = []
        total = {"rows_total": 0, "ok": 0, "label_only": 0, "corrected": 0,
                 "unresolved": 0, "page_fields_filled": 0}
        for f in files:
            s = parse_worksheet(f)
            rows.append((f.stem, s))
            for k in total:
                total[k] += s[k]
    else:
        per = parse_truths_json(p)
        rows = sorted(per.items())
        total = {"rows_total": 0, "ok": 0, "label_only": 0, "corrected": 0,
                 "unresolved": 0, "page_fields_filled": 0}
        for _, s in rows:
            for k in total:
                total[k] += s[k]
    if args.json:
        import json
        print(json.dumps({"per_page": {n: s for n, s in rows},
                          "total": total}, ensure_ascii=False, indent=2))
        return 0
    print(f"{'页面':<14}{'行数':>5}{'OK':>5}{'LBL':>5}{'订正':>5}{'未填':>5}{'页级已填':>7}")
    for name, s in rows:
        print(f"{name:<14}{s['rows_total']:>5}{s['ok']:>5}{s['label_only']:>5}"
              f"{s['corrected']:>5}{s['unresolved']:>5}{s['page_fields_filled']:>5}/{s['page_fields_total']}")
    ok = total["ok"]; tot = total["rows_total"]
    print("-" * 50)
    print(f"合计行 {tot}：OK {ok}（{100*ok/max(1,tot):.1f}%）｜LBL {total['label_only']}｜"
          f"订正 {total['corrected']}｜未填 {total['unresolved']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
