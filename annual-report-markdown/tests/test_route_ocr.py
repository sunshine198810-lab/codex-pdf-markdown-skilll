"""D3 扫描 OCR 表 → 六类主表候选路由（route_ocr_main_statements）单测。"""

import pathlib
import sys
import unittest

_SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))

from route_ocr_main_statements import _cells_of_md_table, md_tables_with_page, route_statement  # noqa: E402


def _html(cells):
    tds = "".join(f"<td>{c}</td>" for c in cells)
    return f"<table><tr>{tds}</tr></table>"


class TestRouteOcr(unittest.TestCase):
    def test_balance_sheet_full(self):
        cells = ["资产", "2025", "2024", "流动资产合计", "非流动资产合计", "资产总计",
                 "流动负债合计", "负债合计", "所有者权益合计", "货币资金", "存货", "固定资产", "负债和所有者权益总计"]
        r = route_statement(cells)
        self.assertEqual(r["candidate"], "balance_sheet")
        self.assertEqual(r["side"], "full")
        self.assertEqual(r["scope"], "unresolved")
        self.assertGreaterEqual(len(r["matched"]), 5)

    def test_income_statement_consolidated(self):
        cells = ["营业收入", "营业成本", "营业利润", "利润总额", "净利润",
                 "归属于母公司股东的净利润", "少数股东损益", "基本每股收益", "2025年度", "2024年度"]
        r = route_statement(cells)
        self.assertEqual(r["candidate"], "income_statement")
        self.assertEqual(r["scope"], "consolidated")

    def test_cash_flow_statement(self):
        cells = ["一、经营活动产生的现金流量净额", "销售商品、提供劳务收到的现金",
                 "二、投资活动产生的现金流量净额", "购建固定资产、无形资产", "三、筹资活动产生的现金流量净额",
                 "四、汇率变动对现金的影响"]
        r = route_statement(cells)
        self.assertEqual(r["candidate"], "cash_flow_statement")

    def test_changes_in_equity(self):
        cells = ["一、上年年末余额", "加：会计政策变更", "二、本年年初余额", "三、本年增减变动金额",
                 "（一）所有者投入资本", "股本", "资本公积", "其他综合收益", "未分配利润", "所有者权益合计"]
        r = route_statement(cells)
        self.assertEqual(r["candidate"], "changes_in_equity")

    def test_non_main_table_is_unresolved(self):
        cells = ["常用词语释义", "证监会", "指中国证券监督管理委员会", "本报告", "指本公司2025年度报告"]
        r = route_statement(cells)
        self.assertIsNone(r["candidate"])
        self.assertEqual(r["confidence"], "low")

    def test_balance_sheet_right_partial_equity_side(self):
        # 资产负债表右侧“续页”：页末只到 负债合计，权益段在下一页 → 靠右侧题头放宽门槛
        cells = ["负债和股东权益", "附注五", "2025年12月31日", "2024年12月31日",
                 "流动负债", "短期借款", "非流动负债", "长期借款",
                 "流动负债合计", "非流动负债合计", "负债合计"]
        r = route_statement(cells)
        self.assertEqual(r["candidate"], "balance_sheet")
        self.assertEqual(r["side"], "equity_side")

    def test_md_table_extraction_and_page(self):
        html = _html(["资产总计", "1"])
        md = f"# p\n![](images/a.jpg)\n{html}\n后附说明\n"
        tables = md_tables_with_page(md, first_page=113)
        self.assertEqual(len(tables), 1)
        self.assertEqual(tables[0][0], 113)
        self.assertEqual(_cells_of_md_table(tables[0][1]), ["资产总计", "1"])


if __name__ == "__main__":
    unittest.main()
