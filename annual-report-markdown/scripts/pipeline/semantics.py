"""semantics：期间、主体、单位和指标候选（对应设计方案 §11）。

先保住口径再讨论标准化。单位倍率换算 ≠ 币种换算；单位未知时 normalized 必须为空，
不猜倍率。本模块提供纯数值/单位辅助；M2 法定主表的指标映射与上下文绑定由 ``financials.py`` 编排。
"""

from __future__ import annotations

from decimal import Decimal
import re
from typing import Optional

# 常见中文倍率 → 乘数（元为基准）
_SCALE_MAP = {
    "元": Decimal("1"),
    "千元": Decimal("1000"),
    "万元": Decimal("10000"),
    "百万元": Decimal("1000000"),
    "亿元": Decimal("100000000"),
    "万元/亿元": None,  # 混合单位需逐列确认，不做猜测
    "人民币元": Decimal("1"),
    None: None,
}


def scale_multiplier(scale: Optional[str]) -> Optional[Decimal]:
    """返回单位倍率；未知单位返回 None（调用方不得猜倍率）。"""
    if scale is None:
        return None
    return _SCALE_MAP.get(scale.strip(), None)


def normalize_decimal(raw: str, scale: Optional[str]) -> Optional[str]:
    """把原字串（去逗号、去括号负号）按倍率换算为十进制字符串。

    - raw 可为 "1,234.50" 或 "(1,234.50)"（括号负数）。
    - 单位未知（multiplier=None）时返回 None。
    - 不做币种换算；结果用字符串避免浮点精度损失。
    """
    multiplier = scale_multiplier(scale)
    if multiplier is None:
        return None
    s = (raw or "").strip().replace(",", "").replace(" ", "")
    negative = False
    if s.startswith("(") and s.endswith(")"):
        negative = True
        s = s[1:-1]
    if s in ("", "-", "—", "--"):
        return None
    try:
        value = Decimal(s)
    except Exception:
        return None
    if negative:
        value = -value
    return str(value * multiplier)


def parse_unit_currency(text: str) -> tuple[Optional[str], Optional[str]]:
    """从明确的金额单位声明提取尺度和币种；不依据公司或报表位置猜测。"""
    compact = re.sub(r"\s+", "", text or "").replace(":", "：")
    scale_pattern = r"百万元|千元|万元|亿元|元"
    matches = re.findall(rf"单位：(?:均以)?(人民币)?({scale_pattern})(?:列示)?", compact)
    matches += re.findall(rf"(?:金额|货币)单位(?:均)?(?:为|以)(人民币)?({scale_pattern})(?:列示)?", compact)
    if not matches:
        return None, None
    currency_mark, scale = matches[-1]
    currency = "CNY" if currency_mark or re.search(r"币种：人民币(?:$|[，,）)])", compact) else None
    return scale, currency


def is_cas_compliance_statement(text: str) -> bool:
    """识别财务报表遵循企业会计准则的明确声明，不匹配一般准则讨论。"""
    compact = re.sub(r"\s+", "", text or "")
    annual_statement = re.search(
        r"本(?:公司|集团)?(?:所)?(?:编制的)?(?:合并)?财务报表符合"
        r"(?:财政部颁布(?:并生效)?的)?"
        r"企业会计准则(?:的)?要求",
        compact,
    )
    interim_statement = re.search(
        r"本中期(?:简要)?财务报表根据(?:中华人民共和国)?财政部颁布的《?企业会计准则第32号",
        compact,
    )
    return bool(annual_statement or interim_statement)


def is_cny_functional_currency_statement(text: str) -> bool:
    """识别主体明确采用人民币作为记账本位币的声明；一般人民币提及不匹配。

    支持 记账/记帐 两种写法，以及"人民币为...记账本位币""记账本位币为人民币"等常见表述；
    主体可为 本公司/公司/集团。不含"记账本位币"语境的人民币提及不会被匹配。
    """
    compact = re.sub(r"\s+", "", text or "")
    patterns = [
        r"(?:本公司|公司|集团)(?:以|采用)人民币(?:作为|为)(?:记[账帐])本位币",
        r"(?:本公司|公司|集团)(?:记[账帐])本位币(?:为|是)人民币",
        r"人民币(?:为|是)(?:本公司|公司|集团)?的(?:记[账帐])本位币",
        r"(?:记[账帐])本位币(?:为|是)人民币",
    ]
    return any(re.search(p, compact) for p in patterns)


def period(kind: str, start: Optional[str] = None, end: Optional[str] = None) -> dict:
    """构建期间结构。kind ∈ instant|duration；年度标签只是显示辅助。"""
    if kind not in ("instant", "duration"):
        raise ValueError("kind 必须是 instant 或 duration")
    return {"kind": kind, "start": start, "end": end}


def map_concept(original_label: str) -> dict:
    """指标概念映射占位：**不把不同口径强行合并**。

    返回 {concept: None, status: 'needs_review'}；标准概念未确认就保留未映射。
    """
    return {"concept": None, "status": "needs_review", "original_label": original_label}


def extract_fact_candidates(payload: dict) -> list:
    """通用表候选提取尚未开放；法定主表请使用 ``financials.build_main_statements``。"""
    raise NotImplementedError(
        "通用表财务语义候选尚未开放；M2 已在 financials.py 接通法定主表。"
        "不允许按相似名称合并不同口径，也不接受模型凭记忆补数。"
    )


capabilities = {
    "engine_wired": True,
    "implemented": ["倍率换算（Decimal 字符串）", "期间结构", "M2 法定主表语义编排（financials.py）"],
    "pending": ["非主表的通用指标候选提取与上下文绑定"],
}
