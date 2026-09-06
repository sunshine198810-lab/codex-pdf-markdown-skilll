import json
import pathlib
import tempfile
import unittest

import audit_regression_matrix as audit


class TestC31RegressionMatrix(unittest.TestCase):
    def test_title_variant_normalization_is_bounded(self):
        self.assertEqual(audit._title_variant("1.合并资产负债表"), "合并资产负债表")
        self.assertEqual(audit._title_variant("中期公司现金流量表"), "公司现金流量表")
        self.assertEqual(audit._title_variant("合并资产负债表（续）"), "合并资产负债表")
        self.assertIsNone(audit._title_variant("合并资产负债表相关说明"))

    def test_conservative_empty_package_is_not_called_capable(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            for sub in ("索引", "_internal", "数据", "正文", "表格"):
                (root / sub).mkdir()
            (root / "索引/manifest.json").write_text(json.dumps({
                "source_pdf": {"path": "/tmp/demo.pdf", "page_count": 1},
                "artifacts": {"tables": 0}, "run": {"pipeline_version": "test"},
            }), encoding="utf-8")
            (root / "_internal/run.json").write_text(json.dumps({
                "config": {"auto_borderless": {"status": "not_needed"}}
            }), encoding="utf-8")
            (root / "索引/quality.json").write_text(json.dumps({
                "schema_check": {"failed": []}
            }), encoding="utf-8")
            (root / "正文/page-0001.md").write_text("1.合并资产负债表\n", encoding="utf-8")
            result = audit.audit_package(root)
            self.assertTrue(result["safety_ok"])
            self.assertEqual(result["capability_state"], "no_supported_logical_table")
            self.assertIn("main_titles_present_but_no_logical_tables", result["issue_classes"])


if __name__ == "__main__":
    unittest.main()
