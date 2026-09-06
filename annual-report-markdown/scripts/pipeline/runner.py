"""runner：编排“标准解析”与 P0 骨架（对应设计方案 §15/§17）。

P0 可实现：`scaffold_package`——建立版本化研究包目录骨架（不覆盖旧包），写入
run.json / manifest.json / quality.json / review_queue.jsonl / 00-阅读入口.md，
overall_state = 'p0_skeleton_not_parsed'。
P0 未实现：`parse_package`（引擎未接线）——明确抛错，不假装成功。
"""

from __future__ import annotations

import datetime
import json
import pathlib

from . import exports, intake
from .version import PIPELINE_VERSION, SCHEMA_VERSION, SKILL_NAME


def _now_iso() -> str:
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def default_package_dir(pdf_path) -> pathlib.Path:
    """默认在 PDF 平级建立 <报告文件名>_研究包。"""
    return pathlib.Path(pdf_path).resolve().parent / (pathlib.Path(pdf_path).stem + "_研究包")


def scaffold_package(pdf_path, output_dir=None, *, copy_pdf: bool = False) -> dict:
    """建立空研究包骨架；返回 manifest。

    若目标研究包目录已存在则拒绝覆盖（版本纪律：不覆盖旧包）。
    """
    pdf_path = pathlib.Path(pdf_path)
    if not pdf_path.is_file():
        raise FileNotFoundError(f"不是可读取的 PDF: {pdf_path}")

    root = pathlib.Path(output_dir) if output_dir else default_package_dir(pdf_path)
    if root.exists() and any(root.iterdir()):
        raise FileExistsError(
            f"目标研究包已存在且非空，拒绝覆盖: {root}。"
            "请使用新目录或先移除旧包。"
        )
    root.mkdir(parents=True, exist_ok=True)

    for sub in ("正文", "表格", "图表", "数据", "索引", "复核", "_internal/pages"):
        (root / sub).mkdir(parents=True, exist_ok=True)

    run_id = datetime.datetime.now().strftime("%Y%m%dT%H%M%S")
    program = {
        "skill_version": SKILL_NAME,
        "pipeline_version": PIPELINE_VERSION,
        "command_line": "run_parse.py scaffold",
    }
    run = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "mode": "p0_scaffold",
        "source_pdf": {
            "path": str(pdf_path.resolve()),
            "sha256_start": intake.sha256_of(pdf_path),
            "sha256_end": None,
            "size_bytes": intake.file_size(pdf_path),
        },
        "program": program,
        "engines": {"renderer": None, "ocr": None, "layout": None, "table": None},
        "config": {"remote_services_off_by_default": True},
        "timing": {
            "started_at": _now_iso(),
            "finished_at": None,
            "elapsed_seconds": None,
            "peak_memory_mb": None,
            "pages_processed": 0,
            "model_calls": 0,
        },
        "failures": ["p0: parsing engines not wired; scaffold only"],
        "page_status": [],
        "output_dir": str(root),
        "overall_state": "p0_skeleton_not_parsed",
    }

    identity = intake.identity_from_filename(pdf_path)
    file_identity = {
        "filename": pdf_path.name,
        "original_path": str(pdf_path.resolve()),
        "sha256": intake.sha256_of(pdf_path),
        "page_count": intake._try_page_count(pdf_path),
        "file_size_bytes": intake.file_size(pdf_path),
        "source_url": None,
    }
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "package_root": str(root),
        "source_pdf": {
            "path": str(pdf_path.resolve()),
            "sha256": file_identity["sha256"],
            "page_count": file_identity["page_count"],
            "copied_into_package": bool(copy_pdf),
        },
        "identity": identity,
        "run": {"run_id": run_id, "pipeline_version": PIPELINE_VERSION},
        "generated_at": _now_iso(),
        "artifacts": {
            "prose_files": [],
            "tables": 0,
            "structured_tables": 0,
            "computable_table_cells": 0,
            "figures": 0,
            "figure_labeled": 0,
            "facts": 0,
            "candidates": 0,
        },
        "overall_state": "p0_skeleton_not_parsed",
        "open_issues": ["解析引擎未接线（P0 → V0.2）"],
        "quality_ref": "索引/quality.json",
        "review_entry_ref": "索引/review_queue.jsonl",
        "entry_00_readme": "00-阅读入口.md",
    }

    if copy_pdf:
        dest = root / "source" / pdf_path.name
        dest.parent.mkdir(parents=True, exist_ok=True)
        import shutil

        shutil.copy2(pdf_path, dest)
        manifest["source_pdf"]["path"] = str(dest)
        manifest["source_pdf"]["copied_into_package"] = True

    exports.write_json(root / "_internal/run.json", run)
    exports.write_json(root / "索引/manifest.json", manifest)

    quality = {
        "schema_version": SCHEMA_VERSION,
        "report_generated_at": _now_iso(),
        "coverage": {
            "content_retention": None,
            "structure_recovery": None,
            "computable": None,
            "review_rate": None,
        },
        "five_layers": {
            "run_and_files": {"ok": True, "note": "只完成了文件级登记"},
            "content_coverage": None,
            "structure": None,
            "text_semantics": None,
            "financial_consistency": None,
        },
        "object_status_summary": {},
        "usage_eligibility_summary": {},
        "validation_results": [],
        "open_issues_count": 1,
        "overall_state": "p0_skeleton_not_parsed",
    }
    exports.write_json(root / "索引/quality.json", quality)
    exports.write_jsonl(root / "索引/review_queue.jsonl", [])

    exports.write_json(
        root / "索引/document_map.json",
        {"sections": [], "unresolved_references": [], "notes": "尚未生成（P0）"},
    )
    # 图表/表格/数据 索引先占位（明确空表，避免误导）
    exports.write_json(root / "表格/index.json", {"logical_tables": [], "state": "empty_p0"})
    exports.write_json(root / "图表/index.json", {"figures": [], "state": "empty_p0"})

    (root / "00-阅读入口.md").write_text(
        exports.entry_00_markdown(root, manifest, run), encoding="utf-8"
    )
    run["timing"]["finished_at"] = _now_iso()
    run["sha256_end"] = intake.sha256_of(pdf_path)  # 结束核对
    exports.write_json(root / "_internal/run.json", run)

    # 产物即校验：jsonschema 可用时对关键产物过 schema，回填 quality.json
    _attach_schema_check(root)
    return manifest


def _attach_schema_check(root) -> None:
    """对研究包关键产物过 schema，并把结果写回 quality.json.validation_results。

    - jsonschema 未安装 → 记录 checked=False（不伪装通过）。
    - 任何产物不合 schema → 追加进 quality 的 open 状态（不掩盖）。
    """
    from . import validation

    results = validation.validate_package(root)
    qpath = root / "索引/quality.json"
    try:
        quality = json.loads(qpath.read_text(encoding="utf-8"))
    except Exception:
        quality = {}
    quality["validation_results"] = results
    quality["schema_check"] = {
        "tool": "jsonschema",
        "available": validation._jsonschema_available(),
        "checked_files": len(results),
        "failed": [r for r in results if r.get("valid") is False],
    }
    exports.write_json(qpath, quality)


def parse_package(pdf_path, output_dir=None, *, mineru_content=None, mineru_start=0,
                  auto_borderless=False):
    """M2/M3 标准解析；可选融合 MinerU 无框线表区域。"""
    from .standard import parse_native_text_package

    root = pathlib.Path(output_dir) if output_dir else default_package_dir(pdf_path)
    return parse_native_text_package(
        pdf_path, root, mineru_content=mineru_content, mineru_start=mineru_start,
        auto_borderless=auto_borderless,
    )
