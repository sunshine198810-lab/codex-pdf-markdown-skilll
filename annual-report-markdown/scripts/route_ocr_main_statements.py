"""route_ocr_main_statements：扫描 OCR 表 → 六类法定主表 候选路由（D3 预研，probe）。

作用：MinerU OCR 对扫描主表页恢复了“表体”（行标签+数值）但**表题（合并资产负债表等）没进
OCR 文本**（见 references/v03-d3-scan-pre-research-2026-09-07.md §6）。本工具用**表体结构
线索**（科目/行名锚点）给每张 OCR 表一个 *candidate* 语句路由，供人工/后续确认。

边界（诚实声明）：
- 输出是 **candidate 路由**（statement type + scope/side 候选 + 命中锚点），不是事实；
- OCR 数值始终 single_channel，本工具不碰数值、不升 facts、不做报表语义；
- 合并/母公司 与 资产负债表左右半页 只在锚点足够时才给候选，否则标 unresolved。

纯标准库，可独立测试：python3 route_ocr_main_statements.py <md> [--first-page 113]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# 每类主表的锚点与“强制出现”条件（先判强制，再计命中数，避免纯计数误判）
_INCOME_A = ("营业收入", "营业成本", "税金及附加", "销售费用", "管理费用",
             "研发费用", "财务费用", "营业利润", "利润总额", "净利润",
             "归属于母公司", "少数股东损益", "基本每股收益", "所得税费用")
_CASHFLOW_A = ("经营活动产生的现金流量", "投资活动产生的现金流量",
               "筹资活动产生的现金流量",
               "经营活动产生的现金流量净额", "投资活动产生的现金流量净额",
               "筹资活动产生的现金流量净额",
               "销售商品、提供劳务收到的现金", "购买商品、接受劳务支付的现金",
               "取得借款收到的现金", "吸收投资收到的现金",
               "偿还债务支付的现金", "购建固定资产", "分配股利",
               "经营活动现金流入小计", "筹资活动现金流入小计",
               "现金及现金等价物净增加额", "汇率变动对现金及现金等价物")
_BALANCE_A = ("流动资产合计", "非流动资产合计", "资产总计", "流动负债合计",
              "非流动负债合计", "负债合计", "所有者权益合计", "股东权益合计",
              "归属于母公司所有者权益合计", "少数股东权益",
              "货币资金", "应收账款", "存货", "固定资产", "负债和所有者权益总计",
              "负债和股东权益总计")
_EQUITY_A = ("上年年末余额", "本年年初余额", "本年增减变动金额",
             "所有者权益合计", "股本", "资本公积", "其他综合收益",
             "盈余公积", "未分配利润", "所有者投入资本")

# 合并 vs 母公司 判别锚点（仅当存在才给 scope 候选）
_CONSO_ANCHORS = ("少数股东权益", "少数股东损益", "归属于母公司股东")
# 权益/负债侧判别（资产负债表左右半页可能分页；紫金用“股东权益”口径）
_BS_RIGHT = ("流动负债合计", "非流动负债合计", "负债合计", "所有者权益合计",
             "股东权益合计", "负债和所有者权益总计", "负债和股东权益总计",
             "负债和股东权益")


def _cells_of_md_table(html: str) -> list[str]:
    """把 <table>…</table> 里所有 <td> 文本取出去除空白。"""
    tds = re.findall(r"<td[^>]*>(.*?)</td>", html, flags=re.S)
    out = []
    for t in tds:
        t = re.sub(r"<[^>]+>", "", t)
        t = re.sub(r"\s+", "", t)
        if t:
            out.append(t)
    return out


def _has(cells: list[str], token: str) -> bool:
    return any(token in c for c in cells)


def _hits(cells: list[str], anchors: tuple) -> list[str]:
    return [a for a in anchors if _has(cells, a)]


def route_statement(cells: list[str]) -> dict:
    """给一张 OCR 表出 candidate 主表路由。cells 为该表全部单元格文本。"""
    scores: dict[str, int] = {}
    # 利润表：强制 营业收入 + (净利润|利润总额)
    if _has(cells, "营业收入") and (_has(cells, "净利润") or _has(cells, "利润总额")):
        m = _hits(cells, _INCOME_A)
        if len(m) >= 5:
            scores["income_statement"] = len(m)
    # 利润表·其他综合收益续段页（无营业收入，但有 OCI 结构 + 归母/少数）
    elif _has(cells, "其他综合收益") and (_has(cells, "归属于母公司") or _has(cells, "少数股东")) \
            and (_has(cells, "税后净额") or _has(cells, "不能重分类") or _has(cells, "将重分类")):
        m = _hits(cells, _INCOME_A)
        scores["income_statement"] = max(len(m), 1)
    # 现金流量表：按节分页（经营/投资/筹资，OCR 可能写“产生的/使用的”），含 现金流量 即候选
    if _has(cells, "现金流量") and any(_has(cells, a) for a in ("经营活动", "投资活动", "筹资活动")):
        m = _hits(cells, _CASHFLOW_A)
        if len(m) >= 4:
            scores["cash_flow_statement"] = len(m)
    # 资产负债表：左页需 资产总计；右页(权益侧)需 负债合计/所有者(股东)权益合计
    has_left = _has(cells, "资产总计")
    has_right_sub = (_has(cells, "负债合计") or _has(cells, "所有者权益合计")
                     or _has(cells, "股东权益合计"))
    is_bs_right_title = _has(cells, "负债和股东权益") or _has(cells, "负债和所有者权益")
    if has_left:
        m = _hits(cells, _BALANCE_A)
        if len(m) >= 4:
            scores["balance_sheet"] = len(m)
    elif has_right_sub and _has_right_side(cells):
        m = _hits(cells, _BALANCE_A)
        # 右侧“续页”（页末到 负债合计、权益段在下一页）锚点较少：有右侧题头即放宽门槛
        if len(m) >= (2 if is_bs_right_title else 4):
            scores["balance_sheet"] = len(m)
    # 权益变动表：必须先出现 上年年末/本年年初/本年增减变动 任一（区分“资产负债权益侧”页）
    if any(_has(cells, a) for a in ("上年年末余额", "本年年初余额", "本年增减变动金额")):
        m = _hits(cells, _EQUITY_A)
        if len(m) >= 5:
            scores["changes_in_equity"] = len(m)

    if not scores:
        return {"candidate": None, "score": 0, "confidence": "low",
                "matched": [], "scope": "unresolved", "side": "unresolved",
                "note": "表体锚点不足以判定为六类主表"}
    best_st = max(scores, key=lambda k: scores[k])
    best_hits = scores[best_st]
    tied = [k for k, v in scores.items() if v == best_hits and k != best_st]
    if best_hits >= 9:
        confidence = "high"
    elif best_hits >= 6:
        confidence = "medium"
    else:
        confidence = "low"

    if any(any(a in c for c in cells) for a in _CONSO_ANCHORS):
        scope = "consolidated"
    else:
        scope = "unresolved"
    side = "unresolved"
    if best_st == "balance_sheet":
        has_right = _has_right_side(cells)
        if has_left and has_right:
            side = "full"
        elif has_left:
            side = "asset_side"
        elif has_right:
            side = "equity_side"
    anchors = sorted({a for st in (best_st, *tied) for a in _hits(
        cells, {"income_statement": _INCOME_A, "cash_flow_statement": _CASHFLOW_A,
                "balance_sheet": _BALANCE_A, "changes_in_equity": _EQUITY_A}[st])})
    result = {
        "candidate": best_st if not tied else None,
        "tied_with": sorted(tied) or None,
        "score": best_hits,
        "confidence": confidence,
        "matched": anchors,
        "scope": scope,
        "side": side,
        "note": "" if not tied else f"并列：{sorted(tied)}",
    }
    return result


def _has_right_side(cells) -> bool:
    return any(any(a in c for c in cells) for a in _BS_RIGHT)


def md_tables_with_page(md_text: str, first_page: int):
    """按 md 行顺序返回 [(page, html)]。

    以图片行 `![](images/...)` 计页（扫描页一页一图）；遇到 <table> 给当前页。
    若图片数与表数不齐，页号尽力而为并标注 page 可为 None 的项由调用方复核。
    """
    page = first_page - 1
    out = []
    in_table = False
    buf = []
    for line in md_text.splitlines():
        if line.startswith("![]("):
            page += 1
            continue
        if "<table>" in line and "</table>" in line:
            out.append((page if page >= first_page else None, line))
            continue
        if "<table>" in line:
            in_table = True
            buf = [line]
            continue
        if in_table:
            buf.append(line)
            if "</table>" in line:
                html = "\n".join(buf)
                out.append((page if page >= first_page else None, html))
                in_table = False
                buf = []
    return out


def content_list_tables(content_list: str, first_physical: int):
    """从 MinerU *_content_list.json 读表并做**页级绑定**。

    content_list 顶层是每页若干子项；``type=='table'`` 项带 ``table_body``(HTML) 与
    ``page_idx``（**切片内 0 起始**）→ 物理页 = ``first_physical + page_idx``。
    这比用 md 图片行计数更可靠（md 一物理页可能多张子图导致页漂移）。
    """
    d = json.loads(Path(content_list).read_text(encoding="utf-8"))
    items = [it for it in d if isinstance(it, dict) and it.get("type") == "table"
             and it.get("table_body")]
    items.sort(key=lambda it: int(it.get("page_idx", 0)))
    return [(first_physical + int(it.get("page_idx", 0)), it["table_body"]) for it in items]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("md", nargs="?", help="MinerU 生成的 .md（含 <table>）")
    ap.add_argument("--content-list", dest="content_list",
                    help="MinerU *_content_list.json（页级绑定优先，推荐）")
    ap.add_argument("--first-page", type=int, default=113, help="切片首个物理页")
    ap.add_argument("--json", action="store_true", help="输出 JSON 行")
    args = ap.parse_args()
    if not args.md and not args.content_list:
        ap.error("需要 md 或 --content-list")
    routes = []
    if args.content_list:
        for page, html in content_list_tables(args.content_list, args.first_page):
            cells = _cells_of_md_table(html)
            r = route_statement(cells)
            r["page"] = page
            r["n_cells"] = len(cells)
            routes.append(r)
    else:
        text = Path(args.md).read_text(encoding="utf-8")
        for page, html in md_tables_with_page(text, args.first_page):
            cells = _cells_of_md_table(html)
            r = route_statement(cells)
            r["page"] = page
            r["n_cells"] = len(cells)
            routes.append(r)
    if args.json:
        for r in routes:
            print(json.dumps(r, ensure_ascii=False))
    else:
        for r in routes:
            if r["candidate"]:
                cand = r["candidate"]
            elif r.get("tied_with"):
                cand = "tie:" + ",".join(r["tied_with"])
            else:
                cand = "non_main/unresolved"
            print(f"p{r['page']}  n={r['n_cells']:<4} → {cand:<24} conf={r['confidence']:<7} "
                  f"scope={r['scope']:<12} side={r['side']:<12} hits={len(r['matched'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
