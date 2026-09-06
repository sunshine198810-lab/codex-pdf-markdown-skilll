#!/usr/bin/env python3
"""进度总览 HTML：扫描 samples/ 下所有已生成切片（含核对报告），生成 samples/progress.html。

不进设计稿回填，进度用独立 HTML 直观展示（按用户要求）。
用法：python3 scripts/build_progress.py
"""
from __future__ import annotations

import argparse
import html as _html
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent  # annual-report-v02


def _esc(s):
    return _html.escape(str(s))


def collect(samples_root: pathlib.Path):
    rows = []
    for verify in sorted(samples_root.glob("**/索引/table_verify.json")):
        slice_dir = verify.parent.parent
        try:
            tv = json.loads(verify.read_text(encoding="utf-8"))
            manifest = json.loads((slice_dir / "索引/manifest.json").read_text(encoding="utf-8"))
        except Exception:
            continue
        src = manifest.get("source_pdf", {}) or {}
        filename = pathlib.Path(str(src.get("path", "?"))).name
        tables = tv.get("tables", {})
        pages = sorted({t.get("physical_page") for t in tables.values()})
        t_ok = tv.get("tables_ok", 0)
        t_tot = tv.get("tables_total", 0)
        r_ok = sum(t.get("rows_ok", 0) for t in tables.values())
        r_tot = sum(t.get("rows_total", 0) for t in tables.values())
        mism = [(tid, t.get("mismatch_labels", [])) for tid, t in tables.items()
                if t.get("verdict") != "ok"]
        eyeball = (slice_dir / "目检/index.html").is_file()
        rel = slice_dir.relative_to(samples_root)
        rows.append({
            "filename": filename,
            "pages": pages,
            "slice": slice_dir,
            "rel": rel,
            "tables_total": t_tot,
            "tables_ok": t_ok,
            "rows_total": r_tot,
            "rows_ok": r_ok,
            "mismatches": mism,
            "eyeball": eyeball,
            "method": tv.get("method", ""),
        })
    rows.sort(key=lambda r: (r["filename"], r["pages"]))
    return rows


def render(rows) -> str:
    t_tot = sum(r["tables_total"] for r in rows)
    t_ok = sum(r["tables_ok"] for r in rows)
    r_tot = sum(r["rows_total"] for r in rows)
    r_ok = sum(r["rows_ok"] for r in rows)
    n_slice = len(rows)
    n_ping = sum(1 for r in rows if r["eyeball"])
    p = [
        "<!DOCTYPE html><html lang=\"zh\"><head><meta charset=\"utf-8\">",
        "<title>年报解析 V0.2 · 切片进度总览</title>",
        "<style>",
        "body{font-family:-apple-system,'PingFang SC',sans-serif;margin:24px;color:#222}",
        "h1{font-size:20px}.cards{display:flex;gap:16px;margin:12px 0 20px;flex-wrap:wrap}",
        ".card{border:1px solid #ddd;border-radius:8px;padding:10px 16px;min-width:120px}",
        ".card b{font-size:22px;display:block}",
        "table{border-collapse:collapse;width:100%;font-size:13px}",
        "th,td{border:1px solid #ccc;padding:5px 8px;text-align:left;vertical-align:top}",
        "th{background:#f2f6fa}.ok{color:#177245;font-weight:600}.bad{color:#b00020;font-weight:600}",
        "td.num{text-align:right}.mut{color:#b00020;font-size:12px}",
        "a{color:#0b5cad;text-decoration:none}",
        "</style></head><body>",
        "<h1>年报解析 V0.2 · 切片进度总览</h1>",
        "<div class='cards'>",
        f"<div class='card'><b>{n_slice}</b>切片</div>",
        f"<div class='card'><b>{n_ping}</b>含目检页</div>",
        f"<div class='card'><b>{t_ok}/{t_tot}</b>表数值一致</div>",
        f"<div class='card'><b>{r_ok}/{r_tot}</b>数据行一致</div>",
        "</div>",
        "<p style='color:#777;font-size:12px'>注：数值一致 = 显示网格与源 PDF 同页 pdfplumber 精确文字层核对一致"
        "（verify_tables 文字层+x坐标法）；不代表语义/期间/单位归属正确，亦未人工终核。</p>",
        "<table><tr><th>报告文件</th><th>物理页</th><th>表核对</th><th>数据行</th>"
        "<th>不一致表</th><th>目检页</th><th>切片目录</th></tr>",
    ]
    for r in rows:
        pages_txt = ", ".join(str(x) for x in r["pages"])
        cls_t = "ok" if (r["tables_ok"] == r["tables_total"] and r["tables_total"] > 0) else "bad"
        cls_r = "ok" if (r["rows_ok"] == r["rows_total"] and r["rows_total"] > 0) else "bad"
        eye = ("<a href='" + _esc(pathlib.PurePosixPath(r["rel"]) / "目检/index.html")
               + "'>打开</a>" if r["eyeball"] else "—")
        muts = ""
        if r["mismatches"]:
            muts = "<span class='mut'>" + _esc("；".join(
                f"{tid}({','.join(str(x) for x in m[:2])})" for tid, m in r["mismatches"]
            )[:200]) + "</span>"
        rel_txt = _esc(str(r["rel"]))
        p.append(
            "<tr>"
            f"<td>{_esc(r['filename'])}</td>"
            f"<td>{_esc(pages_txt)}</td>"
            f"<td class='num'><span class='{cls_t}'>{r['tables_ok']}/{r['tables_total']}</span></td>"
            f"<td class='num'><span class='{cls_r}'>{r['rows_ok']}/{r['rows_total']}</span></td>"
            f"<td>{muts}</td>"
            f"<td>{eye}</td>"
            f"<td class='mut'>{rel_txt}</td>"
            "</tr>"
        )
    p.append("</table></body></html>")
    return "\n".join(p)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--samples", default=str(ROOT / "samples"))
    args = ap.parse_args(argv)
    rows = collect(pathlib.Path(args.samples))
    out = pathlib.Path(args.samples) / "progress.html"
    out.write_text(render(rows), encoding="utf-8")
    t_tot = sum(r["tables_total"] for r in rows)
    t_ok = sum(r["tables_ok"] for r in rows)
    print(f"进度页已生成: {out}")
    print(f"  切片 {len(rows)} 个；表数值一致 {t_ok}/{t_tot}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
