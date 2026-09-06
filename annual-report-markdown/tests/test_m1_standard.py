import json
import pathlib
import tempfile
import unittest

from reportlab.pdfgen import canvas

from pipeline import layout, sections, standard, validation


class TestM1Helpers(unittest.TestCase):
    def test_repeated_marginalia_only_uses_edges(self):
        pages = []
        for n in range(1, 5):
            pages.append(
                {
                    "height_pt": 800,
                    "lines": [
                        {"text": "Demo 2026 Interim Report", "bbox": [20, 20, 300, 32]},
                        {"text": "合计", "bbox": [20, 400, 60, 412]},
                        {"text": f"{n} / 4", "bbox": [280, 770, 320, 782]},
                    ],
                }
            )
        repeated = layout.repeated_marginalia(pages)
        self.assertIn("Demo2026InterimReport", repeated)
        self.assertNotIn("合计", repeated)

    def test_same_page_sections_keep_object_boundaries(self):
        headings = [
            {"title": "第一节 释义", "object_id": "o1", "physical_page": 4, "order": 1, "previous_object_id": "o0"},
            {"title": "第二节 公司简介", "object_id": "o3", "physical_page": 4, "order": 3, "previous_object_id": "o2"},
        ]
        result = sections.build_section_tree({"headings": headings, "page_last_object": {"5": "o9"}, "page_count": 5})
        self.assertEqual(result[0]["end_object_id"], "o2")
        self.assertEqual(result[0]["physical_page_range"], [4, 4])


class TestM1Integration(unittest.TestCase):
    def test_standard_package_is_traceable_and_not_computable(self):
        with tempfile.TemporaryDirectory() as td:
            td = pathlib.Path(td)
            pdf = td / "600519_Demo_2026-interim.pdf"
            c = canvas.Canvas(str(pdf), pagesize=(400, 600))
            for page in range(1, 4):
                c.drawString(40, 570, "Demo 2026 Interim Report")
                c.drawString(40, 530, f"Page {page} native text content retained for evidence.")
                if page == 2:
                    for x in (40, 180, 320):
                        c.line(x, 420, x, 500)
                    for y in (420, 460, 500):
                        c.line(40, y, 320, y)
                    c.drawString(50, 475, "Revenue")
                    c.drawString(190, 475, "2026")
                    c.drawString(50, 435, "Total")
                    c.drawString(190, 435, "100")
                c.drawString(180, 20, f"{page} / 3")
                c.showPage()
            c.save()

            out = td / "package"
            manifest = standard.parse_native_text_package(pdf, out)
            self.assertEqual(manifest["source_pdf"]["page_count"], 3)
            self.assertEqual(manifest["artifacts"]["computable_table_cells"], 0)
            self.assertTrue((out / "正文/page-0002.md").is_file())
            self.assertTrue((out / "_internal/pages/page-0003.json").is_file())
            table_index = json.loads((out / "表格/index.json").read_text(encoding="utf-8"))
            self.assertGreaterEqual(len(table_index["tables"]), 1)
            self.assertTrue(all(not t["eligible_for_calculation"] for t in table_index["tables"]))
            quality = json.loads((out / "索引/quality.json").read_text(encoding="utf-8"))
            self.assertIsNone(quality["coverage"]["content_retention"])
            self.assertEqual(quality["coverage"]["page_processing_rate"], 1.0)
            self.assertIsNone(quality["coverage"]["review_rate"])
            self.assertEqual(quality["usage_eligibility_summary"]["citable"], 0)
            self.assertEqual(quality["usage_eligibility_summary"]["ordinary_prose_citable"], 0)
            self.assertGreaterEqual(quality["review_burden"]["open_tables"], 1)
            self.assertEqual(
                manifest["artifacts"]["table_status"]["detected_fragments"],
                len(table_index["tables"]),
            )
            entry = (out / "00-阅读入口.md").read_text(encoding="utf-8")
            self.assertIn("当前仅依据文件名登记", entry)
            self.assertNotIn("第 1 页内容已交叉登记", entry)
            failures = [r for r in validation.validate_package(out) if r.get("valid") is False]
            self.assertEqual(failures, [])


if __name__ == "__main__":
    unittest.main()
