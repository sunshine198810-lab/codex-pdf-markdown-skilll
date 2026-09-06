#!/usr/bin/env python3
"""切片 → 研究包规范化组装 CLI（V0.2）。

把一个或多个 `run_tableslice` 切片目录组装进研究包目录结构：
  - 复制表格（重编号保证跨切片唯一）/ 正文阅读顺序候选 / 证据；
  - 补齐 document_map / review_queue / 复核页 / 数据空目录；
  - 规范化 manifest / quality / run 并让关键产物过 schema（Step1）。

诚实声明：overall_state=partial；表格为候选中间形态，未人工核对、不可计算。

用法:
  run_assemble.py --out <研究包目录> <切片目录1> [<切片目录2> ...]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pipeline import assemble  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("slices", nargs="+", help="切片目录（run_tableslice 产物），≥1 个")
    ap.add_argument("--out", required=True, help="输出研究包目录（须为空/不存在）")
    ap.add_argument("--pdf", default=None, help="原 PDF 路径（默认取第一个切片 manifest）")
    args = ap.parse_args(argv)

    try:
        manifest = assemble.assemble_package(
            args.slices, args.out, pdf_path=args.pdf
        )
    except (FileNotFoundError, FileExistsError, ValueError) as exc:
        print("ERROR:", exc)
        return 2

    print("已组装研究包（partial，结构候选）:")
    print("  ", manifest["package_root"])
    print(f"  表格候选: {manifest['artifacts']['tables']}  "
          f"覆盖页: {manifest['artifacts'].get('reading_order_pages', 0)}  "
          f"来源切片: {manifest['artifacts']['source_slices']}")
    from pipeline import validation as _val

    results = _val.validate_package(manifest["package_root"])
    ok = sum(1 for r in results if r.get("valid"))
    print(f"产物 schema 校验: {ok}/{len(results)} 通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
