import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

_SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))

from pipeline import borderless, layout, vector_evidence  # noqa: E402


def word(text, x0, x1, top):
    return {"text": text, "x0": x0, "x1": x1, "top": top, "bottom": top + 10}


class TestBorderlessNativeGrid(unittest.TestCase):
    def test_auto_detection_finds_only_collapsed_statement_range(self):
        pages = [
            {"physical_page": 1, "lines": [{"text": "合并资产负债表", "bbox": [0, 10, 100, 20]}],
             "tables": [{"bbox": [0, 30, 100, 90], "grid": {"cols": 2}}]},
            {"physical_page": 2, "lines": [{"text": "合并资产负债表（续）", "bbox": [0, 10, 100, 20]}],
             "tables": [{"bbox": [0, 30, 100, 90], "grid": {"cols": 2}}]},
            {"physical_page": 3, "lines": [{"text": "合并利润表", "bbox": [0, 10, 100, 20]}],
             "tables": [{"bbox": [0, 30, 100, 90], "grid": {"cols": 4}}]},
            {"physical_page": 4, "lines": [{"text": "合并所有者权益变动表", "bbox": [0, 10, 100, 20]}],
             "tables": []},
        ]
        result = borderless.detect_collapsed_main_statement_pages(pages)
        self.assertTrue(result["needed"])
        self.assertEqual(result["pages"], [1, 2])
        self.assertEqual((result["start_page"], result["end_page"]), (1, 2))

    def test_prefixed_title_and_continuation_share_one_slice(self):
        pages = [
            {"physical_page": 1, "lines": [{"text": "1. 合并资产负债表", "bbox": [0, 10, 100, 20]}],
             "tables": [{"bbox": [0, 30, 100, 90], "grid": {"cols": 2}}]},
            {"physical_page": 2, "lines": [{"text": "合并资产负债表（续）", "bbox": [0, 10, 100, 20]}],
             "tables": [{"bbox": [0, 30, 100, 90], "grid": {"cols": 2}}]},
            {"physical_page": 3, "lines": [{"text": "二、合并利润表", "bbox": [0, 10, 100, 20]}],
             "tables": [{"bbox": [0, 30, 100, 90], "grid": {"cols": 4}}]},
        ]
        result = borderless.detect_collapsed_main_statement_pages(pages)
        self.assertEqual(result["pages"], [1, 2])
        self.assertEqual(result["collapsed_statements"][0]["title"], "合并资产负债表")

    def test_repeated_full_title_on_adjacent_page_is_continuation(self):
        pages = [
            {"physical_page": 1, "lines": [{"text": "合并资产负债表", "bbox": [0, 10, 100, 20]}],
             "tables": []},
            {"physical_page": 2, "lines": [{"text": "合并资产负债表", "bbox": [0, 10, 100, 20]}],
             "tables": []},
            {"physical_page": 3, "lines": [{"text": "合并利润表", "bbox": [0, 10, 100, 20]}],
             "tables": [{"bbox": [0, 30, 100, 90], "grid": {"cols": 4}}]},
        ]
        result = borderless.detect_collapsed_main_statement_pages(pages)
        self.assertEqual(result["pages"], [1, 2])
        self.assertEqual(len(result["collapsed_statements"]), 1)

    def test_auto_detection_skips_existing_four_column_main_table(self):
        pages = [{"physical_page": 1,
                  "lines": [{"text": "合并利润表", "bbox": [0, 10, 100, 20]}],
                  "tables": [{"bbox": [0, 30, 100, 90], "grid": {"cols": 4}}]}]
        self.assertFalse(borderless.detect_collapsed_main_statement_pages(pages)["needed"])

    def test_auto_detection_skips_supported_three_column_main_table(self):
        pages = [{"physical_page": 1,
                  "lines": [{"text": "1.合并资产负债表", "bbox": [0, 10, 100, 20]}],
                  "tables": [{"bbox": [0, 30, 100, 90], "grid": {"cols": 3}}]}]
        self.assertFalse(borderless.detect_collapsed_main_statement_pages(pages)["needed"])

    def test_auto_mineru_refuses_more_than_32_pages(self):
        with self.assertRaises(ValueError):
            borderless.run_mineru("x.pdf", "/tmp/x", start_page=1, end_page=33)

    def test_auto_mineru_reports_missing_cli(self):
        with mock.patch("pipeline.borderless.shutil.which", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "MinerU CLI"):
                borderless.run_mineru("x.pdf", "/tmp/x", start_page=1, end_page=1)

    def test_mineru_region_native_words_build_four_columns(self):
        words = [
            word("项", 50, 59, 100), word("目", 63, 72, 100),
            word("附注", 200, 220, 100), word("2025年度", 350, 410, 100),
            word("2024年度", 470, 530, 100),
            word("营业收入", 50, 100, 130), word("五、1", 200, 225, 130),
            word("1,234.50", 360, 415, 130), word("1,100.00", 475, 530, 130),
        ]
        page = {
            "physical_page": 11, "width_pt": 600.0, "height_pt": 800.0,
            "words": words, "lines": layout.words_to_lines(words),
        }
        content = [{
            "type": "table", "page_idx": 0, "bbox": [50, 80, 950, 500],
            "table_caption": [], "table_footnote": [],
            "table_body": "<table><tr><td>项目</td><td>附注</td><td>2025年度</td><td>2024年度</td></tr>"
                          "<tr><td>营业收入</td><td>五、1</td><td>1,234.50</td><td>1,100.00</td></tr></table>",
        }]
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "content_list.json"
            path.write_text(json.dumps(content, ensure_ascii=False), encoding="utf-8")
            out = borderless.load_candidates(path, 10, [page])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["physical_page"], 11)
        self.assertEqual(out[0]["grid"]["cols"], 4)
        cells = {(c["row"], c["col"]): c for c in out[0]["grid"]["cells"]}
        self.assertEqual(cells[(1, 0)]["raw_value"], "营业收入")
        self.assertEqual(cells[(1, 2)]["raw_value"], "1,234.50")
        self.assertIsNotNone(cells[(1, 2)]["bbox"])

    def test_split_year_date_header_rebuilds_four_native_columns(self):
        words = [
            word("2025年", 350, 410, 80), word("2024年", 470, 530, 80),
            word("项目", 50, 80, 100), word("附注", 200, 230, 100),
            word("12月31日", 350, 410, 100), word("12月31日", 470, 530, 100),
            word("资产总计", 50, 110, 130), word("1,000", 360, 410, 130),
            word("900", 480, 530, 130),
        ]
        page = {"physical_page": 11, "width_pt": 600.0, "height_pt": 800.0,
                "words": words, "lines": layout.words_to_lines(words)}
        content = [{
            "type": "table", "page_idx": 0, "bbox": [50, 70, 950, 500],
            "table_caption": [], "table_footnote": [],
            "table_body": "<table><tr><td>项目</td><td>附注</td><td>2025年</td><td>2024年</td><td></td></tr>"
                          "<tr><td>资产总计</td><td></td><td>1,000</td><td>900</td><td></td></tr></table>",
        }]
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "content_list.json"
            path.write_text(json.dumps(content, ensure_ascii=False), encoding="utf-8")
            out = borderless.load_candidates(path, 10, [page])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["grid"]["cols"], 4)
        cells = {(c["row"], c["col"]): c for c in out[0]["grid"]["cells"]}
        self.assertEqual(cells[(0, 2)]["raw_value"], "2025年12月31日")
        self.assertEqual(cells[(0, 3)]["raw_value"], "2024年12月31日")
        self.assertLess(cells[(0, 2)]["bbox"][1], 90)

    def test_blank_label_multiline_period_header_rebuilds_native_four_columns(self):
        words = [
            word("截至2026年6月30日", 390, 470, 100),
            word("截至2025年6月30日", 484, 574, 100),
            word("止6个月期间", 416, 470, 112), word("止6个月期间", 507, 574, 112),
            word("附注五", 344, 380, 124),
            word("（未经审计）", 416, 470, 124), word("（未经审计）", 507, 574, 124),
            word("保险服务收入", 50, 130, 150), word("22", 357, 370, 150),
            word("279,255", 422, 470, 150), word("277,820", 517, 574, 150),
        ]
        page = {"physical_page": 11, "width_pt": 600.0, "height_pt": 800.0,
                "words": words, "lines": layout.words_to_lines(words)}
        content = [{
            "type": "table", "page_idx": 0, "bbox": [50, 112, 970, 500],
            "table_caption": [], "table_footnote": [],
            "table_body": "<table><tr><td></td><td>附注五</td><td>截至2026年6月30日</td><td>截至2025年6月30日</td></tr>"
                          "<tr><td>保险服务收入</td><td>22</td><td>279,255</td><td>277,820</td></tr></table>",
        }]
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "content_list.json"
            path.write_text(json.dumps(content, ensure_ascii=False), encoding="utf-8")
            out = borderless.load_candidates(path, 10, [page])
        self.assertEqual(len(out), 1)
        cells = {(c["row"], c["col"]): c for c in out[0]["grid"]["cells"]}
        self.assertEqual(cells[(0, 0)]["raw_value"], "项目")
        self.assertEqual(cells[(0, 2)]["raw_value"], "截至2026年6月30日止6个月期间（未经审计）")
        self.assertEqual(cells[(1, 1)]["raw_value"], "22")
        self.assertEqual(cells[(1, 2)]["raw_value"], "279,255")

    def test_empty_mineru_html_region_can_use_strict_native_rebuild(self):
        words = [
            word("2026年6月30日", 405, 463, 143), word("2025年12月31日", 484, 548, 143),
            word("附注五", 344, 370, 153), word("（未经审计）", 405, 463, 153),
            word("（经审计）", 492, 548, 153),
            word("股东权益合计", 51, 120, 355), word("1,454,080", 416, 460, 355),
            word("1,415,988", 507, 548, 355),
        ]
        page = {"physical_page": 9, "width_pt": 600.0, "height_pt": 800.0,
                "words": words, "lines": layout.words_to_lines(words)}
        content = [{"type": "table", "page_idx": 0, "bbox": [65, 160, 931, 500],
                    "table_caption": [], "table_footnote": [], "table_body": ""}]
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "content_list.json"
            path.write_text(json.dumps(content), encoding="utf-8")
            items = borderless.load_candidates(path, 8, [page])
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["grid"]["cols"], 4)
        cells = {(c["row"], c["col"]): c for c in items[0]["grid"]["cells"]}
        self.assertEqual(cells[(1, 0)]["raw_value"], "股东权益合计")
        self.assertEqual(cells[(1, 2)]["raw_value"], "1,454,080")

    def test_single_line_blank_label_header_does_not_absorb_first_data_row(self):
        words = [
            word("附注八", 344, 380, 143), word("2025年度", 405, 463, 143),
            word("2024年度", 484, 548, 143),
            word("保险服务收入", 61, 140, 177), word("42", 356, 370, 177),
            word("559,502", 416, 463, 177), word("551,186", 500, 548, 177),
        ]
        page = {"physical_page": 10, "width_pt": 600.0, "height_pt": 800.0,
                "words": words, "lines": layout.words_to_lines(words)}
        content = [{"type": "table", "page_idx": 0, "bbox": [65, 160, 931, 500],
                    "table_caption": [], "table_footnote": [],
                    "table_body": "<table><tr><td></td><td>附注八</td><td>2025年度</td><td>2024年度</td></tr></table>"}]
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "content_list.json"
            path.write_text(json.dumps(content), encoding="utf-8")
            items = borderless.load_candidates(path, 9, [page])
        cells = {(c["row"], c["col"]): c for c in items[0]["grid"]["cells"]}
        self.assertEqual(cells[(0, 2)]["raw_value"], "2025年度")
        self.assertEqual(cells[(1, 0)]["raw_value"], "保险服务收入")
        self.assertEqual(cells[(1, 2)]["raw_value"], "559,502")

    def test_non_four_column_candidate_is_not_promoted(self):
        page = {"physical_page": 1, "width_pt": 600.0, "height_pt": 800.0,
                "words": [], "lines": []}
        content = [{"type": "table", "page_idx": 0, "bbox": [0, 0, 1000, 1000],
                    "table_caption": [], "table_footnote": [],
                    "table_body": "<table><tr><td>A</td><td>B</td></tr></table>"}]
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "content_list.json"
            path.write_text(json.dumps(content), encoding="utf-8")
            self.assertEqual(borderless.load_candidates(path, 0, [page]), [])

    def test_wide_mineru_html_can_still_use_strict_native_four_columns(self):
        words = [
            word("资产", 50, 80, 100), word("附注八", 200, 230, 100),
            word("2025年12月31日", 350, 420, 100), word("2024年12月31日", 470, 540, 100),
            word("货币资金", 50, 100, 130), word("1", 210, 218, 130),
            word("18,339", 370, 420, 130), word("5,163", 490, 540, 130),
        ]
        page = {"physical_page": 11, "width_pt": 600.0, "height_pt": 800.0,
                "words": words, "lines": layout.words_to_lines(words)}
        html = "<table><tr>" + "".join(f"<td>{x}</td>" for x in range(14)) + "</tr></table>"
        content = [{"type": "table", "page_idx": 0, "bbox": [50, 100, 950, 500],
                    "table_caption": [], "table_footnote": [], "table_body": html}]
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "content_list.json"
            path.write_text(json.dumps(content), encoding="utf-8")
            items = borderless.load_candidates(path, 10, [page])
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["grid"]["cols"], 4)

    def test_vector_outline_candidate_is_isolated_from_facts(self):
        words = [word("合并资产负债表", 50, 150, 50), word("资产", 50, 80, 100)]
        page = {"physical_page": 11, "width_pt": 600.0, "height_pt": 800.0,
                "words": words, "lines": layout.words_to_lines(words),
                "native_digit_count": 0, "vector_curve_count": 400}
        content = [{"type": "table", "page_idx": 0, "bbox": [50, 100, 950, 500],
                    "table_caption": [], "table_footnote": [],
                    "table_body": "<table><tr><td>资产</td><td>附注</td><td>2025</td><td>2024</td></tr>"
                                  "<tr><td>货币资金</td><td></td><td>1,234</td><td>1,100</td></tr></table>"}]
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "content_list.json"
            path.write_text(json.dumps(content, ensure_ascii=False), encoding="utf-8")
            result = vector_evidence.inspect(path, 10, [page])
        self.assertEqual(len(result["statements"]), 1)
        self.assertEqual(result["statements"][0]["statement_title"], "合并资产负债表")
        self.assertFalse(result["statements"][0]["eligible_for_calculation"])
        self.assertTrue(all(
            not cell["eligible_for_calculation"]
            for cell in result["fragments"][0]["grid"]["cells"]
        ))

    def test_native_digits_prevent_vector_outline_classification(self):
        words = [word("合并利润表", 50, 150, 50), word("2025", 350, 390, 100)]
        page = {"physical_page": 1, "width_pt": 600.0, "height_pt": 800.0,
                "words": words, "lines": layout.words_to_lines(words),
                "native_digit_count": 4, "vector_curve_count": 400}
        content = [{"type": "table", "page_idx": 0, "bbox": [0, 50, 1000, 500],
                    "table_caption": [], "table_footnote": [],
                    "table_body": "<table><tr><td>项目</td><td>2025</td></tr>"
                                  "<tr><td>收入</td><td>100</td></tr></table>"}]
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "content_list.json"
            path.write_text(json.dumps(content, ensure_ascii=False), encoding="utf-8")
            result = vector_evidence.inspect(path, 0, [page])
        self.assertEqual(result["fragments"], [])


if __name__ == "__main__":
    unittest.main()
