"""P0 骨架冒烟测试（仅标准库）。运行：python3 -m unittest discover -s tests"""

import json
import pathlib
import sys
import tempfile
import unittest

_SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))

from pipeline import runner, validation  # noqa: E402
from pipeline.semantics import normalize_decimal  # noqa: E402
from pipeline.geometry import bbox_from_engine  # noqa: E402


class TestIntakeScaffold(unittest.TestCase):
    def test_sha_and_identity(self):
        from pipeline import intake

        with tempfile.TemporaryDirectory() as td:
            p = pathlib.Path(td) / "600519_贵州茅台_2026年半年度报告.pdf"
            p.write_bytes(b"%PDF-1.4 fake\n")
            digest = intake.sha256_of(p)
            self.assertEqual(len(digest), 64)
            ident = intake.identity_from_filename(p)
            self.assertEqual(
                ident["issuer_identity"]["company_name_as_reported"], "贵州茅台"
            )
            self.assertEqual(ident["report_identity"]["report_type"], "2026年半年度报告")

    def test_scaffold_creates_package(self):
        from pipeline import intake

        with tempfile.TemporaryDirectory() as td:
            td = pathlib.Path(td)
            pdf = td / "600519_贵州茅台_2026年半年度报告.pdf"
            pdf.write_bytes(b"%PDF-1.4 fake\n")
            out = td / "pkg"
            manifest = runner.scaffold_package(pdf, output_dir=out)
            # 关键文件
            for rel in (
                "00-阅读入口.md",
                "_internal/run.json",
                "索引/manifest.json",
                "索引/quality.json",
                "索引/document_map.json",
                "索引/review_queue.jsonl",
                "表格/index.json",
                "图表/index.json",
            ):
                self.assertTrue((out / rel).is_file(), rel)
            # 状态诚实
            self.assertEqual(manifest["overall_state"], "p0_skeleton_not_parsed")
            m = json.loads((out / "索引/manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(m["overall_state"], "p0_skeleton_not_parsed")
            run = json.loads((out / "_internal/run.json").read_text(encoding="utf-8"))
            self.assertEqual(run["mode"], "p0_scaffold")
            # 结束哈希已核对
            self.assertEqual(run["sha256_end"], intake.sha256_of(pdf))

    def test_refuse_overwrite(self):
        with tempfile.TemporaryDirectory() as td:
            td = pathlib.Path(td)
            pdf = td / "x_2025年度报告.pdf"
            pdf.write_bytes(b"%PDF-1.4 fake\n")
            out = td / "pkg"
            out.mkdir()
            (out / "已有文件.txt").write_text("x", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                runner.scaffold_package(pdf, output_dir=out)


class TestSemantics(unittest.TestCase):
    def test_normalize_scale(self):
        self.assertEqual(normalize_decimal("1,234.50", "千元"), "1234500.00")
        self.assertEqual(normalize_decimal("(1,234.50)", "千元"), "-1234500.00")
        # 单位未知必须为空，不猜倍率
        self.assertIsNone(normalize_decimal("123", None))
        self.assertIsNone(normalize_decimal("123", "万元/亿元"))

    def test_geometry(self):
        # MinerU 0-1000 归一化：x、y 均按 1000 归一
        b = bbox_from_engine((1000, 500), (0, 0, 100, 100), 0)
        self.assertEqual(b, (0.0, 0.0, 100.0, 50.0))


class TestSelfcheck(unittest.TestCase):
    def test_selfcheck_clean(self):
        problems = validation.selfcheck()
        self.assertEqual(problems, [], problems)


class TestSchemaValidation(unittest.TestCase):
    """Step1：Schema 校验真正接线（jsonschema 已装时严格校验；未装则跳过不伪装）。"""

    def test_schema_files_self_compliant(self):
        # jsonschema 未安装时返回提示问题；装了则必须全合规
        problems = validation.validate_schema_files()
        if not validation._jsonschema_available():
            self.assertTrue(problems)  # 有“未安装”提示
            return
        self.assertEqual(problems, [], problems)

    def test_scaffold_products_pass_schema(self):
        with tempfile.TemporaryDirectory() as td:
            td = pathlib.Path(td)
            pdf = td / "600519_贵州茅台_2026年半年度报告.pdf"
            pdf.write_bytes(b"%PDF-1.4 fake\n")
            out = td / "pkg"
            runner.scaffold_package(pdf, output_dir=out)
            results = validation.validate_package(out)
            if not validation._jsonschema_available():
                # 未装时如实报告未检查，不伪装通过
                self.assertTrue(results)
                return
            self.assertEqual(len(results), 4)
            for r in results:
                self.assertTrue(r.get("valid"), f"{r['file']}: {r.get('error')}")
            # quality 已回填 schema_check
            q = json.loads((out / "索引/quality.json").read_text(encoding="utf-8"))
            self.assertTrue(q["schema_check"]["available"])
            self.assertEqual(q["schema_check"]["failed"], [])

    def test_validate_json_ref_offline(self):
        # 跨文件 $ref（manifest→identity）离线可解析，不依赖网络
        if not validation._jsonschema_available():
            self.skipTest("jsonschema 未安装")
        ident = {
            "document_id": "600519_贵州茅台",
            "file_identity": {
                "filename": "x.pdf",
                "original_path": "/x.pdf",
                "sha256": "a" * 64,
                "page_count": None,
                "file_size_bytes": 10,
                "source_url": None,
            },
            "verification": {"resolved_from": "filename"},
        }
        res = validation.validate_json(ident, "identity")
        self.assertTrue(res["valid"], res)


if __name__ == "__main__":
    unittest.main()
