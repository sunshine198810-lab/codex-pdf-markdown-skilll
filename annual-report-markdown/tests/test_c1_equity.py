import json
import pathlib
import tempfile
import unittest

from pipeline import equity, exports


def _doc(fragment_id, page, rows):
    cells = []
    width = len(rows[0])
    for ri, row in enumerate(rows):
        for ci, value in enumerate(row):
            cells.append({
                "cell_id": f"{fragment_id}-r{ri}c{ci}", "row": ri, "col": ci,
                "rowspan": 1, "colspan": 1, "raw_value": value, "text": value,
                "kind": "other", "bbox": [10 + ci * 50, 50 + ri * 20, 60 + ci * 50, 70 + ri * 20],
                "evidence_refs": [],
            })
    return {"table_fragment_id": fragment_id, "physical_page": page,
            "row_count": len(rows), "column_count": width, "cells": cells,
            "candidate_status": "needs_review", "eligible_for_calculation": False}


def _native_words(doc):
    return [{"text": c["raw_value"], "x0": c["bbox"][0], "x1": c["bbox"][2],
             "top": c["bbox"][1], "bottom": c["bbox"][3]}
            for c in doc["cells"] if c["raw_value"]]


class TestC1Equity(unittest.TestCase):
    def test_prefixed_equity_title_is_bounded_normalized(self):
        self.assertEqual(equity._base_title("7. 合并所有者权益变动表"),
                         ("合并所有者权益变动表", False))
        self.assertEqual(equity._base_title("中期公司股东权益变动表（续）"),
                         ("公司股东权益变动表", True))
        self.assertEqual(equity._base_title("合并所有者权益变动表附注"), (None, False))

    def test_native_xy_rebuild_recovers_collapsed_grid(self):
        columns = equity._PARENT_COLUMNS
        centers = [100 + i * 50 for i in range(len(columns) - 1)]
        words = []
        for center, label in zip(centers, columns[1:]):
            words.append({"text": label, "x0": center - 10, "x1": center + 10,
                          "top": 60, "bottom": 70})
        labels = ["一、上年年末余额", "二、本年年初余额", "三、本年增减", "四、本年年末余额", "加：会计政策变更"]
        lines = [{"text": "2025年度", "bbox": [300, 30, 360, 40]}]
        for ri, label in enumerate(labels):
            y = 100 + ri * 20
            words.append({"text": label, "x0": 5, "x1": 55, "top": y, "bottom": y + 8})
            words.append({"text": str(ri + 1), "x0": centers[0] - 4, "x1": centers[0] + 4,
                          "top": y, "bottom": y + 8})
            lines.append({"text": label, "bbox": [5, y, 55, y + 8]})
        lines.append({"text": "法定代表人：", "bbox": [5, 210, 80, 220]})
        rebuilt = equity._native_reconstruct_wide(
            {"physical_page": 1, "width_pt": 700, "words": words, "lines": lines}, "parent"
        )
        self.assertTrue(rebuilt["ok"], rebuilt.get("reasons"))
        self.assertEqual(rebuilt["column_count"], 12)
        self.assertEqual(rebuilt["data_rows"], 5)
        self.assertEqual(rebuilt["rows"][0]["values"][0]["raw_value"], "1")

    def test_multilevel_header_is_structure_only(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            (root / "表格").mkdir()
            header = [
                ["项目", "2026年半年度", "", "", "", "", "", ""],
                ["", "归属于母公司所有者权益", "", "", "", "", "少数股东权益", "所有者权益合计"],
                ["", "实收资本(或股本)", "其他权益工具", "", "资本公积", "未分配利润", "", ""],
                ["", "", "优先股", "永续债", "", "", "", ""],
            ]
            data = [[f"{name}", "1", "", "", "2", "3", "4", "10"] for name in
                    ["一、上年期末余额", "二、本年期初余额", "三、本期增减变动", "四、本期期末余额", "加：会计政策变更"]]
            doc = _doc("table-0001", 1, header + data)
            exports.write_json(root / "表格/table-0001.json", doc)
            pages = [{"physical_page": 1, "words": _native_words(doc), "lines": [
                {"text": "合并所有者权益变动表", "bbox": [10, 10, 250, 20]},
            ]}]
            items = [{"table_fragment_id": "table-0001", "physical_page": 1,
                      "bbox": [10, 50, 410, 230], "columns": 8, "json": "表格/table-0001.json",
                      "candidate_status": "needs_review", "eligible_for_calculation": False}]
            review = [{"object_ref": "表格/table-0001.json", "status": "open", "reason": "candidate"}]
            result = equity.build_equity_statements(root, pages, items, [], review, "test", start_index=7)
            self.assertEqual(result["facts"], [])
            self.assertEqual(result["accepted_structure_fragments"], ["table-0001"])
            logical = result["logical_tables"][0]
            self.assertEqual(logical["logical_table_id"], "logical-table-0007")
            self.assertTrue(logical["structure_complete"])
            self.assertFalse(logical["eligible_for_calculation"])
            self.assertIn("其他权益工具", logical["fragment_assessments"][0]["column_header_paths"][2])
            self.assertIn("优先股", logical["fragment_assessments"][0]["column_header_paths"][2])
            self.assertEqual(review[0]["status"], "open")

    def test_collapsed_rows_are_rejected(self):
        rows = [
            ["项目", "2025年度", "", "", "", "", "", ""],
            ["", "股本", "其他权益工具", "", "资本公积", "未分配利润", "", "股东权益合计"],
            ["一、上年年末余额 二、本年年初余额 三、本期增减变动", "1 2 3", "", "", "4 5 6", "7 8 9", "", "10 11 12"],
        ]
        assessment = equity._fragment_assessment(_doc("table-x", 1, rows), {"lines": []})
        self.assertIn("multiple_numeric_tokens_collapsed_into_single_cell", assessment["reasons"])
        self.assertIn("distinct_row_labels_below_5", assessment["reasons"])

    def test_periods_remain_separate_subgroups(self):
        rows = [["项目", "2026年半年度", "", "", "", "", "", ""],
                ["", "股本", "", "", "资本公积", "未分配利润", "", "所有者权益合计"],
                ["一、a", "1", "", "", "2", "3", "", "6"], ["二、b", "1", "", "", "2", "3", "", "6"],
                ["三、c", "1", "", "", "2", "3", "", "6"], ["四、d", "1", "", "", "2", "3", "", "6"],
                ["加：e", "1", "", "", "2", "3", "", "6"]]
        prior = equity._fragment_assessment(_doc("a", 1, rows), {"lines": []})
        rows[0][1] = "2025年半年度"
        current = equity._fragment_assessment(_doc("b", 2, rows), {"lines": []})
        self.assertNotEqual(prior["period"], current["period"])


if __name__ == "__main__":
    unittest.main()
