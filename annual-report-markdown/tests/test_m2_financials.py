import json
import pathlib
import tempfile
import unittest

from pipeline import exports, financials, naming, semantics


def _cell(row, col, text):
    x0 = [10, 150, 200, 300][col]
    x1 = [150, 200, 300, 400][col]
    return {
        "cell_id": f"cell_r{row}_c{col}", "row": row, "col": col,
        "rowspan": 1, "colspan": 1, "raw_value": text, "text": text,
        "kind": "other", "bbox": [x0, 50 + row * 20, x1, 70 + row * 20],
        "evidence_refs": [], "eligible_for_calculation": False,
    }


class TestM2Financials(unittest.TestCase):
    def test_income_tax_validation_uses_reported_sign(self):
        statement = {
            "entity_scope": "consolidated", "statement_type": "income_statement",
            "period_columns": [{"comparison_state": "current"}],
        }
        signed = financials._validation(statement, {"current": {
            "profit_before_tax": financials.Decimal("178993"),
            "income_tax_expense": financials.Decimal("-27867"),
            "net_profit": financials.Decimal("151126"),
        }}, {"current": {"income_tax_expense": {
            "row_label": "减：所得税费用", "raw_value": "(27,867)",
            "parenthesized_negative": True,
        }}})[0]
        self.assertEqual(signed["result"], "passed")
        self.assertEqual(signed["formula_model"], "signed_tax_line_addition")

        positive = financials._validation(statement, {"current": {
            "profit_before_tax": financials.Decimal("178993"),
            "income_tax_expense": financials.Decimal("27867"),
            "net_profit": financials.Decimal("151126"),
        }})[0]
        self.assertEqual(positive["result"], "passed")
        self.assertEqual(positive["formula_model"], "positive_expense_subtraction")

    def test_banking_profile_requires_statement_label_anchors(self):
        banking = financials._detect_industry_profile([
            "存放中央银行款项", "拆出资金", "贷款和垫款", "客户存款",
        ])
        self.assertEqual(banking["profile"], "banking")
        self.assertGreaterEqual(len(banking["matched_anchors"]), 3)
        generic = financials._detect_industry_profile(["货币资金", "存货", "营业收入"])
        self.assertEqual(generic["profile"], "generic_corporate")
        banking_income = financials._detect_industry_profile(["净利息收入", "手续费及佣金收入"])
        self.assertEqual(banking_income["profile"], "banking")

    def test_insurance_profile_takes_precedence_in_mixed_financial_statement(self):
        profile = financials._detect_industry_profile([
            "保险服务收入", "保险服务费用", "保险合同负债",
            "净利息收入", "手续费及佣金收入",
        ])
        self.assertEqual(profile["profile"], "insurance")
        self.assertEqual(len(profile["insurance_matched_anchors"]), 3)

    def test_statement_label_must_not_be_punctuation_only(self):
        self.assertFalse(financials._usable_statement_label("/"))
        self.assertFalse(financials._usable_statement_label("—"))
        self.assertTrue(financials._usable_statement_label("五、净利润"))

    def test_bank_cash_flow_aliases(self):
        self.assertEqual(financials.CONCEPTS["四、汇率变动对现金及现金等价物的影响额"],
                         "effect_of_exchange_rate_changes")
        self.assertEqual(financials.CONCEPTS["五、现金及现金等价物净（减少）/增加额"],
                         "net_increase_in_cash")
        self.assertEqual(financials.CONCEPTS["加：年初现金及现金等价物余额"], "cash_at_beginning")
        self.assertEqual(financials.CONCEPTS["六、年末现金及现金等价物余额"], "cash_at_end")
        self.assertEqual(financials.CONCEPTS["经营活动使用的现金流量净额"],
                         "net_cash_from_operating_activities")
        self.assertEqual(financials.CONCEPTS["五、现金及现金等价物净增加/（减少）额"],
                         "net_increase_in_cash")

    def test_period_headers(self):
        self.assertEqual(
            financials._period_from_header("2026 年 6 月 30 日", "balance_sheet")["end"],
            "2026-06-30",
        )
        self.assertEqual(
            financials._period_from_header("2025 年半年度", "income_statement"),
            {"kind": "duration", "start": "2025-01-01", "end": "2025-06-30"},
        )
        self.assertEqual(
            financials._period_from_header("2025年", "cash_flow_statement"),
            {"kind": "duration", "start": "2025-01-01", "end": "2025-12-31"},
        )
        self.assertEqual(
            financials._period_from_header("截至2026年6月30日止6个月期间（未经审计）", "income_statement"),
            {"kind": "duration", "start": "2026-01-01", "end": "2026-06-30"},
        )
        self.assertIsNone(financials._period_from_header("2025年", "balance_sheet"))
        self.assertEqual(
            financials._period_from_relative_header("期末余额", "balance_sheet", "2026-06-30"),
            {"kind": "instant", "start": None, "end": "2026-06-30"},
        )
        self.assertEqual(
            financials._period_from_relative_header("期初余额", "balance_sheet", "2026-06-30"),
            {"kind": "instant", "start": None, "end": "2025-12-31"},
        )
        self.assertIsNone(financials._period_from_relative_header(
            "期初余额", "balance_sheet", None
        ))

    def test_bounded_title_normalization(self):
        allowed = naming.MAIN_STATEMENT_TITLES
        self.assertEqual(naming.canonical_statement_title("1. 合并资产负债表", allowed)[:2],
                         ("合并资产负债表", False))
        self.assertEqual(naming.canonical_statement_title("中期公司现金流量表", allowed)[:2],
                         ("公司现金流量表", False))
        self.assertEqual(naming.canonical_statement_title("年度合并利润表", allowed)[:2],
                         ("合并利润表", False))
        self.assertEqual(naming.canonical_statement_title("2025年度合并资产负债表（续）", allowed)[:2],
                         ("合并资产负债表", True))
        self.assertEqual(naming.canonical_statement_title("年 月 日合并资产负债表", allowed)[:2],
                         ("合并资产负债表", False))
        self.assertEqual(naming.canonical_statement_title("2025年12月31日公司资产负债表", allowed)[:2],
                         ("公司资产负债表", False))
        self.assertEqual(naming.canonical_statement_title("二、合并利润表（续）", allowed)[:2],
                         ("合并利润表", True))
        self.assertIsNone(naming.canonical_statement_title("合并利润表相关说明", allowed)[0])
        self.assertIsNone(naming.canonical_statement_title("年月日合并利润表相关说明", allowed)[0])

    def test_explicit_cny_unit_variants(self):
        self.assertEqual(semantics.parse_unit_currency("单位：千元 币种：人民币"), ("千元", "CNY"))
        self.assertEqual(semantics.parse_unit_currency("除特别注明外，金额单位为人民币百万元"),
                         ("百万元", "CNY"))
        self.assertEqual(semantics.parse_unit_currency("货币单位均以人民币百万元列示"),
                         ("百万元", "CNY"))
        self.assertEqual(semantics.parse_unit_currency("单位：万元"), ("万元", None))
        self.assertEqual(semantics.normalize_decimal("(1.25)", "百万元"), "-1250000.00")

    def test_cas_compliance_variants(self):
        self.assertTrue(semantics.is_cas_compliance_statement(
            "本财务报表符合财政部颁布并生效的企业会计准则要求"))
        self.assertTrue(semantics.is_cas_compliance_statement(
            "本合并财务报表符合企业会计准则的要求"))
        self.assertTrue(semantics.is_cas_compliance_statement(
            "本公司所编制的财务报表符合企业会计准则的要求"))
        self.assertTrue(semantics.is_cas_compliance_statement(
            "本公司编制的财务报表符合企业会计准则的要求"))
        self.assertTrue(semantics.is_cas_compliance_statement(
            "本中期简要财务报表根据中华人民共和国财政部颁布的《企业会计准则第32号——中期财务报告》编制"))
        self.assertFalse(semantics.is_cas_compliance_statement("公司执行企业会计准则"))
        self.assertFalse(semantics.is_cas_compliance_statement(
            "本期讨论了企业会计准则第32号的影响"))
        self.assertTrue(semantics.is_cny_functional_currency_statement(
            "本公司以人民币为记账本位币，本公司的个别子公司采用其他货币"))
        self.assertFalse(semantics.is_cny_functional_currency_statement(
            "公司持有人民币普通股"))

    def test_cny_functional_currency_variants(self):
        # 记账/记帐两种写法；为/是；本公司/公司/集团；人民币在前的表述。
        self.assertTrue(semantics.is_cny_functional_currency_statement("本公司以人民币为记账本位币"))
        self.assertTrue(semantics.is_cny_functional_currency_statement("公司采用人民币作为记账本位币"))
        self.assertTrue(semantics.is_cny_functional_currency_statement("本公司记账本位币为人民币"))
        self.assertTrue(semantics.is_cny_functional_currency_statement("本公司记帐本位币是人民币"))
        self.assertTrue(semantics.is_cny_functional_currency_statement("人民币为本公司的记账本位币"))
        self.assertTrue(semantics.is_cny_functional_currency_statement("集团采用的记账本位币为人民币"))
        self.assertTrue(semantics.is_cny_functional_currency_statement("记账本位币为人民币"))
        # 不匹配：非人民币本位币、一般人民币提及、错误用词。
        self.assertFalse(semantics.is_cny_functional_currency_statement("公司以美元为记账本位币"))
        self.assertFalse(semantics.is_cny_functional_currency_statement("公司持有人民币普通股"))
        self.assertFalse(semantics.is_cny_functional_currency_statement("以人民币为记账准备"))
        self.assertFalse(semantics.is_cny_functional_currency_statement(""))

    def test_native_cell_match_tolerates_vertical_font_offsets(self):
        page = {"words": [
            {"text": "2025", "x0": 10, "x1": 35, "top": 10.0, "bottom": 20},
            {"text": "年", "x0": 36, "x1": 42, "top": 11.5, "bottom": 21},
            {"text": "12", "x0": 43, "x1": 55, "top": 9.8, "bottom": 20},
            {"text": "月", "x0": 56, "x1": 62, "top": 11.0, "bottom": 21},
            {"text": "31", "x0": 63, "x1": 75, "top": 10.2, "bottom": 20},
            {"text": "日", "x0": 76, "x1": 82, "top": 11.4, "bottom": 21},
        ]}
        self.assertTrue(financials._cell_matches_native_words(page, [5, 5, 90, 25], "2025年12月31日"))

    def test_balance_sheet_evidence_and_equation(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            (root / "表格").mkdir()
            rows = [
                ["项目", "附注", "2026 年 6 月 30 日", "2025 年 12 月 31 日"],
                ["资产总计", "", "100", "90"],
                ["负债合计", "", "40", "35"],
                ["所有者权益（或股东权益）合计", "", "60", "55"],
            ]
            cells = [_cell(r, c, value) for r, row in enumerate(rows) for c, value in enumerate(row)]
            doc = {
                "table_fragment_id": "table-0001", "physical_page": 1,
                "row_count": 4, "column_count": 4, "cells": cells,
                "candidate_status": "needs_review", "eligible_for_calculation": False,
            }
            exports.write_json(root / "表格/table-0001.json", doc)
            native_words = [
                {"text": c["raw_value"], "x0": c["bbox"][0], "top": c["bbox"][1],
                 "x1": c["bbox"][2], "bottom": c["bbox"][3]}
                for c in cells if c["raw_value"]
            ]
            pages = [
                {"physical_page": 1, "words": native_words, "lines": [
                    {"text": "合并资产负债表", "bbox": [10, 10, 150, 20]},
                    {"text": "单位：元 币种：人民币", "bbox": [10, 30, 180, 40]},
                ]},
                {"physical_page": 2, "lines": [
                    {"text": "本公司所编制的财务报表符合企业会计准则的要求", "bbox": [10, 10, 300, 20]},
                ]},
            ]
            items = [{"table_fragment_id": "table-0001", "physical_page": 1,
                      "bbox": [10, 50, 400, 130], "json": "表格/table-0001.json",
                      "eligible_for_calculation": False, "candidate_status": "needs_review"}]
            review = [{"object_ref": "表格/table-0001.json", "status": "open", "reason": "candidate"}]
            result = financials.build_main_statements(
                root, pages, items, [], review, {"document_id": "demo"}, "test"
            )
            self.assertEqual(len(result["logical_tables"]), 1)
            self.assertEqual(len(result["facts"]), 6)
            self.assertTrue(all(x["quality"]["eligible_for_calculation"] for x in result["facts"]))
            self.assertTrue(all(x["result"] == "passed" for x in result["validations"]))
            updated = json.loads((root / "表格/table-0001.json").read_text())
            self.assertEqual(updated["belongs_to_logical_table"], "logical-table-0001")
            self.assertEqual(review[0]["status"], "resolved")
            financials.write_facts_csv(root / "数据/facts.csv", result["facts"])
            csv_text = (root / "数据/facts.csv").read_text(encoding="utf-8-sig")
            self.assertIn("assets_total", csv_text)
            self.assertIn("2026-06-30", csv_text)

    def test_three_column_relative_balance_with_currency_bridge(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            (root / "表格").mkdir()
            rows = [
                ["项目", "期末余额", "期初余额"],
                ["资产总计", "100", "90"],
                ["负债合计", "40", "35"],
                ["所有者权益合计", "60", "55"],
            ]
            cells = [_cell(r, c, value) for r, row in enumerate(rows) for c, value in enumerate(row)]
            doc = {
                "table_fragment_id": "table-0001", "physical_page": 1,
                "row_count": 4, "column_count": 3, "cells": cells,
                "candidate_status": "needs_review", "eligible_for_calculation": False,
            }
            exports.write_json(root / "表格/table-0001.json", doc)
            native_words = [
                {"text": c["raw_value"], "x0": c["bbox"][0], "top": c["bbox"][1],
                 "x1": c["bbox"][2], "bottom": c["bbox"][3]}
                for c in cells if c["raw_value"]
            ]
            pages = [
                {"physical_page": 1, "words": native_words, "lines": [
                    {"text": "1.合并资产负债表", "bbox": [10, 10, 150, 20]},
                    {"text": "2026年06月30日", "bbox": [10, 22, 150, 30]},
                    {"text": "单位：元", "bbox": [10, 32, 80, 40]},
                ]},
                {"physical_page": 2, "lines": [
                    {"text": "本公司以人民币为记账本位币", "bbox": [10, 10, 250, 20]},
                    {"text": "本公司编制的财务报表符合企业会计准则的要求", "bbox": [10, 30, 350, 40]},
                ]},
            ]
            items = [{"table_fragment_id": "table-0001", "physical_page": 1,
                      "bbox": [10, 50, 300, 130], "json": "表格/table-0001.json",
                      "eligible_for_calculation": False, "candidate_status": "needs_review"}]
            result = financials.build_main_statements(
                root, pages, items, [], [], {"document_id": "demo"}, "test"
            )
            self.assertEqual(len(result["facts"]), 6)
            logical = result["logical_tables"][0]
            self.assertEqual(logical["currency"], "CNY")
            self.assertEqual(logical["currency_basis"], "functional_currency_declaration")
            self.assertEqual([p["period"]["end"] for p in logical["period_columns"]],
                             ["2026-06-30", "2025-12-31"])
            self.assertTrue(all(len(p["evidence_refs"]) == 2 for p in logical["period_columns"]))
            self.assertTrue(all(len(f["evidence_refs"]["unit_currency"]) == 2
                                for f in result["facts"]))


if __name__ == "__main__":
    unittest.main()
