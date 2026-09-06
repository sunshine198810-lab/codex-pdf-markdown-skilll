"""C3 所有者权益变动表关键单元格事实层。

本模块重建中文 A 股法定权益变动表的逻辑边界、期间子组和多级列表头。
只有同一列的期初、增减、期末三个显式数值同时具备完整上下文和逐格证据，
且严格满足期初 + 增减 = 期末时，才把这三个单元格升格为 facts。其他值仍停留在结构层。
"""

from __future__ import annotations

import html
import json
import re
from pathlib import Path

from . import exports, naming, semantics
from .financials import (_add_evidence, _cell_matches_native_words, _cell_text, _cells_by_row,
                         _compact, _decimal, _line_match)


EQUITY_TITLES = {
    "合并所有者权益变动表": "consolidated",
    "合并股东权益变动表": "consolidated",
    "母公司所有者权益变动表": "parent",
    "公司所有者权益变动表": "parent",
    "母公司股东权益变动表": "parent",
    "公司股东权益变动表": "parent",
}

_DATA_LABEL = re.compile(r"^(?:[一二三四五六七八九十]+[、.]|加：|减：|前期|其他|本期|上期)")
_NUMBER = re.compile(r"(?<![\d,])(?:\(?-?\d[\d,]*(?:\.\d+)?\)?)(?![\d,])")
_HEADER_ANCHORS = ("股本", "实收资本", "资本公积", "未分配利润", "所有者权益合计", "股东权益合计")
_ROW_START = re.compile(
    r"^(?:[一二三四五六七八九十]+[、.]|加：|前期差错更正|同一控制下企业合并|"
    r"其他$|（[一二三四五六七八九十]+）|[1-9]\d*[、.])"
)

_CONSOLIDATED_COLUMNS = [
    "项目", "股本", "优先股", "永续债", "其他", "资本公积", "减：库存股", "其他综合收益",
    "专项储备", "盈余公积", "一般风险准备", "未分配利润", "其他", "小计", "少数股东权益", "股东权益合计",
]
_PARENT_COLUMNS = [
    "项目", "股本", "优先股", "永续债", "其他", "资本公积", "减：库存股", "其他综合收益",
    "专项储备", "盈余公积", "未分配利润", "股东权益合计",
]

_EQUITY_CONCEPTS = {
    "股本": "share_capital", "实收资本或股本": "share_capital",
    "优先股": "preferred_shares", "永续债": "perpetual_bonds",
    "其他权益工具其他": "other_equity_instruments_other",
    "资本公积": "capital_reserve", "减库存股": "treasury_shares",
    "其他综合收益": "other_comprehensive_income_reserve",
    "专项储备": "special_reserve", "盈余公积": "surplus_reserve",
    "一般风险准备": "general_risk_reserve", "未分配利润": "retained_earnings",
    "其他": "other_equity", "小计": "equity_attributable_to_parent",
    "少数股东权益": "non_controlling_interests",
    "股东权益合计": "equity_total", "所有者权益合计": "equity_total",
}


def _clean(text: str) -> str:
    return _compact(text).replace("(", "（").replace(")", "）")


def _base_title(text: str) -> tuple[str | None, bool]:
    title, continuation, _ = naming.canonical_statement_title(text, set(EQUITY_TITLES))
    return title, continuation


def _starts(pages: list) -> list:
    out = []
    for page in pages:
        for order, line in enumerate(page.get("lines", []), 1):
            title, continuation, source_title = naming.canonical_statement_title(
                line.get("text", ""), set(EQUITY_TITLES)
            )
            if title and not continuation:
                out.append({"title": title, "entity_scope": EQUITY_TITLES[title],
                            "source_title": source_title,
                            "physical_page": page["physical_page"], "top": float(line["bbox"][1]),
                            "bbox": line["bbox"], "order": order})
    return sorted(out, key=lambda x: (x["physical_page"], x["top"]))


def _first_data_row(rows: list) -> int:
    for ri, row in enumerate(rows):
        label = _clean(_cell_text(row[0])) if row else ""
        if _DATA_LABEL.search(label):
            return ri
    return len(rows)


def _period(rows: list, page: dict) -> str | None:
    for row in rows[:4]:
        for cell in row:
            text = _clean(_cell_text(cell))
            m = re.search(r"20\d{2}年(?:半年度|度)", text)
            if m:
                return m.group(0)
    for line in page.get("lines", []):
        m = re.search(r"20\d{2}年(?:半年度|度)", _clean(line.get("text", "")))
        if m:
            return m.group(0)
    return None


def _header_paths(rows: list, header_rows: int) -> list[list[str]]:
    """将 PDF 合并单元格留下的空位按“本行下一个非空单元格”为边界向右展开。"""
    if not rows:
        return []
    cols = len(rows[0])
    paths = [[] for _ in range(cols)]
    for ri in range(header_rows):
        row = rows[ri]
        anchors = [(ci, _clean(_cell_text(cell))) for ci, cell in enumerate(row) if _clean(_cell_text(cell))]
        for ai, (start, text) in enumerate(anchors):
            end = anchors[ai + 1][0] if ai + 1 < len(anchors) else cols
            # 首列“项目”不向数值列扩展。
            if start == 0 and text in {"项目", "项目"}:
                end = min(end, 1)
            for ci in range(start, end):
                if not paths[ci] or paths[ci][-1] != text:
                    paths[ci].append(text)
    return paths


def _numeric_token_count(text: str) -> int:
    # 保留空格：宽表行坍缩的典型特征正是多个数值被空格串在同一单元格。
    return len(_NUMBER.findall(text or ""))


def _union_bbox(words: list) -> list | None:
    if not words:
        return None
    return [min(float(w["x0"]) for w in words), min(float(w["top"]) for w in words),
            max(float(w["x1"]) for w in words), max(float(w["bottom"]) for w in words)]


def _header_key(text: str) -> str:
    return re.sub(r"[\s：:()（）]", "", text or "")


def _header_clusters(page: dict, top: float, bottom: float) -> list[dict]:
    """将表头中竖排的“优/先/股”按相同 x 中心合并，水平长词保持为单簇。"""
    groups = []
    for word in sorted((w for w in page.get("words", []) if top <= float(w["top"]) <= bottom),
                       key=lambda w: ((float(w["x0"]) + float(w["x1"])) / 2, float(w["top"]))):
        cx = (float(word["x0"]) + float(word["x1"])) / 2
        group = next((g for g in groups if abs(g["center"] - cx) <= 3.0), None)
        if group is None:
            group = {"center": cx, "words": []}
            groups.append(group)
        group["words"].append(word)
        group["center"] = sum((float(w["x0"]) + float(w["x1"])) / 2 for w in group["words"]) / len(group["words"])
    out = []
    for group in groups:
        words = sorted(group["words"], key=lambda w: (float(w["top"]), float(w["x0"])))
        out.append({"center": group["center"], "text": "".join(str(w.get("text", "")) for w in words),
                    "bbox": _union_bbox(words)})
    return sorted(out, key=lambda x: x["center"])


def _visual_lines(words: list, tolerance: float = 4.6) -> list[list]:
    lines = []
    for word in sorted(words, key=lambda w: (float(w["top"]), float(w["x0"]))):
        target = next((line for line in reversed(lines[-2:])
                       if abs(sum(float(x["top"]) for x in line) / len(line) - float(word["top"])) <= tolerance), None)
        if target is None:
            target = []
            lines.append(target)
        target.append(word)
    for line in lines:
        line.sort(key=lambda w: float(w["x0"]))
    return lines


def _native_reconstruct_wide(page: dict, scope: str) -> dict:
    """用原生词的 x/y 坐标重建格力式行坍缩权益表。"""
    columns = _CONSOLIDATED_COLUMNS if scope == "consolidated" else _PARENT_COLUMNS
    lines = page.get("lines", [])
    first = next((line for line in lines if _clean(line.get("text", "")).startswith("一、上年")
                  or _clean(line.get("text", "")).startswith("一.上年")), None)
    signature = next((line for line in lines if "法定代表人" in _clean(line.get("text", ""))), None)
    period_line = next((line for line in lines if re.fullmatch(r"20\d{2}年(?:半年度|度)", _clean(line.get("text", "")))), None)
    if not first or not signature or not period_line:
        return {"ok": False, "reasons": ["native_row_or_period_boundary_missing"]}
    header_top = float(period_line["bbox"][3])
    header_bottom = float(first["bbox"][1]) - 1
    clusters = _header_clusters(page, header_top, header_bottom)
    chosen = []
    previous = -1.0
    for label in columns[1:]:
        wanted = _header_key(label)
        matches = [c for c in clusters if c["center"] > previous + 2
                   and (_header_key(c["text"]) == wanted or _header_key(c["text"]).endswith(wanted))]
        if not matches and label == "专项储备":
            # 竖排“专项/储备”可被 PDF 字体拆成“专储”和“项备”两个近邻 x 簇。
            for left, right in zip(clusters, clusters[1:]):
                if left["center"] <= previous + 2 or right["center"] - left["center"] > 12:
                    continue
                if sorted(_header_key(left["text"] + right["text"])) == sorted(wanted):
                    matches = [{"center": (left["center"] + right["center"]) / 2,
                                "text": label,
                                "bbox": [min(left["bbox"][0], right["bbox"][0]),
                                         min(left["bbox"][1], right["bbox"][1]),
                                         max(left["bbox"][2], right["bbox"][2]),
                                         max(left["bbox"][3], right["bbox"][3])]}]
                    break
        if not matches and label == "股东权益合计":
            matches = [c for c in clusters if c["center"] > previous + 2
                       and _header_key(c["text"]) in {"股东权益合计", "所有者权益合计"}]
        if not matches:
            return {"ok": False, "reasons": [f"native_header_column_missing:{label}"],
                    "header_clusters": clusters}
        item = matches[0]
        chosen.append({"label": label, **item})
        previous = item["center"]
    if len(chosen) < 2:
        return {"ok": False, "reasons": ["native_header_columns_insufficient"]}
    label_center = chosen[0]["center"] - 2 * (chosen[1]["center"] - chosen[0]["center"])
    centers = [label_center] + [c["center"] for c in chosen]
    first_boundary = (centers[0] + centers[1]) / 2
    period = _clean(period_line.get("text", ""))
    paths = []
    for ci, label in enumerate(columns):
        if ci == 0:
            paths.append(["项目"])
        elif scope == "consolidated" and ci <= 13:
            path = [period, "归属于母公司股东权益"]
            if 2 <= ci <= 4:
                path.append("其他权益工具")
            path.append(label)
            paths.append(path)
        elif scope == "parent" and 2 <= ci <= 4:
            paths.append([period, "其他权益工具", label])
        else:
            paths.append([period, label])

    data_words = [w for w in page.get("words", [])
                  if float(first["bbox"][1]) - 2 <= float(w["top"]) < float(signature["bbox"][1]) - 1]
    visual = _visual_lines(data_words)
    rows, current = [], None
    failures = []
    native_numeric_tokens = 0
    assigned_numeric_tokens = 0
    for line_words in visual:
        numeric = [w for w in line_words if _decimal(str(w.get("text", ""))) is not None]
        native_numeric_tokens += len(numeric)
        label_words = [w for w in line_words if w not in numeric and float(w["x0"]) < first_boundary + 12]
        label_piece = "".join(str(w.get("text", "")) for w in label_words)
        clean_piece = _clean(label_piece)
        starts_row = bool(clean_piece and _ROW_START.search(clean_piece))
        if starts_row:
            current = {"label": label_piece, "label_words": list(label_words),
                       "values": [None] * (len(columns) - 1)}
            rows.append(current)
        elif current and label_words:
            current["label"] += label_piece
            current["label_words"].extend(label_words)
        if numeric and current is None:
            failures.append("numeric_words_before_first_row")
            continue
        for word in numeric:
            cx = (float(word["x0"]) + float(word["x1"])) / 2
            ci = min(range(1, len(centers)), key=lambda i: abs(centers[i] - cx))
            # 数值中心必须落在该列与相邻列中线构成的槽内。
            left = (centers[ci - 1] + centers[ci]) / 2
            right = (centers[ci] + centers[ci + 1]) / 2 if ci + 1 < len(centers) else float(page.get("width_pt", 10**9))
            if not (left <= cx <= right):
                failures.append(f"numeric_word_outside_column_slot:{word.get('text')}")
                continue
            slot = ci - 1
            if current["values"][slot] is not None:
                failures.append(f"duplicate_numeric_assignment:r{len(rows)}c{ci}")
                continue
            current["values"][slot] = {"raw_value": str(word.get("text", "")),
                                        "bbox": [float(word["x0"]), float(word["top"]),
                                                 float(word["x1"]), float(word["bottom"])]}
            assigned_numeric_tokens += 1
    for row in rows:
        row["label"] = _clean(row["label"])
        row["label_bbox"] = _union_bbox(row.pop("label_words"))
    if len(rows) < 5 or len({r["label"] for r in rows}) < 5:
        failures.append("native_distinct_row_labels_below_5")
    if assigned_numeric_tokens != native_numeric_tokens:
        failures.append("native_numeric_token_accounting_mismatch")
    return {"ok": not failures, "reasons": sorted(set(failures)), "method": "native_word_xy_rows_v1",
            "period": period, "column_count": len(columns), "columns": columns,
            "column_centers": centers, "column_header_paths": paths,
            "header_cells": chosen, "rows": rows, "data_rows": len(rows),
            "distinct_row_labels": len({r["label"] for r in rows}),
            "native_numeric_tokens": native_numeric_tokens,
            "assigned_numeric_tokens": assigned_numeric_tokens}


def _fragment_assessment(doc: dict, page: dict, scope: str | None = None) -> dict:
    rows = _cells_by_row(doc)
    data_start = _first_data_row(rows)
    header_rows = data_start
    labels = [_clean(_cell_text(row[0])) for row in rows[data_start:] if row]
    labels = [x for x in labels if x]
    multi_value = []
    # 如果第一列本身已坍缩，data_start 无法从行标签定位；因此对全表
    # 检查“同格多数值”，表头中的年份只会命中一个数值，不会误报。
    for row in rows:
        for cell in row:
            raw = _cell_text(cell)
            if _numeric_token_count(raw) > 1:
                multi_value.append({"row": cell["row"], "col": cell["col"], "sample": raw[:120]})
    header_text = "|".join(_clean(_cell_text(c)) for row in rows[:header_rows] for c in row)
    reasons = []
    if int(doc.get("column_count", 0)) < 8:
        reasons.append("column_count_below_8")
    if header_rows < 2:
        reasons.append("header_rows_below_2")
    if len(labels) < 5 or len(set(labels)) < 5:
        reasons.append("distinct_row_labels_below_5")
    if multi_value:
        reasons.append("multiple_numeric_tokens_collapsed_into_single_cell")
    if not any(anchor in header_text for anchor in _HEADER_ANCHORS[:2]):
        reasons.append("capital_header_missing")
    if "资本公积" not in header_text:
        reasons.append("capital_reserve_header_missing")
    if "未分配利润" not in header_text:
        reasons.append("retained_earnings_header_missing")
    result = {"rows": rows, "header_rows": header_rows, "data_rows": max(0, len(rows) - data_start),
            "distinct_row_labels": len(set(labels)), "multi_value_cells": multi_value,
            "column_header_paths": _header_paths(rows, header_rows), "reasons": reasons,
            "period": _period(rows, page), "effective_column_count": int(doc.get("column_count", 0)),
            "reconstruction_method": None, "native_reconstruction": None}
    if reasons and scope in {"consolidated", "parent"}:
        reconstructed = _native_reconstruct_wide(page, scope)
        result["native_reconstruction"] = reconstructed
        if reconstructed.get("ok"):
            result.update({
                "raw_failure_reasons": reasons,
                "header_rows": max(2, len(max(reconstructed["column_header_paths"], key=len))),
                "data_rows": reconstructed["data_rows"],
                "distinct_row_labels": reconstructed["distinct_row_labels"],
                "multi_value_cells": [],
                "column_header_paths": reconstructed["column_header_paths"],
                "reasons": [],
                "period": reconstructed["period"],
                "effective_column_count": reconstructed["column_count"],
                "reconstruction_method": reconstructed["method"],
            })
        else:
            result["reasons"] = sorted(set(reasons + reconstructed.get("reasons", [])))
    return result


def _row_role(label: str) -> str | None:
    value = _clean(label)
    if value.startswith("二、") and any(x in value for x in ("期初余额", "年初余额")):
        return "opening"
    if value.startswith("三、") and "增减变动金额" in value:
        return "change"
    if value.startswith("四、") and any(x in value for x in ("期末余额", "年末余额")):
        return "ending"
    return None


def _period_for_role(period_text: str | None, role: str) -> dict | None:
    if not period_text:
        return None
    match = re.search(r"(20\d{2})年(半年度|度)", _clean(period_text))
    if not match:
        return None
    year = int(match.group(1))
    end = f"{year:04d}-06-30" if match.group(2) == "半年度" else f"{year:04d}-12-31"
    if role == "opening":
        return {"kind": "instant", "start": None, "end": f"{year:04d}-01-01"}
    if role == "ending":
        return {"kind": "instant", "start": None, "end": end}
    return {"kind": "duration", "start": f"{year:04d}-01-01", "end": end}


def _column_concept(path: list[str]) -> str | None:
    cleaned = [_header_key(x) for x in path]
    if "其他权益工具" in cleaned and cleaned[-1] == "其他":
        return "other_equity_instruments_other"
    return _EQUITY_CONCEPTS.get(cleaned[-1] if cleaned else "")


def _semantic_rows(doc: dict, assessment: dict, page: dict, evidence: list,
                   logical_id: str, engine_version: str) -> list[dict]:
    reconstructed = assessment.get("native_reconstruction") or {}
    if reconstructed.get("ok"):
        return reconstructed["rows"]
    out = []
    for row in assessment["rows"][assessment["header_rows"]:]:
        if not row:
            continue
        label = _clean(_cell_text(row[0]))
        if not label:
            continue
        label_bbox = row[0].get("bbox")
        label_ev = None
        if label_bbox and _cell_matches_native_words(page, label_bbox, label):
            label_ev = _add_evidence(
                evidence, source_kind="table_cell", granularity="cell", page=doc["physical_page"],
                bbox=label_bbox, object_id=logical_id, full_sentence=label, row_label=label,
                engine_version=engine_version, reason="C3 equity row label matched native words")
        values = []
        for ci, cell in enumerate(row[1:], 1):
            raw = _cell_text(cell)
            value = None
            if _decimal(raw) is not None:
                bbox = cell.get("bbox")
                value_ev = None
                if bbox and _cell_matches_native_words(page, bbox, raw):
                    value_ev = _add_evidence(
                        evidence, source_kind="table_cell", granularity="cell", page=doc["physical_page"],
                        bbox=bbox, object_id=logical_id, full_sentence=raw, row_label=label,
                        column_headers=assessment["column_header_paths"][ci], engine_version=engine_version,
                        reason="C3 equity value matched native words inside physical cell bbox")
                value = {"raw_value": raw, "bbox": bbox, "evidence_ref": value_ev}
            values.append(value)
        out.append({"label": label, "label_bbox": label_bbox,
                    "label_evidence_ref": label_ev, "values": values})
    return out


def _build_c3_facts(identity: dict, logical_id: str, scope: str, doc: dict, assessment: dict,
                    page: dict, evidence: list, engine_version: str, context: dict) -> tuple[list, list, list]:
    rows = _semantic_rows(doc, assessment, page, evidence, logical_id, engine_version)
    role_rows = {_row_role(row["label"]): row for row in rows if _row_role(row["label"])}
    facts, candidates, validations = [], [], []
    period_text = assessment.get("period")
    period_ev = context.get("period_evidence", {}).get(doc["table_fragment_id"])
    comparison = context.get("comparison_state", {}).get(doc["table_fragment_id"], "unknown")
    required_context = bool(context.get("unit_evidence") and context.get("accounting_evidence")
                            and context.get("title_evidence") and period_ev
                            and semantics.scale_multiplier(context.get("unit")) is not None
                            and context.get("currency") == "CNY")
    if set(role_rows) != {"opening", "change", "ending"}:
        validations.append({"check": f"equity_open_change_close:{scope}:{period_text}:rows",
                            "result": "insufficient", "inputs": {}, "difference": None})
        return facts, candidates, validations
    for ci in range(1, assessment["effective_column_count"]):
        path = assessment["column_header_paths"][ci]
        concept = _column_concept(path)
        cells = {role: role_rows[role]["values"][ci - 1] for role in ("opening", "change", "ending")}
        decimals = {role: _decimal(cell["raw_value"]) if cell else None for role, cell in cells.items()}
        check_name = f"equity_open_change_close:{scope}:{period_text}:c{ci}:{concept or 'unmapped'}"
        if not concept or any(value is None for value in decimals.values()):
            validations.append({"check": check_name, "result": "insufficient",
                                "inputs": {k: (str(v) if v is not None else None) for k, v in decimals.items()},
                                "difference": None})
            continue
        difference = decimals["opening"] + decimals["change"] - decimals["ending"]
        passed = difference == 0
        validations.append({"check": check_name, "result": "passed" if passed else "failed",
                            "inputs": {k: str(v) for k, v in decimals.items()}, "difference": str(difference)})
        for role in ("opening", "change", "ending"):
            cell = cells[role]
            row = role_rows[role]
            evidence_ok = bool(cell.get("evidence_ref") and row.get("label_evidence_ref")
                               and assessment.get("column_header_evidence", {}).get(str(ci)))
            accepted = bool(passed and required_context and evidence_ok)
            observation_id = (f"{identity.get('document_id', 'document')}/{logical_id}/"
                              f"p{doc['physical_page']}/{period_text}/c{ci}/{role}")
            obs = {
                "observation_id": observation_id, "source_kind": "table_cell",
                "original_label": row["label"],
                "concept_mapping": {"concept": concept, "status": "accepted"},
                "raw_value": cell["raw_value"], "numeric_value": str(decimals[role]),
                "currency": context.get("currency"), "scale": context.get("unit"),
                "normalized_value": semantics.normalize_decimal(cell["raw_value"], context.get("unit")) if accepted else None,
                "transformation": {"multiplier": str(semantics.scale_multiplier(context.get("unit"))),
                                   "rule_version": "c32-equity-explicit-scale-v1",
                                   "input_refs": [context["unit_evidence"]]} if accepted else {},
                "quantity_type": "amount", "period": _period_for_role(period_text, role),
                "entity_scope": scope, "accounting_basis": "CAS" if context.get("accounting_evidence") else None,
                "restatement_status": "as_reported" if comparison == "current" else "comparative_reported",
                "comparison_state": f"{comparison}_{role}",
                "disclosure_version": {"document_id": identity.get("document_id", "document"),
                                       "disclosed_at": None, "is_revision": False},
                "scope_conditions": {"continuing": None, "tax_inclusive": None, "excludes": []},
                "business_dimensions": {"equity_column_path": path, "movement_role": role},
                "evidence_refs": {
                    "value": [cell["evidence_ref"]] if cell.get("evidence_ref") else [],
                    "row_label": [row["label_evidence_ref"]] if row.get("label_evidence_ref") else [],
                    "column_headers": [assessment.get("column_header_evidence", {}).get(str(ci))]
                    if assessment.get("column_header_evidence", {}).get(str(ci)) else [],
                    "period": [period_ev] if period_ev else [],
                    "unit_currency": [context["unit_evidence"]] if context.get("unit_evidence") else [],
                    "entity_scope": [context["title_evidence"]] if context.get("title_evidence") else [],
                    "accounting_basis": [context["accounting_evidence"]] if context.get("accounting_evidence") else [],
                    "footnote": [],
                },
                "quality": {"text": "checked" if evidence_ok else "needs_review",
                            "structure": "checked", "semantics": "checked" if required_context else "needs_review",
                            "normalization": "computed_from_candidate" if accepted else "none",
                            "eligible_for_calculation": accepted},
                "usage_eligibility": {"readable": True, "traceable": bool(cell.get("evidence_ref")),
                                      "citable": accepted, "computable": accepted},
                "status": "accepted_fact" if accepted else "fact_candidate",
                "statement_type": "changes_in_equity", "logical_table_id": logical_id,
                "physical_fragment_id": doc["table_fragment_id"], "physical_page": doc["physical_page"],
                "cell_bbox": cell.get("bbox"), "value_evidence_ref": cell.get("evidence_ref"),
                "equity_movement_role": role, "column_header_path": path,
                "validation_ref": check_name,
            }
            (facts if accepted else candidates).append(obs)
    return facts, candidates, validations


def _write_html(path: Path, logical: dict, docs: list) -> None:
    bits = ["<!doctype html><html lang='zh-CN'><meta charset='utf-8'>",
            f"<title>{html.escape(logical['caption'])}</title>",
            "<style>table{border-collapse:collapse;margin:1em 0}td,th{border:1px solid #999;padding:4px;vertical-align:top}</style><body>",
            f"<h1>{html.escape(logical['caption'])}</h1>",
            "<p>C3 结构视图；仅 facts 中通过完整证据门及期初+增减=期末校验的关键单元格可计算，其余数值不可计算。</p>"]
    for doc in docs:
        bits.append(f"<h2>{html.escape(doc['table_fragment_id'])} · 物理页 {doc['physical_page']}</h2><table>")
        assessment = next((a for a in logical.get("fragment_assessments", [])
                           if a["fragment_id"] == doc["table_fragment_id"]), {})
        reconstructed = assessment.get("native_reconstruction") or {}
        if reconstructed.get("ok"):
            bits.append("<tr>" + "".join(f"<th>{html.escape(x)}</th>" for x in reconstructed["columns"]) + "</tr>")
            for row in reconstructed["rows"]:
                values = [cell.get("raw_value", "") if cell else "" for cell in row["values"]]
                bits.append("<tr><td>" + html.escape(row["label"]) + "</td>"
                            + "".join(f"<td>{html.escape(v)}</td>" for v in values) + "</tr>")
        else:
            for row in _cells_by_row(doc):
                bits.append("<tr>" + "".join(f"<td>{html.escape(_cell_text(c))}</td>" for c in row) + "</tr>")
        bits.append("</table>")
    bits.append("</body></html>")
    path.write_text("".join(bits), encoding="utf-8")


def build_equity_statements(root, pages: list, table_items: list, evidence: list, review: list,
                            engine_version: str, *, identity: dict | None = None,
                            start_index: int = 1) -> dict:
    """生成权益逻辑表，并仅对通过勾稽的期初/增减/期末单元格产出 facts。"""
    root = Path(root)
    identity = identity or {}
    page_by_number = {p["physical_page"]: p for p in pages}
    starts = _starts(pages)
    # 附注是权益变动表序列的硬停止边界。部分上交所报告不单列
    # “财务报表附注”，而是直接从“三、公司基本情况”开始附注。
    stops = []
    for page in pages:
        for line in page.get("lines", []):
            compact = _clean(line.get("text", ""))
            if compact == "财务报表附注" or re.match(r"^[一二三四五六七八九十]+[、.]公司基本情况$", compact):
                stops.append({"physical_page": page["physical_page"], "top": float(line["bbox"][1]), "title": compact})
    boundaries = sorted(starts + stops, key=lambda x: (x["physical_page"], x["top"]))
    logical_tables, accepted_fragments, needs_review_fragments = [], set(), set()
    facts, candidates, validations = [], [], []
    accounting_line = _line_match(pages, semantics.is_cas_compliance_statement)

    for start in starts:
        start_pos = (start["physical_page"], start["top"])
        end = next((b for b in boundaries if (b["physical_page"], b["top"]) > start_pos), None)
        end_pos = (end["physical_page"], end["top"]) if end else (10**9, 0)
        selected = [t for t in table_items
                    if start_pos < (t["physical_page"], float(t["bbox"][1])) < end_pos
                    and int(t.get("columns", 0)) >= 8]
        if not selected:
            continue
        docs = [json.loads((root / item["json"]).read_text(encoding="utf-8")) for item in selected]
        lid = f"logical-table-{start_index + len(logical_tables):04d}"
        title_ev = _add_evidence(evidence, source_kind="text_span", granularity="span",
                                 page=start["physical_page"], bbox=start["bbox"], object_id=lid,
                                 full_sentence=start.get("source_title", start["title"]),
                                 engine_version=engine_version,
                                 reason="bounded-normalized statutory changes-in-equity title")
        selected_pages = [page_by_number[d["physical_page"]] for d in docs]
        unit_line = next((line for page in selected_pages for line in page.get("lines", [])
                          if semantics.parse_unit_currency(line.get("text", ""))[0] is not None), None)
        unit, currency = semantics.parse_unit_currency(unit_line.get("text", "")) if unit_line else (None, None)
        unit_ev = None
        if unit_line:
            unit_ev = _add_evidence(
                evidence, source_kind="text_span", granularity="span",
                page=next(page["physical_page"] for page in selected_pages if unit_line in page.get("lines", [])),
                bbox=unit_line["bbox"], object_id=lid, full_sentence=unit_line["text"],
                engine_version=engine_version, reason="C3 equity statement unit and currency declaration")
        accounting_ev = None
        if accounting_line:
            accounting_ev = _add_evidence(
                evidence, source_kind="text_span", granularity="span", page=accounting_line["physical_page"],
                bbox=accounting_line["bbox"], object_id=lid, full_sentence=accounting_line["text"],
                engine_version=engine_version, reason="C3 accounting basis declaration")
        assessments = [_fragment_assessment(d, page_by_number.get(d["physical_page"], {}), start["entity_scope"])
                       for d in docs]
        widths = {a["effective_column_count"] for a in assessments}
        global_reasons = []
        if len(widths) != 1:
            global_reasons.append("column_count_inconsistent_across_fragments")

        header_evidence = []
        for doc, assessment in zip(docs, assessments):
            assessment["column_header_evidence"] = {}
            reconstructed = assessment.get("native_reconstruction") or {}
            if reconstructed.get("ok"):
                for i, cell in enumerate(reconstructed["header_cells"], 1):
                    # 这些单元格本身由 page.words 构建；竖排子表头的 bbox 可与
                    # 上层“其他权益工具”重叠，不再用整框字符串相等作二次门。
                    header_ev = _add_evidence(
                        evidence, source_kind="table_cell", granularity="cell", page=doc["physical_page"],
                        bbox=cell["bbox"], object_id=lid, full_sentence=cell["text"],
                        column_headers=[cell["label"]], engine_version=engine_version,
                            reason="C3 multi-level header reconstructed directly from native word coordinates")
                    header_evidence.append(header_ev)
                    assessment["column_header_evidence"][str(i)] = header_ev
                header_rows_iter = []
            else:
                header_rows_iter = assessment["rows"][:assessment["header_rows"]]
            for row in header_rows_iter:
                for cell in row:
                    raw = _cell_text(cell)
                    if not _clean(raw):
                        continue
                    bbox = cell.get("bbox")
                    if bbox and _cell_matches_native_words(page_by_number.get(doc["physical_page"]), bbox, raw):
                        header_ev = _add_evidence(
                            evidence, source_kind="table_cell", granularity="cell", page=doc["physical_page"],
                            bbox=bbox, object_id=lid, full_sentence=raw,
                            column_headers=[raw], engine_version=engine_version,
                            reason="changes-in-equity header matched native words inside physical cell bbox")
                        header_evidence.append(header_ev)
                        if int(cell.get("col", 0)) > 0:
                            assessment["column_header_evidence"][str(cell["col"])] = header_ev
                    else:
                        global_reasons.append(f"header_cell_not_native_verified:{doc['table_fragment_id']}:r{cell['row']}c{cell['col']}")
            if reconstructed.get("ok"):
                reconstruction_evidence = []
                for ri, row in enumerate(reconstructed["rows"], 1):
                    label_ev = _add_evidence(
                        evidence, source_kind="table_cell", granularity="cell", page=doc["physical_page"],
                        bbox=row["label_bbox"], object_id=lid, full_sentence=row["label"], row_label=row["label"],
                        engine_version=engine_version, reason="C3 row label reconstructed from native word coordinates")
                    row["label_evidence_ref"] = label_ev
                    reconstruction_evidence.append(label_ev)
                    for ci, cell in enumerate(row["values"], 1):
                        if not cell:
                            continue
                        value_ev = _add_evidence(
                            evidence, source_kind="table_cell", granularity="cell", page=doc["physical_page"],
                            bbox=cell["bbox"], object_id=lid, full_sentence=cell["raw_value"],
                            row_label=row["label"], column_headers=reconstructed["column_header_paths"][ci],
                            engine_version=engine_version,
                            reason="C3 value assigned to a unique column slot from native word coordinates")
                        cell["evidence_ref"] = value_ev
                        reconstruction_evidence.append(value_ev)
                reconstructed["evidence_refs"] = reconstruction_evidence

        reasons = sorted(set(global_reasons + [r for a in assessments for r in a["reasons"]]))
        structure_ok = not reasons
        period_evidence = {}
        comparison_state = {}
        period_years = []
        for assessment in assessments:
            match = re.search(r"(20\d{2})年", assessment.get("period") or "")
            period_years.append(int(match.group(1)) if match else None)
        latest_year = max((year for year in period_years if year is not None), default=None)
        for doc, assessment, period_year in zip(docs, assessments, period_years):
            comparison_state[doc["table_fragment_id"]] = (
                "current" if latest_year is not None and period_year == latest_year else "comparative"
            )
            period_line = next((line for line in page_by_number[doc["physical_page"]].get("lines", [])
                                if _clean(line.get("text", "")) == _clean(assessment.get("period") or "")), None)
            if period_line:
                period_evidence[doc["table_fragment_id"]] = _add_evidence(
                    evidence, source_kind="text_span", granularity="span", page=doc["physical_page"],
                    bbox=period_line["bbox"], object_id=lid, full_sentence=period_line["text"],
                    engine_version=engine_version, reason="C3 equity statement period header")
        context_failure_reasons = []
        if semantics.scale_multiplier(unit) is None:
            context_failure_reasons.append("unsupported_or_missing_unit_scale")
        if currency != "CNY":
            context_failure_reasons.append("currency_not_explicit_cny")
        if not accounting_ev:
            context_failure_reasons.append("accounting_basis_evidence_missing")
        if not title_ev:
            context_failure_reasons.append("title_evidence_missing")
        if not period_evidence:
            context_failure_reasons.append("period_evidence_missing")
        context = {"unit": unit, "currency": currency,
                   "unit_evidence": unit_ev, "accounting_evidence": accounting_ev,
                   "title_evidence": title_ev, "period_evidence": period_evidence,
                   "comparison_state": comparison_state,
                   "context_failure_reasons": context_failure_reasons}
        logical_facts, logical_candidates, logical_validations = [], [], []
        if structure_ok:
            for doc, assessment in zip(docs, assessments):
                f, c, v = _build_c3_facts(
                    identity, lid, start["entity_scope"], doc, assessment,
                    page_by_number[doc["physical_page"]], evidence, engine_version, context
                )
                logical_facts.extend(f); logical_candidates.extend(c); logical_validations.extend(v)
        facts.extend(logical_facts); candidates.extend(logical_candidates); validations.extend(logical_validations)
        by_period = {}
        for doc, assessment in zip(docs, assessments):
            key = assessment["period"] or "period_unresolved"
            by_period.setdefault(key, []).append(doc["table_fragment_id"])
        subgroups = [{"subgroup_id": f"{lid}-period-{i:02d}", "fragment_ids": ids,
                      "period": period if period != "period_unresolved" else None,
                      "entity_scope": start["entity_scope"], "basis": "CAS"}
                     for i, (period, ids) in enumerate(by_period.items(), 1)]
        logical = {
            "logical_table_id": lid, "caption": start["title"],
            "fragments": [d["table_fragment_id"] for d in docs],
            "join_evidence": ["exact statement title boundary", "fragment order follows physical pages",
                              "periods modeled as separate subgroups", "multi-level headers preserved as column paths"],
            "subgroups": subgroups,
            "forbidden_join_notes": [f"next statement boundary: {end['title']}" if end else "end of report"],
            "candidate_status": "accepted_by_rules" if structure_ok else "needs_review",
            "html_path": f"表格/{lid}.html", "json_path": f"表格/{lid}.json",
            "statement_type": "changes_in_equity", "entity_scope": start["entity_scope"],
            "accepted_scope": "validated_key_cells_only" if logical_facts else ("structure_only" if structure_ok else "none"),
            "eligible_for_calculation": False, "all_cells_eligible": False,
            "facts_emitted": len(logical_facts), "candidates_emitted": len(logical_candidates),
            "c3_validation_results": logical_validations,
            "structure_complete": structure_ok, "structure_failure_reasons": reasons,
            "context_failure_reasons": context_failure_reasons,
            "title_evidence_ref": title_ev, "header_evidence_refs": header_evidence,
            "fragment_assessments": [
                {"fragment_id": d["table_fragment_id"], "physical_page": d["physical_page"],
                 "period": a["period"], "row_count": d.get("row_count"), "column_count": d.get("column_count"),
                 "effective_column_count": a["effective_column_count"],
                 "header_rows": a["header_rows"], "data_rows": a["data_rows"],
                 "distinct_row_labels": a["distinct_row_labels"],
                 "column_header_paths": a["column_header_paths"],
                 "multi_value_cells": a["multi_value_cells"], "failure_reasons": a["reasons"],
                 "raw_failure_reasons": a.get("raw_failure_reasons", []),
                 "reconstruction_method": a.get("reconstruction_method"),
                 "native_reconstruction": a.get("native_reconstruction")}
                for d, a in zip(docs, assessments)
            ],
        }
        exports.write_json(root / "表格" / f"{lid}.json", logical)
        _write_html(root / "表格" / f"{lid}.html", logical, docs)
        logical_tables.append(logical)

        fact_fragments = {fact["physical_fragment_id"] for fact in logical_facts}
        for item, doc in zip(selected, docs):
            doc["belongs_to_logical_table"] = lid
            doc["eligible_for_calculation"] = False
            doc["structure_status"] = "accepted_by_rules" if structure_ok else "needs_review"
            doc["accepted_scope"] = (
                "validated_key_cells_only" if doc["table_fragment_id"] in fact_fragments
                else ("structure_only" if structure_ok else "none")
            )
            assessment = next(a for d, a in zip(docs, assessments) if d["table_fragment_id"] == doc["table_fragment_id"])
            if (assessment.get("native_reconstruction") or {}).get("ok"):
                doc["c2_reconstruction"] = assessment["native_reconstruction"]
            exports.write_json(root / item["json"], doc)
            item["belongs_to_logical_table"] = lid
            item["eligible_for_calculation"] = False
            item["structure_status"] = doc["structure_status"]
            if structure_ok:
                accepted_fragments.add(item["table_fragment_id"])
            else:
                needs_review_fragments.add(item["table_fragment_id"])
            for q in review:
                if q.get("object_ref") == item["json"]:
                    q["status"] = "open"
                    if structure_ok:
                        if doc["table_fragment_id"] in fact_fragments:
                            q["reason"] = "C3 仅期初、增减、期末三类关键单元格通过逐列勾稽；其他权益明细仍未获得计算资格"
                            q["next_step"] = "复核其余明细行；仅 facts 中明确标记 computable 的单元格可引用计算"
                        else:
                            q["reason"] = "C3 权益变动表结构已通过，但没有满足完整证据与逐列勾稽门的关键单元格"
                            q["next_step"] = "复核期间、单位、准则和关键行；未升格数值不得引用计算"
                    else:
                        q["reason"] = "C3 权益变动表原始网格与坐标重建均未通过：" + ", ".join(reasons[:6])
                        q["next_step"] = "人工复核表头与行边界；未解决前不得升格"

    reconstructed_fragments = [
        a["fragment_id"] for logical in logical_tables for a in logical["fragment_assessments"]
        if a.get("reconstruction_method") == "native_word_xy_rows_v1" and not a.get("failure_reasons")
    ]
    fact_fragments = sorted({fact["physical_fragment_id"] for fact in facts})
    return {"logical_tables": logical_tables, "facts": facts, "candidates": candidates,
            "validations": validations,
            "accepted_structure_fragments": sorted(accepted_fragments),
            "needs_review_fragments": sorted(needs_review_fragments),
            "native_reconstructed_fragments": sorted(reconstructed_fragments),
            "fact_fragments": fact_fragments,
            "statement_titles_found": [s["title"] for s in starts]}
