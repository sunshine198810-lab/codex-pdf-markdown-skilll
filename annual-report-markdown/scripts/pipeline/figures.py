"""figures：图表证据与标签（对应设计方案 §10）。

先完整留存与可核对标签（主题/系列/期间/轴单位/印出数值）；描述不下原因、不推导投资结论。
数据图优先处理标题、标签与脚注；估算数据（柱高/折线反推）仅用户明确需要时单独标识生成。
"""

from __future__ import annotations

FIGURE_NOTE_LEVELS = (
    "raw_retained",      # 原图保留
    "verifiable_label",  # 可核对描述与标签
    "estimated_data",    # 估算数据（不进入已验证财务事实）
)


def figure_note(level: str, note: str) -> dict:
    """生成图表说明占位（level ∈ FIGURE_NOTE_LEVELS）。"""
    if level not in FIGURE_NOTE_LEVELS:
        raise ValueError(f"未知图表说明档位: {level}")
    return {"level": level, "note": note, "verified": False}


def extract_figure(region) -> dict:
    """P0 未接线：图表区域识别、标题/图例/轴/单位/脚注提取需版面与视觉引擎。"""
    raise NotImplementedError(
        "图表提取尚未实现：需版面识别（区域）+ 视觉/文字引擎。"
        "未完成的图表必须在入口可见状态，不能宣称全面图表理解。"
    )


capabilities = {
    "engine_wired": False,
    "implemented": ["图表说明档位", "说明占位结构"],
    "pending": ["图表区域识别与标签提取（需引擎）"],
}
