import unittest

from pipeline import equity


def _case(opening="100", change="20", ending="120"):
    rows = [
        ["项目", "股本"],
        ["二、本年年初余额", opening],
        ["三、本年增减变动金额", change],
        ["四、本年年末余额", ending],
    ]
    cells, words = [], []
    for ri, row in enumerate(rows):
        for ci, value in enumerate(row):
            bbox = [10 + ci * 120, 40 + ri * 24, 120 + ci * 120, 60 + ri * 24]
            cells.append({"row": ri, "col": ci, "raw_value": value, "text": value, "bbox": bbox})
            words.append({"text": value, "x0": bbox[0], "x1": bbox[2],
                          "top": bbox[1], "bottom": bbox[3]})
    doc = {"table_fragment_id": "table-0001", "physical_page": 8,
           "row_count": len(rows), "column_count": 2, "cells": cells}
    assessment = {
        "rows": equity._cells_by_row(doc), "header_rows": 1,
        "effective_column_count": 2,
        "column_header_paths": [["项目"], ["2025年度", "股本"]],
        "column_header_evidence": {"1": "evidence-header"},
        "period": "2025年度", "native_reconstruction": None,
    }
    page = {"physical_page": 8, "words": words, "lines": []}
    context = {
        "unit": "元", "currency": "CNY", "unit_evidence": "evidence-unit",
        "accounting_evidence": "evidence-basis", "title_evidence": "evidence-title",
        "period_evidence": {"table-0001": "evidence-period"},
        "comparison_state": {"table-0001": "current"},
    }
    return doc, assessment, page, context


class TestC3Equity(unittest.TestCase):
    def test_balanced_explicit_triple_emits_three_facts(self):
        doc, assessment, page, context = _case()
        evidence = []
        facts, candidates, validations = equity._build_c3_facts(
            {"document_id": "demo"}, "logical-table-0007", "consolidated",
            doc, assessment, page, evidence, "test", context,
        )
        self.assertEqual(len(facts), 3)
        self.assertEqual(candidates, [])
        self.assertEqual(validations[0]["result"], "passed")
        self.assertEqual({f["equity_movement_role"] for f in facts}, {"opening", "change", "ending"})
        self.assertTrue(all(f["quality"]["eligible_for_calculation"] for f in facts))
        self.assertEqual(next(f for f in facts if f["equity_movement_role"] == "opening")["period"]["end"],
                         "2025-01-01")

    def test_failed_relation_stays_candidate(self):
        doc, assessment, page, context = _case(ending="119")
        facts, candidates, validations = equity._build_c3_facts(
            {"document_id": "demo"}, "logical-table-0007", "consolidated",
            doc, assessment, page, [], "test", context,
        )
        self.assertEqual(facts, [])
        self.assertEqual(len(candidates), 3)
        self.assertEqual(validations[0]["result"], "failed")
        self.assertTrue(all(not c["usage_eligibility"]["computable"] for c in candidates))

    def test_blank_is_not_assumed_zero(self):
        doc, assessment, page, context = _case(change="")
        facts, candidates, validations = equity._build_c3_facts(
            {"document_id": "demo"}, "logical-table-0007", "consolidated",
            doc, assessment, page, [], "test", context,
        )
        self.assertEqual(facts, [])
        self.assertEqual(candidates, [])
        self.assertEqual(validations[0]["result"], "insufficient")

    def test_half_year_roles_have_correct_period_shape(self):
        self.assertEqual(equity._period_for_role("2026年半年度", "opening")["end"], "2026-01-01")
        self.assertEqual(equity._period_for_role("2026年半年度", "change"),
                         {"kind": "duration", "start": "2026-01-01", "end": "2026-06-30"})
        self.assertEqual(equity._period_for_role("2026年半年度", "ending")["end"], "2026-06-30")


if __name__ == "__main__":
    unittest.main()
