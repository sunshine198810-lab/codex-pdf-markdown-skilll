"""法定主表标题的有界规范化；只处理版式前后缀，不做模糊语义匹配。"""

from __future__ import annotations

import re


MAIN_STATEMENT_TITLES = {
    "合并资产负债表", "母公司资产负债表", "公司资产负债表",
    "合并利润表", "母公司利润表", "公司利润表",
    "合并现金流量表", "母公司现金流量表", "公司现金流量表",
}
EQUITY_STATEMENT_TITLES = {
    "合并所有者权益变动表", "母公司所有者权益变动表", "公司所有者权益变动表",
    "合并股东权益变动表", "母公司股东权益变动表", "公司股东权益变动表",
}
STOP_TITLES = EQUITY_STATEMENT_TITLES | {"财务报表附注"}
_LEADING_ENUM = re.compile(r"^(?:[1-9]\d*[.、．]|[一二三四五六七八九十]+[、.])")
# 法定报表页常把“年度”置于表题前（如“年度合并利润表”）。这不是
# 期间识别，而是受限的版式前缀：删除后仍必须整行精确命中允许的表题。
_LEADING_REPORT_PERIOD = re.compile(r"^(?:(?:20\d{2})?年度|中期)")
# 某些报表把日期数字画成矢量轮廓，文字层只剩“年月日”。仅当删除该完整
# 日期壳后精确命中法定表题时才接受，不能匹配任意日期说明句。
_LEADING_REPORT_DATE = re.compile(r"^(?:(?:20\d{2})?年(?:1?2)?月(?:3?1)?日|年月日)")


def compact(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def canonical_statement_title(text: str, allowed: set[str]) -> tuple[str | None, bool, str]:
    """返回（规范标题、是否续表、原始紧凑标题），且必须整行命中法定标题。"""
    source = compact(text).lstrip("#>")
    value = _LEADING_ENUM.sub("", source, count=1)
    value = _LEADING_REPORT_PERIOD.sub("", value, count=1)
    value = _LEADING_REPORT_DATE.sub("", value, count=1)
    continuation = bool(re.search(r"（续）$|续$", value))
    value = re.sub(r"（续）$|续$", "", value)
    return (value if value in allowed else None), continuation, source
