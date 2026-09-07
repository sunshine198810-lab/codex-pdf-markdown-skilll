"""general_tables：非主表广度（D1）——全书表格分族 + 财务数值表候选。

对应设计方案：§9.1 全书表格清单与分族、§15 数据层 candidates（先保住口径再讨论标准化）、
§13 对象状态与使用资格。只读版纪律与本文件一致：可定位 ≠ 可引用 ≠ 可计算。

职责边界
--------
* 只处理 ``table_items`` 中未被法定主表（financials）与权益变动表（equity）消费的片段，
  即真正意义上的“非主表”（附注表、经营数据表、治理/指标表等）。
* 给每个非主表片段打一个**候选族标签**（family），用于全书表格清单与分族覆盖统计；
  族标签是候选、可复核，不代表语义已确认。
* 只对“含数值的财务数据表”按单元格生成观察候选（observation，status=fact_candidate）；
  一律 ``eligible_for_calculation=false``、quality 不升级；单位/币种尽力从同页单位行继承，
  期间/主体/准则继承不到就诚实留空，**绝不猜测**。
* 纯文本表（目录/释义/公司信息等）只分类留存，不生成数值候选。

安全不变量
----------
* 不写 facts；不把任何非主表候选标记为可计算；
* 不修改任何已被主表/权益表消费的片段（claimed 之外不触碰）；
* 保持“六张法定主表 + 权益关键单元格”既有事实门完全不变。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from . import exports, semantics


# 候选族（family）。family 只是检索/覆盖分类，不是会计口径结论。
FAMILY_NOTES = "financial_notes"            # 财务报表附注中的附表
FAMILY_OPERATING = "management_discussion_data"  # 经营讨论中的经营/产销/分部等数据表
FAMILY_GOVERNANCE = "governance_or_general"      # 治理/股东/指标等一般数值表
FAMILY_UNCLASSIFIED = "unclassified_financial"   # 含数值但暂无法归类
FAMILY_TEXT = "text_table"                       # 纯文本表（目录/释义/公司信息等）


def _num(text) -> bool:
    """宽松数值判定：去逗号/空格/全角空格/括号负号/百分号后仍为十进制数。"""
    s = (text or "").replace(",", "").replace(" ", "").replace("\u3000", "")
    s = s.strip("()（）、%％").replace("（", "").replace("）", "")
    if not s:
        return False
    return bool(re.fullmatch(r"-?\d+(\.\d+)?", s))


def _numeric_cell_count(cells) -> int:
    return sum(1 for c in cells if _num(c.get("text")))


def _notes_start_page(pages) -> int | None:
    """找“财务报表附注”作为独立正文行的最早物理页；找不到返回 None。"""
    best = None
    for page in pages:
        for line in page.get("lines", []):
            if str(line.get("text", "")).replace(" ", "") == "财务报表附注":
                cand = int(page["physical_page"])
                best = cand if best is None else min(best, cand)
    return best


def _section_label(headings, page_no: int) -> str | None:
    """取物理页 page_no 之前最近的“第X节”标题的标准标签候选；无则 None。"""
    best = None
    best_page = -1
    for h in headings:
        p = int(h.get("physical_page", -1))
        if p <= page_no and p >= best_page:
            best = h.get("title")
            best_page = p
    if best is None:
        return None
    return semantics_label_of_heading(best)


def semantics_label_of_heading(title: str) -> str | None:
    """从节标题给标准标签候选（薄封装 sections.suggest_standard_label，延迟导入避免环）。"""
    from . import sections
    return sections.suggest_standard_label(title or "")


def _family(frag_page: int, numeric_cells: int, notes_start: int | None,
            section: str | None) -> str:
    if numeric_cells == 0:
        return FAMILY_TEXT
    if notes_start is not None and frag_page >= notes_start:
        return FAMILY_NOTES
    if section == "management_discussion":
        return FAMILY_OPERATING
    if section == "corporate_governance":
        return FAMILY_GOVERNANCE
    return FAMILY_UNCLASSIFIED


def _unit_line(page) -> dict | None:
    """同页最近一条明确的金额单位行；没有则 None。"""
    for line in page.get("lines", []):
        if semantics.parse_unit_currency(line.get("text", ""))[0] is not None:
            return line
    return None


def _group_by_row(cells) -> dict[int, list[dict]]:
    rows: dict[int, list[dict]] = {}
    for c in cells:
        rows.setdefault(int(c.get("row", 0)), []).append(c)
    return rows


def build_general_candidates(
    root,
    pages: list,
    table_items: list,
    evidence: list,
    review: list,
    identity: dict,
    engine_version: str,
    *,
    claimed_fragments: set,
    headings: list | None = None,
) -> dict:
    """对非主表片段分类并生成数值候选。返回汇总 dict（见 docstring 尾部字段）。

    参数约定与 financials.build_main_statements / equity.build_equity_statements 一致：
    ``table_items`` 元素含 table_fragment_id/physical_page/bbox/json；fragment JSON 落在
    ``root/item["json"]``，其 ``cells`` 为带 row/col/text/bbox 的扁平网格。
    ``claimed_fragments`` 是被主表/权益逻辑表消费的片段 id 集合——由调用方从
    all_logical_tables 的 fragments 汇总传入，本函数不自行判定，避免双口径。
    """
    root = Path(root)
    identity = identity or {}
    document_id = identity.get("document_id", "document")
    headings = headings or []
    by_page = {p["physical_page"]: p for p in pages}
    notes_start = _notes_start_page(pages)
    if notes_start is None:
        # 少数报告不单列“财务报表附注”独立行；此时用“已消费的主表/权益表最大物理页 + 1”
        # 作为附注区间的近似起点——六张主表与权益表通常止于附注之前，其后同一财务报告
        # 章节内基本为附注表。族标签仍是候选，不构成语义结论。
        claimed_pages = [
            int(t["physical_page"]) for t in table_items
            if t["table_fragment_id"] in claimed_fragments
        ]
        if claimed_pages:
            notes_start = max(claimed_pages) + 1
    page_unit_evidence = {}

    general = [
        t for t in table_items
        if t["table_fragment_id"] not in claimed_fragments
    ]
    families: dict[str, int] = {}
    candidates: list[dict] = []
    candidate_fragments: list[str] = []
    text_fragments: list[str] = []
    reviewed_ids: set[str] = set()

    for item in sorted(general, key=lambda t: (int(t["physical_page"]), t["bbox"][0])):
        tid = item["table_fragment_id"]
        frag_page = int(item["physical_page"])
        doc = json.loads((root / item["json"]).read_text(encoding="utf-8"))
        cells = doc.get("cells") or []
        numeric_cells = _numeric_cell_count(cells)
        section = _section_label(headings, frag_page)
        family = _family(frag_page, numeric_cells, notes_start, section)
        families[family] = families.get(family, 0) + 1

        page = by_page.get(frag_page, {})
        unit_line = _unit_line(page) if page else None
        unit = currency = None
        unit_ev = None
        if unit_line:
            unit, currency = semantics.parse_unit_currency(unit_line.get("text", ""))
            if unit is not None:
                if frag_page in page_unit_evidence:
                    unit_ev = page_unit_evidence[frag_page]
                else:
                    unit_ev = _add_evidence(
                        evidence, page=page, bbox=unit_line["bbox"],
                        full_sentence=unit_line["text"], engine_version=engine_version,
                        reason="general table unit declaration (page-level inheritance)")
                    page_unit_evidence[frag_page] = unit_ev
        frag_ev = doc.get("evidence_ref")  # 表级证据（pdfplumber 原生检出），候选可定位用

        family_desc = {
            FAMILY_NOTES: "财务报表附注中的附表：结构候选已生成，单位/期间/主体多数待核验",
            FAMILY_OPERATING: "经营讨论中的经营/产销/分部等数据表：候选已生成，语义待核验",
            FAMILY_GOVERNANCE: "治理/股东/指标类数值表：候选已生成，语义待核验",
            FAMILY_UNCLASSIFIED: "含数值但暂无法归类的表：候选已生成，语义与分类待核验",
            FAMILY_TEXT: "纯文本表（目录/释义/公司信息等）：只分类留存，不生成数值候选",
        }[family]

        if family == FAMILY_TEXT:
            text_fragments.append(tid)
            doc["general_family"] = family
            doc["candidate_status"] = "classified_text_table"
            exports.write_json(root / item["json"], doc)
            item["general_family"] = family
            item["candidate_status"] = "classified_text_table"
            for q in review:
                if q.get("object_ref") == item["json"]:
                    q["family"] = family
                    q["reason"] = family_desc
                    q["next_step"] = "纯文本表无需财务候选；保留用于目录/清单核对"
                    reviewed_ids.add(id(q))
            continue

        candidate_fragments.append(tid)
        rows = _group_by_row(cells)
        row_candidates = 0
        for row_no in sorted(rows):
            row = sorted(rows[row_no], key=lambda c: int(c.get("col", 0)))
            label_cell = row[0] if row else None
            label = str(label_cell.get("text", "")).strip() if label_cell else ""
            if _num(label):
                label = ""
            values = [c for c in row if int(c.get("col", 0)) > 0 and _num(c.get("text"))]
            if not values or not label:
                continue
            for cell in values:
                col = int(cell.get("col", 0))
                raw = str(cell.get("text", "")).strip()
                obs_id = f"{document_id}/gen-{tid}/r{row_no}c{col}"
                candidates.append({
                    "observation_id": obs_id,
                    "source_kind": "table_cell",
                    "original_label": label,
                    "concept_mapping": semantics.map_concept(label),
                    "raw_value": raw,
                    "numeric_value": _decimal(raw),
                    "currency": currency,
                    "scale": unit,
                    "normalized_value": None,
                    "transformation": {},
                    "entity_scope": None,
                    "accounting_basis": None,
                    "comparison_state": None,
                    "disclosure_version": {"document_id": document_id,
                                           "disclosed_at": None, "is_revision": False},
                    "scope_conditions": {"continuing": None, "tax_inclusive": None, "excludes": []},
                    "business_dimensions": {},
                    "general_family": family,
                    "period_unbound_reason": "general table column/header semantics not bound (D1 breadth)",
                    "evidence_refs": {
                        "value": [frag_ev] if frag_ev else [],
                        "row_label": [frag_ev] if frag_ev else [],
                        "period": [],
                        "unit_currency": [unit_ev] if unit_ev else [],
                        "entity_scope": [],
                        "accounting_basis": [],
                        "footnote": [],
                    },
                    "quality": {"text": "needs_review", "structure": "needs_review",
                                "semantics": "needs_review", "normalization": "none",
                                "eligible_for_calculation": False},
                    "usage_eligibility": {"readable": True, "traceable": bool(frag_ev),
                                          "citable": False, "computable": False},
                    "status": "fact_candidate",
                    "logical_table_id": None,
                    "general_table_fragment_id": tid,
                    "physical_fragment_id": tid,
                    "physical_page": frag_page,
                    "cell_bbox": cell.get("bbox"),
                    "candidate_reason": (
                        "D1 non-main-table numeric candidate: structure present, "
                        "unit/period/entity/basis not fully bound; not eligible for calculation"
                    ),
                })
                row_candidates += 1

        doc["general_family"] = family
        doc["candidate_status"] = "candidate_generated"
        doc["candidate_count"] = row_candidates
        exports.write_json(root / item["json"], doc)
        item["general_family"] = family
        item["candidate_status"] = "candidate_generated"
        for q in review:
            if q.get("object_ref") == item["json"]:
                q["family"] = family
                q["reason"] = family_desc + (f"（生成 {row_candidates} 条候选）" if row_candidates else "")
                q["next_step"] = (
                    "人工核对族标签与表头/单位/期间；语义与证据未齐全前 "
                    "candidates 一律不可计算（eligible_for_calculation=false）"
                )
                reviewed_ids.add(id(q))

    # 观察值候选按 observation.schema 收紧：quantity_type 未知时省略而非置 null
    #（period/restatement_status 未绑定本来就不写入，避免 null 撞 schema 类型约束）。
    for c in candidates:
        if c.get("quantity_type") is None:
            c.pop("quantity_type", None)

    return {
        "fragments_classified": len(general),
        "families": families,
        "candidate_tables": len(candidate_fragments),
        "text_tables": len(text_fragments),
        "candidate_fragments": sorted(candidate_fragments),
        "text_fragments": sorted(text_fragments),
        "candidates": candidates,
    }


def _add_evidence(evidence: list, *, page: dict, bbox, full_sentence: str,
                  engine_version: str, reason: str) -> str:
    """生成一条 text_span 证据（granularity=span）；返回 evidence_id。

    用独立前缀 ``ev-general-`` 生成稳定 id，不与 standard/financials 的自增
    ``ev-NNNNNN`` 撞号；仍是唯一 id，可被 evidence_refs 正常引用。
    """
    seq = len([e for e in evidence if str(e.get("evidence_id", "")).startswith("ev-general-")])
    evid = f"ev-general-{seq:05d}"
    evidence.append({
        "evidence_id": evid,
        "source_kind": "text_span",
        "granularity": "span",
        "locate": {"physical_page": int(page["physical_page"]),
                   "printed_page_label": None, "bbox": bbox,
                   "pdf_reference": f"page={page['physical_page']}"},
        "context": {"full_sentence": full_sentence},
        "footnote_refs": [],
        "engine": "pdfplumber",
        "engine_version": engine_version,
        "processing": [],
        "candidate_reason": reason,
        "confidence": None,
    })
    return evid


def _decimal(raw: str):
    s = (raw or "").replace(",", "").replace(" ", "").replace("\u3000", "")
    negative = s.startswith("(") and s.endswith(")")
    s = s.strip("()").replace("%", "").replace("％", "")
    if not s:
        return None
    try:
        from decimal import Decimal, InvalidOperation
        value = Decimal(s)
    except InvalidOperation:
        return None
    if negative:
        value = -value
    return str(value)


capabilities = {
    "engine_wired": True,
    "implemented": ["非主表全书分族", "数值表观察候选（eligible=false）", "纯文本表分类留存"],
    "pending": ["族内高复用表(固定资产/存货/收入拆分等)的深度语义与勾稽门", "期间/主体/单位的列级绑定"],
}
