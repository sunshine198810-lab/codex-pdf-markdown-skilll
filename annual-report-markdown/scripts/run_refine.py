#!/usr/bin/env python3
"""核心财务整理入口（设计方案 §15 方式 B）。V0.2 升级版。

小而有用的动作：`extract_candidates`
  从研究包表格中定位指定指标（行标签归一匹配），把命中写成 **候选**
  （数据/candidates-<run_id>.jsonl，并同步 数据/candidates.jsonl 为最新）。

诚实边界：
  - 只写 candidates，绝不写 facts（facts 需语义/期间/单位/主体验证通过）。
  - eligible_for_calculation=false；本工具不做财务校验，不宣称可计算事实。
  - 行标签经文字层核对（traced）与网格扫描（untraced）会如实区分。

用法:
  run_refine.py <研究包目录> [指标...] [--exact] [--list-tables]
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
    ap.add_argument("indicators", nargs="*", help="指标名；空=列出全部候选行供选择")
    ap.add_argument("--exact", action="store_true", help="整词归一==标签（默认包含匹配）")
    ap.add_argument("--list-tables", action="store_true", help="仅列出包内表格与行标签，不提取")
    args = ap.parse_args(argv)

    pkg = Path(args.package_dir)
    if not pkg.is_dir():
        print("ERROR: 研究包目录不存在:", pkg)
        return 2

    # 诚实检查：p0 空骨架不能整理
    try:
        m = refine._manifest(pkg)
    except FileNotFoundError as exc:
        print("ERROR:", exc)
        return 2
    if m.get("overall_state") == "p0_skeleton_not_parsed":
        print("ERROR: 该研究包是 p0 空骨架（未解析/无表格），不能做核心财务整理。"
              "\n请先用 run_tableslice 切片并用 run_assemble 组装。")
        return 2

    if args.list_tables or not args.indicators:
        for item in refine._all_tables(pkg):
            tid = item["logical_table_id"]
            rows = refine._labels_from_verify(pkg, tid)
            print(f"\n## {tid} · p{item.get('physical_page')} · "
                  f"{'已核对' if rows is not None else '网格扫描'}")
            if rows is None:
                try:
                    rows = refine._rows_from_grid(refine._table_doc(pkg, tid))
                except Exception as exc:  # noqa: BLE001
                    print("  无法读取:", exc)
                    continue
            for r in rows:
                ok = {True: "✓", False: "✗", None: "·"}.get(r.get("ok"))
                print(f"  [{ok}] {r['label']}")
        if not args.indicators and not args.list_tables:
            print("\n未给指标名——上面是可定位的行标签。请指定指标（可用包含匹配）再提取。")
        return 0

    try:
        summary = refine.extract_candidates(
            pkg, args.indicators, match="exact" if args.exact else "contains"
        )
    except Exception as exc:  # noqa: BLE001
        print("ERROR:", exc)
        return 2

    print(f"指标候选提取完成（run {summary['run_id']}）:")
    for ind, cnt in summary["by_indicator"].items():
        print(f"  {ind}: {cnt} 行")
    print(f"共写入 {summary['candidates_written']} 条候选 → {summary['candidates_path']}")
    print("诚实提示：候选不可计算（未做语义/期间/单位/主体绑定），未写入 facts。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
