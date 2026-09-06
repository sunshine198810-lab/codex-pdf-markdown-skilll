"""refine：B/C 两种方式的“小而有用”实现（对应设计方案 §15）。

B（核心财务整理）`extract_candidates`：
  从研究包已核对的表格行中定位指定指标（行标签归一匹配），把命中写成候选
  （数据/candidates-<run_id>.jsonl 并同步 数据/candidates.jsonl 为最新）。
  诚实边界：包目前是结构候选 + 文字层核对，尚未做语义/期间/单位/主体绑定，
  因此**只写 candidates，绝不写 facts**；eligible_for_calculation=false。

C（局部复核）`local_review`：
  对研究包指定物理页/表，沿用原始证据（源 PDF 文字层）重新做数值核对，
  生成 _internal/local_review-<run_id>.json，并刷新 复核/index.html 与
  review_queue（把不一致表置为待复核）。

设计说明：这些不是“完整 B/C 能力”（那需要 V0.2 真解析 + 语义校验），
而是把纯桩升级为能在当前候选包上做真实小动作、且结论诚实可回查的工具。
"""

from __future__ import annotations

import datetime
import json
import pathlib
import re

from . import exports, intake
from .version import PIPELINE_VERSION, SCHEMA_VERSION

_PUNCT = re.compile(r"[\s，。、；：？！“”‘’\"'（）()\[\]{}<>《》·—–-−_~.,;:!?…]+")
_MARKER = re.compile(r"以[“”\"'（(]?[－—–-一二_＿][“”\"'）)]?号填列")
_NUM = re.compile(r"^[\(（]?[-\u2212]?\d[\d,]*(?:\.\d+)?[\)）]?%?$")


def _norm(s: str) -> str:
    s = s or ""
    s = _MARKER.sub("以号填列", s)
    return _PUNCT.sub("", s)


def _now() -> str:
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def _run_id() -> str:
    return datetime.datetime.now().strftime("%Y%m%dT%H%M%S")


def _load(package_dir, rel, default=None):
    p = pathlib.Path(package_dir) / rel
    if not p.is_file():
        return default
    try:
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return default


def _manifest(package_dir) -> dict:
    m = _load(package_dir, "索引/manifest.json")
    if not m:
        raise FileNotFoundError(f"不是有效研究包目录（缺 索引/manifest.json）: {package_dir}")
    return m


def _labels_from_verify(package_dir, table_id):
    """从 索引/table_verify.json 取某表已核对的 (label, values) 列表（文字层核对结果）。"""
    tv = _load(package_dir, "索引/table_verify.json")
    if not tv or not tv.get("tables"):
        return None
    t = tv["tables"].get(table_id)
    if not t or not t.get("row_results"):
        return None
    out = []
    for r in t["row_results"]:
        if r.get("kind") != "data":
            continue
        vals = [v for v in (r.get("values_in_grid") or []) if _NUM.match(v.strip())]
        out.append({
            "label": r.get("label"),
            "values": vals,
            "ok": r.get("ok"),
            "issue": r.get("issue"),
        })
    return out


def _table_doc(package_dir, table_id) -> dict:
    with open(pathlib.Path(package_dir) / "表格" / f"{table_id}.json", encoding="utf-8") as fh:
        return json.load(fh)


def _rows_from_grid(doc) -> list:
    """退化路径：直接扫表格 JSON 网格（无核对结果时），仅供定位候选，标注 untraced。"""
    grid = doc.get("grid", {})
    cells = grid.get("cells", [])
    rows_n = grid.get("rows", 0)
    cols_n = grid.get("cols", 0)
    mat = {}
    for c in cells:
        for rr in range(c.get("row", 0), c.get("row", 0) + c.get("rowspan", 1)):
            for cc in range(c.get("col", 0), c.get("col", 0) + c.get("colspan", 1)):
                mat[(rr, cc)] = c
    out = []
    for r in range(rows_n):
        label = ""
        vals = []
        for cc in range(cols_n):
            c = mat.get((r, cc))
            if c is None or not (r == c.get("row") and cc == c.get("col")):
                continue
            t = (c.get("text") or "").strip()
            if cc == 0:
                label = t
            elif _NUM.match(t):
                vals.append(t)
        if label:
            out.append({"label": label, "values": vals, "ok": None, "issue": "grid_scan_untraced"})
    return out


def _all_tables(package_dir):
    idx = _load(package_dir, "表格/index.json")
    if not idx or not idx.get("tables"):
        return []
    return [t for t in idx["tables"] if t.get("logical_table_id")]


def extract_candidates(package_dir, indicators, *, match="contains") -> dict:
    """B：定位指标行并生成候选（写 candidates，不写 facts）。

    匹配默认 contains（归一后包含），防止过度要求整词相等漏掉“归属于母公司…净利润”。
    命中行为有核对结果 → traced；否则 grid_scan → untraced（诚实，不算已核对）。
    """
    package_dir = pathlib.Path(package_dir)
    m = _manifest(package_dir)
    indicators = [x for x in (indicators or []) if x.strip()]
    cands = []
    seen = set()
    for item in _all_tables(package_dir):
        tid = item["logical_table_id"]
        rows = _labels_from_verify(package_dir, tid)
        traced = rows is not None
        if not traced:
            try:
                rows = _rows_from_grid(_table_doc(package_dir, tid))
            except Exception:  # noqa: BLE001
                continue
        for row in rows:
            label = row.get("label") or ""
            ln = _norm(label)
            if not ln:
                continue
            for ind in indicators:
                inn = _norm(ind)
                if not inn:
                    continue
                hit = (ln == inn) if match == "exact" else (inn in ln)
                if not hit:
                    continue
                key = (tid, ln)
                if key in seen:
                    continue
                seen.add(key)
                cands.append({
                    "candidate_type": "financial_value_row",
                    "schema_version": SCHEMA_VERSION,
                    "indicator": ind,
                    "matched_label": label,
                    "table_id": tid,
                    "physical_page": item.get("physical_page"),
                    "raw_values": row.get("values", []),
                    "unit": None,
                    "period": None,
                    "entity": None,
                    "basis": None,
                    "traceable": traced,
                    "row_check": {"ok": row.get("ok"), "issue": row.get("issue")},
                    "eligible_for_calculation": False,
                    "usage": {"readable": False, "traceable": traced,
                              "citable": False, "computable": False},
                    "note": ("行标签经文字层核对" if traced else
                             "网格扫描候选，未核对（untraced）"),
                    "created_at": _now(),
                })

    run_id = _run_id()
    cand_path = package_dir / "数据" / f"candidates-{run_id}.jsonl"
    (package_dir / "数据").mkdir(parents=True, exist_ok=True)
    exports.write_jsonl(cand_path, cands)
    # 同步“最新”candidates.jsonl（保留 run 版本文件作为历史）
    exports.write_jsonl(package_dir / "数据/candidates.jsonl", cands)

    # 更新 manifest 计数与 quality note
    if m:
        m.setdefault("artifacts", {})["candidates"] = len(cands)
        m["run"]["run_id"] = run_id
        m["generated_at"] = _now()
        exports.write_json(package_dir / "索引/manifest.json", m)
    q = _load(package_dir, "索引/quality.json") or {}
    q["five_layers"] = q.get("five_layers") or {}
    q["five_layers"]["run_and_files"] = {
        "ok": True,
        "note": f"B 指标候选提取：{len(indicators)} 指标 / 命中 {len(cands)} 行（candidates 非 facts）",
    }
    q["note"] = q.get("note", "") + f"；B 提取候选 {len(cands)} 行（不可计算，见 数据/candidates-{run_id}.jsonl）"
    exports.write_json(package_dir / "索引/quality.json", q)

    return {
        "run_id": run_id,
        "candidates_written": len(cands),
        "candidates_path": str(cand_path),
        "indicators": indicators,
        "by_indicator": {i: sum(1 for c in cands if c["indicator"] == i) for i in indicators},
    }


def local_review(package_dir, *, page=None, table=None) -> dict:
    """C：对研究包局部重跑文字层数值核对，生成新版本复核记录并刷新复核页。

    需要源 PDF 可读（manifest.source_pdf.path）。返回复核摘要。
    """
    package_dir = pathlib.Path(package_dir)
    m = _manifest(package_dir)
    src = m.get("source_pdf", {}).get("path")
    if not src or not pathlib.Path(src).is_file():
        raise FileNotFoundError(f"源 PDF 不可读，无法局部复核: {src}")

    import verify_tables  # noqa: PLC0415  (scripts 顶层工具，供 CLI 入口使用)

    # check_dir 指向 _internal，避免在研究包根新增切片专用 目检/ 目录
    check_dir = package_dir / "_internal" / "local_review_check"
    result = verify_tables.verify_slice(src, package_dir, check_dir=check_dir)
    # 定位目标
    tables = result["tables"]
    if table is not None:
        targets = {table: tables.get(table)} if table in tables else {}
        if table not in tables:
            raise KeyError(f"研究包中不存在表 {table}")
    elif page is not None:
        targets = {tid: t for tid, t in tables.items() if t.get("physical_page") == page}
        if not targets:
            raise KeyError(f"物理页 {page} 无表格")
    else:
        targets = tables

    run_id = _run_id()
    rec = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "mode": "local_review",
        "source_pdf": src,
        "scope": {"page": page, "table": table},
        "method": "verify_tables(文字层+x坐标)",
        "generated_at": _now(),
        "targets": {
            tid: {
                "physical_page": t.get("physical_page"),
                "rows_total": t.get("rows_total"),
                "rows_ok": t.get("rows_ok"),
                "verdict": t.get("verdict"),
                "mismatch_labels": t.get("mismatch_labels", []),
            }
            for tid, t in targets.items()
        },
    }
    exports.write_json(package_dir / "_internal" / f"local_review-{run_id}.json", rec)

    # 刷新 review_queue：不一致/待核对表 → open（按影响优先）
    queue_path = package_dir / "索引/review_queue.jsonl"
    existing = []
    if queue_path.is_file():
        existing = [json.loads(x) for x in queue_path.read_text(encoding="utf-8").splitlines() if x.strip()]
    have = {q.get("object_ref") for q in existing}
    nq = max((q.get("priority", 0) for q in existing), default=0)
    for tid, t in targets.items():
        if t.get("verdict") != "ok":
            ref = f"表格/{tid}.json"
            if ref not in have:
                nq += 1
                existing.append({
                    "review_id": f"rev-{nq:04d}",
                    "priority": 1,
                    "object_ref": ref,
                    "reason": "局部复核：数值核对不一致（文字层+x坐标）",
                    "evidence_clip": None,
                    "candidates": [],
                    "methods_tried": ["verify_tables(文字层+x坐标)"],
                    "next_step": "人工核对原始页 / x坐标列向重建",
                    "status": "open",
                    "created_at": _now(),
                })
                have.add(ref)
    exports.write_jsonl(queue_path, existing)

    # 刷新复核页（复用 assemble 的渲染；就地重建）
    _refresh_review_html(package_dir)
    return {"run_id": run_id, "record": f"_internal/local_review-{run_id}.json",
            "reviewed_tables": len(targets),
            "tables_ok": sum(1 for t in targets.values() if t.get("verdict") == "ok"),
            "targets": rec["targets"]}


def _refresh_review_html(package_dir) -> None:
    """重生成 复核/index.html（与 assemble 输出同构，反映最新核对状态）。"""
    from . import assemble  # noqa: PLC0415

    m = _manifest(package_dir)
    idx = _load(package_dir, "表格/index.json") or {}
    tables = idx.get("tables", [])
    tv = _load(package_dir, "索引/table_verify.json") or {}
    per = {tid: {"verdict": t.get("verdict"), "rows_ok": t.get("rows_ok"),
                 "rows_total": t.get("rows_total"),
                 "mismatch_labels": t.get("mismatch_labels", [])}
           for tid, t in (tv.get("tables") or {}).items()}
    queue_path = package_dir / "索引/review_queue.jsonl"
    queue = []
    if queue_path.is_file():
        queue = [json.loads(x) for x in queue_path.read_text(encoding="utf-8").splitlines() if x.strip()]
    pages = sorted({t.get("physical_page") for t in tables if t.get("physical_page")})
    html = assemble._review_index_html(package_dir, m, tables, per, queue, pages)
    (package_dir / "复核/index.html").write_text(html, encoding="utf-8")
