#!/usr/bin/env python3
"""局部复核入口（设计方案 §15 方式 C）。V0.2 升级版。

小而有用的动作：`local_review`
  对研究包指定物理页/表沿用原始证据（源 PDF 文字层）重做数值核对，
  生成 _internal/local_review-<run_id>.json，并把不一致表置入 review_queue、
  刷新 复核/index.html。

诚实边界：
  - 只做“单元格内容 vs 原页文字层”一致性复核；不做语义/期间/单位归属。
  - 结论以人工终核为准；本工具提供可回查的记录，不代替人工。

用法:
  run_local.py <研究包目录> --page N | --table <id>
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pipeline import refine  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("package_dir", help="研究包目录（由 run_tableslice/run_assemble 生成）")
    ap.add_argument("--page", type=int, help="指定物理页（1 起始）")
    ap.add_argument("--table", help="逻辑表 ID（如 table-0001）")
    args = ap.parse_args(argv)

    pkg = Path(args.package_dir)
    if not pkg.is_dir():
        print("ERROR: 研究包目录不存在:", pkg)
        return 2
    if args.page is None and args.table is None:
        print("用法: run_local.py <研究包> --page N | --table <id>")
        return 2

    try:
        summary = refine.local_review(pkg, page=args.page, table=args.table)
    except (FileNotFoundError, KeyError) as exc:
        print("ERROR:", exc)
        return 2
    except Exception as exc:  # noqa: BLE001
        print("ERROR: 局部复核失败:", exc)
        return 2

    print(f"局部复核完成（run {summary['run_id']}）:")
    for tid, t in summary["targets"].items():
        verdict = "一致" if t["verdict"] == "ok" else "不一致"
        mm = "；".join(t.get("mismatch_labels", [])[:5])
        print(f"  {tid} · p{t.get('physical_page')} · 数值核对 {verdict} "
              f"({t.get('rows_ok')}/{t.get('rows_total')} 行)" + (f" · 不一致: {mm}" if mm else ""))
    print(f"复核记录: {summary['record']}")
    print("诚实提示：仅文字层数值一致性复核；语义/期间/单位以人工终核为准。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
