"""切片 → 研究包组装测试（assemble.py）。"""

import json
import pathlib
import sys
import tempfile
import unittest

_SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))

from pipeline import assemble, exports, intake, validation  # noqa: E402
from pipeline.version import SCHEMA_VERSION  # noqa: E402


def _make_slice(sd: pathlib.Path, *, code="600519", company="贵州茅台", tables=2, pdf_path=None):
    """构造一个最小 run_tableslice 式切片目录（含 manifest/quality/表格/正文/证据）。"""
    sd = pathlib.Path(sd)
    for sub in ("正文", "表格", "索引"):
        (sd / sub).mkdir(parents=True, exist_ok=True)
    # 真实存在的假 PDF（assemble 会对源做 file_size/sha 登记）
    pdf = pdf_path or (sd.parent / f"{code}_{company}_2026年半年度报告.pdf")
    pdf = pathlib.Path(pdf)
    if not pdf.is_file():
        pdf.write_bytes(b"%PDF-1.4 fake\n")
    sha = intake.sha256_of(pdf)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "package_root": str(sd),
        "source_pdf": {"path": str(pdf), "sha256": sha},
        "identity": {
            "document_id": f"{code}_{company}",
            "issuer_identity": {"company_name_as_reported": company},
            "report_identity": {"report_type": "2026年半年度报告"},
            "verification": {"resolved_from": "filename"},
        },
        "run": {"run_id": "20260905T000000", "pipeline_version": "0.2.0-p0"},
        "generated_at": "2026-09-05T00:00:00+08:00",
        "artifacts": {"tables": tables, "structured_tables": tables},
        "overall_state": "partial",
        "open_issues": ["未人工核对"],
    }
    exports.write_json(sd / "索引/manifest.json", manifest)
    quality = {
        "schema_version": SCHEMA_VERSION,
        "report_generated_at": "2026-09-05T00:00:00+08:00",
        "coverage": {"content_retention": None, "structure_recovery": None,
                     "computable": None, "review_rate": None},
        "five_layers": {"run_and_files": {"ok": True}, "content_coverage": {},
                        "structure": {"candidate_tables": tables, "verified": 0}},
        "object_status_summary": {"extracted": tables},
        "usage_eligibility_summary": {"computable": 0, "citable": 0, "traceable": tables},
        "overall_state": "partial",
    }
    exports.write_json(sd / "索引/quality.json", quality)
    exports.write_jsonl(sd / "索引/evidence.jsonl", [])
    exports.write_json(sd / "表格/index.json", {"tables": [], "state": "candidate_only"})
    for i in range(1, tables + 1):
        tid = f"table-{i:04d}"
        doc = {
            "logical_table_id": tid,
            "physical_page": 10 + i,
            "caption_candidates": [f"测试表{i}"],
            "footnote_candidates": [],
            "grid": {
                "rows": 2, "cols": 2,
                "cells": [
                    {"cell_id": "c0", "row": 0, "col": 0, "text": "项目", "kind": "column_header",
                     "candidate_status": "extracted",
                     "usage": {"computable": False}},
                    {"cell_id": "c1", "row": 0, "col": 1, "text": "期末余额", "kind": "column_header",
                     "candidate_status": "extracted",
                     "usage": {"computable": False}},
                ],
            },
            "evidence_ref": f"ev-{tid}",
            "candidate_status": "extracted",
            "overall_eligible_for_calculation": False,
        }
        exports.write_json(sd / "表格" / f"{tid}.json", doc)
        (sd / "表格" / f"{tid}.html").write_text(
            exports.table_document_html(tid, {"fragment": doc, "evidence": {}}),
            encoding="utf-8",
        )
    # reading order 候选
    ro = {"physical_page": 10, "width_pt": 100, "height_pt": 100,
          "reading_lines": [{"top": 0, "x0": 0, "text": "测试"}],
          "engine": "pdfplumber", "candidate_status": "extracted"}
    exports.write_json(sd / "正文/reading-order-p0010.json", ro)


class TestAssemble(unittest.TestCase):
    def test_assemble_basic_and_schema(self):
        with tempfile.TemporaryDirectory() as td:
            td = pathlib.Path(td)
            s1 = td / "slice1"
            _make_slice(s1, tables=2)
            out = td / "pkg"
            manifest = assemble.assemble_package([s1], out)
            self.assertEqual(manifest["overall_state"], "partial")
            self.assertEqual(manifest["artifacts"]["tables"], 2)
            # 关键文件
            for rel in (
                "00-阅读入口.md", "_internal/run.json", "索引/manifest.json",
                "索引/quality.json", "索引/document_map.json",
                "索引/review_queue.jsonl", "复核/index.html", "表格/index.json",
                "表格/table-0001.json", "表格/table-0001.html", "数据/facts.jsonl",
            ):
                self.assertTrue((out / rel).is_file(), rel)
            # schema 校验
            if validation._jsonschema_available():
                res = validation.validate_package(out)
                self.assertEqual(len(res), 5, res)
                for r in res:
                    self.assertTrue(r.get("valid"), f"{r['file']}: {r.get('error')}")

    def test_assemble_refuse_mismatched_sources(self):
        with tempfile.TemporaryDirectory() as td:
            td = pathlib.Path(td)
            s1, s2 = td / "s1", td / "s2"
            _make_slice(s1, tables=1)
            _make_slice(s2, tables=1)
            # 篡改 s2 的 sha 使其源不一致
            p = s2 / "索引/manifest.json"
            m = json.loads(p.read_text(encoding="utf-8"))
            m["source_pdf"]["sha256"] = "b" * 64
            exports.write_json(p, m)
            with self.assertRaises(ValueError):
                assemble.assemble_package([s1, s2], td / "pkg")

    def test_renumber_across_slices(self):
        with tempfile.TemporaryDirectory() as td:
            td = pathlib.Path(td)
            s1, s2 = td / "s1", td / "s2"
            _make_slice(s1, tables=1)
            _make_slice(s2, tables=1)
            out = td / "pkg"
            manifest = assemble.assemble_package([s1, s2], out)
            self.assertEqual(manifest["artifacts"]["tables"], 2)
            # 重编号后 id 唯一
            idx = json.loads((out / "表格/index.json").read_text(encoding="utf-8"))
            ids = [t["logical_table_id"] for t in idx["tables"]]
            self.assertEqual(len(ids), len(set(ids)))
            self.assertEqual(ids, ["table-0001", "table-0002"])


if __name__ == "__main__":
    unittest.main()
