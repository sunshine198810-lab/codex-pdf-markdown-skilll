#!/usr/bin/env python3
"""汇总多个研究包的能力结果与安全不变量，不把保守拒绝误记为解析成功。"""

from __future__ import annotations

import argparse
import json
import re
import sys
from decimal import Decimal
from pathlib import Path

from pipeline import naming, semantics


MAIN_TITLES = naming.MAIN_STATEMENT_TITLES
REQUIRED_EVIDENCE = ("value", "row_label", "period", "unit_currency", "entity_scope", "accounting_basis")


def _read_json(path: Path, default=None):
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _title_variant(line: str) -> str | None:
    return naming.canonical_statement_title(line, MAIN_TITLES)[0]


def _title_inventory(root: Path) -> dict:
    exact, variants = set(), set()
    for path in sorted((root / "正文").glob("page-*.md")):
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            compact = re.sub(r"\s+", "", line).lstrip("#>")
            normalized = _title_variant(line)
            if compact in MAIN_TITLES:
                exact.add(compact)
            elif normalized:
                variants.add(compact)
    return {"exact": sorted(exact), "normalized_variants": sorted(variants)}


def _logical_tables(root: Path) -> list[dict]:
    out = []
    for path in sorted((root / "表格").glob("logical-table-*.json")):
        item = _read_json(path)
        if item:
            out.append(item)
    return out


def _equity_group_failures(facts: list[dict]) -> int:
    groups: dict[tuple, dict[str, Decimal]] = {}
    for fact in facts:
        if fact.get("statement_type") != "changes_in_equity":
            continue
        key = (fact.get("logical_table_id"), fact.get("physical_fragment_id"),
               tuple(fact.get("column_header_path") or []))
        groups.setdefault(key, {})[fact.get("equity_movement_role")] = Decimal(fact["numeric_value"])
    return sum(
        set(values) != {"opening", "change", "ending"}
        or values["opening"] + values["change"] != values["ending"]
        for values in groups.values()
    )


def audit_package(root: Path) -> dict:
    root = root.resolve()
    manifest = _read_json(root / "索引/manifest.json", {})
    run = _read_json(root / "_internal/run.json", {})
    quality = _read_json(root / "索引/quality.json", {})
    facts = _read_jsonl(root / "数据/facts.jsonl")
    candidates = _read_jsonl(root / "数据/candidates.jsonl")
    evidence = _read_jsonl(root / "索引/evidence.jsonl")
    reviews = _read_jsonl(root / "索引/review_queue.jsonl")
    logical = _logical_tables(root)
    title_inventory = _title_inventory(root)
    evidence_ids = {item.get("evidence_id") for item in evidence}
    fact_ids = [item.get("observation_id") for item in facts]
    missing_required = 0
    dangling_required = 0
    ineligible_facts = 0
    unusable_row_labels = 0
    for fact in facts:
        refs = fact.get("evidence_refs") or {}
        required = REQUIRED_EVIDENCE + (("column_headers",)
                                        if fact.get("statement_type") == "changes_in_equity" else ())
        for kind in required:
            values = refs.get(kind) or []
            if not values:
                missing_required += 1
            dangling_required += sum(ref not in evidence_ids for ref in values)
        if not fact.get("quality", {}).get("eligible_for_calculation") \
                or not fact.get("usage_eligibility", {}).get("computable"):
            ineligible_facts += 1
        if not re.search(r"[\u4e00-\u9fffA-Za-z0-9]", str(fact.get("original_label") or "")):
            unusable_row_labels += 1
    main = [item for item in facts if item.get("statement_type") != "changes_in_equity"]
    equity = [item for item in facts if item.get("statement_type") == "changes_in_equity"]
    main_logical = [item for item in logical if item.get("statement_type") != "changes_in_equity"]
    equity_logical = [item for item in logical if item.get("statement_type") == "changes_in_equity"]
    visual_candidate_statements = int((manifest.get("artifacts") or {}).get("visual_candidate_statements", 0))
    schema = quality.get("schema_check") or {}
    schema_failed = len(schema.get("failed") or [])
    issues = []
    auto = run.get("config", {}).get("auto_borderless") or {}
    if auto.get("status") == "failed":
        issues.append("engine_failed_conservative")
    title_candidates = set(title_inventory["exact"])
    title_candidates.update(filter(None, (_title_variant(x) for x in title_inventory["normalized_variants"])))
    linked_titles = {(_title_variant(item.get("caption", "")) or item.get("caption")) for item in main_logical}
    unlinked_titles = sorted(title_candidates - linked_titles)
    if not main_logical and title_candidates:
        issues.append("main_titles_present_but_no_logical_tables")
    elif unlinked_titles:
        issues.append("some_main_titles_present_but_not_linked")
    if main_logical and not main:
        issues.append("main_structure_without_facts")
    if equity_logical and not equity:
        issues.append("equity_structure_without_facts")
    if any(item.get("unit_scale") is not None
           and semantics.scale_multiplier(item.get("unit_scale")) is None for item in main_logical):
        issues.append("unsupported_unit_scale")
    if any(not item.get("evidence_refs", {}).get("unit_currency") for item in main_logical):
        issues.append("missing_unit_currency_context")
    if any(not item.get("evidence_refs", {}).get("accounting_basis") for item in main_logical):
        issues.append("missing_accounting_basis_context")
    if any(len(item.get("period_columns") or []) != 2
           or any(not col.get("period") or not col.get("evidence_ref")
                  for col in item.get("period_columns") or []) for item in main_logical):
        issues.append("incomplete_period_context")
    if any(any(check.get("result") == "failed" for check in item.get("validation_results") or [])
           for item in main_logical):
        issues.append("financial_relation_failed")
    safety = {
        "schema_failed": schema_failed,
        "duplicate_observation_ids": len(fact_ids) - len(set(fact_ids)),
        "ineligible_items_in_facts": ineligible_facts,
        "unusable_row_labels_in_facts": unusable_row_labels,
        "missing_required_evidence_slots": missing_required,
        "dangling_required_evidence_refs": dangling_required,
        "equity_group_failures": _equity_group_failures(facts),
    }
    safety_ok = not any(safety.values())
    if main or equity:
        capability = "facts_emitted"
    elif main_logical or equity_logical:
        capability = "structure_only"
    elif visual_candidate_statements:
        capability = "visual_review_only"
    else:
        capability = "no_supported_logical_table"
    source = manifest.get("source_pdf") or run.get("source_pdf") or {}
    issuer = (manifest.get("identity") or {}).get("issuer_identity") or {}
    return {
        "package": str(root), "source_pdf": Path(str(source.get("path", "unknown"))).name,
        "company": issuer.get("company_name_as_reported"),
        "pipeline_version": (manifest.get("run") or {}).get("pipeline_version"),
        "pages": source.get("page_count"), "overall_state": manifest.get("overall_state"),
        "auto_borderless_status": auto.get("status"),
        "table_fragments": (manifest.get("artifacts") or {}).get("tables", 0),
        "logical_main_statements": len(main_logical), "logical_equity_statements": len(equity_logical),
        "visual_candidate_statements": visual_candidate_statements,
        "main_facts": len(main), "equity_facts": len(equity), "facts": len(facts),
        "candidates": len(candidates), "open_reviews": sum(x.get("status") == "open" for x in reviews),
        "title_inventory": title_inventory, "unlinked_main_titles": unlinked_titles,
        "capability_state": capability,
        "issue_classes": sorted(set(issues)), "safety": safety, "safety_ok": safety_ok,
    }


def render_markdown(items: list[dict]) -> str:
    lines = [
        "# 年报解析回归矩阵", "",
        "> 这是能力与安全边界审计，不是人工标注准确率。相邻年份按发行人聚集，不能当作独立样本。", "",
        "| 报告 | 页数 | 自动升级 | 逻辑主表 | 视觉候选 | 权益表 | 主表 facts | 权益 facts | 能力状态 | 安全门 | 问题分类 |",
        "|---|---:|---|---:|---:|---:|---:|---:|---|---|---|",
    ]
    for item in items:
        issues = ", ".join(item["issue_classes"]) or "—"
        lines.append(
            f"| {item['source_pdf']} | {item['pages'] or '—'} | {item['auto_borderless_status'] or '—'} | "
            f"{item['logical_main_statements']} | {item['visual_candidate_statements']} | {item['logical_equity_statements']} | "
            f"{item['main_facts']} | {item['equity_facts']} | {item['capability_state']} | "
            f"{'通过' if item['safety_ok'] else '失败'} | {issues} |"
        )
    lines.extend(["", "## 安全不变量", ""])
    for item in items:
        failed = {key: value for key, value in item["safety"].items() if value}
        lines.append(f"- `{item['source_pdf']}`：" + ("全部为 0" if not failed else json.dumps(failed, ensure_ascii=False)))
    lines.extend(["", "## 解释边界", "",
                  "- `safety_ok=true` 只表示没有把不合格数据错误写入 facts，不表示报告结构化完整。",
                  "- `structure_only`、`visual_review_only` 和 `no_supported_logical_table` 都是能力缺口，不能用保守拒绝掩盖。",
                  "- 标题候选来自逐页 Markdown 的确定性扫描，仅用于失败分类，不会反向生成 facts。", ""])
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("packages", nargs="+", type=Path)
    parser.add_argument("--json", type=Path, required=True)
    parser.add_argument("--markdown", type=Path, required=True)
    args = parser.parse_args(argv)
    items = [audit_package(path) for path in args.packages]
    payload = {"schema_version": "c44-regression-matrix-v1", "packages": items,
               "summary": {"packages": len(items), "safety_passed": sum(x["safety_ok"] for x in items),
                           "facts_emitted": sum(x["capability_state"] == "facts_emitted" for x in items),
                           "structure_only": sum(x["capability_state"] == "structure_only" for x in items),
                           "visual_review_only": sum(x["capability_state"] == "visual_review_only" for x in items),
                           "no_supported_logical_table": sum(x["capability_state"] == "no_supported_logical_table" for x in items)}}
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.markdown.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.markdown.write_text(render_markdown(items), encoding="utf-8")
    print(f"回归矩阵: {len(items)} 份；安全门 {payload['summary']['safety_passed']}/{len(items)}")
    print(f"JSON: {args.json}\nMarkdown: {args.markdown}")
    return 0 if payload["summary"]["safety_passed"] == len(items) else 1


if __name__ == "__main__":
    sys.exit(main())
