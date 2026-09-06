"""tables：网格、单元格、跨页逻辑表（对应设计方案 §9）。

表格处理顺序：定位 → 识别格线/边界/合并 → 文字回填单元格 → 保留多级表头/层级
→ 识别数据区/表头/行标题 → 建立单元格→表头/行名/单位/期间/注释关系 → 跨页拼接
→ 检查后生成阅读表与语义候选。P0 提供纯辅助函数，真实识别未接线。
"""

from __future__ import annotations


def cell_key(row: int, col: int) -> str:
    """单元格键（用于关系引用）。"""
    return f"cell_r{row}_c{col}"


def header_path(column_headers: list, row_headers: list) -> list:
    """占位：返回表头路径表示（P0 仅示意数据结构）。"""
    return {"columns": list(column_headers), "rows": list(row_headers)}


def split_physical_fragments(rows: list) -> list:
    """P0 未接线：真实跨页切分与拼接需版面/几何引擎。"""
    raise NotImplementedError(
        "跨页逻辑表拼接尚未实现：需先识别物理表片段并核对拼接依据"
        "（页相邻/续表/列几何/表头路径/期间/币种/主体/注释/行项延续）。"
        "禁止仅凭同标题拼接换年度/换主体/母公司表/另一期间子表。"
    )


def convert_merged_rows(rows: list) -> list:
    """占位：把带合并单元格的行表示展开为每观察值一行的长表（CSV 派生视图前）。

    P0 仅示范接口；真实展开须依据已核实的合并结构与表头关系。
    """
    return list(rows)


# ---------------------------------------------------------------------------
# 结构候选解析：MinerU 的 <table> HTML → 单元格网格（无框线/多级表头候选层）
# ---------------------------------------------------------------------------
from html.parser import HTMLParser  # noqa: E402


class _TableHtmlParser(HTMLParser):
    """把 <table> 的 <tr>/<td>/<th>（含 rowspan/colspan）解析为行单元格列表。"""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows: list = []  # 每行 list of {text,rowspan,colspan}
        self._in_table = 0
        self._row = None
        self._cell = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "table":
            self._in_table += 1
        elif tag == "tr" and self._in_table:
            self._row = []
            self.rows.append(self._row)
        elif tag in ("td", "th") and self._row is not None:
            try:
                rs = int(a.get("rowspan", 1))
            except ValueError:
                rs = 1
            try:
                cs = int(a.get("colspan", 1))
            except ValueError:
                cs = 1
            self._cell = {"text": "", "rowspan": max(1, rs), "colspan": max(1, cs)}
            self._row.append(self._cell)

    def handle_endtag(self, tag):
        if tag == "table":
            self._in_table = max(0, self._in_table - 1)
        elif tag in ("td", "th"):
            self._cell = None
        elif tag == "tr":
            pass

    def handle_data(self, data):
        if self._cell is not None:
            self._cell["text"] += data


def _clean_cell_text(s: str) -> str:
    """去实体、去首尾空白、把单元格内换行折为空格。保留原 token（杂散字符可见，不擅改）。"""
    import html as _html

    s = _html.unescape(s or "")
    s = " ".join(s.split())
    return s


def html_to_grid(html: str) -> dict:
    """把表格 HTML 转为网格（结构候选）。

    返回:
      {"rows": R, "cols": C,
       "cells": [{"cell_id","row","col","rowspan","colspan","text","kind":...}]}
    说明: 这是 MinerU/引擎的“结构候选网格”，不是已验证事实；坐标为行/列索引，
    不编造像素坐标（对照 quality.md：只有表级框就报告 table）。文本杂散 token 保留。
    """
    parser = _TableHtmlParser()
    parser.feed(html or "")
    parser.close()
    rows_parsed = parser.rows
    mat: list = []  # 展开矩阵（存放单元格对象，rowspan/colspan 展开填充）

    for i, row in enumerate(rows_parsed):
        while len(mat) <= i:
            mat.append([])
        j = 0
        for cell in row:
            while j < len(mat[i]) and mat[i][j] is not None:
                j += 1
            obj = {
                "row": i,
                "col": j,
                "rowspan": cell["rowspan"],
                "colspan": cell["colspan"],
                "text": _clean_cell_text(cell["text"]),
            }
            for rr in range(i, i + cell["rowspan"]):
                while len(mat) <= rr:
                    mat.append([])
                while len(mat[rr]) < j + cell["colspan"]:
                    mat[rr].append(None)
                for cc in range(j, j + cell["colspan"]):
                    mat[rr][cc] = obj
            j += cell["colspan"]

    rows = len(mat)
    cols = max((len(r) for r in mat), default=0)
    seen = set()
    cells = []
    for r in range(rows):
        for c in range(cols):
            # rowspan 可以让后续行比最宽行短；缺位是空格，不应越界。
            obj = mat[r][c] if c < len(mat[r]) else None
            if obj is None or id(obj) in seen:
                continue
            seen.add(id(obj))
            cells.append(
                {
                    "cell_id": cell_key(obj["row"], obj["col"]),
                    "row": obj["row"],
                    "col": obj["col"],
                    "rowspan": obj["rowspan"],
                    "colspan": obj["colspan"],
                    "text": obj["text"],
                    "kind": "data",
                }
            )
    cells.sort(key=lambda x: (x["row"], x["col"]))
    return {"rows": rows, "cols": cols, "cells": cells, "matrix": mat}


def grid_to_table_fragment(grid: dict, fragment_id: str, physical_page: int) -> dict:
    """把网格组装成符合 table.schema.json 的物理表片段（结构候选）。"""
    return {
        "table_fragment_id": fragment_id,
        "belongs_to_logical_table": None,
        "physical_page": physical_page,
        "caption": None,
        "units_declaration": None,
        "has_repeated_header": False,
        "header_rows": 0,
        "row_count": grid.get("rows", 0),
        "column_count": grid.get("cols", 0),
        "cells": grid.get("cells", []),
        "candidate_status": "extracted",
    }


def extracted_rows_to_grid(rows: list) -> dict:
    """pdfplumber ``Table.extract`` 行矩阵 → 保守网格候选。

    不推断 rowspan/colspan；空值保留为空单元格，确保视觉复核时列数稳定。
    """
    rows = rows or []
    cols = max((len(r or []) for r in rows), default=0)
    cells = []
    for ri, row in enumerate(rows):
        row = list(row or []) + [None] * (cols - len(row or []))
        for ci, value in enumerate(row):
            text = _clean_cell_text("" if value is None else str(value))
            cells.append(
                {
                    "cell_id": cell_key(ri, ci),
                    "row": ri,
                    "col": ci,
                    "rowspan": 1,
                    "colspan": 1,
                    "text": text,
                    "raw_value": text,
                    "kind": "blank" if not text else "other",
                    "evidence_refs": [],
                }
            )
    return {"rows": len(rows), "cols": cols, "cells": cells}


capabilities = {
    "engine_wired": True,
    "implemented": [
        "单元格键/表头路径占位",
        "MinerU <table> HTML → 单元格网格解析（rowspan/colspan，候选层）",
        "网格 → table.schema 物理表片段",
        "V0.3-B1 MinerU 区域 + 原生词无框线四列网格",
    ],
    "pending": [
        "非四列无框线表、多级表头与 OCR 证据路径",
    ],
}
