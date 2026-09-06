import pathlib
import sys
import unittest

_SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))

from pipeline import financials  # noqa: E402


def w(text, x0, x1, top, bottom):
    return {"text": text, "x0": float(x0), "x1": float(x1),
            "top": float(top), "bottom": float(bottom)}


def _page():
    # 模拟广核 p94：表头(项目/期末余额/期初余额)+ 数据行，数值与标签基线略错位。
    words = [
        w("项目", 118, 148, 100, 110),
        w("期末余额", 268, 318, 100, 110),
        w("期初余额", 428, 478, 100, 110),
        w("流动资产：", 55, 95, 122, 132),
        w("货币资金", 58, 98, 150, 160),
        w("20,826,720,819.03", 296, 338, 153, 162),  # 期末（基线错位）
        w("17,026,296,200.85", 456, 498, 153, 162),  # 期初
        w("流动资产合计", 55, 100, 178, 188),
        w("77,891,455,021.68", 296, 338, 180, 189),
        w("72,711,369,859.36", 456, 498, 180, 189),
    ]
    return {"physical_page": 1, "words": words}


def _oversegmented_doc():
    # pdfplumber 把 3 列过切成 9 列的表头/数据网格。
    cells = []
    headers = ["", "项目", "", "期末余额", "", "", "期初余额", "", ""]
    for ci, t in enumerate(headers):
        cells.append({"row": 0, "col": ci, "text": t, "raw_value": t,
                      "bbox": [100 + ci * 40, 100, 130 + ci * 40, 110]})
    data = [("货币资金", "20,826,720,819.03", "17,026,296,200.85"),
            ("流动资产合计", "77,891,455,021.68", "72,711,369,859.36")]
    for ri, (label, v1, v2) in enumerate(data, 1):
        cells.append({"row": ri, "col": 1, "text": label, "raw_value": label,
                      "bbox": [55, 150 + ri * 28, 100, 160 + ri * 28]})
        cells.append({"row": ri, "col": 2, "text": v1, "raw_value": v1,
                      "bbox": [296, 153 + ri * 27, 338, 162 + ri * 27]})
        cells.append({"row": ri, "col": 6, "text": v2, "raw_value": v2,
                      "bbox": [456, 153 + ri * 27, 498, 162 + ri * 27]})
    return {"table_fragment_id": "table-0001", "physical_page": 1,
            "column_count": 9, "row_count": 3, "bbox": [50, 95, 540, 200],
            "structure_source": "pdfplumber_ruled_table", "cells": cells}


class TestNativeRebuildOversegmented(unittest.TestCase):
    def test_rebuilds_oversegmented_grid_to_three_columns(self):
        rebuilt = financials._native_rebuild_oversegmented(_oversegmented_doc(), _page())
        self.assertIsNotNone(rebuilt)
        self.assertEqual(rebuilt["column_count"], 3)
        self.assertEqual(rebuilt["reconstruction_method"], "native_xy_oversegment_v1")
        # 表头行：col0=项目 col1=期末余额 col2=期初余额
        header = [c for c in rebuilt["cells"] if c["row"] == 0]
        by_col = {c["col"]: c["text"] for c in header}
        self.assertEqual(by_col[0], "项目")
        self.assertEqual(by_col[1], "期末余额")
        self.assertEqual(by_col[2], "期初余额")
        # 货币资金行两值正确（按标签定位，不硬编码行号）
        money_row = {}
        for c in rebuilt["cells"]:
            if c["col"] == 0 and c["text"] == "货币资金":
                money_row = {cc["col"]: cc["text"] for cc in rebuilt["cells"] if cc["row"] == c["row"]}
        self.assertEqual(money_row.get(0), "货币资金")
        self.assertEqual(money_row.get(1), "20,826,720,819.03")
        self.assertEqual(money_row.get(2), "17,026,296,200.85")

    def test_normal_grid_untouched(self):
        doc = _oversegmented_doc()
        doc["column_count"] = 3
        self.assertIsNone(financials._native_rebuild_oversegmented(doc, _page()))

    def test_no_period_anchors_and_few_numerics_returns_none(self):
        # 页面上数值不足 / 聚不出两值列 → 保守返回 None
        page = _page()
        page["words"] = [w for w in page["words"] if "货币资金" not in w["text"]
                         and "20,826" not in w["text"] and "17,026" not in w["text"]]
        page["words"] += [w("存货", 60, 90, 300, 310), w("123", 290, 310, 300, 310)]
        self.assertIsNone(financials._native_rebuild_oversegmented(_oversegmented_doc(), page))


if __name__ == "__main__":
    unittest.main()
