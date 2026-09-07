"""D1 非主表广度测试：分族、数值候选生成、单位继承、纯文本表与 claimed 不触碰。"""

import json
import pathlib
import sys
import tempfile
import unittest

_SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))

from pipeline import exports, general_tables  # noqa: E402


def _cell(row, col, text):
    x0 = 60 + col * 80
    x1 = x0 + 70
    return {
        "cell_id": f"g_r{row}_c{col}", "row": row, "col": col,
        "rowspan": 1, "colspan": 1, "raw_value": text, "text": text,
        "kind": "other", "bbox": [x0, 300 - row * 20, x1, 316 - row * 20],
        "evidence_refs": [], "eligible_for_calculation": False,
    }


def _note_table(tid, physical_page):
    """一张带行标签与数值的附注式表：header 行 + 3 数据行。"""
    cells = [
        _cell(0, 0, "项目"), _cell(0, 1, "期末余额"), _cell(0, 2, "期初余额"),
        _cell(1, 0, "存货"), _cell(1, 1, "1,234.56"), _cell(1, 2, "1,000.00"),
        _cell(2, 0, "应收账款"), _cell(2, 1, "98.00"), _cell(2, 2, "（100.00）"),
        _cell(3, 0, "减：坏账准备"), _cell(3, 1, "—"), _cell(3, 2, ""),
    ]
    return {
        "table_fragment_id": tid, "belongs_to_logical_table": None,
        "physical_page": physical_page, "caption": None, "units_declaration": None,
        "has_repeated_header": False, "header_rows": 0,
        "row_count": 4, "column_count": 3,
        "cells": cells, "candidate_status": "needs_review",
        "eligible_for_calculation": False,
        "bbox": [50, 240, 520, 340], "evidence_ref": f"ev-{tid}",
        "structure_source": "pdfplumber_ruled_table",
    }


def _write_items(root, root_meta):
    """写入片段文件并返回 table_items 元信息（不含 claimed）。"""
    meta = []
    for tid, page, path in root_meta:
        doc = _note_table(tid, page)
        exports.write_json(root / path, doc)
        meta.append({
            "table_fragment_id": tid, "physical_page": page,
            "bbox": doc["bbox"], "rows": doc["row_count"], "columns": doc["column_count"],
            "json": path, "html": path.replace(".json", ".html"),
            "candidate_status": "needs_review", "eligible_for_calculation": False,
            "structure_source": doc["structure_source"],
        })
    return meta


class TestGeneralTableClassification(unittest.TestCase):
    def test_notes_numeric_table_produces_ineligible_candidates_with_unit(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            (root / "表格").mkdir(parents=True)
            items = _write_items(root, [
                ("table-0100", 60, "表格/table-0100.json"),
                ("table-0200", 60, "表格/table-0200.json"),
            ])
            pages = [
                {"physical_page": 50, "lines": [{"text": "财务报表附注", "bbox": [10, 10, 200, 20]}]},
                {"physical_page": 60, "lines": [
                    {"text": "单位：人民币元", "bbox": [10, 20, 200, 30]},
                    {"text": "五、存货", "bbox": [10, 60, 200, 70]},
                ]},
            ]
            evidence, review = [], []
            # 真实管线里每个表格片段在 M1 页循环阶段就有一条 open 复核项；此处预置以验证更新。
            review.append({"review_id": "rev-gen-1", "priority": 2,
                           "object_ref": "表格/table-0100.json", "reason": "start",
                           "evidence_clip": None, "candidates": [], "methods_tried": [],
                           "next_step": "", "status": "open", "created_at": "t"})
            claimed = {"table-0200"}
            res = general_tables.build_general_candidates(
                root, pages, items, evidence, review, {"document_id": "doc1"}, "test",
                claimed_fragments=claimed,
            )
            self.assertEqual(res["fragments_classified"], 1)  # claimed 不参与
            self.assertEqual(res["families"], {"financial_notes": 1})
            self.assertEqual(res["candidate_tables"], 1)
            self.assertEqual(res["text_tables"], 0)
            cands = res["candidates"]
            # 行标签 + 数值单元格才生成候选；—/空不生成，“（100.00）”括号负号保留原值
            self.assertEqual(len(cands), 4)
            labels = {c["original_label"] for c in cands}
            self.assertEqual(labels, {"存货", "应收账款"})
            self.assertTrue(all(c["quality"]["eligible_for_calculation"] is False for c in cands))
            self.assertTrue(all(c["status"] == "fact_candidate" for c in cands))
            self.assertTrue(all(c["usage_eligibility"]["computable"] is False for c in cands))
            self.assertTrue(all(c["normalized_value"] is None for c in cands))
            # 同页单位行被继承到候选
            self.assertEqual(cands[0]["scale"], "元")
            self.assertEqual(cands[0]["currency"], "CNY")
            self.assertEqual(cands[0]["general_family"], "financial_notes")
            # 未绑定期间/主体：诚实为空（键省略，不写 null）
            self.assertIsNone(cands[0].get("period"))
            self.assertIsNone(cands[0]["entity_scope"])
            # 复核项被更新并保持 open
            note_review = [q for q in review if q.get("object_ref") == "表格/table-0100.json"]
            self.assertEqual(len(note_review), 1)
            self.assertEqual(note_review[0]["family"], "financial_notes")
            self.assertEqual(note_review[0]["status"], "open")
            # claimed 片段的 review 与文件未被触碰
            claimed_review = [q for q in review if q.get("object_ref") == "表格/table-0200.json"]
            self.assertEqual(claimed_review, [])
            claimed_doc = json.loads((root / "表格/table-0200.json").read_text(encoding="utf-8"))
            self.assertNotIn("general_family", claimed_doc)

    def test_unclassified_numeric_without_unit_line_leaves_unit_none(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            (root / "表格").mkdir(parents=True)
            items = _write_items(root, [("table-0300", 30, "表格/table-0300.json")])
            pages = [{"physical_page": 30, "lines": [{"text": "xx 经营数据（无单位行）", "bbox": [10, 10, 300, 20]}]}]
            res = general_tables.build_general_candidates(
                root, pages, items, [], [], {"document_id": "doc1"}, "test",
                claimed_fragments=set(), headings=[],
            )
            self.assertEqual(res["families"], {"unclassified_financial": 1})
            self.assertEqual(len(res["candidates"]), 4)
            self.assertIsNone(res["candidates"][0]["scale"])
            self.assertIsNone(res["candidates"][0]["currency"])
            # 单元测试不写文件失败即可；候选 readable 且不可引用
            self.assertTrue(res["candidates"][0]["usage_eligibility"]["readable"])
            self.assertFalse(res["candidates"][0]["usage_eligibility"]["citable"])

    def test_management_discussion_heading_tags_operating_family(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            (root / "表格").mkdir(parents=True)
            items = _write_items(root, [("table-0400", 20, "表格/table-0400.json")])
            pages = [{"physical_page": 20, "lines": [{"text": "第三节 管理层讨论与分析", "bbox": [10, 10, 300, 20]}]}]
            headings = [{"title": "第三节 管理层讨论与分析", "physical_page": 15}]
            res = general_tables.build_general_candidates(
                root, pages, items, [], [], {"document_id": "doc1"}, "test",
                claimed_fragments=set(), headings=headings,
            )
            self.assertEqual(res["families"], {"management_discussion_data": 1})

    def test_notes_fallback_uses_last_claimed_page_when_no_notes_heading(self):
        """无独立“财务报表附注”行时，以主表/权益表最大页+1 作为附注起点近似。"""
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            (root / "表格").mkdir(parents=True)
            items = _write_items(root, [
                ("table-0100", 60, "表格/table-0100.json"),
                ("table-0200", 45, "表格/table-0200.json"),  # 已消费的主表片段
            ])
            pages = [{"physical_page": 45, "lines": [{"text": "母公司现金流量表", "bbox": [10, 10, 200, 20]}]},
                     {"physical_page": 60, "lines": [{"text": "存货（附注无独立标题行）", "bbox": [10, 10, 300, 20]}]}]
            res = general_tables.build_general_candidates(
                root, pages, items, [], [], {"document_id": "doc1"}, "test",
                claimed_fragments={"table-0200"},
            )
            # table-0200 被消费，不参与；table-0100 在 p60 > 45+1 → financial_notes
            self.assertEqual(res["families"], {"financial_notes": 1})
            self.assertEqual(res["candidate_tables"], 1)

    def test_pure_text_table_is_classified_only_no_candidates(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            (root / "表格").mkdir(parents=True)
            doc = _note_table("table-0500", 5)
            doc["cells"] = [
                _cell(0, 0, "常用词语释义"), _cell(0, 1, ""),
                _cell(1, 0, "证监会"), _cell(1, 1, "指中国证券监督管理委员会"),
            ]
            doc["row_count"], doc["column_count"] = 2, 2
            exports.write_json(root / "表格/table-0500.json", doc)
            items = [{
                "table_fragment_id": "table-0500", "physical_page": 5,
                "bbox": doc["bbox"], "rows": 2, "columns": 2,
                "json": "表格/table-0500.json", "html": "表格/table-0500.html",
                "candidate_status": "needs_review", "eligible_for_calculation": False,
                "structure_source": "pdfplumber_ruled_table",
            }]
            pages = [{"physical_page": 5, "lines": [{"text": "第一节 释义", "bbox": [10, 10, 200, 20]}]}]
            res = general_tables.build_general_candidates(
                root, pages, items, [], [], {"document_id": "doc1"}, "test",
                claimed_fragments=set(),
            )
            self.assertEqual(res["families"], {"text_table": 1})
            self.assertEqual(res["text_tables"], 1)
            self.assertEqual(res["candidate_tables"], 0)
            self.assertEqual(res["candidates"], [])


if __name__ == "__main__":
    unittest.main()
