#!/usr/bin/env python3
"""批量跑 V0.2 核心链路：对多份报告的“财务主表页”做 MinerU 切片 + 自动数值核对 + 目检页。

流程（每个 (报告, 物理页)）：
  1. 用 pdfplumber 在报告中定位标记页（默认优先“合并资产负债表/合并利润表/合并现金流量表”）；
  2. 对选定页跑 MinerU pipeline 单页 → content_list.json；
  3. run_tableslice.build_slice（切片 + 自动文字层核对）；
  4. build_eyeball.build（目检页 index.html + 原页图）。

产出：samples/batch/<报告>/p<NN>_<切片>/… 各含 表格/索引/目检。
进度总览：另用 scripts/build_progress.py 生成 samples/progress.html。

诚实：本批量只产出“自动化观察结果”（未人工标注），结构/核对状态见各包 quality.json/table_verify。
"""
from __future__ import annotations

import argparse
import pathlib
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from pipeline import adapters  # noqa: E402
import run_tableslice  # noqa: E402
import build_eyeball  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent  # skill 根（annual-report-v02 / 移植后 .codex/skills/…）
DEFAULT_OUT_ROOT = str(ROOT / "samples" / "batch")
DEFAULT_MARKERS = ["合并资产负债表", "合并利润表", "合并现金流量表", "主要会计数据"]

# 默认报告 PDF 目录（含 5 份样例报告）。优先级：--pdf-root > REPORT_PDF_DIR 环境变量 > 开发态探测。
# 移植到 Codex 后无样例目录时应显式传 --pdfs/--pdf-root，避免默默找不到文件。
DEFAULT_SAMPLE_NAMES = [
    "600519_贵州茅台_2026年半年度报告.pdf",
    "600887_伊利股份_2025年度报告.pdf",
    "600900_长江电力_2025年度报告.pdf",
    "601600_中国铝业_2026年半年度报告.pdf",
    "601816_京沪高铁_2025年度报告.pdf",
]


def _resolve_pdf_root(cli_value=None) -> pathlib.Path | None:
    """解析默认报告目录；找不到时返回 None（由调用方报错，绝不静默用不存在的目录）。"""
    import os

    if cli_value:
        return pathlib.Path(cli_value)
    env = os.environ.get("REPORT_PDF_DIR")
    if env:
        return pathlib.Path(env)
    # 开发态探测：skill 根上一级（workspace 目录）若有样例报告则使用
    dev_candidate = ROOT.parent
    if (dev_candidate / DEFAULT_SAMPLE_NAMES[0]).is_file():
        return dev_candidate
    return None


def locate_marker_pages(pdf_path, markers, max_per_report=2, min_digit_words=8):
    """按 pdfplumber 全页文字定位标记页；要求该页“数字词≥阈值”以避开目录/叙述页。"""
    ev = adapters.pdfplumber_evidence(str(pdf_path))  # 全页
    hits = {}
    for p in ev["pages"]:
        text = "".join(w["text"] for w in p["words"])
        digit_words = sum(1 for w in p["words"] if any(ch.isdigit() for ch in w["text"]))
        if digit_words < min_digit_words:
            continue
        for m in markers:
            if m in text and m not in hits:
                hits[m] = p["physical_page"]
    pages = []
    for m in markers:
        pg = hits.get(m)
        if pg and pg not in pages:
            pages.append(pg)
    return pages[:max_per_report]


def run_mineru_page(pdf_path, page, work_root) -> pathlib.Path:
    """跑 MinerU pipeline 单页，返回 content_list.json 路径。"""
    pdf_path = str(pdf_path)
    stem = pathlib.Path(pdf_path).stem
    out = pathlib.Path(work_root) / f"{stem}_p{page:03d}"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)
    mineru = shutil.which("mineru")
    if not mineru:
        raise RuntimeError("未找到 mineru 可执行文件")
    cmd = [
        mineru, "-p", pdf_path, "-o", str(out),
        "-b", "pipeline", "-m", "auto",
        "-s", str(page - 1), "-e", str(page - 1), "-l", "ch",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    # MinerU 关闭期可能报 libc++ 噪音，以产物为准
    content = out / stem / "auto" / f"{stem}_content_list.json"
    if not content.is_file():
        raise RuntimeError(
            f"mineru 未产出 content_list (page {page}); rc={proc.returncode}\n"
            + (proc.stderr[-800:] if proc.stderr else "")
        )
    return content


def process_report(pdf_path, out_root, markers, max_per_report, work_root):
    pdf_path = pathlib.Path(pdf_path)
    stem = pdf_path.stem
    pages = locate_marker_pages(pdf_path, markers, max_per_report)
    rows = []
    for page in pages:
        slice_dir = out_root / stem / f"p{page:03d}_切片"
        if slice_dir.exists() and any(slice_dir.iterdir()):
            rows.append({"page": page, "slice": str(slice_dir), "skipped": "exists"})
            continue
        try:
            content = run_mineru_page(pdf_path, page, work_root)
            result = run_tableslice.build_slice(
                str(pdf_path), slice_dir,
                pages={page}, mineru=str(content), mineru_start=page - 1,
            )
            build_eyeball.build(str(pdf_path), slice_dir, None)
            rows.append({
                "page": page,
                "slice": str(slice_dir),
                "tables": len(result.get("tables") or []),
                "verify": result.get("verify"),
                "warnings": result.get("warnings", []),
            })
        except Exception as exc:  # noqa: BLE001
            rows.append({"page": page, "slice": str(slice_dir), "error": str(exc)[:300]})
    return stem, rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pdfs", nargs="*", help="报告 PDF 列表（缺省=取默认目录样例 5 份）")
    ap.add_argument("--pdf-root", default=None,
                    help="默认报告目录（默认: REPORT_PDF_DIR 环境变量 > 开发态探测）")
    ap.add_argument("--markers", nargs="*", default=DEFAULT_MARKERS)
    ap.add_argument("--max-pages", type=int, default=1)
    ap.add_argument("--out-root", default=DEFAULT_OUT_ROOT)
    args = ap.parse_args(argv)

    root = pathlib.Path(args.out_root)
    root.mkdir(parents=True, exist_ok=True)
    if args.pdfs:
        pdfs = [pathlib.Path(p) for p in args.pdfs]
    else:
        pdf_dir = _resolve_pdf_root(args.pdf_root)
        if pdf_dir is None:
            print("ERROR: 未指定报告目录。请传 --pdfs 或 --pdf-root，"
                  "或设环境变量 REPORT_PDF_DIR（本机无样例报告目录可自动探测）。")
            return 2
        pdfs = [pdf_dir / name for name in DEFAULT_SAMPLE_NAMES]
    pdfs = [p for p in pdfs if p.is_file()]
    if not pdfs:
        print("ERROR: 在指定目录下未找到任何样例报告 PDF（已按名称过滤）。请检查 --pdfs/--pdf-root。")
        return 2

    with tempfile.TemporaryDirectory(prefix="batch_mineru_") as work:
        summary = []
        for pdf in pdfs:
            stem, rows = process_report(pdf, root, args.markers, args.max_pages, work)
            for r in rows:
                if "error" in r:
                    print(f"[FAIL] {stem} p{r['page']}: {r['error'][:160]}")
                elif r.get("skipped"):
                    print(f"[SKIP] {stem} p{r['page']}（已存在）")
                else:
                    v = r.get("verify") or {}
                    print(f"[OK  ] {stem} p{r['page']}: 表 {v.get('tables_ok','?')}/{v.get('tables_total','?')} "
                          f"· 数据行 {v.get('data_rows_ok','?')}/{v.get('data_rows_total','?')} "
                          f"-> {r['slice']}")
            summary.append({"report": stem, "rows": rows})
    print(f"\n批量完成。进度总览请运行: python3 scripts/build_progress.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
