#!/usr/bin/env python3
"""V0.2 核心切片（表格结构候选 + 证据回查）。

把“已实测可行”的两层接到 pipeline：
  - pdfplumber 证据层：词级坐标 + 阅读顺序候选（正文 reading-order 文件）；
  - MinerU 表格结构候选：content_list.json → 表格 JSON/HTML（rowspan/colspan、表题/表注候选）。

诚实声明：
  - 产出是“结构候选”，candidate_status=extracted，未人工核对，eligible_for_calculation=false；
  - caption 是候选（MinerU 会过度捕获前文行）；只给表级证据，不编造单元格像素坐标；
  - 只处理指定页的表格结构候选，不做正文语义/财务语义/校验（V0.2 后段）。

用法:
  run_tableslice.py <report.pdf> --mineru <content_list.json> --mineru-start 13 \
                    --pages 14-15 --out <输出目录>
  run_tableslice.py <report.pdf> --pages 2        # 仅 pdfplumber 证据/阅读顺序候选
"""
from __future__ import annotations

import argparse
import datetime
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import verify_tables  # noqa: E402

from pipeline import adapters, exports, intake, tables as tables_mod  # noqa: E402
from pipeline.version import PIPELINE_VERSION, SCHEMA_VERSION, SKILL_NAME  # noqa: E402


def _now():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def _parse_pages(spec: str):
    """'14-15' 或 '14,16' → set{14,15,...}；None 表示不过滤。"""
    if not spec:
        return None
    out = set()
    for part in spec.split(","):
        part = part.strip()
        m = re.fullmatch(r"(\d+)-(\d+)", part)
        if m:
            out.update(range(int(m.group(1)), int(m.group(2)) + 1))
        elif part.isdigit():
            out.add(int(part))
    return out


def _lines_from_words(words, tol=3.0):
    """top 邻近 + 左→右分组为阅读行（纯启发式，候选）。"""
    ws = sorted(words, key=lambda w: (round(w["top"] / tol), w["x0"]))
    lines = []
    for w in ws:
        if not lines or abs(w["top"] - lines[-1]["top"]) > tol:
            lines.append({"top": w["top"], "x0": w["x0"], "words": [w["text"]]})
        else:
            lines[-1]["words"].append(w["text"])
    for ln in lines:
        ln["text"] = " ".join(ln["words"])
        del ln["words"]
    return lines


def _build_reading_order(pdf_path, pages):
    """pdfplumber 阅读顺序候选（每页一个 JSON）。pdfplumber 不可用时跳过。"""
    try:
        ev = adapters.pdfplumber_evidence(pdf_path, pages=pages)
    except Exception as exc:  # noqa: BLE001
        return [], f"pdfplumber 不可用，跳过阅读顺序候选: {exc}"
    docs = []
    for p in ev["pages"]:
        docs.append(
            {
                "physical_page": p["physical_page"],
                "width_pt": p["width_pt"],
                "height_pt": p["height_pt"],
                "reading_lines": _lines_from_words(p["words"]),
                "engine": ev["engine"],
                "engine_version": ev["engine_version"],
                "candidate_status": "extracted",
            }
        )
    return docs, None


def build_slice(pdf_path, out_dir, *, pages=None, mineru=None, mineru_start=0) -> dict:
    pdf_path = Path(pdf_path)
    if not pdf_path.is_file():
        raise FileNotFoundError(pdf_path)
    root = Path(out_dir)
    if root.exists() and any(root.iterdir()):
        raise FileExistsError(f"输出目录已存在且非空: {root}")
    root.mkdir(parents=True, exist_ok=True)

    identity = intake.identity_from_filename(pdf_path)
    sha = intake.sha256_of(pdf_path)

    evidence_rows = []
    table_docs = []
    warnings = []

    # ---- pdfplumber 阅读顺序候选 ----
    ro_docs, err = _build_reading_order(pdf_path, pages)
    if err:
        warnings.append(err)
    for d in ro_docs:
        exports.write_json(
            root / "正文" / f"reading-order-p{d['physical_page']:04d}.json", d
        )

    # ---- MinerU 表格结构候选 ----
    mineru_tables = []
    if mineru:
        ingested = adapters.run_adapter(
            "mineru_tables", str(mineru), start_page0=mineru_start
        )
        mineru_tables = ingested["tables"]
        if pages:
            mineru_tables = [t for t in mineru_tables if t["physical_page"] in pages]
        warnings.append(
            f"MinerU content_list 表数={ingested['table_count']}；切片保留={len(mineru_tables)}"
        )

    table_index = []
    for n, mt in enumerate(mineru_tables, start=1):
        table_id = f"table-{n:04d}"
        ev_id = f"ev-{table_id}"
        # 证据：表级定位（不编造单元格像素坐标）
        evidence = {
            "evidence_id": ev_id,
            "source_kind": "table",
            "granularity": "table",
            "locate": {
                "physical_page": mt["physical_page"],
                "printed_page_label": None,
                "bbox_norm": mt.get("bbox_norm"),
                "object_id": table_id,
                "pdf_reference": str(pdf_path),
            },
            "engine": "mineru",
            "engine_version": ingested.get("engine_version"),
            "processing": [],
            "confidence": None,
            "caption_note": "caption 为候选，可能过度捕获前文行",
        }
        evidence_rows.append(evidence)

        # 单元格资格：结构候选，未人工核对
        cells = mt["grid"].get("cells", [])
        for c in cells:
            c["candidate_status"] = "extracted"
            c["usage"] = {
                "readable": False,
                "traceable": False,
                "citable": False,
                "computable": False,
            }
        doc = {
            "logical_table_id": table_id,
            "physical_page": mt["physical_page"],
            "caption_candidates": mt.get("caption_candidates", []),
            "footnote_candidates": mt.get("footnote_candidates", []),
            "grid": {"rows": mt["grid"]["rows"], "cols": mt["grid"]["cols"], "cells": cells},
            "evidence_ref": ev_id,
            "candidate_status": "extracted",
            "overall_eligible_for_calculation": False,
        }
        table_docs.append(doc)
        exports.write_json(root / "表格" / f"{table_id}.json", doc)
        (root / "表格" / f"{table_id}.html").write_text(
            exports.table_document_html(table_id, {"fragment": doc, "evidence": evidence}),
            encoding="utf-8",
        )
        table_index.append(
            {
                "logical_table_id": table_id,
                "physical_page": mt["physical_page"],
                "rows": mt["grid"]["rows"],
                "cols": mt["grid"]["cols"],
                "cells": len(cells),
                "json": f"表格/{table_id}.json",
                "html": f"表格/{table_id}.html",
                "evidence": ev_id,
                "candidate_status": "extracted",
            }
        )

    exports.write_json(root / "表格/index.json", {"tables": table_index, "state": "candidate_only"})
    exports.write_jsonl(root / "索引/evidence.jsonl", evidence_rows)

    # ---- quality（诚实：部分完成，仅结构候选）----
    quality = {
        "schema_version": SCHEMA_VERSION,
        "report_generated_at": _now(),
        "coverage": {
            "content_retention": {"tables_captured": len(mineru_tables), "note": "仅指定页表格块"},
            "structure_recovery": {"tables_structured": len(table_docs), "note": "结构候选，未人工核对"},
            "computable": None,
            "review_rate": {"reviewed": 0, "candidates": len(mineru_tables)},
        },
        "five_layers": {
            "run_and_files": {"ok": True},
            "content_coverage": {"warnings": warnings},
            "structure": {"candidate_tables": len(table_docs), "verified": 0},
            "text_semantics": None,
            "financial_consistency": None,
        },
        "object_status_summary": {"extracted": len(mineru_tables), "accepted_by_rules": 0},
        "usage_eligibility_summary": {"computable": 0, "citable": 0, "traceable": len(mineru_tables)},
        "validation_results": [],
        "open_issues_count": len(table_docs) + len(warnings),
        "overall_state": "partial",
        "note": "V0.2 核心切片：仅表格结构候选 + 证据回查；未做人工核对/语义/财务校验",
    }
    exports.write_json(root / "索引/quality.json", quality)

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "package_root": str(root),
        "source_pdf": {"path": str(pdf_path.resolve()), "sha256": sha},
        "identity": identity,
        "run": {"run_id": datetime.datetime.now().strftime("%Y%m%dT%H%M%S"),
                "pipeline_version": PIPELINE_VERSION},
        "generated_at": _now(),
        "artifacts": {
            "tables": len(table_docs),
            "structured_tables": len(table_docs),
            "computable_table_cells": 0,
            "reading_order_pages": len(ro_docs),
            "evidence": len(evidence_rows),
        },
        "overall_state": "partial",
        "open_issues": ["结构候选未人工核对；语义与财务校验未做（V0.2 后段）"],
        "entry_00_readme": "00-阅读入口.md",
    }
    exports.write_json(root / "索引/manifest.json", manifest)
    exports.write_json(root / "_internal/run.json", {
        "schema_version": SCHEMA_VERSION,
        "run_id": manifest["run"]["run_id"],
        "mode": "tableslice_v02_core",
        "source_pdf": {"path": str(pdf_path.resolve()), "sha256_start": sha, "sha256_end": None},
        "program": {"skill_version": SKILL_NAME, "pipeline_version": PIPELINE_VERSION,
                    "command_line": "run_tableslice.py"},
        "engines": {"renderer": "pdfplumber", "ocr": None, "layout": None, "table": "mineru-pipeline"},
        "config": {},
        "timing": {"started_at": _now(), "finished_at": _now(), "elapsed_seconds": None,
                   "pages_processed": len(ro_docs), "model_calls": 0},
        "failures": warnings,
        "page_status": [{"physical_page": t["physical_page"], "status": "processed"}
                        for t in mineru_tables],
        "output_dir": str(root),
        "overall_state": "partial",
    })

    # ---- 00-阅读入口.md ----
    lines = [
        "# 阅读入口（V0.2 核心切片 · 表格结构候选）",
        "",
        f"> 状态：**partial** — 仅表格结构候选 + 证据回查；未人工核对、未做语义/财务校验。",
        "",
        "- 原 PDF：" + str(pdf_path.resolve()),
        "- SHA-256：`" + sha + "`",
        "- 公司/报告（来自文件名，待封面核对）：" + str(identity.get("issuer_identity", {}).get("company_name_as_reported", "—")),
        "",
        "## 切片范围",
        "- 物理页过滤：" + (",".join(map(str, sorted(pages))) if pages else "全部（MinerU 输入内）"),
        "- MinerU 表数（切片内）：" + str(len(mineru_tables)),
        "- 阅读顺序候选页数：" + str(len(ro_docs)),
        "",
        "## 目录",
        "- 表格/index.json；table-*.json/.html（结构候选，HTML 可人工复核）",
        "- 索引/evidence.jsonl（表级证据：物理页 + bbox_norm + 引擎版本）",
        "- 索引/quality.json（诚实状态）",
        "- 正文/reading-order-pNNNN.json（pdfplumber 阅读顺序候选）",
        "",
        "## 使用注意",
        "- candidate_status=extracted：**未人工核对，不可引用、不可计算**。",
        "- caption/footnote 是候选，可能过度捕获或缺失。",
        "- 单元格无像素级坐标（只有表级 bbox_norm）；不编造单元格坐标。",
    ]
    (root / "00-阅读入口.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    # ---- 表体数值核对（固化的文字层 + x 坐标法，见 verify_tables.py）----
    verify_summary = None
    if table_docs:
        try:
            vres = verify_tables.verify_slice(str(pdf_path), root)
            t_ok, t_tot = vres["tables_ok"], vres["tables_total"]
            r_ok = sum(x["rows_ok"] for x in vres["tables"].values())
            r_tot = sum(x["rows_total"] for x in vres["tables"].values())
            verify_summary = {
                "tables_ok": t_ok, "tables_total": t_tot,
                "data_rows_ok": r_ok, "data_rows_total": r_tot,
            }
            quality["five_layers"]["text_semantics"] = {"table_value_check": verify_summary}
            quality["note"] = (quality.get("note", "")
                               + f"；数值核对（文字层+x坐标）：表 {t_ok}/{t_tot} 一致、"
                                 f"数据行 {r_ok}/{r_tot}（详见 索引/table_verify.md）")
            exports.write_json(root / "索引/quality.json", quality)
            lines.append(
                f"- 数值核对：表 {t_ok}/{t_tot} 一致（数据行 {r_ok}/{r_tot}）；详见 索引/table_verify.md"
            )
            (root / "00-阅读入口.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            warnings.append("表体数值核对未执行: " + str(exc))

    return {"tables": table_docs, "evidence": evidence_rows, "warnings": warnings,
            "verify": verify_summary}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("pdf", help="上市公司报告 PDF")
    ap.add_argument("--mineru", help="MinerU content_list.json 路径（可选）")
    ap.add_argument("--mineru-start", type=int, default=0,
                    help="MinerU -s 起始页（0 起始）；用于 page_idx→physical_page 换算")
    ap.add_argument("--pages", default=None, help="物理页过滤，如 '14-15' 或 '14,16'")
    ap.add_argument("--out", required=True, help="输出切片目录（须为空/不存在）")
    args = ap.parse_args(argv)

    try:
        pages = _parse_pages(args.pages)
        result = build_slice(
            args.pdf,
            args.out,
            pages=pages,
            mineru=args.mineru,
            mineru_start=args.mineru_start,
        )
    except (FileNotFoundError, FileExistsError) as exc:
        print("ERROR:", exc)
        return 2
    for w in result["warnings"]:
        print("warn:", w)
    print(f"已生成切片（状态 partial，结构候选）: {Path(args.out).resolve()}")
    print(f"  表格候选: {len(result['tables'])}  证据行: {len(result['evidence'])}")
    if result.get("verify"):
        v = result["verify"]
        print(f"  数值核对: 表 {v['tables_ok']}/{v['tables_total']} 一致；"
              f"数据行 {v['data_rows_ok']}/{v['data_rows_total']}（见 索引/table_verify.md）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
