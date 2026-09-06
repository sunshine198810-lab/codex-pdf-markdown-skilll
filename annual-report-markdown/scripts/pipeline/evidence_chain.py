"""多通道识别、逐格一致性判定与人工签核记录（对应设计方案 §12 证据 / §13.4 复核队列）。

机制骨架（不依赖任何第三方识别引擎）：
  - 每个独立识别通道为同一单元格产出一份候选（`CellCandidate`）；通道可注册、可替换。
  - 对同一单元格的多个通道候选做逐格一致性判定：
        consistent       >=2 通道，且归一化数值一致
        conflict         >=2 通道，且归一化数值不一致
        single_channel   只有 1 个通道给出可用候选（无法交叉验证）
        insufficient     没有可用候选
  - 人工签核记录（`SignOffRecord`）与候选分开保存；重跑不得抹掉人工决定。

诚实边界：
  - 本模块只做“机制”。单个通道（如 MinerU OCR）现在就是唯一通道，因此矢量轮廓数字
    会得到 `single_channel`，仍不可引用或计算。要升级为 facts，必须先接入第二个独立
    识别通道，并完成逐格一致性与人工签核。
  - `normalize_numeric` 只剥离格式（逗号/括号/百分号），不做单位倍率或币种换算；
    那属于语义层，不在本模块范围（见 semantics / §11.2）。
"""

from __future__ import annotations

import datetime
import json
import pathlib
import re
from decimal import Decimal, InvalidOperation

# 逐格一致性判定结果
VERDICTS = ("consistent", "conflict", "single_channel", "insufficient")

# 人工签核决定
SIGN_OFF_DECISIONS = ("pending", "accepted", "rejected", "deferred")

# 独立识别通道注册表（mechanism 骨架；第二通道接入时在此登记）
_CHANNELS: dict[str, dict] = {}


def _now() -> str:
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


# ---- 通道注册 ---------------------------------------------------------------

def register_channel(channel_id: str, engine: str, *, engine_version: str = "unknown",
                     method: str = "", note: str = "") -> dict:
    """登记一条独立识别通道。通道应按“来源与模型”区分，不能复用同一模型当两个通道。"""
    _CHANNELS[channel_id] = {
        "channel_id": channel_id,
        "engine": engine,
        "engine_version": engine_version,
        "method": method,
        "note": note,
        "registered_at": _now(),
    }
    return _CHANNELS[channel_id]


def list_channels() -> dict:
    """返回当前登记的通道副本。"""
    return dict(_CHANNELS)


# ---- 数值归一化（只剥离格式） ------------------------------------------------

def parse_numeric(text) -> dict | None:
    """把单元格文字解析为可比较的数值核心。

    返回 {"value": 十进制字符串, "percent": bool}，或 None（无法解析）。
    剥离：首尾空白、括号负数、前导 +/‑、千分位逗号、内部空格、末尾百分号。
    用 Decimal 保精度，`f` 格式避免科学计数法；不做倍率/币种换算。
    """
    if text is None:
        return None
    s = str(text).strip()
    if not s:
        return None
    negative = False
    if s.startswith("(") and s.endswith(")"):
        negative = True
        s = s[1:-1]
    percent = s.endswith("%")
    if percent:
        s = s[:-1]
    if s.startswith("+"):
        s = s[1:]
    elif s.startswith("-"):
        negative = True
        s = s[1:]
    s = s.replace(",", "").replace(" ", "")
    if not re.fullmatch(r"\d+(\.\d+)?", s):
        return None
    try:
        value = Decimal(s)
    except InvalidOperation:
        return None
    if negative:
        value = -value
    canonical = format(value, "f")
    if "." in canonical:
        canonical = canonical.rstrip("0").rstrip(".")
    return {"value": canonical, "percent": percent}


def normalize_numeric(text) -> str | None:
    """返回可比较的数值核心字符串（便于测试/断言），无法解析返回 None。"""
    parsed = parse_numeric(text)
    return parsed["value"] if parsed else None


def _candidate(channel_id: str, cell) -> dict:
    """由单元格文本构造一份通道候选。"""
    parsed = parse_numeric(cell.get("text") if cell.get("text") is not None else cell.get("raw_value"))
    return {
        "channel_id": channel_id,
        "raw_value": cell.get("text") if cell.get("text") is not None else cell.get("raw_value"),
        "normalized_value": parsed["value"] if parsed else None,
        "percent": parsed["percent"] if parsed else False,
        "row": int(cell.get("row", 0)),
        "col": int(cell.get("col", 0)),
    }


# ---- 逐格一致性判定 ---------------------------------------------------------

def compare_channel_candidates(candidates: list[dict]) -> dict:
    """对同一单元格的多个通道候选做一致性判定。

    参数每个候选应含 `channel_id` 与 `normalized_value`（可由 `_candidate` 生成）。
    返回 `verdict`、参与的 `channels`、归一化值集合与原因。
    """
    usable = [c for c in candidates if c.get("normalized_value") is not None]
    if not usable:
        return {"verdict": "insufficient", "channels": [], "normalized_values": [],
                "detail": "没有可用数值候选"}
    if len(usable) < 2:
        c = usable[0]
        return {"verdict": "single_channel", "channels": [c["channel_id"]],
                "normalized_values": [c["normalized_value"]],
                "detail": "仅有单一独立通道，无法交叉验证"}
    values = [c["normalized_value"] for c in usable]
    unique = sorted(set(values))
    if len(unique) == 1:
        return {"verdict": "consistent", "channels": [c["channel_id"] for c in usable],
                "normalized_values": [unique[0]], "detail": "所有通道一致"}
    return {"verdict": "conflict", "channels": [c["channel_id"] for c in usable],
            "normalized_values": unique, "detail": "通道间数值不一致"}


def per_cell_consistency(grids_by_channel: dict[str, dict]) -> dict:
    """对同一结构网格的多个通道候选做逐格一致性判定。

    `grids_by_channel`: {channel_id: grid}，grid 需含 `cells`（每个 cell 有 row/col/text）。
    返回 `per_cell`（key= r{row}c{col}）、参与的 `channels` 与 `counts` 汇总。
    """
    by_key: dict[tuple[int, int], list[dict]] = {}
    for channel_id, grid in grids_by_channel.items():
        for cell in grid.get("cells", []):
            key = (int(cell.get("row", 0)), int(cell.get("col", 0)))
            by_key.setdefault(key, []).append(_candidate(channel_id, cell))
    per_cell: dict[str, dict] = {}
    for key, cands in by_key.items():
        per_cell[f"r{key[0]}c{key[1]}"] = compare_channel_candidates(cands)
    counts = {v: 0 for v in VERDICTS}
    for verdict in per_cell.values():
        counts[verdict["verdict"]] += 1
    return {
        "per_cell": per_cell,
        "channels": sorted(grids_by_channel.keys()),
        "counts": counts,
        "second_channel_present": len(grids_by_channel) >= 2,
    }


def fragment_consistency(grid: dict, channel_id: str, *, second_grid: dict | None = None,
                         second_channel_id: str = "channel-b") -> dict:
    """便捷函数：对某物理表片段做逐格一致性判定（第一通道 + 可选的第二通道）。"""
    grids = {channel_id: grid}
    if second_grid is not None:
        grids[second_channel_id] = second_grid
    return per_cell_consistency(grids)


# ---- 人工签核记录 -----------------------------------------------------------

def new_signoff(object_ref: str, *, reviewer: str | None = None,
                decision: str = "pending", note: str | None = None,
                eligible_for_calculation: bool = False,
                replace_sign_off_id: str | None = None,
                run_id: str | None = None) -> dict:
    """构造一条人工签核记录。`decision` 为 pending/accepted/rejected/deferred。

    只有 decision=accepted 且 eligible_for_calculation=True 时，才对下游开放计算资格；
    记录保留旧候选，`replace_sign_off_id` 记录被替换的旧签核。
    """
    if decision not in SIGN_OFF_DECISIONS:
        raise ValueError(f"未知签核决定: {decision!r}")
    seq = datetime.datetime.now().strftime("%Y%m%dT%H%M%S%f")
    return {
        "sign_off_id": f"so-{seq}",
        "object_ref": object_ref,
        "object_kind": "vector_fragment" if str(object_ref).startswith("复核/vector-outline-index.json#") else "other",
        "reviewer": reviewer,
        "decision": decision,
        "recorded_at": _now(),
        "note": note,
        "eligible_for_calculation": bool(eligible_for_calculation) and decision == "accepted",
        "replace_sign_off_id": replace_sign_off_id,
        "run_id": run_id,
    }


def write_signoffs(path, records) -> None:
    """把签核记录追加写入 `复核/signoffs.jsonl`（每行一条，保留历史，不覆盖）。"""
    p = pathlib.Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def load_signoffs(path) -> list:
    """读取 `复核/signoffs.jsonl`。文件不存在返回空列表。"""
    p = pathlib.Path(path)
    if not p.is_file():
        return []
    records = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def aggregate_signoffs(records: list) -> dict:
    """汇总签核记录：按决定计数、可计算数量、待签核数量。"""
    counts = {d: 0 for d in SIGN_OFF_DECISIONS}
    for record in records:
        decision = record.get("decision")
        if decision in counts:
            counts[decision] += 1
    return {
        "total": len(records),
        "by_decision": counts,
        "eligible_for_calculation": sum(
            bool(r.get("eligible_for_calculation")) for r in records
        ),
        "pending": counts.get("pending", 0),
    }
