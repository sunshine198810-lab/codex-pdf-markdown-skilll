"""layout：内容分区与阅读顺序（对应设计方案 §7.2）。

先识别区域（正文/表格/图表/脚注/页眉页脚/未知），再处理区域内部顺序；
不把“双栏”贴满全页后把内容分两半。正文阅读顺序与表格读取方式分开。
"""

from __future__ import annotations

import math
import re
from collections import Counter

REGION_KINDS = (
    "prose",
    "table",
    "figure",
    "footnote",
    "header_footer",
    "heading",
    "unknown",
)


def destination_of_region(kind: str) -> str:
    """区域类型 → 对象去向（账目枚举见 object.schema.json）。"""
    mapping = {
        "prose": "into_prose",
        "heading": "into_prose",
        "table": "into_table",
        "figure": "into_figure",
        "footnote": "into_prose",
        "header_footer": "into_marginalia",
        "unknown": "unresolved",
    }
    return mapping.get(kind, "unresolved")


def classify_regions(page_payload: dict) -> list:
    """把标准解析页中的行、表格和图片转换为区域候选。

    这是 M1 的确定性基线，不声称解决任意复杂版面。它的价值是让每个已检测对象都有
    去向；无法可靠分类的对象进入 ``unknown``，而不是静默丢弃。
    """
    out = []
    for line in page_payload.get("lines", []):
        kind = "header_footer" if line.get("is_marginalia") else (
            "heading" if line.get("is_heading") else "prose"
        )
        out.append({**line, "kind": kind, "destination": destination_of_region(kind)})
    for table in page_payload.get("tables", []):
        out.append({**table, "kind": "table", "destination": "into_table"})
    for figure in page_payload.get("figures", []):
        out.append({**figure, "kind": "figure", "destination": "into_figure"})
    return out


def normalize_marginal_text(text: str) -> str:
    """页眉页脚重复检测用归一化；只作用于页边缘候选，不全局删字。"""
    text = re.sub(r"\s+", "", text or "")
    text = re.sub(r"\d+\s*/\s*\d+", "#/#", text)
    text = re.sub(r"第?\s*\d+\s*页", "第#页", text)
    return text


def words_to_lines(words: list, *, y_tolerance: float = 3.0) -> list:
    """按 y 聚类、按 x 排序生成可追溯行；返回行 bbox 与原词列表。"""
    ordered = sorted(words or [], key=lambda w: (float(w["top"]), float(w["x0"])))
    groups = []
    for word in ordered:
        center = (float(word["top"]) + float(word["bottom"])) / 2
        target = None
        for group in reversed(groups[-4:]):
            if abs(center - group["center"]) <= y_tolerance:
                target = group
                break
        if target is None:
            target = {"center": center, "words": []}
            groups.append(target)
        target["words"].append(word)
        target["center"] = sum(
            (float(w["top"]) + float(w["bottom"])) / 2 for w in target["words"]
        ) / len(target["words"])

    lines = []
    for group in groups:
        ws = sorted(group["words"], key=lambda w: float(w["x0"]))
        parts = []
        prev = None
        for w in ws:
            token = str(w.get("text", ""))
            if prev is not None:
                gap = float(w["x0"]) - float(prev["x1"])
                # 中文紧排；明显列间距或拉丁词间距才加空格。
                prev_text = str(prev.get("text", ""))
                digit_cjk_boundary = bool(
                    (prev_text and prev_text[-1].isdigit() and re.match(r"[\u3400-\u9fff]", token))
                    or (token and token[0].isdigit() and re.search(r"[\u3400-\u9fff]$", prev_text))
                )
                if gap > max(3.0, min(12.0, len(token) * 0.6)) or (gap > 1.0 and digit_cjk_boundary):
                    parts.append(" ")
            parts.append(token)
            prev = w
        lines.append(
            {
                "text": "".join(parts).strip(),
                "bbox": [
                    min(float(w["x0"]) for w in ws),
                    min(float(w["top"]) for w in ws),
                    max(float(w["x1"]) for w in ws),
                    max(float(w["bottom"]) for w in ws),
                ],
                "words": ws,
            }
        )
    return lines


def repeated_marginalia(pages: list) -> set:
    """识别重复页边缘行。至少 3 页且覆盖 35% 页面才判为重复边注。"""
    counts = Counter()
    page_count = len(pages)
    for page in pages:
        height = float(page.get("height_pt", 1))
        seen = set()
        for line in page.get("lines", []):
            top, bottom = line["bbox"][1], line["bbox"][3]
            # 横向页的页脚常落在 85% 附近；仍要求跨页重复，避免删正文。
            if top <= height * 0.09 or bottom >= height * 0.85:
                norm = normalize_marginal_text(line.get("text", ""))
                if norm:
                    seen.add(norm)
        counts.update(seen)
    threshold = max(3, math.ceil(page_count * 0.35))
    return {text for text, count in counts.items() if count >= threshold}


capabilities = {
    "engine_wired": True,
    "implemented": ["区域类型枚举", "区域→对象去向映射", "词到行聚类", "页边缘重复边注识别"],
    "pending": ["复杂多栏与跨栏标题的模型级版面判定"],
}
