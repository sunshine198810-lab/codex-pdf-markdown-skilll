import json
import pathlib
import sys
import tempfile
import unittest

_SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))

from pipeline import evidence_chain  # noqa: E402


def cell(row, col, text):
    return {"row": row, "col": col, "text": text}


class TestNormalizeNumeric(unittest.TestCase):
    def test_strips_format_commas_and_parens(self):
        self.assertEqual(evidence_chain.normalize_numeric("1,234.50"), "1234.5")
        self.assertEqual(evidence_chain.normalize_numeric("(1,234)"), "-1234")
        self.assertEqual(evidence_chain.normalize_numeric(" 1,234,567 "), "1234567")

    def test_percent_keeps_numeric_core(self):
        parsed = evidence_chain.parse_numeric("5.5%")
        self.assertEqual(parsed, {"value": "5.5", "percent": True})

    def test_blank_or_non_numeric_returns_none(self):
        self.assertIsNone(evidence_chain.normalize_numeric(""))
        self.assertIsNone(evidence_chain.normalize_numeric("–"))
        self.assertIsNone(evidence_chain.normalize_numeric("—"))
        self.assertIsNone(evidence_chain.normalize_numeric(None))
        self.assertIsNone(evidence_chain.normalize_numeric("N/A"))


class TestCompareChannelCandidates(unittest.TestCase):
    def _cand(self, channel_id, text):
        return {"channel_id": channel_id,
                "raw_value": text,
                "normalized_value": evidence_chain.normalize_numeric(text)}

    def test_consistent_when_multiple_channels_agree(self):
        cands = [self._cand("mineru", "1,234.50"), self._cand("image-ocr", "1234.50")]
        verdict = evidence_chain.compare_channel_candidates(cands)
        self.assertEqual(verdict["verdict"], "consistent")
        self.assertEqual(verdict["normalized_values"], ["1234.5"])

    def test_conflict_when_channels_disagree(self):
        cands = [self._cand("mineru", "1,234"), self._cand("image-ocr", "1,235")]
        verdict = evidence_chain.compare_channel_candidates(cands)
        self.assertEqual(verdict["verdict"], "conflict")
        self.assertEqual(verdict["normalized_values"], ["1234", "1235"])

    def test_single_channel_cannot_cross_validate(self):
        cands = [self._cand("mineru", "1,234")]
        verdict = evidence_chain.compare_channel_candidates(cands)
        self.assertEqual(verdict["verdict"], "single_channel")

    def test_insufficient_when_no_usable_candidate(self):
        cands = [self._cand("mineru", "—"), self._cand("image-ocr", "N/A")]
        verdict = evidence_chain.compare_channel_candidates(cands)
        self.assertEqual(verdict["verdict"], "insufficient")


class TestPerCellConsistency(unittest.TestCase):
    def test_single_channel_grid_reports_single_channel_verdicts(self):
        grid = {"cells": [cell(1, 1, "1,234"), cell(1, 2, "货币资金")]}
        result = evidence_chain.per_cell_consistency({"mineru": grid})
        self.assertFalse(result["second_channel_present"])
        self.assertEqual(result["channels"], ["mineru"])
        # 数值格为 single_channel；非数值标签格为 insufficient
        self.assertEqual(result["counts"]["single_channel"], 1)
        self.assertEqual(result["counts"]["insufficient"], 1)
        self.assertEqual(result["per_cell"]["r1c1"]["verdict"], "single_channel")
        self.assertEqual(result["per_cell"]["r1c2"]["verdict"], "insufficient")

    def test_two_channels_consistent_marks_consistent_counts(self):
        grid_a = {"cells": [cell(1, 1, "1,234")]}
        grid_b = {"cells": [cell(1, 1, "1234")]}
        result = evidence_chain.per_cell_consistency({"mineru": grid_a, "image-ocr": grid_b})
        self.assertTrue(result["second_channel_present"])
        self.assertEqual(result["per_cell"]["r1c1"]["verdict"], "consistent")


class TestRegisterChannel(unittest.TestCase):
    def test_register_and_list_channels(self):
        evidence_chain.register_channel("image-ocr", "paddleocr", engine_version="1.0",
                                        method="image_ocr")
        channels = evidence_chain.list_channels()
        self.assertIn("image-ocr", channels)
        self.assertEqual(channels["image-ocr"]["engine"], "paddleocr")


class TestSignOffRecords(unittest.TestCase):
    def test_new_signoff_defaults_to_pending_and_not_eligible(self):
        rec = evidence_chain.new_signoff("复核/vector-outline-index.json#vector-statement-01")
        self.assertEqual(rec["decision"], "pending")
        self.assertFalse(rec["eligible_for_calculation"])

    def test_eligible_only_when_accepted_and_flag_set(self):
        rec = evidence_chain.new_signoff(
            "复核/vector-outline-index.json#vector-statement-01",
            decision="accepted", eligible_for_calculation=True, reviewer="eric")
        self.assertTrue(rec["eligible_for_calculation"])

    def test_non_accepted_cannot_be_eligible(self):
        rec = evidence_chain.new_signoff(
            "复核/vector-outline-index.json#vector-statement-01",
            decision="rejected", eligible_for_calculation=True)
        self.assertFalse(rec["eligible_for_calculation"])

    def test_rejects_unknown_decision(self):
        with self.assertRaises(ValueError):
            evidence_chain.new_signoff("x", decision="unknown")

    def test_write_load_aggregate_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "signoffs.jsonl"
            evidence_chain.write_signoffs(path, [
                evidence_chain.new_signoff("a", decision="accepted", eligible_for_calculation=True, reviewer="r1"),
                evidence_chain.new_signoff("b", decision="pending"),
            ])
            loaded = evidence_chain.load_signoffs(path)
            self.assertEqual(len(loaded), 2)
            agg = evidence_chain.aggregate_signoffs(loaded)
            self.assertEqual(agg["total"], 2)
            self.assertEqual(agg["eligible_for_calculation"], 1)
            self.assertEqual(agg["pending"], 1)

    def test_load_missing_file_returns_empty(self):
        self.assertEqual(evidence_chain.load_signoffs("/tmp/nonexistent-signoffs.jsonl"), [])


if __name__ == "__main__":
    unittest.main()
