"""M2 主表语义层：逻辑表、上下文证据、观察值与适用勾稽。

当前支持原生文字中文财报中六类法定主表（合并/母公司的资产负债表、利润表、现金流量表）。
识别失败或上下文不完整时只生成候选，不授予计算资格。
"""

from __future__ import annotations

import re
import html
import statistics
import csv
from decimal import Decimal, InvalidOperation

from . import exports, naming, semantics


STATEMENT_TITLES = {
    "合并资产负债表": ("balance_sheet", "consolidated"),
    "母公司资产负债表": ("balance_sheet", "parent"),
    "合并利润表": ("income_statement", "consolidated"),
    "母公司利润表": ("income_statement", "parent"),
    "合并现金流量表": ("cash_flow_statement", "consolidated"),
    "母公司现金流量表": ("cash_flow_statement", "parent"),
    "公司资产负债表": ("balance_sheet", "parent"),
    "公司利润表": ("income_statement", "parent"),
    "公司现金流量表": ("cash_flow_statement", "parent"),
}

CONCEPTS = {
    "资产总计": "assets_total",
    "资产合计": "assets_total",
    "负债合计": "liabilities_total",
    "所有者权益（或股东权益）合计": "equity_total",
    "股东权益合计": "equity_total",
    "所有者权益合计": "equity_total",
    "负债和所有者权益（或股东权益）总计": "liabilities_and_equity_total",
    "负债和股东权益总计": "liabilities_and_equity_total",
    "负债及股东权益总计": "liabilities_and_equity_total",
    "负债和所有者权益总计": "liabilities_and_equity_total",
    "营业收入": "revenue",
    "一、营业收入": "revenue",
    "其中：营业收入": "revenue",
    "营业成本": "cost_of_revenue",
    "减：营业成本": "cost_of_revenue",
    "利润总额": "profit_before_tax",
    "四、利润总额": "profit_before_tax",
    "四、利润总额（亏损总额以“-”号填列）": "profit_before_tax",
    "三、利润总额（亏损总额以“-”号填列）": "profit_before_tax",
    "所得税费用": "income_tax_expense",
    "减：所得税费用": "income_tax_expense",
    "净利润": "net_profit",
    "五、净利润": "net_profit",
    "五、净利润（净亏损以“-”号填列）": "net_profit",
    "四、净利润（净亏损以“-”号填列）": "net_profit",
    "经营活动产生的现金流量净额": "net_cash_from_operating_activities",
    "经营活动使用的现金流量净额": "net_cash_from_operating_activities",
    "投资活动产生的现金流量净额": "net_cash_from_investing_activities",
    "投资活动使用的现金流量净额": "net_cash_from_investing_activities",
    "筹资活动产生的现金流量净额": "net_cash_from_financing_activities",
    "筹资活动使用的现金流量净额": "net_cash_from_financing_activities",
    "四、汇率变动对现金及现金等价物的影响": "effect_of_exchange_rate_changes",
    "四、汇率变动对现金及现金等价物的影响额": "effect_of_exchange_rate_changes",
    "五、现金及现金等价物净增加额": "net_increase_in_cash",
    "五、现金及现金等价物净（减少）/增加额": "net_increase_in_cash",
    "五、现金及现金等价物净增加/（减少）额": "net_increase_in_cash",
    "加：期初现金及现金等价物余额": "cash_at_beginning",
    "加：年初现金及现金等价物余额": "cash_at_beginning",
    "六、期末现金及现金等价物余额": "cash_at_end",
    "六、年末现金及现金等价物余额": "cash_at_end",
}

BANKING_PROFILE_ANCHORS = (
    "存放中央银行款项", "拆出资金", "买入返售金融资产", "贷款和垫款",
    "客户存款", "吸收存款", "净利息收入", "手续费及佣金收入",
)

INSURANCE_PROFILE_ANCHORS = (
    "保险服务收入", "保险服务费用", "分出保费的分摊", "摊回保险服务费用",
    "分出再保险合同资产", "保险合同负债", "分出再保险合同负债",
    "收到签发保险合同保费取得的现金",
    "收到分入再保险合同的现金净额", "支付签发保险合同赔付款项的现金",
    "支付签发保险合同赔款的现金",
)


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def _clean_label(text: str) -> str:
    return re.sub(r"\s+", "", text or "").replace("–", "-").replace("—", "-")


def _usable_statement_label(label: str) -> bool:
    """拒绝版面噪声占位符；事实必须有可识别的行科目。"""
    return bool(re.search(r"[\u4e00-\u9fffA-Za-z0-9]", label or ""))


def _decimal(raw: str):
    s = (raw or "").strip().replace(",", "").replace(" ", "")
    if not s or s in {"-", "—", "--"}:
        return None
    negative = s.startswith("(") and s.endswith(")")
    if negative:
        s = s[1:-1]
    try:
        value = Decimal(s)
    except InvalidOperation:
        return None
    return -value if negative else value


def _detect_industry_profile(labels: list[str]) -> dict:
    """仅依财务报表科目判定行业模型，不使用公司名称。"""
    compact_labels = [_clean_label(label) for label in labels]
    matched = [anchor for anchor in BANKING_PROFILE_ANCHORS
               if any(anchor in label for label in compact_labels)]
    insurance_matched = [anchor for anchor in INSURANCE_PROFILE_ANCHORS
                         if any(anchor in label for label in compact_labels)]
    banking_income_pair = all(
        anchor in matched for anchor in ("净利息收入", "手续费及佣金收入")
    )
    profile = ("insurance" if len(insurance_matched) >= 3
               else ("banking" if len(matched) >= 3 or banking_income_pair
                     else "generic_corporate"))
    return {
        "profile": profile,
        "rule": "c42-statement-label-anchors-v1",
        "matched_anchors": matched,
        "insurance_matched_anchors": insurance_matched,
        "minimum_anchor_count": 3,
        "banking_income_anchor_pair": banking_income_pair,
    }


def _iso_date(text: str):
    compact = _compact(text)
    m = re.search(r"(20\d{2})年(\d{1,2})月(\d{1,2})日", compact)
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    return None


def _period_from_header(text: str, statement_type: str):
    compact = _compact(text)
    m = re.search(r"截至(20\d{2})年6月30日止6个月期间", compact)
    if m and statement_type in {"income_statement", "cash_flow_statement"}:
        year = int(m.group(1))
        return {"kind": "duration", "start": f"{year:04d}-01-01", "end": f"{year:04d}-06-30"}
    date = _iso_date(text)
    if date:
        return {"kind": "instant", "start": None, "end": date}
    m = re.search(r"(20\d{2})年(?:半年度|1[—－-]6月|1月至6月)", compact)
    if m and statement_type in {"income_statement", "cash_flow_statement"}:
        year = int(m.group(1))
        return {"kind": "duration", "start": f"{year:04d}-01-01", "end": f"{year:04d}-06-30"}
    m = re.search(r"(20\d{2})年度", compact)
    if m and statement_type in {"income_statement", "cash_flow_statement"}:
        year = int(m.group(1))
        return {"kind": "duration", "start": f"{year:04d}-01-01", "end": f"{year:04d}-12-31"}
    m = re.fullmatch(r"(20\d{2})年(?:（(?:经重述|已重述)）)?", compact)
    if m and statement_type in {"income_statement", "cash_flow_statement"}:
        year = int(m.group(1))
        return {"kind": "duration", "start": f"{year:04d}-01-01", "end": f"{year:04d}-12-31"}
    return None


def _period_from_relative_header(text: str, statement_type: str, statement_date: str | None):
    """把资产负债表相对列头绑定到本表明确日期；无日期时拒绝推导。"""
    compact = _compact(text)
    if statement_type != "balance_sheet" or not statement_date:
        return None
    match = re.fullmatch(r"(20\d{2})-(\d{2})-(\d{2})", statement_date)
    if not match:
        return None
    year = int(match.group(1))
    if compact == "期末余额":
        return {"kind": "instant", "start": None, "end": statement_date}
    if compact == "期初余额":
        return {"kind": "instant", "start": None, "end": f"{year - 1:04d}-12-31"}
    return None


def _line_match(pages: list, predicate, *, after=None, before=None):
    for page in pages:
        pno = page["physical_page"]
        for order, line in enumerate(page.get("lines", []), 1):
            pos = (pno, float(line["bbox"][1]))
            if after and pos < after:
                continue
            if before and pos >= before:
                continue
            if predicate(line.get("text", "")):
                return {"physical_page": pno, "order": order, **line}
    return None


def _statement_starts(pages: list) -> list:
    found = []
    for page in pages:
        for order, line in enumerate(page.get("lines", []), 1):
            title, continuation, source_title = naming.canonical_statement_title(
                line.get("text", ""), set(STATEMENT_TITLES)
            )
            if title and not continuation:
                kind, scope = STATEMENT_TITLES[title]
                found.append({"title": title, "source_title": source_title,
                              "statement_type": kind, "entity_scope": scope,
                              "physical_page": page["physical_page"], "top": float(line["bbox"][1]),
                              "bbox": line["bbox"], "order": order})
    ordered = sorted(found, key=lambda x: (x["physical_page"], x["top"]))
    deduped = []
    for item in ordered:
        if (deduped and item["title"] == deduped[-1]["title"]
                and item["physical_page"] == deduped[-1]["physical_page"] + 1):
            continue
        deduped.append(item)
    return deduped


def _cells_by_row(table_doc: dict) -> list:
    rows = []
    for ri in range(int(table_doc.get("row_count", 0))):
        row = []
        for ci in range(int(table_doc.get("column_count", 0))):
            cell = next((c for c in table_doc.get("cells", []) if c["row"] == ri and c["col"] == ci), None)
            row.append(cell or {"row": ri, "col": ci, "raw_value": "", "text": ""})
        rows.append(row)
    return rows


def _cell_text(cell: dict) -> str:
    return str(cell.get("raw_value", cell.get("text", "")) or "")


def _cell_matches_native_words(page: dict, bbox, expected: str) -> bool:
    """用单元格框内的原生词对象复核抽取值；不一致时不得升级为 fact。"""
    if not bbox or not page:
        return False
    words = []
    for word in page.get("words", []):
        cx = (float(word["x0"]) + float(word["x1"])) / 2
        cy = (float(word["top"]) + float(word["bottom"])) / 2
        if bbox[0] - 1 <= cx <= bbox[2] + 1 and bbox[1] - 1 <= cy <= bbox[3] + 1:
            words.append(word)
    visual_lines = []
    for word in sorted(words, key=lambda w: (float(w["top"]), float(w["x0"]))):
        target = next((line for line in reversed(visual_lines[-2:])
                       if abs(sum(float(x["top"]) for x in line) / len(line)
                              - float(word["top"])) <= 3.5), None)
        if target is None:
            target = []
            visual_lines.append(target)
        target.append(word)
    observed_visual = _compact("".join(
        "".join(str(w.get("text", "")) for w in sorted(line, key=lambda x: float(x["x0"])))
        for line in visual_lines
    ))
    # 某些嵌入字体把同一视觉行的 top 写出大于聚类阈值的偏移；保留旧的
    # 全局 y/x 确定性顺序作为第二条原生证据路径。两者都只读取同一 bbox 内原生词。
    observed_yx = _compact("".join(
        str(w.get("text", "")) for w in sorted(words, key=lambda x: (float(x["top"]), float(x["x0"])))
    ))
    wanted = _compact(expected)
    return bool(wanted and any(wanted == observed or wanted in observed
                               for observed in (observed_visual, observed_yx)))


def _column_geometry(table_doc: dict) -> list:
    if table_doc.get("column_bounds") and len(table_doc["column_bounds"]) == 4:
        return table_doc["column_bounds"]
    out = []
    for col in range(int(table_doc.get("column_count", 0))):
        boxes = [c.get("bbox") for c in table_doc.get("cells", []) if c.get("col") == col and c.get("bbox")]
        if not boxes:
            out.append(None)
        else:
            # 合计行或分组标题可能是跨列单元格；中位数比 min/max 更能代表常规列边界。
            out.append([statistics.median(b[0] for b in boxes), statistics.median(b[2] for b in boxes)])
    return out


def _geometry_consistent(docs: list, tolerance: float = 3.0) -> bool:
    if docs and all(d.get("structure_source") == "mineru_borderless_region" for d in docs):
        # 续页可整体左右偏移；30pt 仅用于同标题相邻无框线片段，
        # 期间/主体/单位和标题边界仍需另行一致。
        tolerance = 30.0
    geoms = [_column_geometry(d) for d in docs]
    widths = {len(g) for g in geoms}
    if not geoms or len(widths) != 1 or next(iter(widths), 0) not in {3, 4}:
        return False
    baseline = next((g for g in geoms if all(x is not None for x in g)), None)
    if baseline is None:
        return False
    for geom in geoms:
        for expected, actual in zip(baseline, geom):
            if actual is None:
                continue
            if abs(expected[0] - actual[0]) > tolerance or abs(expected[1] - actual[1]) > tolerance:
                return False
    return True


def _add_evidence(evidence: list, *, source_kind: str, granularity: str, page: int, bbox, object_id: str,
                  full_sentence=None, row_label=None, column_headers=None, engine_version=None, reason=None) -> str:
    eid = f"ev-m2-{len([e for e in evidence if str(e.get('evidence_id', '')).startswith('ev-m2-')]) + 1:06d}"
    evidence.append({
        "evidence_id": eid,
        "source_kind": source_kind,
        "granularity": granularity,
        "locate": {"physical_page": page, "printed_page_label": None, "bbox": bbox,
                   "object_id": object_id, "pdf_reference": f"page={page}"},
        "context": {"row_label": row_label, "column_headers": column_headers,
                    "full_sentence": full_sentence, "unit_currency_source": None,
                    "period_source": None, "entity_source": None},
        "footnote_refs": [],
        "engine": "pdfplumber+annual-report-m2-rules",
        "engine_version": engine_version,
        "processing": [],
        "candidate_reason": reason,
        "confidence": None,
    })
    return eid


def _validation(statement: dict, concept_values: dict, concept_presentations: dict | None = None) -> list:
    """只执行前提明确的主表关系；缺输入返回 insufficient。"""
    out = []
    scope = statement["entity_scope"]
    periods = statement["period_columns"]
    concept_presentations = concept_presentations or {}
    for period_col in periods:
        key = period_col["comparison_state"]
        values = concept_values.get(key, {})
        if statement["statement_type"] == "balance_sheet":
            required = ("assets_total", "liabilities_total", "equity_total")
            if all(values.get(x) is not None for x in required):
                lhs = values["assets_total"]
                rhs = values["liabilities_total"] + values["equity_total"]
                out.append({"check": f"balance_equation:{scope}:{key}", "result": "passed" if lhs == rhs else "failed",
                            "inputs": {"assets": str(lhs), "liabilities": str(values['liabilities_total']), "equity": str(values['equity_total'])},
                            "difference": str(lhs - rhs)})
            else:
                out.append({"check": f"balance_equation:{scope}:{key}", "result": "insufficient", "inputs": {}, "difference": None})
        elif statement["statement_type"] == "income_statement":
            required = ("profit_before_tax", "income_tax_expense", "net_profit")
            if all(values.get(x) is not None for x in required):
                tax = values["income_tax_expense"]
                # 报表存在两种合法展示：正数费用行需减去；括号负数行已带
                # 符号，应直接相加。规则只依原始单元格的数值展示，不尝试两种公式择优。
                signed_tax_line = tax < 0
                lhs = (values["profit_before_tax"] + tax if signed_tax_line
                       else values["profit_before_tax"] - tax)
                rhs = values["net_profit"]
                out.append({"check": f"profit_tax_net:{scope}:{key}", "result": "passed" if lhs == rhs else "failed",
                            "inputs": {x: str(values[x]) for x in required}, "difference": str(lhs - rhs),
                            "formula_model": ("signed_tax_line_addition" if signed_tax_line
                                              else "positive_expense_subtraction"),
                            "presentation": concept_presentations.get(key, {}).get("income_tax_expense", {})})
            else:
                out.append({"check": f"profit_tax_net:{scope}:{key}", "result": "insufficient", "inputs": {}, "difference": None})
        elif statement["statement_type"] == "cash_flow_statement":
            flow_keys = ("net_cash_from_operating_activities", "net_cash_from_investing_activities",
                         "net_cash_from_financing_activities", "effect_of_exchange_rate_changes", "net_increase_in_cash")
            if all(values.get(x) is not None for x in flow_keys):
                calc = sum(values[x] for x in flow_keys[:-1])
                shown = values["net_increase_in_cash"]
                out.append({"check": f"cash_flow_net_change:{scope}:{key}", "result": "passed" if calc == shown else "failed",
                            "inputs": {x: str(values[x]) for x in flow_keys}, "difference": str(calc - shown)})
            else:
                out.append({"check": f"cash_flow_net_change:{scope}:{key}", "result": "insufficient", "inputs": {}, "difference": None})
            balance_keys = ("net_increase_in_cash", "cash_at_beginning", "cash_at_end")
            if all(values.get(x) is not None for x in balance_keys):
                calc = values["net_increase_in_cash"] + values["cash_at_beginning"]
                shown = values["cash_at_end"]
                out.append({"check": f"cash_begin_change_end:{scope}:{key}", "result": "passed" if calc == shown else "failed",
                            "inputs": {x: str(values[x]) for x in balance_keys}, "difference": str(calc - shown)})
            else:
                out.append({"check": f"cash_begin_change_end:{scope}:{key}", "result": "insufficient", "inputs": {}, "difference": None})
    return out


_PERIOD_HEADER_WORDS = {
    "期末余额", "期初余额", "本期金额", "上期金额", "年末余额", "年初余额",
    "期末", "期初", "本期末", "上期末",
}


def _native_rebuild_oversegmented(doc: dict, page) -> dict | None:
    """把 pdfplumber 过切分（>4 列）的有框线主表片段按原生词重建为 3 列。

    前提：doc 网格列数 >4 且该页原生词能干净聚出“行标签 + 两个期间值列”。
    满足时返回替换 cells/column_count/row_count 的 doc 副本（column_count=3），
    否则返回 None → 调用方保持原 doc，走既有拒绝路径（零回归风险）。

    保守门：无两个值列锚点、两组不够分离、数值列过宽（疑似真多列）、行数过少
    或结果无数值时一律返回 None，绝不把可疑网格当成 3 列主表。
    """
    if not page or int(doc.get("column_count", 0) or 0) <= 4:
        return None
    words = page.get("words") or []
    if len(words) < 10:
        return None
    bbox = doc.get("bbox")
    if not bbox or len(bbox) != 4:
        return None
    bx0, by0, bx1, by1 = (float(v) for v in bbox)
    region = [
        w for w in words
        if w.get("text") and bx0 - 3 <= float(w.get("x0", 1e9))
        and float(w.get("x1", 0)) <= bx1 + 3
        and by0 - 3 <= float(w.get("top", 1e9)) <= by1 + 3
    ]
    if not region:
        return None

    def _center(w):
        return (float(w["x0"]) + float(w["x1"])) / 2.0

    # 期间表头锚点（期末余额/期初余额/本期金额/上期金额…）
    anchors = sorted({
        _center(w) for w in region
        if str(w.get("text", "")).replace(" ", "").replace("\u3000", "") in _PERIOD_HEADER_WORDS
    })
    numeric = [w for w in region if re.search(r"\d", str(w.get("text", "")))]
    if len(anchors) >= 2:
        lo, hi = anchors[0], anchors[-1]
    else:
        # 续页常无重复期头：把数值词 x 中心按最大间隔切两组作两个值列
        centers = sorted(_center(w) for w in numeric)
        if len(centers) < 6:
            return None
        gaps = [(centers[i + 1] - centers[i], i) for i in range(len(centers) - 1)]
        gap, idx = max(gaps, key=lambda g: g[0])
        if gap < 60 or idx < 2 or len(centers) - idx - 2 < 2:
            return None
        lo = (centers[0] + centers[idx]) / 2
        hi = (centers[idx + 1] + centers[-1]) / 2
    if hi - lo < 80:
        return None
    # 值列宽度守卫：任一“值列”数值过宽说明可能是真多列，不重建
    lo_spread = [c for w in numeric if (c := _center(w)) < (lo + hi) / 2]
    hi_spread = [c for w in numeric if (c := _center(w)) >= (lo + hi) / 2]
    if lo_spread and (max(lo_spread) - min(lo_spread)) > 130:
        return None
    if hi_spread and (max(hi_spread) - min(hi_spread)) > 130:
        return None

    # y 重叠聚类成视觉行（标签与数值基线可能错位，须按重叠而非 top 取整）
    visual = []
    for w in sorted(region, key=lambda w: (float(w["top"]), float(w["x0"]))):
        placed = False
        for row in visual:
            if float(w["top"]) < row["bottom"] - 1 and float(w["bottom"]) > row["top"] + 1:
                row["words"].append(w)
                row["top"] = min(row["top"], float(w["top"]))
                row["bottom"] = max(row["bottom"], float(w["bottom"]))
                placed = True
                break
        if not placed:
            visual.append({"top": float(w["top"]), "bottom": float(w["bottom"]), "words": [w]})
    visual.sort(key=lambda r: r["top"])
    if len(visual) < 3:
        return None

    mid = (lo + hi) / 2.0
    label_right = lo - 10.0
    new_cells = []
    numeric_cell_count = 0
    for ri, row in enumerate(visual):
        groups = {"0": [], "1": [], "2": []}
        for w in sorted(row["words"], key=lambda w: float(w["x0"])):
            if not re.search(r"\d", str(w.get("text", ""))) and _center(w) < label_right:
                groups["0"].append(w)
            else:
                groups["1" if _center(w) < mid else "2"].append(w)
        for ci_key, part in (("0", 0), ("1", 1), ("2", 2)):
            tokens = groups[ci_key]
            if not tokens:
                continue
            text = " ".join(w["text"] for w in tokens)
            new_cells.append({
                "row": ri, "col": part, "text": text, "raw_value": text,
                "bbox": [min(float(w["x0"]) for w in tokens), min(float(w["top"]) for w in tokens),
                         max(float(w["x1"]) for w in tokens), max(float(w["bottom"]) for w in tokens)],
            })
            if part in (1, 2) and re.search(r"\d", text):
                numeric_cell_count += 1
    if numeric_cell_count < 2:
        return None
    rebuilt = dict(doc)
    rebuilt["cells"] = new_cells
    rebuilt["column_count"] = 3
    rebuilt["row_count"] = len(visual)
    rebuilt["reconstruction_method"] = "native_xy_oversegment_v1"
    rebuilt["structure_source"] = str(doc.get("structure_source", "")) + "+native-rebuild"
    return rebuilt


def build_main_statements(root, pages: list, table_items: list, evidence: list, review: list,
                          identity: dict, engine_version: str) -> dict:
    """生成逻辑主表与观察值；直接写 logical-table JSON/HTML，返回汇总。"""
    starts = _statement_starts(pages)
    page_by_number = {page["physical_page"]: page for page in pages}
    stop_titles = {
        "合并所有者权益变动表", "母公司所有者权益变动表",
        "合并股东权益变动表", "公司股东权益变动表", "母公司股东权益变动表",
        "财务报表附注",
    }
    boundaries = list(starts)
    for page in pages:
        for order, line in enumerate(page.get("lines", []), 1):
            canonical, _, source_title = naming.canonical_statement_title(line.get("text", ""), stop_titles)
            if canonical:
                boundaries.append({"title": canonical, "source_title": source_title,
                                   "physical_page": page["physical_page"],
                                   "top": float(line["bbox"][1]), "bbox": line["bbox"], "order": order})
    boundaries.sort(key=lambda x: (x["physical_page"], x["top"]))
    accounting_line = _line_match(pages, semantics.is_cas_compliance_statement)
    first_start_pos = ((starts[0]["physical_page"], starts[0]["top"]) if starts else None)
    functional_currency_line = _line_match(
        pages, semantics.is_cny_functional_currency_statement, after=first_start_pos
    )
    if functional_currency_line is None:
        # 记账本位币声明常位于财务报表附注"公司基本情况"节，早于第一张主表；
        # 此时做文档级回退（仍要求是明确的记账本位币声明，见 semantics）。
        functional_currency_line = _line_match(
            pages, semantics.is_cny_functional_currency_statement
        )
    logical_tables = []
    facts = []
    candidates = []
    validations = []
    accepted_fragments = set()

    for si, start in enumerate(starts):
        start_pos = (start["physical_page"], start["top"])
        next_start = next(
            (item for item in boundaries if (item["physical_page"], item["top"]) > start_pos),
            None,
        )
        end_pos = (next_start["physical_page"], next_start["top"]) if next_start else (10**9, 0)
        selected = [t for t in table_items if start_pos < (t["physical_page"], float(t["bbox"][1])) < end_pos]
        if not selected:
            continue
        docs = []
        for item in selected:
            import json
            from pathlib import Path
            docs.append(json.loads((Path(root) / item["json"]).read_text(encoding="utf-8")))
        # C4.6：pdfplumber 有框线表过切分（>4 列）时按原生词重建为 3 列主表片段。
        # 重建失败返回 None → 保持原 doc（既有拒绝路径），不产生新误判。
        rebuilt_fragments = []
        for i, doc in enumerate(docs):
            rebuilt = _native_rebuild_oversegmented(
                doc, page_by_number.get(doc.get("physical_page"))
            )
            if rebuilt is not None:
                docs[i] = rebuilt
                rebuilt_fragments.append(doc.get("table_fragment_id"))

        lid = f"logical-table-{len(logical_tables) + 1:04d}"
        title_ev = _add_evidence(evidence, source_kind="text_span", granularity="span",
                                 page=start["physical_page"], bbox=start["bbox"], object_id=lid,
                                 full_sentence=start.get("source_title", start["title"]), engine_version=engine_version,
                                 reason="bounded-normalized statutory statement title")
        unit_line = _line_match(
            pages,
            lambda t: semantics.parse_unit_currency(t)[0] is not None,
                                after=start_pos, before=end_pos)
        unit = currency = None
        unit_ev = None
        currency_ev = None
        currency_basis = None
        if unit_line:
            unit, currency = semantics.parse_unit_currency(unit_line["text"])
            unit_ev = _add_evidence(evidence, source_kind="text_span", granularity="span",
                                    page=unit_line["physical_page"], bbox=unit_line["bbox"], object_id=lid,
                                    full_sentence=unit_line["text"], engine_version=engine_version,
                                    reason="unit and currency declaration")
            if currency == "CNY":
                currency_ev = unit_ev
                currency_basis = "explicit_unit_line"
        if unit is not None and currency is None and functional_currency_line:
            currency = "CNY"
            currency_ev = _add_evidence(
                evidence, source_kind="text_span", granularity="span",
                page=functional_currency_line["physical_page"], bbox=functional_currency_line["bbox"],
                object_id=lid, full_sentence=functional_currency_line["text"],
                engine_version=engine_version,
                reason="C3.3 CNY bridge from explicit functional-currency declaration"
            )
            currency_basis = "functional_currency_declaration"

        accounting_ev = None
        if accounting_line:
            accounting_ev = _add_evidence(evidence, source_kind="text_span", granularity="span",
                                          page=accounting_line["physical_page"], bbox=accounting_line["bbox"], object_id=lid,
                                          full_sentence=accounting_line["text"], engine_version=engine_version,
                                          reason="accounting basis declaration")

        statement_date_line = _line_match(
            pages, lambda text: _iso_date(text) is not None, after=start_pos, before=end_pos
        )
        statement_date = _iso_date(statement_date_line["text"]) if statement_date_line else None
        statement_date_ev = None
        if statement_date_line:
            statement_date_ev = _add_evidence(
                evidence, source_kind="text_span", granularity="span",
                page=statement_date_line["physical_page"], bbox=statement_date_line["bbox"],
                object_id=lid, full_sentence=statement_date_line["text"],
                engine_version=engine_version, reason="explicit statutory statement date"
            )
        header_row = None
        header_doc = None
        note_col = None
        value_cols = None
        for doc in docs:
            for row in _cells_by_row(doc):
                if len(row) in {3, 4} and _clean_label(_cell_text(row[0])) == "项目":
                    header_row, header_doc = row, doc
                    note_col = 1 if len(row) == 4 else None
                    value_cols = (2, 3) if len(row) == 4 else (1, 2)
                    break
            if header_row:
                break
        period_columns = []
        period_evidence = {}
        if header_row:
            for col, state in zip(value_cols, ("current", "comparative")):
                raw_header = _cell_text(header_row[col])
                parsed = _period_from_header(raw_header, start["statement_type"])
                derivation = "explicit_header"
                if parsed is None:
                    parsed = _period_from_relative_header(
                        raw_header, start["statement_type"], statement_date
                    )
                    derivation = "relative_header_anchored_to_statement_date" if parsed else None
                bbox = header_row[col].get("bbox")
                if parsed and bbox and _cell_matches_native_words(
                    page_by_number.get(header_doc["physical_page"]), bbox, raw_header
                ):
                    eid = _add_evidence(evidence, source_kind="table_cell", granularity="cell",
                                        page=header_doc["physical_page"], bbox=bbox, object_id=lid,
                                        full_sentence=raw_header, column_headers=[raw_header], engine_version=engine_version,
                                        reason="statement period header")
                    period_evidence[col] = eid
                refs = ([period_evidence[col]] if period_evidence.get(col) else [])
                if derivation == "relative_header_anchored_to_statement_date" and statement_date_ev:
                    refs.append(statement_date_ev)
                period_columns.append({"col": col, "raw_header": raw_header, "period": parsed,
                                       "comparison_state": state, "evidence_ref": period_evidence.get(col),
                                       "evidence_refs": refs, "derivation": derivation})

        pages_used = sorted({d["physical_page"] for d in docs})
        geometry_ok = _geometry_consistent(docs)
        pages_ok = all(b - a <= 1 for a, b in zip(pages_used, pages_used[1:]))
        context_failure_reasons = []
        if semantics.scale_multiplier(unit) is None:
            context_failure_reasons.append("unsupported_or_missing_unit_scale")
        if currency != "CNY":
            context_failure_reasons.append("currency_not_explicit_cny")
        if len(period_columns) != 2:
            context_failure_reasons.append("period_column_count_not_two")
        elif any(not p["period"] for p in period_columns):
            context_failure_reasons.append("period_semantics_missing")
        elif any(not p["evidence_ref"] for p in period_columns):
            context_failure_reasons.append("period_evidence_missing")
        if not accounting_ev:
            context_failure_reasons.append("accounting_basis_evidence_missing")
        if not geometry_ok:
            context_failure_reasons.append("column_geometry_inconsistent")
        if not pages_ok:
            context_failure_reasons.append("non_adjacent_fragments")
        context_ok = not context_failure_reasons
        join_evidence = ["statement title boundary", "adjacent physical pages", "three-or-four-column geometry consistent",
                         "period headers inherited from first fragment", "same entity scope and unit"]
        if not geometry_ok:
            join_evidence.append("CONFLICT: column geometry inconsistent")
        if not pages_ok:
            join_evidence.append("CONFLICT: non-adjacent fragments")

        rows_out = []
        concept_values = {"current": {}, "comparative": {}}
        concept_presentations = {"current": {}, "comparative": {}}
        label_evidence_by_label = {}
        for doc in docs:
            for row in _cells_by_row(doc):
                if not value_cols or len(row) <= max(value_cols):
                    continue
                label = _clean_label(_cell_text(row[0]))
                if not _usable_statement_label(label) or label == "项目":
                    continue
                note = _cell_text(row[note_col]) if note_col is not None else ""
                label_bbox = row[0].get("bbox")
                label_ev = None
                if label_bbox and _cell_matches_native_words(
                    page_by_number.get(doc["physical_page"]), label_bbox, label
                ):
                    label_ev = _add_evidence(evidence, source_kind="table_cell", granularity="cell",
                                             page=doc["physical_page"], bbox=label_bbox, object_id=lid,
                                             full_sentence=label, row_label=label, engine_version=engine_version,
                                             reason="statement row label")
                if label_ev:
                    label_evidence_by_label.setdefault(label, []).append(label_ev)
                values_out = []
                for period_col in period_columns:
                    col = period_col["col"]
                    cell = row[col]
                    raw = _cell_text(cell)
                    numeric = _decimal(raw)
                    if numeric is None:
                        values_out.append({"column": col, "raw_value": raw, "numeric_value": None,
                                           "eligible_for_calculation": False})
                        continue
                    value_ev = None
                    if cell.get("bbox") and _cell_matches_native_words(
                        page_by_number.get(doc["physical_page"]), cell["bbox"], raw
                    ):
                        value_ev = _add_evidence(evidence, source_kind="table_cell", granularity="cell",
                                                 page=doc["physical_page"], bbox=cell["bbox"], object_id=lid,
                                                 full_sentence=raw, row_label=label,
                                                 column_headers=[period_col["raw_header"]], engine_version=engine_version,
                                                 reason="table extraction matched native words inside physical cell bbox")
                    accepted = bool(context_ok and label_ev and value_ev)
                    concept = CONCEPTS.get(label)
                    obs_id = f"{identity.get('document_id', 'document')}/{lid}/p{doc['physical_page']}/r{cell['row']}c{col}"
                    ev_refs = {
                        "value": [value_ev] if value_ev else [],
                        "row_label": [label_ev] if label_ev else [],
                        "period": period_col.get("evidence_refs") or [],
                        "unit_currency": list(dict.fromkeys(
                            ref for ref in (unit_ev, currency_ev) if ref
                        )),
                        "entity_scope": [title_ev],
                        "accounting_basis": [accounting_ev] if accounting_ev else [],
                        "footnote": [],
                    }
                    obs = {
                        "observation_id": obs_id,
                        "source_kind": "table_cell",
                        "original_label": label,
                        "concept_mapping": {"concept": concept, "status": "accepted" if concept else "unmapped"},
                        "raw_value": raw,
                        "numeric_value": str(numeric),
                        "currency": currency,
                        "scale": unit,
                        "normalized_value": semantics.normalize_decimal(raw, unit) if accepted else None,
                        "transformation": {"multiplier": str(semantics.scale_multiplier(unit)),
                                           "rule_version": "c32-explicit-scale-v1",
                                           "input_refs": [unit_ev]} if accepted else {},
                        "quantity_type": "per_share_amount" if "每股收益" in label else "amount",
                        "entity_scope": start["entity_scope"],
                        "accounting_basis": "CAS" if accounting_ev else None,
                        "restatement_status": "as_reported" if period_col["comparison_state"] == "current" else "comparative_reported",
                        "comparison_state": period_col["comparison_state"],
                        "disclosure_version": {"document_id": identity.get("document_id", "document"), "disclosed_at": None, "is_revision": False},
                        "scope_conditions": {"continuing": None, "tax_inclusive": None, "excludes": []},
                        "business_dimensions": {},
                        "evidence_refs": ev_refs,
                        "quality": {"text": "checked" if value_ev else "needs_review",
                                    "structure": "checked" if context_ok else "needs_review",
                                    "semantics": "checked" if context_ok else "needs_review",
                                    "normalization": "computed_from_candidate" if accepted else "none",
                                    "eligible_for_calculation": accepted},
                        "usage_eligibility": {"readable": True, "traceable": bool(value_ev),
                                              "citable": accepted, "computable": accepted},
                        "status": "accepted_fact" if accepted else "fact_candidate",
                        "statement_type": start["statement_type"],
                        "logical_table_id": lid,
                        "physical_fragment_id": doc["table_fragment_id"],
                        "physical_page": doc["physical_page"],
                        "cell_bbox": cell.get("bbox"),
                        "value_evidence_ref": value_ev,
                        "note_reference": note or None,
                    }
                    if period_col["period"] is not None:
                        obs["period"] = period_col["period"]
                    (facts if accepted else candidates).append(obs)
                    values_out.append({"column": col, "raw_value": raw, "numeric_value": str(numeric),
                                       "observation_id": obs_id, "evidence_ref": value_ev,
                                       "eligible_for_calculation": accepted})
                    if concept:
                        concept_values[period_col["comparison_state"]][concept] = numeric
                        concept_presentations[period_col["comparison_state"]][concept] = {
                            "row_label": label,
                            "raw_value": raw,
                            "parenthesized_negative": raw.strip().startswith("(") and raw.strip().endswith(")"),
                        }
                rows_out.append({"label": label, "note_reference": note or None,
                                 "label_evidence_ref": label_ev, "physical_page": doc["physical_page"],
                                 "fragment_id": doc["table_fragment_id"], "values": values_out})

        industry_profile = _detect_industry_profile([row["label"] for row in rows_out])
        industry_profile["evidence_refs"] = list(dict.fromkeys(
            evidence_ref
            for anchor in (industry_profile["matched_anchors"]
                           + industry_profile.get("insurance_matched_anchors", []))
            for label, refs in label_evidence_by_label.items() if anchor in label
            for evidence_ref in refs
        ))
        logical = {
            "logical_table_id": lid,
            "caption": start["title"],
            "fragments": [d["table_fragment_id"] for d in docs],
            "native_rebuild_fragments": rebuilt_fragments,
            "reconstruction_method": "native_xy_oversegment_v1" if rebuilt_fragments else None,
            "join_evidence": join_evidence,
            "subgroups": [{"subgroup_id": f"{lid}-main", "fragment_ids": [d["table_fragment_id"] for d in docs],
                           "period": " | ".join(p["raw_header"] for p in period_columns),
                           "entity_scope": start["entity_scope"], "basis": "CAS" if accounting_ev else None}],
            "forbidden_join_notes": [f"next statement boundary: {next_start['title']}" if next_start else "end of main statement sequence"],
            "candidate_status": "accepted_by_rules" if context_ok else "needs_review",
            "html_path": f"表格/{lid}.html",
            "json_path": f"表格/{lid}.json",
            "statement_type": start["statement_type"],
            "entity_scope": start["entity_scope"],
            "industry_profile": industry_profile,
            "currency": currency,
            "currency_basis": currency_basis,
            "unit_scale": unit,
            "accounting_basis": "CAS" if accounting_ev else None,
            "period_columns": period_columns,
            "rows": rows_out,
            "evidence_refs": {"title": [title_ev], "unit_currency": list(dict.fromkeys(
                                  ref for ref in (unit_ev, currency_ev) if ref
                              )),
                              "statement_date": [statement_date_ev] if statement_date_ev else [],
                              "accounting_basis": [accounting_ev] if accounting_ev else []},
            "eligible_for_calculation": context_ok,
            "context_failure_reasons": context_failure_reasons,
        }
        statement_validations = _validation(logical, concept_values, concept_presentations)
        logical["validation_results"] = statement_validations
        validations.extend(statement_validations)
        if any(v["result"] == "failed" for v in statement_validations):
            logical["candidate_status"] = "needs_review"
            logical["eligible_for_calculation"] = False
            # 已生成事实降级为候选，不能在关系失败时保留计算资格。
            affected = [x for x in facts if x.get("logical_table_id") == lid]
            for obs in affected:
                obs["status"] = "fact_candidate"
                obs["quality"]["eligible_for_calculation"] = False
                obs["usage_eligibility"]["computable"] = False
                obs["normalized_value"] = None
                candidates.append(obs)
                facts.remove(obs)
        elif context_ok:
            accepted_fragments.update(logical["fragments"])

        exports.write_json(f"{root}/表格/{lid}.json", logical)
        note_header = "<th>附注</th>" if note_col is not None else ""
        rows_html = ["<table><thead><tr><th>项目</th>" + note_header + "".join(f"<th>{html.escape(p['raw_header'])}</th>" for p in period_columns) + "</tr></thead><tbody>"]
        for row in rows_out:
            vals = "".join(f"<td>{html.escape(v.get('raw_value',''))}</td>" for v in row["values"])
            note_cell = (f"<td>{html.escape(row.get('note_reference') or '')}</td>"
                         if note_col is not None else "")
            rows_html.append(f"<tr><td>{html.escape(row['label'])}</td>{note_cell}{vals}</tr>")
        rows_html.append("</tbody></table>")
        from pathlib import Path
        Path(f"{root}/表格/{lid}.html").write_text(
            "<!doctype html><html lang='zh-CN'><meta charset='utf-8'><title>" + html.escape(start["title"]) + "</title>"
            "<style>table{border-collapse:collapse}td,th{border:1px solid #999;padding:4px}</style><body>"
            f"<h1>{html.escape(start['title'])}</h1><p>主体：{html.escape(start['entity_scope'])}；单位：{html.escape(unit or '')}；币种：{html.escape(currency or '')}；准则：CAS</p>"
            + "".join(rows_html) + "</body></html>", encoding="utf-8")
        logical_tables.append(logical)

    # 主表片段升级并关闭其 M1 泛化复核项；原始网格不改值，只增加资格/关系字段。
    import json
    from pathlib import Path
    for item in table_items:
        if item["table_fragment_id"] not in accepted_fragments:
            continue
        path = Path(root) / item["json"]
        doc = json.loads(path.read_text(encoding="utf-8"))
        doc["candidate_status"] = "accepted_by_rules"
        doc["eligible_for_calculation"] = True
        logical = next(x for x in logical_tables if item["table_fragment_id"] in x["fragments"])
        doc["belongs_to_logical_table"] = logical["logical_table_id"]
        accepted_coords = set()
        evidence_by_coord = {}
        for fact in facts:
            if fact.get("physical_fragment_id") != item["table_fragment_id"]:
                continue
            match = re.search(r"/r(\d+)c(\d+)$", fact["observation_id"])
            if match:
                coord = (int(match.group(1)), int(match.group(2)))
                accepted_coords.add(coord)
                evidence_by_coord[coord] = fact.get("value_evidence_ref")
        for cell in doc.get("cells", []):
            coord = (cell.get("row"), cell.get("col"))
            cell["eligible_for_calculation"] = coord in accepted_coords
            value_evidence = evidence_by_coord.get(coord)
            if value_evidence:
                cell.setdefault("evidence_refs", [])
                if value_evidence not in cell["evidence_refs"]:
                    cell["evidence_refs"].append(value_evidence)
        exports.write_json(path, doc)
        item["candidate_status"] = "accepted_by_rules"
        item["eligible_for_calculation"] = True
        item["belongs_to_logical_table"] = logical["logical_table_id"]
        for q in review:
            if q.get("object_ref") == item["json"]:
                q["status"] = "resolved"
                q["reason"] = "M2 已完成主表边界、列几何和语义上下文规则验收"
                q["next_step"] = "保留用于抽样人工复核；无需阻塞已接受事实"

    return {"logical_tables": logical_tables, "facts": facts, "candidates": candidates,
            "validations": validations, "accepted_fragments": sorted(accepted_fragments),
            "statement_titles_found": [s["title"] for s in starts]}


def write_facts_csv(path, facts: list) -> None:
    """导出可计算事实长表；JSONL 仍是保留完整证据结构的主数据。"""
    fields = [
        "observation_id", "logical_table_id", "statement_type", "entity_scope",
        "original_label", "concept", "raw_value", "numeric_value", "normalized_value",
        "currency", "scale", "period_kind", "period_start", "period_end",
        "comparison_state", "physical_page", "value_evidence_ref",
    ]
    from pathlib import Path
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for fact in facts:
            period = fact.get("period") or {}
            writer.writerow({
                "observation_id": fact.get("observation_id"),
                "logical_table_id": fact.get("logical_table_id"),
                "statement_type": fact.get("statement_type"),
                "entity_scope": fact.get("entity_scope"),
                "original_label": fact.get("original_label"),
                "concept": (fact.get("concept_mapping") or {}).get("concept"),
                "raw_value": fact.get("raw_value"),
                "numeric_value": fact.get("numeric_value"),
                "normalized_value": fact.get("normalized_value"),
                "currency": fact.get("currency"), "scale": fact.get("scale"),
                "period_kind": period.get("kind"), "period_start": period.get("start"),
                "period_end": period.get("end"),
                "comparison_state": fact.get("comparison_state"),
                "physical_page": fact.get("physical_page"),
                "value_evidence_ref": fact.get("value_evidence_ref"),
            })


capabilities = {
    "engine_wired": True,
    "implemented": ["六类中文法定主表边界", "跨页逻辑表", "逐格证据", "主体期间单位准则绑定", "主表勾稽", "银行与保险科目证据画像", "可计算事实分层"],
    "pending": ["年度/季度更多期间模板", "港股繁英主表", "未回归保险版式", "重述列和多币种局部覆盖"],
}
