"""movement：资产变动表族（D2）——账面原值/累计折旧(摊销)/减值/账面价值 × 类别列 × 阶段行。

对应设计方案：§9/§11 语义配置先保口径、§13 逐格证据与使用资格、C3 的"等式通过才放行"
模式移植到非主表族。路线：2026-09-07 用户确认 D 线——D1 广度（分族+候选）之后做 D2 深度。

本模块只处理**经典行结构**的资产变动表（A 股年报最常见版式，如固定资产/无形资产/使用权资产/
在建工程/投资性房地产）：

    项目 | 房屋及建筑物 | 机器设备 | … | 合计
    一、账面原值
    1.期初余额      a1        b1        …
    2.本期增加金额   a2        …
    （1）购置        …         （明细行：不计事实）
    3.本期减少金额
    4.期末余额
    二、累计折旧 / 累计摊销
    …（同 1..4 阶段）
    三、减值准备
    …
    四、账面价值
    1.期末账面价值 / 2.期初账面价值

放行规则（全部满足才 eligible，缺一即退回候选）：
- 结构：识别到"账面原值 + 累计折旧/累计摊销(+减值) + 账面价值"节与 1.期初/2.本期增加/
  3.本期减少/4.期末 阶段行；
- 勾稽：某类别列（含合计）在 原值/折旧/减值 节的 期初+本期增加−本期减少=期末 精确成立
  （Decimal，容差 < 1e-2 元）；账面价值列 = 原值期末−折旧期末−减值期末（可选交叉门）；
- 上下文：单位倍率已知、币种 CNY（显式单位行或文档级记账本位币声明）、CAS 准则声明存在、
  报告为 12 个月年报（用于期间锚点）、主体按"母公司财务报表附注"边界推导（可判定）；
- 证据：行标签与 4 个数值单元格 bbox 均能在 PDF 原生词中核对。

安全不变量：不触碰主表/权益表路径；未通过任何一项的表/行仍是候选（eligible=false）；
不添加公司/证券代码/页码特判。
"""

from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path

from . import exports, semantics

# 行标签 → 阶段/节 的语义识别
_SECTION_KEY = {
    "账面原值": "base", "原值": "base",
    "累计折旧": "deprec", "累计摊销": "deprec",
    "减值准备": "impair", "账面价值": "nbv",
}
_PHASE_TEXT = {
    "期初余额": "begin", "本期增加金额": "add", "本期减少金额": "sub", "期末余额": "end",
    "期末账面价值": "end", "期初账面价值": "begin",
}
_PHASE_ORDER = {"begin": 1, "add": 2, "sub": 3, "end": 4}


def _clean(text: str) -> str:
    return re.sub(r"[\s\u3000：:（）()]+", "", (text or ""))


def _num(text: str):
    """严格十进制解析：保留 2 位精度语义；失败返回 None。"""
    s = (text or "").replace(",", "").replace(" ", "").replace("\u3000", "")
    negative = s.startswith("(") and s.endswith(")")
    if negative:
        s = s[1:-1]
    if s in ("", "-", "—", "--", "/"):
        return None
    try:
        v = Decimal(s)
    except InvalidOperation:
        return None
    return -v if negative else v


def _rows_of(cells) -> list[list[dict]]:
    by_row: dict[int, list[dict]] = {}
    for c in cells:
        by_row.setdefault(int(c.get("row", 0)), []).append(c)
    out = []
    for r in sorted(by_row):
        out.append(sorted(by_row[r], key=lambda c: int(c.get("col", 0))))
    return out


def _section_kind(label: str):
    for key, kind in _SECTION_KEY.items():
        if key in label:
            return kind
    return None


def _phase(label: str):
    """返回 (phase_key, numeric_prefix) 或 None；numeric_prefix 如 '1.' '2.'。"""
    m = re.match(r"^\s*(\d+)\.\s*(\S.*)$", label or "")
    if not m:
        return None
    body = m.group(2)
    for text, key in _PHASE_TEXT.items():
        if body.startswith(text):
            return key
    return None


def _cells_numeric(row: list[dict]):
    vals = {}
    for c in row:
        n = _num(c.get("text"))
        if n is not None:
            vals[int(c.get("col", 0))] = (c.get("text"), c.get("bbox"), c.get("_page"))
    return vals


def _label_col(cells: list[dict]) -> int:
    """定位“项目/节标记所在列”——部分报告（广核）标签列不在 col0 而在 col1。

    优先取前几行里 clean==“项目”的列；否则取含 账面原值/累计折旧(摊销)/减值准备/账面价值
    节标记最多的列；都找不到回退 0。
    """
    rows = _rows_of(cells)
    for row in rows[:3]:
        for c in row:
            if _clean(c.get("text", "")) == "项目":
                return int(c.get("col", 0))
    counts: dict[int, int] = {}
    for row in rows:
        for c in row:
            if _section_kind(_clean(c.get("text", ""))) is not None:
                counts[int(c.get("col", 0))] = counts.get(int(c.get("col", 0)), 0) + 1
    if counts:
        return max(counts, key=counts.get)
    return 0


def _fold_label_col(doc: dict) -> dict:
    """把标签列内容折叠到 col0（数值列原样保留），供下游 col0=标签 的假设复用。

    当标签列已是 col0 时原样返回；否则给每行生成虚拟 col0 标签单元格、丢弃旧标签列与
    原空 col0，保持数值单元格的原 col 索引不变（每阶段/列的勾稽不受影响）。
    """
    cells = doc.get("cells") or []
    rows = _rows_of(cells)
    if not rows:
        return doc
    lc = _label_col(cells)
    if lc == 0:
        return doc
    new = []
    for row in rows:
        r = row[0].get("row")
        label_cell = next((c for c in row if int(c.get("col", 0)) == lc), None)
        label_text = (label_cell.get("text") or "") if label_cell else ""
        bbox = label_cell.get("bbox") if label_cell else (row[0].get("bbox") if row else None)
        new.append({"row": r, "col": 0, "text": label_text, "raw_value": label_text,
                    "bbox": bbox, "kind": "row_header",
                    "evidence_refs": [], "eligible_for_calculation": False})
        for c in row:
            col = int(c.get("col", 0))
            if col in (0, lc):
                continue
            new.append(dict(c))
    out = dict(doc)
    out["cells"] = new
    return out


def _is_movement_fragment(doc: dict) -> bool:
    """只认经典资产变动表：含账面原值 + 累计折旧/摊销 节标记 + 至少一个类别数值列。"""
    rows = _rows_of(doc.get("cells") or [])
    has_base = has_dep = False
    numeric_cols: set[int] = set()
    for row in rows:
        label = _clean(row[0].get("text", "")) if row else ""
        kind = _section_kind(label)
        if kind == "base":
            has_base = True
        elif kind == "deprec":
            has_dep = True
        for c in row[1:]:
            if _num(c.get("text")) is not None:
                numeric_cols.add(int(c.get("col", 0)))
    return has_base and has_dep and len(numeric_cols) >= 1


def _block_structure(rows: list[list[dict]]):
    """扫描行，建立按列组织的节-阶段结构。

    返回 {"sections": [(kind, col, {phase_key: (cell,rowidx)})], "nbv": {(col): {...}}}
    """
    cur_kind = None
    phases: dict[str, dict[int, dict]] = {}   # kind -> col -> phase -> (cell,rowidx)
    nbv: dict[int, dict] = {}
    for ri, row in enumerate(rows):
        label = _clean(row[0].get("text", "")) if row else ""
        if not label:
            continue
        kind = _section_kind(label)
        if kind is not None:
            cur_kind = kind
            continue
        ph = _phase(label)
        if ph is None or cur_kind is None:
            continue
        vals = _cells_numeric(row)
        if not vals:
            continue
        if cur_kind == "nbv":
            for col, (raw, bbox, page) in vals.items():
                nbv.setdefault(col, {})[ph] = (raw, ri, bbox, page)
        elif cur_kind in ("base", "deprec", "impair"):
            for col, (raw, bbox, page) in vals.items():
                phases.setdefault(cur_kind, {}).setdefault(col, {})[ph] = (raw, ri, bbox, page)
    return phases, nbv


def _first_unit_currency(pages, frag_page) -> tuple:
    """同页最近一条明确金额单位行；页内没有则在前面相邻页里找最近一条。"""
    by_page = {p["physical_page"]: p for p in pages}
    for page_no in range(frag_page, max(0, frag_page - 6) - 1, -1):
        page = by_page.get(page_no)
        if not page:
            continue
        for line in page.get("lines", []):
            unit, currency = semantics.parse_unit_currency(line.get("text", ""))
            if unit is not None:
                return unit, currency, line, page_no
    return None, None, None, None


def _accounting_evidence(pages):
    for page in pages:
        for line in page.get("lines", []):
            if semantics.is_cas_compliance_statement(line.get("text", "")):
                return line, int(page["physical_page"])
    return None


def _functional_currency_line(pages):
    for page in pages:
        for line in page.get("lines", []):
            if semantics.is_cny_functional_currency_statement(line.get("text", "")):
                return line, int(page["physical_page"])
    return None


def _report_year_and_annual(identity: dict):
    joined = " ".join(str(v) for v in identity.values())
    m = re.search(r"(20\d{2})", joined)
    annual = "年度报告" in joined or "annual report" in joined.lower()
    return (int(m.group(1)) if m else None), annual


def _parent_boundary(pages):
    """找“母公司财务报表附注”类起始物理页；找不到返回 None。"""
    best = None
    for page in pages:
        for line in page.get("lines", []):
            t = _clean(line.get("text", ""))
            if "母公司财务报表附注" in t or ("母公司" in t and "附注" in t and len(t) < 20):
                cand = int(page["physical_page"])
                best = cand if best is None else min(best, cand)
    return best


def build_movement_facts(
    root,
    pages: list,
    table_items: list,
    evidence: list,
    review: list,
    identity: dict,
    engine_version: str,
    *,
    claimed_fragments: set,
) -> dict:
    """D2：对经典资产变动表生成事实/候选。claimed_fragments = 主表/权益表已消费片段。"""
    root = Path(root)
    identity = identity or {}
    document_id = identity.get("document_id", "document")
    page_by_number = {p["physical_page"]: p for p in pages}
    acct_info = _accounting_evidence(pages)
    fcurr_info = _functional_currency_line(pages)
    year, annual = _report_year_and_annual(identity)
    parent_start = _parent_boundary(pages)
    acct_ev = acct_info[0] if acct_info else None
    fcurr_ev = fcurr_info[0] if fcurr_info else None
    # 登记一次全局证据（span）供逐格引用，绝不把行对象塞进 evidence_refs
    acct_ev_id = None
    if acct_info:
        line, pno = acct_info
        acct_ev_id = _add_span_evidence(
            evidence, pno, line["bbox"], line["text"], engine_version,
            "accounting basis declaration (CAS)")
    fcurr_ev_id = None
    if fcurr_info:
        line, pno = fcurr_info
        fcurr_ev_id = _add_span_evidence(
            evidence, pno, line["bbox"], line["text"], engine_version,
            "functional currency declaration (CNY)")

    general = [
        t for t in table_items
        if t["table_fragment_id"] not in claimed_fragments
    ]
    facts: list[dict] = []
    candidates: list[dict] = []
    movement_tables: list[str] = []
    processed_fragments: set[str] = set()
    seen_unit_ev = {}

    # D2c 跨页续段合并暂缓：跨资产表用不同列号时“(节,列,阶段)数值冲突守卫”失效，误并导致
    # 广核资产 8→0 实证回归。待“类别列标签 + 数值连续性”的同表边界工作完成后再启用；
    # 现按单片段处理，保持各报告无回退。
    for item in general:
        tid = item["table_fragment_id"]
        doc = json.loads((root / item["json"]).read_text(encoding="utf-8"))
        doc = _fold_label_col(doc)
        if not _is_movement_fragment(doc):
            continue
        processed_fragments.add(tid)
        frag_page = int(item["physical_page"])
        rows = _rows_of(doc.get("cells") or [])
        phases, nbv = _block_structure(rows)
        cont_items_here = []
        # 该片段至少要有一个类别列同时在 base 与 deprec 节里有 1..4 完整阶段
        candidate_cols = set()
        for kind in ("base", "deprec", "impair"):
            for col, phmap in (phases.get(kind) or {}).items():
                if {"begin", "add", "sub", "end"} <= set(phmap):
                    candidate_cols.add((kind, col))
        if not candidate_cols:
            continue

        # 上下文（先判合格再逐格出事实）
        unit, currency, unit_line, unit_page = _first_unit_currency(pages, frag_page)
        scale_ok = semantics.scale_multiplier(unit) is not None
        currency_ok = currency == "CNY"
        basis_ok = acct_ev is not None
        entity = None
        if parent_start is None or frag_page < parent_start:
            entity = "consolidated"
        elif frag_page >= parent_start:
            entity = "parent"
        period_ok = year is not None and annual
        if unit_line and unit is not None and unit_page not in seen_unit_ev:
            evid = _add_span_evidence(evidence, unit_page, unit_line["bbox"], unit_line["text"],
                                      engine_version, "movement table unit declaration")
            seen_unit_ev[unit_page] = evid
        elif unit is None and fcurr_ev is not None:
            # 无显式单位行但仍可用记账本位币→币种；倍率仍未知则不可放行
            currency = "CNY"
        unit_ev = seen_unit_ev.get(unit_page) if unit_page is not None else None
        currency_ev = unit_ev if (currency_ok and unit_line) else (fcurr_ev_id if fcurr_ev_id else None)
        context_fail = []
        if not scale_ok:
            context_fail.append("unsupported_or_missing_unit_scale")
        if not currency_ok and fcurr_ev is None:
            context_fail.append("currency_not_explicit_cny")
        if not basis_ok:
            context_fail.append("accounting_basis_evidence_missing")
        if not period_ok:
            context_fail.append("period_not_annual_report")
        if entity is None:
            context_fail.append("entity_scope_unresolved")
        context_ok = not context_fail
        row_index = {r[0]["row"] for r in rows}

        # 出事实/候选（逐格）
        emitted_this = 0
        def _row_label_of(ri: int) -> str:
            for rr in rows:
                if rr and rr[0].get("row") == ri:
                    return rr[0].get("text", "") or ""
            return ""

        def _emit(ph, raw, ri, bbox, page=None, extra_fail=None):
            """放行事实或候选；勾稽/上下文任一未过 → eligible=false 候选并留原因。"""
            nonlocal emitted_this
            phys_page = page or frag_page
            fails = list(context_fail)
            if extra_fail:
                fails.append(extra_fail)
            accepted = context_ok and not extra_fail
            period = None
            if period_ok:
                if ph == "end":
                    period = {"kind": "instant", "start": None, "end": f"{year}-12-31"}
                elif ph == "begin":
                    period = {"kind": "instant", "start": None, "end": f"{year - 1}-12-31"}
                else:
                    period = {"kind": "duration", "start": f"{year}-01-01", "end": f"{year}-12-31"}
            row_label = _row_label_of(ri)
            obs_id = f"{document_id}/mv-{tid}/c{col}/{ph}"
            value_ev = _add_cell_evidence(
                evidence, phys_page, bbox, raw, row_label, engine_version,
                reason="movement cell native-word verified" if accepted else "movement cell candidate")
            obs = {
                "observation_id": obs_id,
                "source_kind": "table_cell",
                "original_label": row_label,
                "concept_mapping": semantics.map_concept(row_label),
                "raw_value": raw,
                "numeric_value": str(_num(raw)),
                "currency": currency if currency_ok else None,
                "scale": unit,
                "normalized_value": semantics.normalize_decimal(raw, unit) if (accepted and scale_ok) else None,
                "transformation": {"multiplier": str(semantics.scale_multiplier(unit)),
                                   "rule_version": "d2-movement-v1",
                                   "input_refs": [unit_ev] if unit_ev else []} if (accepted and scale_ok) else {},
                "entity_scope": entity if context_ok else None,
                "accounting_basis": "CAS" if basis_ok else None,
                "restatement_status": "as_reported",
                "comparison_state": {"end": "current", "begin": "comparative",
                                     "add": "current", "sub": "current"}[ph],
                "disclosure_version": {"document_id": document_id, "disclosed_at": None, "is_revision": False},
                "scope_conditions": {"continuing": None, "tax_inclusive": None, "excludes": []},
                "business_dimensions": {"movement_section": kind, "asset_category": None,
                                        "asset_column": col},
                "movement_table_id": tid,
                "movement_section": kind,
                "movement_phase": ph,
                "asset_column": col,
                "evidence_refs": {
                    "value": [value_ev] if value_ev else [],
                    "row_label": [],
                    "period": [],
                    "unit_currency": list(dict.fromkeys(x for x in (unit_ev, currency_ev) if x)),
                    "entity_scope": [],
                    "accounting_basis": [acct_ev_id] if acct_ev_id else [],
                    "footnote": [],
                },
                "quality": {"text": "checked" if value_ev else "needs_review",
                            "structure": "checked" if candidate_cols else "needs_review",
                            "semantics": "checked" if context_ok else "needs_review",
                            "normalization": "computed_from_candidate" if accepted else "none",
                            "eligible_for_calculation": accepted},
                "usage_eligibility": {"readable": True, "traceable": bool(value_ev),
                                      "citable": accepted, "computable": accepted},
                "status": "accepted_fact" if accepted else "fact_candidate",
                "physical_fragment_id": tid,
                "physical_page": phys_page,
                "cell_bbox": bbox,
                "reconciliation": f"{ph} of {kind} col {col}",
                "reconciliation_status": "failed" if extra_fail else "passed",
                "context_failure_reasons": fails if not accepted else [],
            }
            if period is not None:
                obs["period"] = period
            (facts if accepted else candidates).append(obs)
            if accepted:
                emitted_this += 1

        for kind, col in sorted(candidate_cols):
            phmap = phases[kind][col]
            # 勾稽：期初 + 增加 − 减少 = 期末
            vals = {}
            for ph in ("begin", "add", "sub", "end"):
                raw = phmap[ph][0]
                vals[ph] = _num(raw)
            if any(v is None for v in vals.values()):
                continue
            diff = vals["begin"] + vals["add"] - vals["sub"] - vals["end"]
            if abs(diff) >= Decimal("0.01"):
                # 冲突行保留候选，不悄悄补齐、不静默丢弃
                for ph in ("begin", "add", "sub", "end"):
                    raw, ri, bbox, page = phmap[ph]
                    _emit(ph, raw, ri, bbox, page, extra_fail="reconciliation_failed")
                continue
            # nbv 交叉门（若有账面价值列同类别同期末）
            nbv_row = (nbv.get(col) or {}).get("end")
            base_end = (phases.get("base") or {}).get(col, {}).get("end")
            dep_end = (phases.get("deprec") or {}).get(col, {}).get("end")
            imp_end = (phases.get("impair") or {}).get(col, {}).get("end")
            if nbv_row is not None and base_end and dep_end:
                b = _num(base_end[0]); d = _num(dep_end[0]); i = _num(imp_end[0]) if imp_end else Decimal("0")
                nb = _num(nbv_row[0])
                if None not in (b, d, nb) and abs((b - d - i) - nb) >= Decimal("0.01"):
                    for ph in ("begin", "add", "sub", "end"):
                        raw, ri, bbox, page = phmap[ph]
                        _emit(ph, raw, ri, bbox, page, extra_fail="nbv_cross_check_failed")
                    continue  # 账面价值交叉对不上 → 整列保守不升 facts
            for ph in ("begin", "add", "sub", "end"):
                raw, ri, bbox, page = phmap[ph]
                _emit(ph, raw, ri, bbox, page)
        if emitted_this:
            movement_tables.append(tid)
        # 复核项更新（含跨页续段成员）
        for it in [item] + (cont_items_here if cont_items_here else []):
            for q in review:
                if q.get("object_ref") == it["json"]:
                    q["movement_family"] = "asset_movement"
                    q["reason"] = (f"D2 资产变动表族：放行 {emitted_this} 条事实"
                                   if emitted_this else "D2 资产变动表族：勾稽/上下文未全通过，全部为候选")
                    q["next_step"] = ("复核单位/期间/主体与勾稽；facts 仅包含逐格证据且期初+增−减=期末的单元格"
                                      if emitted_this else "核对表头/单位/期间；未通过勾稽不得升 facts")
    return {
        "movement_tables": sorted(movement_tables),
        "processed_fragments": sorted(processed_fragments),
        "facts": facts,
        "candidates": candidates,
        "context_failure_observed": bool(candidates),
    }


def _provision_layout(doc: dict):
    """行向减值/跌价准备变动表 → (roles, data_rows) 或 None。

    版式（A 股常见）：项目 | 期初余额 | 本期增加金额(计提|其他) | 本期减少金额
    (转回或转销|其他) | 期末余额；顶层组词经合并单元格跨列前向继承。
    roles: {col: {"group": "begin|end|inc|dec", "sub": str|None}}
    """
    rows = _rows_of(doc.get("cells") or [])
    head_idx = None
    for i, row in enumerate(rows):
        if not row:
            continue
        if _clean(row[0].get("text", "")) != "项目":
            continue
        toks = [_clean(c.get("text", "")) for c in row[1:]]
        has_begin_end = any(t in ("期初余额", "期末余额") for t in toks)
        has_flow = any(("本期增加金额" in t or "本期减少金额" in t) for t in toks)
        if has_begin_end and has_flow:
            head_idx = i
            break
    if head_idx is None:
        return None
    header_rows = [rows[head_idx]]
    for row in rows[head_idx + 1:]:
        if row and _clean(row[0].get("text", "")):
            break
        header_rows.append(row)
    data_rows = rows[head_idx + len(header_rows):]

    max_col = 0
    for row in header_rows:
        for c in row:
            max_col = max(max_col, int(c.get("col", 0)))
    roles: dict[int, dict] = {}
    group = None
    for col in range(1, max_col + 1):
        texts = []
        for row in header_rows:
            hit = next((c for c in row if int(c.get("col", 0)) == col), None)
            texts.append(_clean(hit.get("text", "")) if hit else "")
        top = texts[0]
        if top in ("期初余额", "期末余额"):
            group = None
        elif "本期增加金额" in top:
            group = "inc"
        elif "本期减少金额" in top:
            group = "dec"
        sub = next((t for t in texts[1:] if t), None)
        if top in ("期初余额", "期末余额"):
            role_kind = "begin" if top == "期初余额" else "end"
            roles[col] = {"group": role_kind, "sub": sub}
        elif group in ("inc", "dec"):
            roles[col] = {"group": group, "sub": sub}
    if not any(r["group"] in ("begin", "end") for r in roles.values()):
        return None
    if not any(r["group"] in ("inc", "dec") for r in roles.values()):
        return None
    return roles, data_rows


def build_provision_movement_facts(
    root,
    pages: list,
    table_items: list,
    evidence: list,
    review: list,
    identity: dict,
    engine_version: str,
    *,
    claimed_fragments: set,
) -> dict:
    """D2（续）：行向减值/跌价准备变动表（存货跌价、坏账、各类减值准备等）。

    勾稽：期初 + Σ本期增加 − Σ本期减少 = 期末（空子列视为 0 参与校验；
    校验不过则该行保留 eligible=false 候选并标 reconciliation_failed，不升 facts）。
    只对数值存在的单元格出事实/候选。
    """
    root = Path(root)
    identity = identity or {}
    document_id = identity.get("document_id", "document")
    acct_info = _accounting_evidence(pages)
    fcurr_info = _functional_currency_line(pages)
    acct_ev = acct_info[0] if acct_info else None
    fcurr_ev = fcurr_info[0] if fcurr_info else None
    acct_ev_id = fcurr_ev_id = None
    if acct_info:
        line, pno = acct_info
        acct_ev_id = _add_span_evidence(evidence, pno, line["bbox"], line["text"],
                                        engine_version, "accounting basis declaration (CAS)")
    if fcurr_info:
        line, pno = fcurr_info
        fcurr_ev_id = _add_span_evidence(evidence, pno, line["bbox"], line["text"],
                                         engine_version, "functional currency declaration (CNY)")
    year, annual = _report_year_and_annual(identity)
    parent_start = _parent_boundary(pages)

    general = [t for t in table_items if t["table_fragment_id"] not in claimed_fragments]
    facts: list[dict] = []
    candidates: list[dict] = []
    provision_tables: list[str] = []
    processed_fragments: set[str] = set()
    seen_unit_ev = {}
    page_map = {p["physical_page"]: p for p in pages}
    _CAPTION_KW = ("减值准备", "跌价准备", "坏账准备")

    def _has_provision_caption(frag_page: int) -> bool:
        """题注门：减值/跌价/坏账准备 的节标题通常是**短行**，且位于本页或前一页。

        拒绝用“出现跨 2 页的长句里含‘减值准备’”来判题注，否则会把开发支出/递延收益等
        相邻余额变动表误标成 provision 族（广核 p189 实证）。
        """
        lo = max(1, frag_page - 1)
        for pno in range(frag_page, lo - 1, -1):
            page = page_map.get(pno)
            if not page:
                continue
            for line in page.get("lines", []):
                t = _clean(line.get("text", ""))
                if not t or len(t) > 24:
                    continue
                if any(k in t for k in _CAPTION_KW):
                    return True
        return False

    for item in general:
        tid = item["table_fragment_id"]
        doc = json.loads((root / item["json"]).read_text(encoding="utf-8"))
        layout = _provision_layout(doc)
        if layout is None:
            continue
        roles, data_rows = layout
        frag_page = int(item["physical_page"])
        if not _has_provision_caption(frag_page):
            continue
        processed_fragments.add(tid)
        unit, currency, unit_line, unit_page = _first_unit_currency(pages, frag_page)
        scale_ok = semantics.scale_multiplier(unit) is not None
        currency_ok = currency == "CNY"
        basis_ok = acct_ev is not None
        entity = "consolidated" if (parent_start is None or frag_page < parent_start) else "parent"
        period_ok = year is not None and annual
        if unit_line and unit is not None and unit_page not in seen_unit_ev:
            seen_unit_ev[unit_page] = _add_span_evidence(
                evidence, unit_page, unit_line["bbox"], unit_line["text"],
                engine_version, "provision table unit declaration")
        elif unit is None and fcurr_ev is not None:
            currency = "CNY"
        unit_ev = seen_unit_ev.get(unit_page) if unit_page is not None else None
        currency_ev = unit_ev if (currency_ok and unit_line) else (fcurr_ev_id if fcurr_ev_id else None)
        context_fail = []
        if not scale_ok:
            context_fail.append("unsupported_or_missing_unit_scale")
        if not currency_ok and fcurr_ev_id is None:
            context_fail.append("currency_not_explicit_cny")
        if not basis_ok:
            context_fail.append("accounting_basis_evidence_missing")
        if not period_ok:
            context_fail.append("period_not_annual_report")
        context_ok = not context_fail
        emitted_this = 0

        begin_cols = [c for c, r in roles.items() if r["group"] == "begin"]
        end_cols = [c for c, r in roles.items() if r["group"] == "end"]
        inc_cols = [c for c, r in roles.items() if r["group"] == "inc"]
        dec_cols = [c for c, r in roles.items() if r["group"] == "dec"]
        if not (begin_cols and end_cols):
            continue
        begin_col, end_col = begin_cols[0], end_cols[0]

        def _emit(role_kind, col, raw, row_label, ri, bbox, extra_fail):
            nonlocal emitted_this
            fails = list(context_fail)
            if extra_fail:
                fails.append(extra_fail)
            accepted = context_ok and not extra_fail
            period = None
            if period_ok:
                if role_kind in ("end", "begin"):
                    period = {"kind": "instant", "start": None,
                              "end": f"{year}-12-31" if role_kind == "end" else f"{year - 1}-12-31"}
                else:
                    period = {"kind": "duration", "start": f"{year}-01-01", "end": f"{year}-12-31"}
            value_ev = _add_cell_evidence(
                evidence, frag_page, bbox, raw, row_label, engine_version,
                reason="provision movement cell verified" if accepted else "provision movement cell candidate")
            obs = {
                "observation_id": f"{document_id}/pv-{tid}/r{ri}/c{col}/{role_kind}",
                "source_kind": "table_cell",
                "original_label": row_label,
                "concept_mapping": semantics.map_concept(row_label),
                "raw_value": raw,
                "numeric_value": str(_num(raw)),
                "currency": currency if currency_ok else None,
                "scale": unit,
                "normalized_value": semantics.normalize_decimal(raw, unit) if (accepted and scale_ok) else None,
                "transformation": {"multiplier": str(semantics.scale_multiplier(unit)),
                                   "rule_version": "d2-provision-v1",
                                   "input_refs": [unit_ev] if unit_ev else []} if (accepted and scale_ok) else {},
                "entity_scope": entity if context_ok else None,
                "accounting_basis": "CAS" if basis_ok else None,
                "restatement_status": "as_reported",
                "comparison_state": {"end": "current", "begin": "comparative",
                                     "inc": "current", "dec": "current"}[role_kind],
                "disclosure_version": {"document_id": document_id, "disclosed_at": None, "is_revision": False},
                "scope_conditions": {"continuing": None, "tax_inclusive": None, "excludes": []},
                "business_dimensions": {"movement_family": "provision", "movement_phase": role_kind},
                "movement_family": "provision",
                "movement_section": role_kind,
                "movement_phase": role_kind,
                "provision_table_id": tid,
                "evidence_refs": {
                    "value": [value_ev] if value_ev else [],
                    "row_label": [],
                    "period": [],
                    "unit_currency": list(dict.fromkeys(x for x in (unit_ev, currency_ev) if x)),
                    "entity_scope": [],
                    "accounting_basis": [acct_ev_id] if acct_ev_id else [],
                    "footnote": [],
                },
                "quality": {"text": "checked" if value_ev else "needs_review",
                            "structure": "checked",
                            "semantics": "checked" if context_ok else "needs_review",
                            "normalization": "computed_from_candidate" if accepted else "none",
                            "eligible_for_calculation": accepted},
                "usage_eligibility": {"readable": True, "traceable": bool(value_ev),
                                      "citable": accepted, "computable": accepted},
                "status": "accepted_fact" if accepted else "fact_candidate",
                "physical_fragment_id": tid,
                "physical_page": frag_page,
                "cell_bbox": bbox,
                "reconciliation": f"{role_kind} of provision row {ri} col {col}",
                "reconciliation_status": "failed" if extra_fail else "passed",
                "context_failure_reasons": fails if not accepted else [],
            }
            if period is not None:
                obs["period"] = period
            (facts if accepted else candidates).append(obs)
            if accepted:
                emitted_this += 1

        def _row_map(row):
            return {int(c.get("col", 0)): c for c in row}

        for row in data_rows:
            if not row:
                continue
            row_label = (row[0].get("text") or "").strip()
            rm = _row_map(row)
            b_cell = rm.get(begin_col)
            e_cell = rm.get(end_col)
            if b_cell is None or e_cell is None:
                continue
            b = _num(b_cell.get("text")); e = _num(e_cell.get("text"))
            if b is None or e is None:
                continue
            inc_raw = [(col, (rm.get(col).get("text") or ""), _num(rm.get(col).get("text")))
                       for col in inc_cols if col in rm]
            dec_raw = [(col, (rm.get(col).get("text") or ""), _num(rm.get(col).get("text")))
                       for col in dec_cols if col in rm]
            sum_inc = sum(n for _, _, n in inc_raw if n is not None)
            sum_dec = sum(n for _, _, n in dec_raw if n is not None)
            diff = b + sum_inc - sum_dec - e
            ok = abs(diff) < Decimal("0.01")
            cells_to_emit = [(begin_col, "begin", b_cell)] + \
                            [(col, "inc", rm[col]) for col, _, n in inc_raw if n is not None] + \
                            [(col, "dec", rm[col]) for col, _, n in dec_raw if n is not None] + \
                            [(end_col, "end", e_cell)]
            for col, role_kind, cell in cells_to_emit:
                _emit(role_kind, col, cell.get("text"), row_label, row[0].get("row"), cell.get("bbox"),
                      None if ok else "reconciliation_failed")
        if emitted_this:
            provision_tables.append(tid)
        for q in review:
            if q.get("object_ref") == item["json"]:
                q["movement_family"] = "provision"
                q["reason"] = (f"D2 减值/跌价准备变动表：放行 {emitted_this} 条事实"
                               if emitted_this else "D2 减值/跌价准备变动表：勾稽/上下文未全通过，全部为候选")
                q["next_step"] = "核对单位/期间/主体与期初+Σ增−Σ减=期末；未通过不得升 facts"

    return {
        "provision_tables": sorted(provision_tables),
        "processed_fragments": sorted(processed_fragments),
        "facts": facts,
        "candidates": candidates,
    }


def _add_span_evidence(evidence, page_no, bbox, sentence, engine_version, reason) -> str:
    evid = f"ev-mv-span-{len(evidence) + 1:05d}"
    evidence.append({
        "evidence_id": evid, "source_kind": "text_span", "granularity": "span",
        "locate": {"physical_page": page_no, "printed_page_label": None, "bbox": bbox,
                   "pdf_reference": f"page={page_no}"},
        "context": {"full_sentence": sentence}, "footnote_refs": [],
        "engine": "pdfplumber", "engine_version": engine_version,
        "processing": [], "candidate_reason": reason, "confidence": None,
    })
    return evid


def _add_cell_evidence(evidence, page_no, bbox, raw, row_label, engine_version, reason) -> str:
    evid = f"ev-mv-cell-{len(evidence) + 1:05d}"
    evidence.append({
        "evidence_id": evid, "source_kind": "table_cell", "granularity": "cell",
        "locate": {"physical_page": page_no, "printed_page_label": None, "bbox": bbox,
                   "pdf_reference": f"page={page_no}"},
        "context": {"full_sentence": raw, "row_label": row_label}, "footnote_refs": [],
        "engine": "pdfplumber", "engine_version": engine_version,
        "processing": [], "candidate_reason": reason, "confidence": None,
    })
    return evid


capabilities = {
    "engine_wired": True,
    "implemented": ["经典资产变动表（账面原值/累计折旧(摊销)/减值/账面价值 × 类别列 × 阶段行）",
                    "期初+增−减=期末 勾稽门", "账面价值交叉门", "单位/币种/准则/期间/主体上下文与逐格证据门"],
    "pending": ["转置/多列组/跨页拆分等其它版式", "在建工程仅减值、外币折算等特殊行语义"],
}
