"""pp_ocr_pages：PP-StructureV3（PaddleX）按物理页 OCR 主表 → 逐页表 cells JSON（D3 双通道 adapter）。

用法（用 paddle venv 的 python 运行）：
    tests/_research_packages/_D3_paddlevenv/bin/python scripts/pp_ocr_pages.py \
        --pdf /Users/eric/Documents/copilot设计skilll/601899_紫金矿业_2025年度报告.pdf \
        --pages 113,114,116,118,120,124 \
        --out tests/_research_packages/_D3_paddle/out --scale 2.5 [--keep-raw]

每页输出 out/pp_pNNN.json：
  { "page":113, "w":..,"h":.., "engine":"PP-StructureV3", "models":{...},
    "tables":[ {"html": "<table>…", "rows": [[cell,…],…], "cell_count":N} ] }

只读渲染+OCR，不触碰 facts。渲染依赖 pypdfium2；OCR 依赖 paddlex(PP-StructureV3)。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")


def rows_of_html(html: str) -> list[list[str]]:
    """与 MinerU 侧同一约定：<tr> 内 <td> 文本去标签、去空白。"""
    out = []
    for r in re.findall(r"<tr[^>]*>(.*?)</tr>", html or "", flags=re.S):
        tds = re.findall(r"<td[^>]*>(.*?)</td>", r, flags=re.S)
        cells = []
        for t in tds:
            t = re.sub(r"<[^>]+>", "", t)
            t = re.sub(r"\s+", "", t)
            cells.append(t)
        if cells:
            out.append(cells)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--pages", default="113,114,116,118,120,124")
    ap.add_argument("--out", required=True)
    ap.add_argument("--scale", type=float, default=2.5)
    ap.add_argument("--keep-raw", action="store_true")
    args = ap.parse_args()

    pages = [int(x) for x in args.pages.split(",") if x.strip()]
    out = Path(args.out)
    rawdir = out / "raw"
    work = out / "work"
    work.mkdir(parents=True, exist_ok=True)
    if args.keep_raw:
        rawdir.mkdir(parents=True, exist_ok=True)

    import pypdfium2 as pdfium
    from paddlex import create_pipeline

    print("loading PDF…", flush=True)
    pdf = pdfium.PdfDocument(args.pdf)
    pipe = create_pipeline("PP-StructureV3")
    print("pipeline ready", flush=True)

    for page in pages:
        t0 = time.time()
        png = work / f"p{page}.png"
        pdf[page - 1].render(scale=args.scale).to_pil().save(png)
        res = list(pipe.predict(
            input=str(png), use_doc_orientation_classify=False,
            use_doc_unwarping=False, use_textline_orientation=False))
        r = res[0]
        info = r.json if hasattr(r, "json") else {}
        top = info.get("res", info) if isinstance(info, dict) else {}
        tables = []
        for tbl in (top.get("table_res_list") or []):
            html = (tbl or {}).get("pred_html") or ""
            rows = rows_of_html(html)
            tables.append({"html": html, "rows": rows,
                           "cell_count": sum(len(x) for x in rows)})
        payload = {
            "page": page,
            "w": top.get("width"), "h": top.get("height"),
            "engine": "PP-StructureV3",
            "models": (top.get("model_settings") or {}),
            "n_tables": len(tables),
            "tables": tables,
        }
        (out / f"pp_p{page}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        if args.keep_raw:
            (rawdir / f"p{page}.raw.json").write_text(
                json.dumps(info, ensure_ascii=False), encoding="utf-8")
        nrows = sum(len(t["rows"]) for t in tables)
        print(f"page {page}: {len(tables)} tables / {nrows} rows / "
              f"tables_cells={[t['cell_count'] for t in tables]} / {time.time()-t0:.1f}s",
              flush=True)
    pdf.close()
    print("done ->", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
