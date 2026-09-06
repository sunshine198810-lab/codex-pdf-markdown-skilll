"""sections：目录、章节和附注映射（对应设计方案 §8.1）。

书签与目录都是候选，须与正文位置核对。保留原始目录与标准标签两套信息。
标准章节标签应可配置，不把某一年的章节序号写死。
"""

from __future__ import annotations

from typing import Optional
import re

# 常见标准标签示例（可配置；仅辅助检索路由，不覆盖原文）
STANDARD_LABELS = (
    "company_information",
    "important_notice",
    "key_indicators",
    "management_discussion",
    "corporate_governance",
    "financial_statements",
    "notes",
    "significant_matters",
    "risk_factors",
    "other",
)


def suggest_standard_label(original_title: str) -> Optional[str]:
    """按标题原文给一个标准标签候选（纯规则占位；P0 不保证准确）。

    仅用于检索路由，不覆盖原文标题。无法确认时返回 None。
    """
    t = (original_title or "").strip()
    low = t.lower()
    if "管理" in t and ("讨论" in t or "经营" in t):
        return "management_discussion"
    if "重要提示" in t or "释义" in t:
        return "important_notice"
    if "主要会计" in t and "指标" in t:
        return "key_indicators"
    if "公司信息" in t or "公司简介" in t or "基本资料" in t:
        return "company_information"
    if "治理" in t or "董事" in t:
        return "corporate_governance"
    if "合并资产负债" in t or "合并利润" in t or "现金流量" in t or "财务报表" in t or "财务报告" in t:
        return "financial_statements"
    if "附注" in t or "notes" in low or "财务报表附注" in t:
        return "notes"
    if "风险" in t:
        return "risk_factors"
    if "重要事项" in t or "重大事项" in t:
        return "significant_matters"
    return None


def build_section_tree(payload: dict) -> list:
    """从正文标题对象构建章节地图。

    ``payload`` 需提供 ``headings``（含 object_id/page/title）与 ``page_last_object``。
    同页多节按对象顺序保存，不用“下一节页码减一”伪造边界。
    """
    headings = sorted(
        payload.get("headings", []),
        key=lambda h: (h.get("physical_page", 0), h.get("order", 0)),
    )
    page_last = payload.get("page_last_object", {})
    page_count = int(payload.get("page_count", 0) or 0)
    out = []
    for i, heading in enumerate(headings):
        nxt = headings[i + 1] if i + 1 < len(headings) else None
        start_page = int(heading["physical_page"])
        if nxt:
            end_page = int(nxt["physical_page"])
            end_object = nxt.get("previous_object_id")
        else:
            end_page = page_count or start_page
            end_object = page_last.get(str(end_page)) or page_last.get(end_page)
        out.append(
            {
                "section_id": f"section-{i + 1:03d}",
                "original_title": heading["title"],
                "standard_label": suggest_standard_label(heading["title"]),
                "start_object_id": heading.get("object_id"),
                "end_object_id": end_object,
                "physical_page_range": [start_page, end_page],
                "confidence": heading.get("confidence", "rule_high"),
                "candidate_status": "accepted_by_rules",
            }
        )
    return out


_SECTION_RE = re.compile(r"^(第[一二三四五六七八九十百零〇0-9]+节)\s*(.{1,60})$")


def heading_from_line(text: str) -> Optional[str]:
    """识别 A 股/港股常见顶层“第…节”标题；返回原文或 None。"""
    clean = " ".join((text or "").split()).strip()
    match = _SECTION_RE.match(clean)
    if not match:
        return None
    tail = match.group(2).strip(" ：:")
    if not tail:
        return None
    return f"{match.group(1)} {tail}"


capabilities = {
    "engine_wired": True,
    "implemented": ["标准标签词典", "第X节正文标题识别", "同页多节对象边界地图"],
    "pending": ["港股英文目录层级与 PDF 书签交叉核验"],
}
