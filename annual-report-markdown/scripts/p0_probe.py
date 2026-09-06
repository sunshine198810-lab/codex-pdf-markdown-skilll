#!/usr/bin/env python3
"""P0 引擎/几何选型探针（探索性工具，非交付解析器）。

用途：在真实年报样本上验证“可靠字符位置”是否可取得，复现设计方案 §2 记录的
V0.1 失败场景，为 P0 选型（references/engines.md）与困难页处理提供实证。

诚实声明：
- 本脚本是选型用实验工具。结果只用于判断“几何/证据层是否可行”，
  不授予任何文字转录“可引用/可计算”资格（见 references/quality.md）。
- 无 pdfplumber 时回退 pypdf 坐标回调；两者都不可用时报告“无几何证据可取得”。

用法：
  p0_probe.py <pdf>            # 默认页面：2,5,8,26 摘要 + 全书页眉/合计统计
  p0_probe.py <pdf> --pages 2,5,8,26 --full
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path


def open_pdf(path):
    """返回可迭代 pdfplumber page 的对象；优先 pdfplumber，回退 pypdf 几何。"""
    pdfplumber = _import("pdfplumber")
    if pdfplumber is not None:
        return pdfplumber.open(str(path)), "pdfplumber"
    pypdf = _import("pypdf")
    if pypdf is not None:
        return _PypdfBackend(pypdf.PdfReader(str(path))), "pypdf_visitor"
    raise RuntimeError("无可用几何库：需要 pdfplumber 或 pypdf")


def _import(name):
    try:
        return __import__(name)
    except Exception:
        return None


def group_lines(words, tol=2.5):
    """按 top 邻近 + x 排序重建阅读行（纯启发式，仅探针用）。

    返回 [ {top, bottom, x0, x1, text, char_count}, ... ] 按阅读顺序。
    注意：同页左右两栏会被合并成一行 → 专用于暴露“双栏被误判”现象。
    """
    words = sorted(words, key=lambda w: (round(w["top"] / tol), w["x0"]))
    lines = []
    for w in words:
        if not lines or abs(w["top"] - lines[-1]["top"]) > tol:
            lines.append(
                {
                    "top": w["top"],
                    "bottom": w["bottom"],
                    "x0": w["x0"],
                    "x1": w["x1"],
                    "words": [w["text"]],
                    "char_count": len(w["text"]),
                }
            )
        else:
            prev = lines[-1]
            prev["x1"] = max(prev["x1"], w["x1"])
            prev["words"].append(w["text"])
            prev["char_count"] += len(w["text"])
    for ln in lines:
        ln["text"] = " ".join(ln["words"])
    return lines


def column_signal(lines, width, margin=0.25):
    """粗判“疑似双栏”：是否存在一批行，其左半(x0<0.5w)与右半都有独立起始列头。启发式。"""
    left = [ln for ln in lines if ln["x0"] < width * 0.5]
    right = [ln for ln in lines if ln["x0"] > width * (0.5 + margin)]
    return {"left_lines": len(left), "right_started_lines": len(right)}


def first_lines(lines, n=8):
    return [ln["text"][:50] for ln in lines[:n]]


def probe_pages(pdf, pages):
    """对指定物理页逐页体检。"""
    out = []
    for i, page in enumerate(pdf.pages, start=1):
        if pages and i not in pages:
            continue
        try:
            words = page.extract_words()
            chars = page.chars if hasattr(page, "chars") else []
            h = page.height
            w = page.width
            zero_coords = sum(1 for c in chars if c.get("x0") == 0 and c.get("top") == 0)
            lines = group_lines(words)
            long_lines = [ln for ln in lines if ln["char_count"] >= 300]
            header_th = max(45.0, h * 0.06)
            footer_th = min(h - 45.0, h * 0.94)
            top_strip = " ".join(
                t["text"] for t in lines if t["top"] <= header_th
            )
            bot_strip = " ".join(
                t["text"] for t in lines if t["bottom"] >= footer_th
            )
            # 章节编号出现顺序（一、二、…）
            import re

            nums = re.findall(
                r"(十一|十二|十|九|八|七|六|五|四|三|二|一)（?[、.．]", top_strip + "\n" + "\n".join(ln["text"] for ln in lines)
            )
            order_ok = None
            if len(nums) >= 2:
                order_ok = nums == sorted(nums, key=len)
            out.append(
                {
                    "physical_page": i,
                    "rotation": getattr(page, "rotation", 0),
                    "width_pt": w,
                    "height_pt": h,
                    "word_count": len(words),
                    "char_count": len(chars),
                    "chars_with_zero_coord": zero_coords,
                    "line_count": len(lines),
                    "long_lines_ge300_chars": [
                        {"n": ln["char_count"], "head": ln["text"][:40]} for ln in long_lines
                    ],
                    "headings_order_ok": order_ok,
                    "top_strip_head": top_strip[:60],
                    "bottom_strip_head": bot_strip[:40],
                    "column_signal": column_signal(lines, w),
                    "first_lines": first_lines(lines),
                }
            )
        except Exception as exc:  # noqa: BLE001
            out.append({"physical_page": i, "error": str(exc)})
    return out


def scan_global(pdf):
    """全书扫描：页眉/页脚模板分组 + “合计”位置 + 语言 + 双栏候选 + 无框线数字页。"""
    top_counter = Counter()
    bottom_counter = Counter()
    heji_by_position = []
    number_dense_no_table = []
    two_column_pages = []
    lang_stats = Counter()
    for i, page in enumerate(pdf.pages, start=1):
        words = page.extract_words()
        lines = group_lines(words)
        header_th = max(45.0, page.height * 0.06)
        footer_th = min(page.height - 45.0, page.height * 0.94)
        tops = " ".join(t["text"] for t in lines if t["top"] <= header_th)
        bottoms = " ".join(t["text"] for t in lines if t["bottom"] >= footer_th)
        if tops:
            top_counter[tops[:80]] += 1
        if bottoms:
            bottom_counter[bottoms[:40]] += 1
        # 语言判定（拉丁字母 vs CJK）
        all_text = "".join(w["text"] for w in words)
        latin = sum(1 for c in all_text if c.isascii() and c.isalpha())
        cjk = sum(1 for c in all_text if "\u4e00" <= c <= "\u9fff")
        tot = latin + cjk
        if tot:
            ratio = latin / tot
            tag = "mostly_latin" if ratio > 0.7 else ("mixed" if ratio > 0.2 else "mostly_cjk")
            lang_stats[tag] += 1
        # “合计”所在 y 位置分类（页眉/页脚/表内）
        for w in words:
            if "合计" in w["text"]:
                zone = (
                    "header"
                    if w["top"] <= header_th
                    else ("footer" if w["bottom"] >= footer_th else "body/table")
                )
                heji_by_position.append({"page": i, "top": round(w["top"], 1), "zone": zone, "text": w["text"]})
        # 疑似无框线数字页：数字词多但 pdfplumber 没找到 ruled 表格
        digits = sum(1 for w in words if any(c.isdigit() for c in w["text"]))
        try:
            ntables = len(page.find_tables())
        except Exception:
            ntables = -1
        if digits >= 40 and ntables == 0:
            number_dense_no_table.append({"page": i, "digit_words": digits, "words": len(words)})
        # 双栏候选：右起始行足够多且与左行同数量级
        cs = column_signal(lines, page.width)
        if cs["right_started_lines"] >= 5 and cs["right_started_lines"] > 0.4 * max(1, cs["left_lines"]):
            two_column_pages.append({"page": i, "right_started": cs["right_started_lines"], "left": cs["left_lines"]})
    return {
        "page_count": len(pdf.pages),
        "language": dict(lang_stats),
        "repeated_top_templates": top_counter.most_common(10),
        "distinct_top_templates_ge3": {k: v for k, v in top_counter.items() if v >= 3},
        "heji_count": len(heji_by_position),
        "heji_in_header_footer": [h for h in heji_by_position if h["zone"] != "body/table"],
        "borderless_number_dense_pages": number_dense_no_table,
        "borderless_number_dense_count": len(number_dense_no_table),
        "two_column_candidate_pages": two_column_pages,
        "two_column_candidate_count": len(two_column_pages),
    }


class _PypdfBackend:
    """pypdf 坐标回调回退实现：pypdf 6.x extract_text(visitor_text/visitor_space)。"""

    def __init__(self, reader):
        self._reader = reader
        self.pages = [_PypdfPage(p) for p in reader.pages]

    @property
    def page_count(self):
        return len(self._reader.pages)


class _PypdfPage:
    def __init__(self, pypdf_page):
        self._p = pypdf_page
        self.rotation = getattr(self._p, "rotation", 0)
        mb = self._p.mediabox
        self.width = float(mb.width)
        self.height = float(mb.height)
        self.chars = []

    def extract_words(self):
        """用 visitor 回调收集文本段坐标（词级粒度由 pdfplumber 之外的近似）。"""
        words = []

        def visitor(text, cm, tm, font_dict, font_size):
            if text is None or not text.strip():
                return
            # 由文本矩阵 tm 求基线起点；此为探针近似
            import math

            x = tm[4]
            y = tm[5]
            top_pdf = self.height - y  # PDF y 向上 → 转 top 向下
            # 词宽近似
            words.append(
                {
                    "text": text,
                    "x0": x,
                    "x1": x + len(text) * (font_size or 10) * 0.5,
                    "top": top_pdf,
                    "bottom": top_pdf + (font_size or 10),
                }
            )

        self._p.extract_text(visitor_text=visitor, visitor_space=visitor)
        return words

    def find_tables(self):
        return []


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("pdf")
    ap.add_argument("--pages", default="2,5,8,26", help="逗号分隔物理页（1 起始）")
    ap.add_argument("--full", action="store_true", help="全书扫描页眉/合计/无框线数字页")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args(argv)

    pdf_path = Path(args.pdf)
    if not pdf_path.is_file():
        print("ERROR: 文件不存在", pdf_path)
        return 2
    pages = {int(x) for x in args.pages.split(",") if x.strip()}

    try:
        handle, engine = open_pdf(pdf_path)
    except RuntimeError as exc:
        print("ERROR:", exc)
        return 2

    try:
        result = {
            "engine": engine,
            "pdf": str(pdf_path),
            "pages": sorted(pages),
            "page_detail": probe_pages(handle, pages),
        }
        if args.full:
            result["global_scan"] = scan_global(handle)
    finally:
        try:
            handle.close()
        except Exception:
            pass

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    print(f"引擎: {engine} | {pdf_path.name} | pdfplumber页数: {len(handle.pages) if engine=='pdfplumber' else 'n/a'}")
    for d in result["page_detail"]:
        if "error" in d:
            print(f"\n[页 {d['physical_page']}] ERROR {d['error']}")
            continue
        print(
            f"\n[物理页 {d['physical_page']}] rot={d['rotation']} "
            f"words={d['word_count']} chars={d['char_count']} "
            f"zero_coord={d['chars_with_zero_coord']} lines={d['line_count']}"
        )
        print(f"   章节顺序ok={d['headings_order_ok']}  顶栏头={d['top_strip_head'][:40]!r}")
        for lg in d["long_lines_ge300_chars"]:
            print(f"   ⚠超长行 {lg['n']} 字符: {lg['head']!r}...")
        cs = d["column_signal"]
        print(
            f"   双栏信号: 左起行={cs['left_lines']} 右起始行={cs['right_started_lines']} "
            f"(右>左×0.4 才可能是双栏)"
        )
        for ln in d["first_lines"][:5]:
            print(f"     · {ln}")
    if "global_scan" in result:
        g = result["global_scan"]
        print("\n===== 全书扫描 =====")
        print(f"页数: {g['page_count']}  语言分布: {g['language']}")
        print(f"重复顶栏模板数(≥3页): {len(g['distinct_top_templates_ge3'])}")
        for tpl, cnt in g["distinct_top_templates_ge3"].items():
            print(f"   ×{cnt}  {tpl[:60]!r}")
        print(f"“合计”出现 {g['heji_count']} 次；页眉/页脚中的合计: {g['heji_in_header_footer'][:8]}")
        bd = g["borderless_number_dense_pages"]
        print(f"疑似无框线高数字页: {g['borderless_number_dense_count']} 页 ->", bd[:10])
        tc = g["two_column_candidate_pages"]
        print(f"疑似双栏页: {g['two_column_candidate_count']} 页 ->", tc[:12])
    return 0


if __name__ == "__main__":
    sys.exit(main())
