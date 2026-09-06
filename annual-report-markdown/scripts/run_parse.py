#!/usr/bin/env python3
"""标准解析入口（设计方案 §15 方式 A）。

用法:
  run_parse.py selfcheck                         自检模块/Schema
  run_parse.py inspect   <report.pdf>            来源登记/体检（可运行）
  run_parse.py scaffold  <report.pdf> [--output DIR] [--copy-pdf]  建立研究包骨架（可运行）
  run_parse.py checkschema <研究包目录>           对关键产物过 schema 校验（可运行）
  run_parse.py run       <report.pdf> [--output DIR]  V0.3-C4.4 标准解析
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pipeline import intake, runner, validation
from pipeline.version import DISPLAY_NAME, PIPELINE_VERSION, SCHEMA_VERSION


def _cmd_selfcheck() -> int:
    problems = validation.selfcheck()
    if not problems:
        schema_ok = "schema 自我合规 ✓" if not validation.validate_schema_files() else "schema 自我合规有问题"
        avail = "jsonschema 已装" if validation._jsonschema_available() else "jsonschema 未装（跳过严格校验）"
        print(f"selfcheck OK: {PIPELINE_VERSION}, schema {SCHEMA_VERSION} · {schema_ok} · {avail}")
        return 0
    for p in problems:
        print("PROBLEM:", p)
    return 1


def _cmd_checkschema(package_dir: str) -> int:
    """对已生成研究包的关键产物过 schema；不通过则退出码非 0。"""
    import json as _json

    pkg = Path(package_dir)
    if not pkg.is_dir():
        print("ERROR: 研究包目录不存在:", pkg)
        return 2
    results = validation.validate_package(pkg)
    failed = 0
    for r in results:
        mark = "OK  " if r.get("valid") else "FAIL"
        if not r.get("valid"):
            failed += 1
        detail = r.get("error") or r.get("reason") or ""
        print(f"{mark} {r.get('file'):38s} schema={r.get('schema'):14s} {detail}")
    print(f"checkschema: {len(results)-failed}/{len(results)} 通过"
          + ("" if failed == 0 else f"，{failed} 个未通过"))
    # 写回 quality.schema_check（复用 runner 逻辑）
    try:
        from pipeline import runner as _runner
        _runner._attach_schema_check(pkg)
    except Exception as exc:  # noqa: BLE001
        print("说明: 无法回填 quality.schema_check:", exc)
    return 1 if failed else 0


def _cmd_inspect(pdf: str) -> int:
    try:
        payload = intake.inspect_pdf(pdf)
    except FileNotFoundError as exc:
        print("ERROR:", exc)
        return 2
    import json

    print(json.dumps(payload, ensure_ascii=False, indent=2))
    print("说明: 仅文件级登记；引擎未接线，页数探测可选。")
    return 0


def _cmd_scaffold(pdf: str, output: str | None, copy_pdf: bool) -> int:
    try:
        manifest = runner.scaffold_package(pdf, output_dir=output, copy_pdf=copy_pdf)
    except (FileNotFoundError, FileExistsError) as exc:
        print("ERROR:", exc)
        return 2
    print("已建立研究包骨架（未解析）:")
    print("  ", manifest["package_root"])
    print("状态: p0_skeleton_not_parsed —— 引擎未接线，请勿当作已解析资料。")
    # 产物 schema 校验摘要
    from pipeline import validation as _val

    results = _val.validate_package(manifest["package_root"])
    ok = sum(1 for r in results if r.get("valid"))
    print(f"产物 schema 校验: {ok}/{len(results)} 通过"
          + ("" if ok == len(results) else "（部分未通过，请运行 checkschema 查看）"))
    return 0


def _cmd_run(pdf: str, output: str | None, mineru_content: str | None, mineru_start: int,
             auto_borderless: bool) -> int:
    try:
        manifest = runner.parse_package(
            pdf, output_dir=output, mineru_content=mineru_content, mineru_start=mineru_start,
            auto_borderless=auto_borderless,
        )
    except (FileNotFoundError, FileExistsError, RuntimeError, ValueError) as exc:
        print("ERROR:", exc)
        return 2
    print(f"已生成 {PIPELINE_VERSION} 研究包:")
    print("  ", manifest["package_root"])
    print("状态:", manifest["overall_state"])
    print("页数:", manifest["source_pdf"].get("page_count"))
    print("表格片段:", manifest["artifacts"].get("tables"))
    print("逻辑主表:", manifest["artifacts"].get("logical_tables", 0))
    print("可计算事实:", manifest["artifacts"].get("facts", 0))
    print("待复核问题:", len(manifest.get("open_issues", [])))
    results = validation.validate_package(manifest["package_root"])
    failed = [r for r in results if r.get("valid") is False]
    print(f"Schema 校验: {len(results)-len(failed)}/{len(results)} 通过")
    return 1 if failed else 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="run_parse",
        description=f"{DISPLAY_NAME} · 标准解析入口（{PIPELINE_VERSION}）",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_sc = sub.add_parser("selfcheck", help="自检模块与 Schema")
    p_sc.set_defaults(func=lambda a: _cmd_selfcheck())

    p_cs = sub.add_parser("checkschema", help="对研究包关键产物过 schema 校验")
    p_cs.add_argument("package_dir", help="研究包目录")
    p_cs.set_defaults(func=lambda a: _cmd_checkschema(a.package_dir))

    p_in = sub.add_parser("inspect", help="来源登记/体检")
    p_in.add_argument("pdf", help="上市公司报告 PDF 路径")
    p_in.set_defaults(func=lambda a: _cmd_inspect(a.pdf))

    p_sf = sub.add_parser("scaffold", help="建立空研究包骨架")
    p_sf.add_argument("pdf", help="PDF 路径")
    p_sf.add_argument("--output", help="目标研究包目录（默认 PDF 平级 <名>_研究包）")
    p_sf.add_argument("--copy-pdf", action="store_true", help="复制 PDF 进包（离线便携）")
    p_sf.set_defaults(func=lambda a: _cmd_scaffold(a.pdf, a.output, a.copy_pdf))

    p_run = sub.add_parser("run", help="V0.3-C4.4 标准解析（主表事实 + 矢量轮廓数字隔离复核）")
    p_run.add_argument("pdf", help="PDF 路径")
    p_run.add_argument("--output", help="目标研究包目录")
    p_run.add_argument("--mineru-content", help="MinerU content_list.json（无框线表区域候选）")
    p_run.add_argument("--mineru-start", type=int, default=0,
                       help="MinerU 切片起始页（0起始），用于换算物理页")
    p_run.add_argument("--auto-borderless", action="store_true",
                       help="自动发现坍缩的中文 A 股四列主表并有界调用本机 MinerU")
    p_run.set_defaults(func=lambda a: _cmd_run(
        a.pdf, a.output, a.mineru_content, a.mineru_start, a.auto_borderless
    ))

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
