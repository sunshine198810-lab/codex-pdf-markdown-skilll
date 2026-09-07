"""D2 资产变动表族测试：识别、期初+增−减=期末 勾稽门、账面价值交叉门、候选回退与负例。"""

import pathlib
import sys
import tempfile
import unittest

_SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))

from pipeline import exports, movement  # noqa: E402


def _cell(row, col, text):
    return {
        "cell_id": f"c{row}_{col}", "row": row, "col": col,
        "rowspan": 1, "colspan": 1, "raw_value": text, "text": text,
        "kind": "other", "bbox": [60 + col * 80, 400 - row * 20, 130 + col * 80, 414 - row * 20],
        "evidence_refs": [], "eligible_for_calculation": False,
    }


def _clean_movement_table():
    """三段 2 类别+合计，全部勾稽成立（col1=土地/col2=机器/col3=合计）。"""
    cells = []
    # header
    cells += [_cell(0, 0, "项目"), _cell(0, 1, "土地、房屋"), _cell(0, 2, "机器设备"), _cell(0, 3, "合计")]
    # 一、账面原值
    cells += [_cell(1, 0, "一、账面原值")]
    cells += [_cell(2, 0, "1.期初余额"), _cell(2, 1, "100"), _cell(2, 2, "50"), _cell(2, 3, "150")]
    cells += [_cell(3, 0, "2.本期增加金额"), _cell(3, 1, "20"), _cell(3, 2, "10"), _cell(3, 3, "30")]
    cells += [_cell(4, 0, "3.本期减少金额"), _cell(4, 1, "5"), _cell(4, 2, "5"), _cell(4, 3, "10")]
    cells += [_cell(5, 0, "4.期末余额"), _cell(5, 1, "115"), _cell(5, 2, "55"), _cell(5, 3, "170")]
    # 二、累计折旧
    cells += [_cell(6, 0, "二、累计折旧")]
    cells += [_cell(7, 0, "1.期初余额"), _cell(7, 1, "30"), _cell(7, 2, "20"), _cell(7, 3, "50")]
    cells += [_cell(8, 0, "2.本期增加金额"), _cell(8, 1, "10"), _cell(8, 2, "8"), _cell(8, 3, "18")]
    cells += [_cell(9, 0, "3.本期减少金额"), _cell(9, 1, "2"), _cell(9, 2, "1"), _cell(9, 3, "3")]
    cells += [_cell(10, 0, "4.期末余额"), _cell(10, 1, "38"), _cell(10, 2, "27"), _cell(10, 3, "65")]
    # 四、账面价值（供交叉门）
    cells += [_cell(11, 0, "四、账面价值")]
    cells += [_cell(12, 0, "1.期末账面价值"), _cell(12, 1, "77"), _cell(12, 2, "28"), _cell(12, 3, "105")]
    cells += [_cell(13, 0, "2.期初账面价值"), _cell(13, 1, "70"), _cell(13, 2, "30"), _cell(13, 3, "100")]
    return {"cells": cells, "column_count": 4, "row_count": 14}


def _broken_base_fragment():
    """账面原值某列 期初+增−减≠期末 → 该节该列退回候选；累计折旧列仍成立 → 可放行。"""
    cells = [_cell(0, 0, "项目"), _cell(0, 1, "土地、房屋"), _cell(0, 2, "合计")]
    cells += [_cell(1, 0, "一、账面原值")]
    cells += [_cell(2, 0, "1.期初余额"), _cell(2, 1, "100"), _cell(2, 2, "150")]
    cells += [_cell(3, 0, "2.本期增加金额"), _cell(3, 1, "20"), _cell(3, 2, "30")]
    cells += [_cell(4, 0, "3.本期减少金额"), _cell(4, 1, "5"), _cell(4, 2, "10")]
    cells += [_cell(5, 0, "4.期末余额"), _cell(5, 1, "999"), _cell(5, 2, "170")]  # col1 不平
    cells += [_cell(6, 0, "二、累计折旧")]
    cells += [_cell(7, 0, "1.期初余额"), _cell(7, 1, "30"), _cell(7, 2, "50")]
    cells += [_cell(8, 0, "2.本期增加金额"), _cell(8, 1, "10"), _cell(8, 2, "18")]
    cells += [_cell(9, 0, "3.本期减少金额"), _cell(9, 1, "2"), _cell(9, 2, "3")]
    cells += [_cell(10, 0, "4.期末余额"), _cell(10, 1, "38"), _cell(10, 2, "65")]
    return {"cells": cells, "column_count": 3, "row_count": 11}


def _inventory_style_table():
    """负例：存货跌价准备变动表（无数值节/账面原值标记）→ 不应被识别为资产变动表。"""
    cells = [_cell(0, 0, "项目"), _cell(0, 1, "期初余额"), _cell(0, 2, "本期增加金额"), _cell(0, 3, "本期减少金额"), _cell(0, 4, "期末余额")]
    cells += [_cell(1, 0, "原材料"), _cell(1, 1, "10"), _cell(1, 2, "20"), _cell(1, 3, "5"), _cell(1, 4, "25")]
    cells += [_cell(2, 0, "库存商品"), _cell(2, 1, "20"), _cell(2, 2, "30"), _cell(2, 3, "10"), _cell(2, 4, "40")]
    return {"cells": cells, "column_count": 5, "row_count": 3}


def _write(root, tid, doc):
    exports.write_json(root / "表格" / f"{tid}.json", doc)
    return {"table_fragment_id": tid, "physical_page": 60, "bbox": [50, 200, 500, 400],
            "rows": doc["row_count"], "columns": doc["column_count"],
            "json": f"表格/{tid}.json", "html": f"表格/{tid}.html",
            "candidate_status": "needs_review", "eligible_for_calculation": False,
            "structure_source": "pdfplumber_ruled_table"}


class TestMovementD2(unittest.TestCase):
    def _pages(self):
        return [
            {"physical_page": 5, "lines": [
                {"text": "本公司财务报表符合企业会计准则的要求", "bbox": [10, 10, 300, 20]},
                {"text": "本公司以人民币为记账本位币", "bbox": [10, 30, 300, 40]},
            ]},
            {"physical_page": 60, "lines": [
                {"text": "单位：人民币元", "bbox": [10, 20, 200, 30]},
                {"text": "固定资产", "bbox": [10, 60, 120, 70]},
            ]},
        ]

    def test_clean_movement_table_emits_reconciled_facts(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            (root / "表格").mkdir(parents=True)
            item = _write(root, "table-mv-1", _clean_movement_table())
            res = movement.build_movement_facts(
                root, self._pages(), [item], [], [], {"document_id": "600900_长电_2025年度报告"},
                "test", claimed_fragments=set(),
            )
            # base(3列)+deprec(3列) × 4 阶段 = 24 facts；账面价值仅作交叉门不单独放行
            self.assertEqual(len(res["facts"]), 24)
            self.assertTrue(all(f["quality"]["eligible_for_calculation"] for f in res["facts"]))
            self.assertTrue(all(f["status"] == "accepted_fact" for f in res["facts"]))
            self.assertEqual(len(res["movement_tables"]), 1)
            # 单位/币种/准则/期间/主体已绑定
            f = next(x for x in res["facts"] if x["movement_phase"] == "end" and x["asset_column"] == 1)
            self.assertEqual(f["scale"], "元")
            self.assertEqual(f["currency"], "CNY")
            self.assertEqual(f["entity_scope"], "consolidated")
            self.assertEqual(f["period"]["end"], "2025-12-31")
            self.assertEqual(f["accounting_basis"], "CAS")

    def test_unreconciled_row_stays_candidate_while_good_col_facts(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            (root / "表格").mkdir(parents=True)
            item = _write(root, "table-mv-2", _broken_base_fragment())
            res = movement.build_movement_facts(
                root, self._pages(), [item], [], [], {"document_id": "600900_长电_2025年度报告"},
                "test", claimed_fragments=set(),
            )
            # 账面原值 col1 不平 → 候选；col2(合计) 平 → 原值 facts 4；折旧 col1+col2 各 4
            self.assertEqual(len(res["facts"]), 12)
            self.assertEqual(len(res["candidates"]), 4)  # base col1 4 阶段
            self.assertTrue(all(not c["quality"]["eligible_for_calculation"] for c in res["candidates"]))
            self.assertTrue(all(c["movement_section"] == "base" for c in res["candidates"]))

    def test_inventory_style_table_not_recognized(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            (root / "表格").mkdir(parents=True)
            item = _write(root, "table-mv-3", _inventory_style_table())
            res = movement.build_movement_facts(
                root, self._pages(), [item], [], [], {"document_id": "600900_长电_2025年度报告"},
                "test", claimed_fragments=set(),
            )
            self.assertEqual(res["facts"], [])
            self.assertEqual(res["candidates"], [])
            self.assertEqual(res["processed_fragments"], [])
            self.assertEqual(res["movement_tables"], [])


def _provision_table():
    """存货跌价准备变动表（类别作行，计提/其他/转回或转销/其他 子列）。"""
    cells = [_cell(0, 0, "项目"), _cell(0, 1, "期初余额"), _cell(0, 2, "本期增加金额"),
             _cell(0, 3, ""), _cell(0, 4, "本期减少金额"), _cell(0, 5, ""), _cell(0, 6, "期末余额")]
    cells += [_cell(1, 0, ""), _cell(1, 1, ""), _cell(1, 2, "计提"), _cell(1, 3, "其他"),
              _cell(1, 4, "转回或转销"), _cell(1, 5, "其他"), _cell(1, 6, "")]
    cells += [_cell(2, 0, "原材料"), _cell(2, 1, "100"), _cell(2, 2, "20"), _cell(2, 3, ""),
              _cell(2, 4, "5"), _cell(2, 5, ""), _cell(2, 6, "115")]
    cells += [_cell(3, 0, "包装材料"), _cell(3, 1, "200"), _cell(3, 2, "30"), _cell(3, 3, ""),
              _cell(3, 4, "10"), _cell(3, 5, ""), _cell(3, 6, "300")]  # 200+30−10=220≠300
    cells += [_cell(4, 0, "在产品"), _cell(4, 1, ""), _cell(4, 2, ""), _cell(4, 3, ""),
              _cell(4, 4, ""), _cell(4, 5, ""), _cell(4, 6, "")]
    cells += [_cell(5, 0, "合计"), _cell(5, 1, "300"), _cell(5, 2, "50"), _cell(5, 3, ""),
              _cell(5, 4, "15"), _cell(5, 5, ""), _cell(5, 6, "335")]
    return {"cells": cells, "column_count": 7, "row_count": 6}


class TestMovementProvisionD2(unittest.TestCase):
    def test_provision_matches_rows_and_unmatched_row_candidates(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            (root / "表格").mkdir(parents=True)
            item = _write(root, "table-pv-1", _provision_table())
            pages = [
                {"physical_page": 5, "lines": [
                    {"text": "本公司财务报表符合企业会计准则的要求", "bbox": [10, 10, 300, 20]},
                    {"text": "本公司以人民币为记账本位币", "bbox": [10, 30, 300, 40]},
                ]},
                {"physical_page": 60, "lines": [
                    {"text": "单位：人民币元", "bbox": [10, 20, 200, 30]},
                    {"text": "存货跌价准备及合同履约成本减值准备", "bbox": [10, 60, 300, 70]},
                ]},
            ]
            res = movement.build_provision_movement_facts(
                root, pages, [item], [], [], {"document_id": "600887_伊利_2025年度报告"},
                "test", claimed_fragments=set(),
            )
            # 原材料 + 合计 各 begin/计提/转回/end=4 facts；包装材料不平→4 候选；在产品空行跳过
            self.assertEqual(len(res["facts"]), 8)
            self.assertEqual(len(res["candidates"]), 4)
            self.assertTrue(all(f["quality"]["eligible_for_calculation"] for f in res["facts"]))
            self.assertTrue(all(c["reconciliation_status"] == "failed" for c in res["candidates"]))
            self.assertEqual(len(res["provision_tables"]), 1)

    def test_fixed_asset_movement_not_matched_by_provision(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            (root / "表格").mkdir(parents=True)
            item = _write(root, "table-pv-2", _clean_movement_table())
            pages = [{"physical_page": 60, "lines": [{"text": "单位：人民币元", "bbox": [10, 20, 200, 30]}]}]
            res = movement.build_provision_movement_facts(
                root, pages, [item], [], [], {"document_id": "600887_伊利_2025年度报告"},
                "test", claimed_fragments=set(),
            )
            self.assertEqual(res["facts"], [])
            self.assertEqual(res["candidates"], [])
            self.assertEqual(res["processed_fragments"], [])
            self.assertEqual(res["provision_tables"], [])

    def test_provision_requires_caption_gate(self):
        """无减值/跌价/坏账准备题注时，即使版式匹配也不放行（防递延收益等误标）。"""
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            (root / "表格").mkdir(parents=True)
            item = _write(root, "table-pv-3", _provision_table())
            pages = [
                {"physical_page": 5, "lines": [
                    {"text": "本公司财务报表符合企业会计准则的要求", "bbox": [10, 10, 300, 20]},
                    {"text": "本公司以人民币为记账本位币", "bbox": [10, 30, 300, 40]},
                ]},
                {"physical_page": 60, "lines": [{"text": "单位：人民币元", "bbox": [10, 20, 200, 30]}]},
            ]
            res = movement.build_provision_movement_facts(
                root, pages, [item], [], [], {"document_id": "600887_伊利_2025年度报告"},
                "test", claimed_fragments=set(),
            )
            self.assertEqual(res["facts"], [])
            self.assertEqual(res["processed_fragments"], [])

    def test_long_paragraph_keyword_does_not_satisfy_caption_gate(self):
        """长句里提及“减值准备”不算题注（防开发支出等相邻表被误标）。"""
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            (root / "表格").mkdir(parents=True)
            item = _write(root, "table-pv-4", _provision_table())
            pages = [{"physical_page": 60, "lines": [
                {"text": "单位：人民币元", "bbox": [10, 20, 200, 30]},
                {"text": "本公司对存货跌价准备按成本与可变现净值孰低原则进行计提与复核，详见附注。", "bbox": [10, 60, 500, 70]},
            ]}]
            res = movement.build_provision_movement_facts(
                root, pages, [item], [], [], {"document_id": "600887_伊利_2025年度报告"},
                "test", claimed_fragments=set(),
            )
            self.assertEqual(res["facts"], [])
            self.assertEqual(res["processed_fragments"], [])


def _guanghe_movement_table():
    """广核版式：标签列在 col1（col0 与 col2 空），类别数值列从 col3 起。"""
    def g(row, col, text):
        c = _cell(row, col, text)
        return c
    cells = [g(0, 0, ""), g(0, 1, "项目"), g(0, 2, ""), g(0, 3, "房屋及建筑物"),
             g(0, 4, "机器设备"), g(0, 5, "合计"), g(0, 6, "")]
    cells += [g(1, 0, ""), g(1, 1, "一、账面原值")]
    cells += [g(2, 1, "1.期初余额"), g(2, 3, "100"), g(2, 4, "60"), g(2, 5, "160")]
    cells += [g(3, 1, "2.本期增加金额"), g(3, 3, "20"), g(3, 5, "20")]
    cells += [g(4, 1, "3.本期减少金额"), g(4, 3, "5"), g(4, 5, "5")]
    cells += [g(5, 1, "4.期末余额"), g(5, 3, "115"), g(5, 4, "60"), g(5, 5, "175")]
    cells += [g(6, 0, ""), g(6, 1, "二、累计折旧")]
    cells += [g(7, 1, "1.期初余额"), g(7, 3, "30"), g(7, 5, "30")]
    cells += [g(8, 1, "2.本期增加金额"), g(8, 3, "10"), g(8, 5, "10")]
    cells += [g(9, 1, "3.本期减少金额"), g(9, 3, "2"), g(9, 5, "2")]
    cells += [g(10, 1, "4.期末余额"), g(10, 3, "38"), g(10, 5, "38")]
    return {"cells": cells, "column_count": 7, "row_count": 11}


class TestMovementLabelColFold(unittest.TestCase):
    def test_label_col_detected_and_folded(self):
        doc = _guanghe_movement_table()
        self.assertEqual(movement._label_col(doc["cells"]), 1)
        folded = movement._fold_label_col(doc)
        self.assertTrue(movement._is_movement_fragment(folded))
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            (root / "表格").mkdir(parents=True)
            item = _write(root, "table-gh-1", doc)
            pages = [
                {"physical_page": 5, "lines": [
                    {"text": "本公司财务报表符合企业会计准则的要求", "bbox": [10, 10, 300, 20]},
                    {"text": "本公司以人民币为记账本位币", "bbox": [10, 30, 300, 40]},
                ]},
                {"physical_page": 60, "lines": [
                    {"text": "单位：人民币元", "bbox": [10, 20, 200, 30]},
                    {"text": "固定资产", "bbox": [10, 60, 120, 70]},
                ]},
            ]
            res = movement.build_movement_facts(
                root, pages, [item], [], [], {"document_id": "003816_广核_2025年度报告"},
                "test", claimed_fragments=set(),
            )
            # 房屋(3)与合计(5)的 base+deprec 各 4 阶段 = 16；机器设备(4)无增减不参与
            self.assertEqual(len(res["facts"]), 16)
            self.assertTrue(all(f["quality"]["eligible_for_calculation"] for f in res["facts"]))

    def test_col0_aligned_still_works(self):
        doc = _clean_movement_table()
        self.assertEqual(movement._label_col(doc["cells"]), 0)
        folded = movement._fold_label_col(doc)
        self.assertIs(folded, doc)


if __name__ == "__main__":
    unittest.main()
