"""assemble：切片 → 研究包规范化组装（V0.2，对应设计方案 §15 产物结构）。

把 `run_tableslice` 产出的一个或多个切片目录（表格结构候选 + 证据 + 阅读顺序候选）
组装进**研究包目录结构**：复制表格/正文/证据，补齐 document_map / review_queue /
复核页入口，规范化 manifest / quality / run 并让其满足对应 schema。

诚实边界：
  - 切片表格 JSON 是“候选中间形态”，不是 table.schema 的物理片段结构；本模块**不改写**其
    单元格，只重编号 table_id 以保证跨切片唯一，并在 quality 注明形态差异。
  - 不做正文语义 / 财务语义 / 跨页逻辑表拼接（V0.2 后段）。candidate_status 保持
    `extracted`、未人工核对、eligible_for_calculation=false。
  - overall_state=partial；B（核心财务整理）/C（局部复核）可在此输入上运行。
"""

from __future__ import annotations

import datetime
import json
import pathlib
import re

from . import exports, intake, validation
from .version import PIPELINE_VERSION, SCHEMA_VERSION, SKILL_NAME

_RE_NUM = re.compile(r"table-(\d+)")


def _now() -> str:
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def _read_json(path) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _renumber(table_id: str, mapping: dict) -> str:
    """按映射重编号 table-0007 → 新 id（mapping: 旧 id → 新 id）。"""
    return mapping.get(table_id, table_id)


def _collect_slice(slice_dir: pathlib.Path, index: int) -> dict:
    """读取一个切片目录，返回其内容；缺少关键文件时抛错（诚实：不允许静默丢对象）。"""
    sd = pathlib.Path(slice_dir)
    manifest = _read_json(sd / "索引/manifest.json")
    quality = _read_json(sd / "索引/quality.json")
    tables_json = list((sd / "表格").glob("table-*.json"))
    reading = list((sd / "正文").glob("reading-order-p*.json"))
    evidence_rows = []
    ev_path = sd / "索引/evidence.jsonl"
    if ev_path.is_file():
        for line in ev_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                evidence_rows.append(json.loads(line))
    # 核对状态（可选）
    verify = None
    if (sd / "索引/table_verify.json").is_file():
        verify = _read_json(sd / "索引/table_verify.json")
    check = None
    if (sd / "目检/table_check.json").is_file():
        check = _read_json(sd / "目检/table_check.json")

    # 本 slice 内表排序（重编号在 assemble 全局层做，避免跨 slice 冲突）
    ordered = sorted(
        tables_json,
        key=lambda p: (
            _read_json(p).get("physical_page", 0),
            int(_RE_NUM.search(p.stem).group(1)) if _RE_NUM.search(p.stem) else 0,
        ),
    )
    tables = [{"old_id": p.stem, "path": p} for p in ordered]
    return {
        "index": index,
        "slice_dir": str(sd.resolve()),
        "manifest": manifest,
        "quality": quality,
        "tables": tables,
        "reading": sorted(reading),
        "evidence": evidence_rows,
        "verify": verify,
        "check": check,
    }


def assemble_package(
    slice_dirs,
    out_dir,
    *,
    pdf_path=None,
) -> dict:
    """把一个或多个切片目录组装成研究包；返回 manifest 与汇总。"""
    slice_dirs = [pathlib.Path(s) for s in slice_dirs]
    if not slice_dirs:
        raise ValueError("至少需要一个切片目录")
    for s in slice_dirs:
        if not (s / "索引/manifest.json").is_file():
            raise FileNotFoundError(f"不是有效切片目录（缺 索引/manifest.json）: {s}")

    collected = [_collect_slice(sd, i) for i, sd in enumerate(slice_dirs, start=1)]

    # ---- 源 PDF 一致性：以第一个切片为准，其余 sha 必须一致 ----
    base = collected[0]
    src_path = pdf_path or base["manifest"]["source_pdf"]["path"]
    sha = base["manifest"]["source_pdf"]["sha256"]
    for c in collected[1:]:
        other_sha = c["manifest"]["source_pdf"]["sha256"]
        if other_sha != sha:
            raise ValueError(
                f"切片源不一致（sha 不同），拒绝组装: {c['slice_dir']} "
                f"({other_sha} != {sha})"
            )

    root = pathlib.Path(out_dir)
    if root.exists() and any(root.iterdir()):
        raise FileExistsError(f"目标研究包已存在且非空，拒绝覆盖: {root}")
    root.mkdir(parents=True, exist_ok=True)
    for sub in ("正文", "表格", "图表", "数据", "索引", "复核", "_internal/pages"):
        (root / sub).mkdir(parents=True, exist_ok=True)

    # ---- 全局重编号（跨切片唯一），并同步 html / evidence / index ----
    # 先收集全部表 doc，按 (physical_page, 来源切片 index, 原序号) 排序后统一编号
    gathered = []
    for c in collected:
        for t in c["tables"]:
            doc = _read_json(t["path"])
            seq = int(_RE_NUM.search(t["old_id"]).group(1)) if _RE_NUM.search(t["old_id"]) else 0
            gathered.append(
                {
                    "slice_index": c["index"],
                    "old_id": t["old_id"],
                    "path": t["path"],
                    "doc": doc,
                    "physical_page": doc.get("physical_page", 0),
                    "seq": seq,
                    "slice_dir": c["slice_dir"],
                }
            )
    gathered.sort(key=lambda g: (g["physical_page"], g["slice_index"], g["seq"]))
    mapping = {}
    for n, g in enumerate(gathered, start=1):
        mapping[g["old_id"]] = f"table-{n:04d}"

    all_tables = []
    for n, g in enumerate(gathered, start=1):
        new_id = f"table-{n:04d}"
        doc = dict(g["doc"])
        doc["logical_table_id"] = new_id
        if "evidence_ref" in doc and doc["evidence_ref"]:
            doc["evidence_ref"] = _renumber(doc["evidence_ref"], mapping)
        exports.write_json(root / "表格" / f"{new_id}.json", doc)
        # 重新生成 HTML（用 table_document_html，id 同步）；证据行取自其来源切片
        c = collected[g["slice_index"] - 1]
        ev = next(
            (e for e in c["evidence"] if e.get("evidence_id") == doc.get("evidence_ref")),
            {"evidence_id": None, "engine": "mineru",
             "locate": {"physical_page": doc.get("physical_page")}},
        )
        (root / "表格" / f"{new_id}.html").write_text(
            exports.table_document_html(new_id, {"fragment": doc, "evidence": ev}),
            encoding="utf-8",
        )
        all_tables.append(
            {
                "logical_table_id": new_id,
                "old_id": g["old_id"],
                "physical_page": doc.get("physical_page"),
                "slice": g["slice_dir"],
                "rows": doc.get("grid", {}).get("rows"),
                "cols": doc.get("grid", {}).get("cols"),
                "json": f"表格/{new_id}.json",
                "html": f"表格/{new_id}.html",
                "candidate_status": doc.get("candidate_status", "extracted"),
            }
        )

    # ---- 阅读顺序候选（按页去重）----
    seen_pages = set()
    for c in collected:
        for p in c["reading"]:
            d = _read_json(p)
            pg = d.get("physical_page")
            if pg in seen_pages:
                continue
            seen_pages.add(pg)
            exports.write_json(root / "正文" / p.name, d)

    # ---- 证据合并（evidence_id 同步为 ev-table-XXXX）----
    ev_rows = []
    for c in collected:
        for e in c["evidence"]:
            e = dict(e)
            if e.get("evidence_id"):
                e["evidence_id"] = _renumber(e["evidence_id"], mapping)
            loc = e.get("locate", {})
            if loc.get("object_id"):
                loc = dict(loc)
                loc["object_id"] = _renumber(loc["object_id"], mapping)
                e["locate"] = loc
            ev_rows.append(e)
    exports.write_jsonl(root / "索引/evidence.jsonl", ev_rows)

    # ---- 表格 index ----
    exports.write_json(
        root / "表格/index.json",
        {"tables": all_tables, "state": "assemble_partial_candidate"},
    )
    # 图表/数据 目录空占位（明确 empty，不制造假内容）
    exports.write_json(root / "图表/index.json", {"figures": [], "state": "empty"})
    exports.write_jsonl(root / "数据/candidates.jsonl", [])
    exports.write_jsonl(root / "数据/facts.jsonl", [])

    # ---- 核对汇总（verify / table_check）----
    # 切片 table_verify.json 是 verify_slice 输出：每表含 rows_ok/rows_total/verdict/
    # mismatch_labels/row_results。逐表保留完整明细并重命名 id，行数用各表求和。
    tables_ok = rows_ok = tables_tot = rows_tot = 0
    per_table = {}
    for c in collected:
        v = c.get("verify")
        if v:
            vt = v.get("tables", {})
            for old_id, item in vt.items():
                new_id = _renumber(old_id, mapping)
                item = dict(item)
                item["physical_page"] = item.get("physical_page", 0)
                per_table[new_id] = item  # 保留 row_results，供 B 识别 traced
                tables_tot += 1
                if item.get("verdict") == "ok":
                    tables_ok += 1
                rows_tot += item.get("rows_total", 0) or 0
                rows_ok += item.get("rows_ok", 0) or 0
    verify_summary = None
    if tables_tot:
        verify_summary = {
            "tables_ok": tables_ok,
            "tables_total": tables_tot,
            "data_rows_ok": rows_ok,
            "data_rows_total": rows_tot,
        }
    exports.write_json(
        root / "索引/table_verify.json",
        {
            "source_pdf": str(pathlib.Path(src_path).resolve()),
            "schema_version": SCHEMA_VERSION,
            "method": "pdfplumber_text_layer + x_order prefix check (汇总自切片)",
            "tables_total": tables_tot,
            "tables_ok": tables_ok,
            "summary": verify_summary,
            "tables": per_table,
            "note": "由切片核对结果汇总（文字层+x坐标）；结构候选未做语义核对",
        },
    )

    # ---- document_map（诚实：尚无章节映射，登记切片覆盖页为 unresolved）----
    covered_pages = sorted({t["physical_page"] for t in all_tables if t.get("physical_page")})
    exports.write_json(
        root / "索引/document_map.json",
        {
            "sections": [],
            "unresolved_references": [],
            "notes": (
                f"由 {len(collected)} 个切片组装；覆盖物理页 {covered_pages}。"
                "章节地图尚未生成（需正文/目录引擎，V0.2 后段）。"
            ),
        },
    )

    # ---- review_queue（从核对不一致/未核对生成；按影响排序）----
    run_id = datetime.datetime.now().strftime("%Y%m%dT%H%M%S")
    queue = []
    n = 0
    for tid, item in sorted(per_table.items()):
        mismatch = item.get("mismatch_labels") or []
        verdict = item.get("verdict")
        if verdict is False or mismatch:
            n += 1
            queue.append(
                {
                    "review_id": f"rev-{n:04d}",
                    "priority": 1 if verdict is False else 2,
                    "object_ref": f"表格/{tid}.json",
                    "reason": (
                        "数值核对不一致（文字层+x坐标）"
                        if verdict is False
                        else f"待人工核对标签: {mismatch[:5]}"
                    ),
                    "evidence_clip": None,
                    "candidates": [],
                    "methods_tried": ["verify_tables(文字层+x坐标)"],
                    "next_step": "人工核对原始页 / x坐标列向重建",
                    "status": "open",
                    "created_at": _now(),
                }
            )
    # 全部表格未人工核对 → 记一条总括（避免刷屏，按影响最高 1 条）
    if all_tables and not queue:
        queue.append(
            {
                "review_id": "rev-0001",
                "priority": 3,
                "object_ref": "表格/index.json",
                "reason": "全部表格为结构候选（extracted），尚未人工核对语义/期间/单位/主体",
                "evidence_clip": None,
                "candidates": [],
                "methods_tried": [],
                "next_step": "人工抽查复核 / 语义与财务校验（V0.2 后段）",
                "status": "open",
                "created_at": _now(),
            }
        )
    exports.write_jsonl(root / "索引/review_queue.jsonl", queue)

    # ---- manifest ----
    page_count = None
    try:
        page_count = intake._try_page_count(src_path)
    except Exception:  # noqa: BLE001
        page_count = None
    # identity 规范化：旧切片 identity 可能缺 file_identity（identity.schema required）
    identity = dict(base["manifest"].get("identity") or {})
    if "file_identity" not in identity or identity.get("file_identity") is None:
        identity["file_identity"] = intake.file_identity_of(src_path, page_count=page_count)
    if not identity.get("document_id"):
        identity["document_id"] = intake.identity_from_filename(src_path).get("document_id")
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "package_root": str(root),
        "source_pdf": {
            "path": str(pathlib.Path(src_path).resolve()),
            "sha256": sha,
            "page_count": page_count,
        },
        "identity": identity,
        "run": {"run_id": run_id, "pipeline_version": PIPELINE_VERSION},
        "generated_at": _now(),
        "artifacts": {
            "prose_files": [],
            "tables": len(all_tables),
            "structured_tables": len(all_tables),
            "computable_table_cells": 0,
            "figures": 0,
            "figure_labeled": 0,
            "facts": 0,
            "candidates": 0,
            "evidence": len(ev_rows),
            "reading_order_pages": len(seen_pages),
            "source_slices": len(collected),
        },
        "overall_state": "partial",
        "open_issues": [
            "切片组装为研究包：结构候选，未人工核对/语义/财务校验",
            "表格为候选中间形态，未声明满足 table.schema（物理片段 schema）",
            "章节地图/跨页逻辑表未生成（V0.2 后段）",
        ],
        "quality_ref": "索引/quality.json",
        "review_entry_ref": "索引/review_queue.jsonl",
        "entry_00_readme": "00-阅读入口.md",
    }
    exports.write_json(root / "索引/manifest.json", manifest)

    # ---- run.json ----
    run = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "mode": "assemble_slices",
        "source_pdf": {
            "path": str(pathlib.Path(src_path).resolve()),
            "sha256_start": sha,
            "sha256_end": None,
            "size_bytes": intake.file_size(src_path),
        },
        "program": {
            "skill_version": SKILL_NAME,
            "pipeline_version": PIPELINE_VERSION,
            "command_line": "pipeline.assemble.assemble_package",
        },
        "engines": {"renderer": "pdfplumber(切片)", "table": "mineru-pipeline(切片)"},
        "config": {},
        "timing": {
            "started_at": _now(),
            "finished_at": _now(),
            "elapsed_seconds": None,
            "peak_memory_mb": None,
            "pages_processed": len(seen_pages),
            "model_calls": 0,
        },
        "failures": ["结构候选未人工核对；语义/财务校验未做"],
        "page_status": [
            {"physical_page": p, "status": "processed"} for p in covered_pages
        ],
        "output_dir": str(root),
        "overall_state": "partial",
    }
    exports.write_json(root / "_internal/run.json", run)

    # ---- quality（规范化 + schema 校验回填）----
    quality = {
        "schema_version": SCHEMA_VERSION,
        "report_generated_at": _now(),
        "coverage": {
            # 覆盖率是 0..1 整体比例（见 quality.schema）；切片组装无法对全书算整体覆盖率 → null
            "content_retention": None,
            "structure_recovery": None,
            "computable": None,
            "review_rate": None,
            "by_language_industry_scan": None,
        },
        "five_layers": {
            "run_and_files": {"ok": True},
            "content_coverage": {
                "source_slices": len(collected),
                "tables_captured": len(all_tables),
                "covered_pages": covered_pages,
                "note": "仅覆盖切片页，非全书覆盖率",
            },
            "structure": {"candidate_tables": len(all_tables), "verified": tables_ok},
            "text_semantics": (
                {"table_value_check": verify_summary} if verify_summary else None
            ),
            "financial_consistency": None,
        },
        "object_status_summary": {
            "extracted": len(all_tables),
            "accepted_by_rules": 0,
            "needs_review": len(queue),
        },
        "usage_eligibility_summary": {
            "computable": 0,
            "citable": 0,
            "traceable": len(all_tables),
        },
        "validation_results": [],
        "open_issues_count": len(all_tables) + len(queue) + len(collected),
        "overall_state": "partial",
        "note": (
            f"由 {len(collected)} 个切片组装；表格为候选中间形态（非 table.schema 物理片段结构），"
            "未改写单元格。"
            + (f"数值核对（文字层+x坐标）：表 {tables_ok}/{tables_tot}、数据行 {rows_ok}/{rows_tot}。"
               if verify_summary else "")
        ),
    }
    exports.write_json(root / "索引/quality.json", quality)

    # ---- 复核页入口（静态汇总，链接各表格 HTML + 原 PDF）----
    (root / "复核/index.html").write_text(
        _review_index_html(root, manifest, all_tables, per_table, queue, covered_pages),
        encoding="utf-8",
    )

    # ---- 00-阅读入口.md ----
    (root / "00-阅读入口.md").write_text(
        _entry_00_markdown(root, manifest, run, all_tables, verify_summary, covered_pages),
        encoding="utf-8",
    )

    # ---- 产物 schema 校验（Step1：产物即校验）----
    _attach_schema_check(root)
    return manifest


def _attach_schema_check(root) -> None:
    """对研究包关键产物过 schema 并回填 quality.json（jsonschema 可用时）。"""
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


def _review_index_html(root, manifest, tables, per_table, queue, covered_pages) -> str:
    rows = []
    for t in tables:
        tid = t["logical_table_id"]
        item = per_table.get(tid, {})
        verdict = item.get("verdict")
        if verdict is True:
            badge = '<span style="color:#1a7f37">✓ 数值核对一致</span>'
        elif verdict is False:
            badge = '<span style="color:#d1242f">✗ 数值核对不一致</span>'
        else:
            badge = '<span style="color:#8a6d00">待核对</span>'
        mismatch = "；".join((item.get("mismatch_labels") or [])[:3])
        note = f" <small style='color:#666'>{mismatch}</small>" if mismatch else ""
        rows.append(
            f"<tr><td>{tid}</td><td>p{t.get('physical_page')}</td>"
            f"<td>{badge}{note}</td>"
            f"<td><a href='../表格/{tid}.html'>HTML</a> · "
            f"<a href='../表格/{tid}.json'>JSON</a></td></tr>"
        )
    qrows = "\n".join(rows) or "<tr><td colspan='4'>（无表格）</td></tr>"
    queue_rows = "\n".join(
        f"<tr><td>{q.get('review_id')}</td><td>{q.get('priority')}</td>"
        f"<td>{q.get('object_ref')}</td><td>{q.get('reason')}</td></tr>"
        for q in queue
    ) or "<tr><td colspan='4'>（无待办）</td></tr>"
    pages = ", ".join(f"p{p}" for p in covered_pages) or "—"
    src = manifest.get("source_pdf", {}).get("path", "—")
    return "\n".join(
        [
            "<!DOCTYPE html><html lang='zh'><head><meta charset='utf-8'>",
            "<title>复核 · 年报研究包</title>",
            "<style>body{font-family:sans-serif;margin:24px}"
            "table{border-collapse:collapse;margin:12px 0}"
            "td,th{border:1px solid #bbb;padding:4px 10px;font-size:13px;text-align:left}</style>",
            "</head><body>",
            "<h2>复核入口（切片组装研究包 · partial）</h2>",
            f"<p>原 PDF：<code>{src}</code></p>",
            f"<p>覆盖物理页：{pages} · 表格数：{len(tables)}</p>",
            "<h3>表格核对状态</h3>",
            "<table><tr><th>表</th><th>物理页</th><th>状态</th><th>打开</th></tr>",
            qrows,
            "</table>",
            "<h3>复核队列（review_queue）</h3>",
            "<table><tr><th>review_id</th><th>优先级</th><th>对象</th><th>原因</th></tr>",
            queue_rows,
            "</table>",
            "<p style='color:#666;font-size:12px'>复核结论 = 人工终核为准；本页只汇总机器候选与文字层核对。</p>",
            "</body></html>",
        ]
    )


def _entry_00_markdown(root, manifest, run, tables, verify_summary, covered_pages) -> str:
    fi = manifest.get("source_pdf", {})
    iss = (manifest.get("identity", {}) or {}).get("issuer_identity", {}) or {}
    vtxt = ""
    if verify_summary:
        vtxt = (
            f"\n- 数值核对（文字层+x坐标）：表 {verify_summary['tables_ok']}/"
            f"{verify_summary['tables_total']} 一致；数据行 {verify_summary['data_rows_ok']}/"
            f"{verify_summary['data_rows_total']}（见 `索引/table_verify.md` 与 `复核/index.html`）"
        )
    return "\n".join(
        [
            "# 阅读入口（研究包 · 由切片组装）",
            "",
            f"> 状态：**partial** — 表格结构候选 + 证据回查 + 文字层数值核对；"
            "未人工核对语义/财务校验，不可计算。",
            "",
            "- 原 PDF：" + str(fi.get("path", "—")),
            "- SHA-256：`" + str(fi.get("sha256", "—")) + "`",
            "- 公司名（来自文件名，待封面核对）：" + str(iss.get("company_name_as_reported", "—")),
            "",
            "## 覆盖",
            "",
            "- 覆盖物理页：" + (", ".join(f"p{p}" for p in covered_pages) or "—"),
            "- 表格结构候选：" + str(len(tables)),
            "- 阅读顺序候选页：见 `正文/reading-order-p*.json`",
            vtxt,
            "",
            "## 目录",
            "",
            "- 表格/index.json；table-*.json/.html（结构候选，HTML 可人工复核）",
            "- 索引/evidence.jsonl（证据）；索引/table_verify.json（数值核对汇总）",
            "- 索引/manifest.json · quality.json · document_map.json · review_queue.jsonl",
            "- 复核/index.html（表格核对状态 + 复核队列）",
            "- 数据/facts.jsonl、candidates.jsonl（空：未做语义，不制造假事实）",
            "- 正文/reading-order-pNNNN.json（pdfplumber 阅读顺序候选）",
            "",
            "## 使用注意",
            "",
            "- candidate_status=extracted：未人工核对，不可引用、不可计算。",
            "- 表格为候选中间形态，不声明满足 table.schema（物理片段 schema）。",
            "- caption/footnote 是候选，可能过度捕获或缺失。",
            "- 单元格无像素级坐标（只有表级 bbox_norm）。",
            "",
            "## 原 PDF 与复核",
            "",
            "- 复核入口：`复核/index.html`",
            "- 原 PDF：" + str(fi.get("path", "—")) + "（只读来源）",
            "",
        ]
    )
