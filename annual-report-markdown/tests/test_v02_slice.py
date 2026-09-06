"""V0.2 核心切片逻辑测试（仅标准库；不依赖真实 MinerU/pdfplumber）。"""

import json
import pathlib
import sys
import tempfile
import unittest

_SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))

from pipeline import adapters, exports  # noqa: E402
from pipeline.tables import grid_to_table_fragment, html_to_grid  # noqa: E402

_HTML_MULTIHEAD = (
    '<table><tr><td rowspan="2">公司名称</td><td colspan="2">税率</td>'
    "<td></td></tr><tr><td>2025年</td><td>2024年</td><td>起始年度及有效期</td></tr>"
    "<tr><td>西藏公司</td><td>15%</td><td>15%</td><td>2011年至2030年</td></tr></table>"
)


class TestHtmlToGrid(unittest.TestCase):
    def test_rowspan_colspan_preserved(self):
        g = html_to_grid(_HTML_MULTIHEAD)
        by = {(c["row"], c["col"]): c for c in g["cells"]}
        self.assertEqual(g["rows"], 3)
        self.assertEqual(g["cols"], 4)
        # 公司名称: row0col0, rowspan 2
        self.assertEqual(by[(0, 0)]["text"], "公司名称")
        self.assertEqual(by[(0, 0)]["rowspan"], 2)
        # 税率 colspan 2
        self.assertEqual(by[(0, 1)]["text"], "税率")
        self.assertEqual(by[(0, 1)]["colspan"], 2)
        # 第二行表头 2025/2024 子列与第三行数据
        self.assertEqual(by[(1, 1)]["text"], "2025年")
        self.assertEqual(by[(2, 0)]["text"], "西藏公司")
        # 合并区不应有独立单元格对象重复（矩阵展开只算一次）
        ids = [c["cell_id"] for c in g["cells"]]
        self.assertEqual(len(ids), len(set(ids)))

    def test_ragged_rows_from_rowspan_do_not_overrun(self):
        g = html_to_grid(
            "<table><tr><td rowspan='2'>A</td><td>B</td><td>C</td></tr>"
            "<tr><td colspan='2'>D</td></tr><tr><td>E</td></tr></table>"
        )
        self.assertEqual(g["cols"], 3)
        self.assertTrue(any(c["text"] == "E" for c in g["cells"]))

    def test_grid_to_fragment_schema_shape(self):
        g = html_to_grid("<table><tr><td>a</td><td>1</td></tr></table>")
        frag = grid_to_table_fragment(g, "frag-1", physical_page=7)
        self.assertEqual(frag["physical_page"], 7)
        self.assertEqual(frag["row_count"], 1)
        self.assertEqual(frag["column_count"], 2)
        self.assertEqual(frag["candidate_status"], "extracted")


class TestGridToHtml(unittest.TestCase):
    def test_html_keeps_spans(self):
        g = html_to_grid(_HTML_MULTIHEAD)
        html = exports.grid_to_html(g)
        self.assertIn('rowspan="2"', html)
        self.assertIn('colspan="2"', html)
        self.assertIn("公司名称", html)


class TestMineruAdapterAndSlice(unittest.TestCase):
    def _make_content(self, path, table_body, page_idx=0):
        block = {
            "type": "table",
            "page_idx": page_idx,
            "bbox": [65, 100, 900, 200],
            "img_path": "x.jpg",
            "table_caption": ["测试表"],
            "table_footnote": [],
            "table_body": table_body,
        }
        path.write_text(json.dumps([block], ensure_ascii=False), encoding="utf-8")

    def test_mineru_tables_offset_physical_page(self):
        with tempfile.TemporaryDirectory() as td:
            td = pathlib.Path(td)
            cp = td / "content_list.json"
            self._make_content(cp, "<table><tr><td>总资产</td><td>100</td></tr></table>", page_idx=0)
            res = adapters.run_adapter("mineru_tables", str(cp), start_page0=12)
            self.assertEqual(res["table_count"], 1)
            # physical_page = start_page0 + page_idx + 1
            self.assertEqual(res["tables"][0]["physical_page"], 13)
            self.assertEqual(res["tables"][0]["grid"]["cols"], 2)

    def test_build_slice_partial_honest(self):
        import run_tableslice

        with tempfile.TemporaryDirectory() as td:
            td = pathlib.Path(td)
            pdf = td / "600519_贵州茅台_2026年半年度报告.pdf"
            pdf.write_bytes(b"%PDF-1.4 fake\n")
            cp = td / "content_list.json"
            self._make_content(cp, "<table><tr><td>a</td><td>1</td></tr></table>", page_idx=0)
            out = td / "slice"
            result = run_tableslice.build_slice(
                pdf, out, pages={9}, mineru=str(cp), mineru_start=8
            )
            self.assertEqual(result["tables"][0]["physical_page"], 9)
            # 产物与状态
            self.assertTrue((out / "表格/table-0001.json").is_file())
            self.assertTrue((out / "表格/table-0001.html").is_file())
            self.assertTrue((out / "索引/evidence.jsonl").is_file())
            q = json.loads((out / "索引/quality.json").read_text(encoding="utf-8"))
            self.assertEqual(q["overall_state"], "partial")
            t = json.loads((out / "表格/table-0001.json").read_text(encoding="utf-8"))
            self.assertFalse(t["overall_eligible_for_calculation"])
            self.assertEqual(t["grid"]["cells"][0]["candidate_status"], "extracted")
            # 阅读顺序候选：pdf 是假的 → 应产生 warning 而不是崩溃
            self.assertTrue(any("pdfplumber" in w for w in result["warnings"]))


class TestVerifyNumNormalize(unittest.TestCase):
    """verify_tables 数字归一：MinerU 逗号后空格需与文字层数字视为一致。"""

    def test_spaced_numbers_are_numeric_and_equal(self):
        import verify_tables

        # MinerU 形如 "13, 788, 280, 669.65" 应判定为数字
        self.assertTrue(verify_tables._is_num("13, 788, 280, 669.65"))
        # 与文字层无空格写法归一后相等
        self.assertEqual(
            verify_tables._numkey("13, 788, 280, 669.65"),
            verify_tables._numkey("13,788,280,669.65"),
        )
        # 负数/百分比/括号归一
        self.assertTrue(verify_tables._is_num("(8, 595)"))
        self.assertTrue(verify_tables._is_num("15%"))
        self.assertFalse(verify_tables._is_num("七、1"))
        self.assertFalse(verify_tables._is_num("上升0.2个百分点"))


if __name__ == "__main__":
    unittest.main()
