"""gen_ocr_truth_html：为扫描 OCR 主表页生成“人工真值标注”单页 HTML（D3，probe）。

用法：
    python3 scripts/gen_ocr_truth_html.py \
        tests/_research_packages/_D3_mineru_main/auto/<...>_content_list.json \
        --pdf /Users/eric/Documents/copilot设计skilll/601899_紫金矿业_2025年度报告.pdf \
        --first-page 113 --pages 113,114,116,118,120,124 \
        --out tests/_research_packages/_D3_truth_kit

每页：
  - 左栏 = 原 PDF 该物理页截图（pypdfium2 光栅，可切换 1:1 放大细读数字）
  - 右栏 = 页面级真值 5 输入 + 逐行 OCR（全列）表 + 每行“判定(OK/LBL/订正)+订正内容”
右上角“下载真值 JSON”导出单文件 *_truths.json（用户保存后交回，compare_ocr_truth.py 读取）。

不触碰 facts；OCR 数值签核前保持 single_channel。
依赖：pypdfium2（本机已装）；纯渲染为 dev 工具，不入 pipeline 运行时。
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gen_ocr_truth_kit import _rows_of_html  # noqa: E402
from route_ocr_main_statements import _cells_of_md_table, content_list_tables, route_statement  # noqa: E402


PAGE_FIELDS = [
    ("stmt", "报表类型", "资产负债表 / 利润表 / 现金流量表 / 权益变动表"),
    ("consol", "合并 / 母公司", "合并 / 母公司 / 不清楚"),
    ("side", "左右半页", "资产侧 / 负债权益侧 / 整表 / 续页"),
    ("currency", "币种单位", "如：人民币元 / 千元 / 万元"),
    ("header", "列期间表头", "如：2025年12月31日 / 2024年12月31日"),
]

TPL = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>D3 扫描主表 · 人工真值标注</title>
<style>
:root{--bd:#d8dee6;--ac:#2563eb;--ok:#16a34a;--lbl:#d97706;--fix:#dc2626;--mut:#8a94a3;}
*{box-sizing:border-box;}
body{margin:0;font:14px/1.5 -apple-system,"PingFang SC","Helvetica Neue",sans-serif;color:#1f2430;background:#f3f5f8;}
header{position:sticky;top:0;z-index:20;background:#fff;border-bottom:1px solid var(--bd);padding:10px 16px;display:flex;gap:14px;align-items:center;flex-wrap:wrap;}
header h1{font-size:15px;margin:0;font-weight:650;}
header .hint{color:var(--mut);font-size:12px;}
button{font:inherit;border:1px solid var(--bd);background:#fff;border-radius:8px;padding:6px 12px;cursor:pointer;}
button.primary{background:var(--ac);border-color:var(--ac);color:#fff;}
button:hover{filter:brightness(.97);}
section.page{display:grid;grid-template-columns:minmax(0,52%) minmax(0,48%);gap:0;border-bottom:1px solid var(--bd);background:#fff;}
@media (max-width:1100px){section.page{grid-template-columns:1fr;}}
.left{border-right:1px solid var(--bd);padding:10px;background:#fafbfd;}
.left .title{font-weight:600;margin-bottom:8px;display:flex;align-items:center;gap:8px;flex-wrap:wrap;}
.badge{font-size:11px;padding:1px 8px;border-radius:20px;background:#eef2ff;color:var(--ac);}
.badge.unresolved{background:#fef2f2;color:var(--fix);}
.imgwrap{overflow:auto;max-height:86vh;border:1px solid var(--bd);background:#fff;border-radius:8px;}
.imgwrap img{width:100%;display:block;}
.imgwrap.orig img{width:auto;max-width:none;}
.toolbar{display:flex;gap:10px;align-items:center;margin:6px 0 2px;font-size:12px;color:var(--mut);}
.right{padding:12px 16px;}
.right .sec{margin-bottom:12px;}
.right h3{font-size:13px;margin:0 0 6px;color:#374151;}
.grid5{display:grid;grid-template-columns:1fr 1fr;gap:8px 12px;}
.grid5 label{display:block;font-size:11px;color:var(--mut);margin-bottom:2px;}
.grid5 input{width:100%;font:inherit;border:1px solid var(--bd);border-radius:6px;padding:5px 8px;}
.grid5 .full{grid-column:1/-1;}
.rowbox{max-height:66vh;overflow:auto;border:1px solid var(--bd);border-radius:8px;}
table.rows{width:100%;border-collapse:collapse;font-size:12px;}
table.rows th{position:sticky;top:0;background:#eef1f5;z-index:5;text-align:left;padding:5px 7px;border-bottom:1px solid var(--bd);}
table.rows td{padding:3px 7px;border-bottom:1px solid #eef1f5;vertical-align:top;}
td.lab{font-weight:600;white-space:nowrap;}
td.val{max-width:340px;word-break:break-all;color:#374151;}
select.vd{font:inherit;border:1px solid var(--bd);border-radius:6px;padding:2px 4px;}
select.vd.OK{color:var(--ok);font-weight:600;}select.vd.LBL{color:var(--lbl);}select.vd.FIX{color:var(--fix);}
input.fix{width:210px;font:inherit;border:1px solid var(--bd);border-radius:6px;padding:2px 6px;}
input.fix:disabled{background:#f3f5f8;color:#c3cad4;}
.small{font-size:11px;color:var(--mut);}
</style>
</head>
<body>
<header>
  <h1>D3 扫描主表 · 人工真值标注（对照原 PDF 页填，标完点右上“下载真值 JSON”）</h1>
  <span class="hint">判定：<span style="color:var(--ok)">OK</span>=该行标签与数值与原文一致｜
  <span style="color:var(--lbl)">LBL</span>=仅标签错/漏｜<span style="color:var(--fix)">订正</span>=在右侧写正确内容。</span>
  <span style="flex:1"></span>
  <button onclick="allRowsOK()">当前页全标 OK</button>
  <button onclick="resetAll()">全部重置</button>
  <button class="primary" onclick="downloadJSON()">下载真值 JSON</button>
  <button id="copybtn" onclick="copyJSON()">复制 JSON（备用）</button>
  <span id="status" class="small" style="min-width:150px"></span>
</header>
__SECTIONS__
<script>
const PAGES = __PAGES_JSON__;
const FIELDKEYS = ["stmt","consol","side","currency","header"];
function el(id){return document.getElementById(id);}
function val(id){return el(id).value.trim();}
function currentPage(){
  const secs=Array.from(document.querySelectorAll('section.page'));
  let cur=null;
  for(const s of secs){ const r=s.getBoundingClientRect();
    if(r.top < window.innerHeight*0.55 && r.bottom > 0){ cur=s.id.slice(1); } }
  return cur || (secs.length? secs[0].id.slice(1) : (Object.keys(PAGES)[0]||'113'));
}
function setVerdict(page,n,v){
  const sel=el(`vd_${page}_${n}`); const inp=el(`fix_${page}_${n}`);
  sel.value=v;
  sel.className='vd'+(v==='OK'?' OK':v==='LBL'?' LBL':v==='FIX'?' FIX':'');
  inp.disabled = !(v==='LBL'||v==='FIX');
  if(v==='OK'||v===''){inp.value='';}
}
function setRows(page,v){
  const p=PAGES[page]; if(!p) return;
  p.rows.forEach((r)=>{ const s=el(`vd_${page}_${r.n}`); if(s){ setVerdict(page,r.n,v);} });
}
function allRowsOK(page){
  page = String(page||currentPage());
  if(!PAGES[page]) return;
  if(!confirm(`把物理页 ${page} 全部 ${PAGES[page].rows.length} 行标为 OK？\n（误点可点该页“重置未标”，或直接刷新页面回到全未标）`)) return;
  setRows(page,'OK'); setStatus(`第 ${page} 页已全标 OK（仅内存，未下载不影响）`,'var(--ok)');
}
function resetPage(page){
  page=String(page||currentPage());
  if(!PAGES[page]) return;
  setRows(page,''); setStatus(`第 ${page} 页已重置为未标`,'#374151');
}
function resetAll(){
  if(!confirm('把全部页重置为“未标”？仅当前内存态，不影响已下载文件。')) return;
  Object.keys(PAGES).forEach((k)=>{ if(k!=='__first'){ setRows(k,''); } });
  setStatus('全部页面已重置为未标','#374151');
}
function collect(page){
  const p=PAGES[page];
  const pl={}; FIELDKEYS.forEach((k,i)=>{pl[k]=val(`pl_${page}_${i}`);});
  return {route:p.route, page_level:pl,
    rows:p.rows.map((r)=>({n:r.n,label:r.label,cells:r.cells,
      verdict:el(`vd_${page}_${r.n}`).value,
      corrected:el(`fix_${page}_${r.n}`).value}))};
}
function downloadJSON(){
  const pages={}; Object.keys(PAGES).forEach((k)=>{ if(k==='__first'){return;} pages[k]=collect(k); });
  const data={kit:'ocr-truth-html',first_page:PAGES.__first,generated_by:'gen_ocr_truth_html.py',pages};
  const blob=new Blob([JSON.stringify(data,null,1)],{type:'application/json'});
  const a=document.createElement('a'); a.href=URL.createObjectURL(blob);
  a.download='_D3_truths.json'; a.click(); URL.revokeObjectURL(a.href);
}
function buildData(){
  const pages={}; Object.keys(PAGES).forEach((k)=>{ if(k==='__first'){return;} pages[k]=collect(k); });
  return {kit:'ocr-truth-html',first_page:PAGES.__first,generated_by:'gen_ocr_truth_html.py',pages};
}
function setStatus(msg,color){const s=el('status'); if(s){s.textContent=msg; s.style.color=color||'#374151';} }
function copyJSON(){
  const txt=JSON.stringify(buildData(),null,1);
  const ta=document.createElement('textarea'); ta.value=txt;
  ta.style.position='fixed'; ta.style.opacity='0'; document.body.appendChild(ta);
  ta.select(); let ok=false;
  try{ ok=document.execCommand('copy'); }catch(e){ ok=false; }
  document.body.removeChild(ta);
  if(navigator.clipboard && navigator.clipboard.writeText){
    navigator.clipboard.writeText(txt).then(()=>setStatus('已复制，粘贴保存为 _D3_truths.json','var(--ok)'),
      ()=>setStatus(ok?'已复制（旧式），请粘贴保存为 _D3_truths.json':'复制失败：请用“下载真值 JSON”', ok?'var(--ok)':'var(--fix)'));
  } else {
    setStatus(ok?'已复制（旧式），请粘贴保存为 _D3_truths.json':'复制失败：请用“下载真值 JSON”', ok?'var(--ok)':'var(--fix)');
  }
}
</script>
</body>
</html>
"""


def render_image(pdf_path: str, physical: int, scale: float = 2.0) -> str:
    import pypdfium2 as pdfium
    pdf = pdfium.PdfDocument(pdf_path)
    page = pdf[physical - 1]
    try:
        bmp = page.render(scale=scale)
        pil = bmp.to_pil()
        buf = io.BytesIO()
        pil.save(buf, "PNG")
        return base64.b64encode(buf.getvalue()).decode("ascii")
    finally:
        pdf.close()


def _safe(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def build_section(page: int, html: str, route: dict, img_b64: str) -> str:
    rows = _rows_of_html(html)[:150]
    cand = route.get("candidate") or ("tie:" + ",".join(route.get("tied_with") or [])) or "unresolved"
    unresolved = cand == "unresolved" or cand.startswith("tie")
    bad = ' unresolved' if unresolved else ''
    conf = route.get("confidence"); side = route.get("side"); scope = route.get("scope")
    side_hint = "，" + str(side) if side else ""
    conf_hint = f"，conf={conf}" if conf else ""
    sugg_stmt = "" if unresolved else cand
    sugg_side = ("资产侧" if side == "asset_side" else "负债权益侧" if side == "equity_side"
                 else "整表" if side == "full" else "")
    L = [f'<section class="page" id="p{page}">']
    L.append('<div class="left">')
    L.append(f'<div class="title">物理页 {page} '
             f'<span class="badge{bad}">机器路由：{_safe(cand)}</span>'
             f'<span class="small">{conf_hint}{side_hint} scope={scope}</span></div>')
    L.append(f'<div class="imgwrap" id="iw{page}">'
             f'<img alt="原 PDF 第 {page} 页" src="data:image/png;base64,{img_b64}"></div>')
    L.append('<div class="toolbar"><input type="checkbox" '
             f'onchange="el(\'iw{page}\').classList.toggle(\'orig\',this.checked)"> 1:1 原始尺寸（放大细读数字，横滚查看）</div>')
    L.append('</div>')
    L.append('<div class="right">')
    L.append('<div class="sec"><h3>① 页面级真值</h3><div class="grid5">')
    for i, (key, lab, ph) in enumerate(PAGE_FIELDS):
        val = sugg_stmt if key == "stmt" and sugg_stmt else (sugg_side if key == "side" and sugg_side else "")
        cls = ' class="full"' if key == "header" else ""
        L.append(f'<div{cls}><label>{lab}</label>'
                 f'<input id="pl_{page}_{i}" placeholder="{_safe(ph)}" value="{_safe(val)}"></div>')
    L.append('</div></div>')
    L.append('<div class="sec" style="display:flex;gap:10px;align-items:center;flex-wrap:wrap">'
             '<h3 style="margin:0">② 逐行判定</h3>'
             f'<button onclick="allRowsOK({page})" style="padding:2px 8px;font-size:12px">整页标 OK</button>'
             f'<button onclick="resetPage({page})" style="padding:2px 8px;font-size:12px;color:var(--fix);border-color:#f3c1c1">本页重置未标</button>'
             '<span class="small">若某行跨页续段/被公章遮挡，请在“订正内容”注明“续段”/“遮挡”。</span></div>')
    L.append('<div class="rowbox"><table class="rows"><thead><tr>'
             '<th style="width:34px">#</th><th>行标签(OCR)</th><th>OCR 其余列</th>'
             '<th style="width:92px">判定</th><th style="width:220px">订正内容</th></tr></thead><tbody>')
    for i, row in enumerate(rows, 1):
        label = _safe(row[0]) if row else ""
        rest = " / ".join(_safe(c) for c in row[1:]) if len(row) > 1 else ""
        if not rest:
            rest = '<span class="small">（无其余列）</span>'
        L.append(f'<tr><td>{i}</td><td class="lab">{label}</td><td class="val">{rest}</td>'
                 f'<td><select class="vd" id="vd_{page}_{i}" onchange="setVerdict({page},{i},this.value)">'
                 '<option value="">未标</option><option value="OK">OK</option>'
                 '<option value="LBL">LBL</option><option value="FIX">订正</option></select></td>'
                 f'<td><input class="fix" id="fix_{page}_{i}" disabled '
                 'placeholder="LBL:正确标签 / 订正:正确整行(空格分隔)"></td></tr>')
    L.append('</tbody></table></div>')
    L.append('</div></section>')
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("content_list", help="MinerU *_content_list.json")
    ap.add_argument("--pdf", required=True, help="原 PDF 路径")
    ap.add_argument("--first-page", type=int, default=113)
    ap.add_argument("--pages", default="113,114,116,118,120,124")
    ap.add_argument("--out", required=True, help="输出目录")
    ap.add_argument("--scale", type=float, default=2.0, help="PDF 光栅倍率（默认2.0≈1190px宽）")
    args = ap.parse_args()
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    want = [int(x) for x in args.pages.split(",") if x.strip()]
    tables = {p: h for p, h in content_list_tables(args.content_list, args.first_page)}
    pages_json = {"__first": args.first_page}
    sections = []
    for page in want:
        html = tables.get(page)
        if not html:
            print("!! 无 OCR 表:", page); continue
        cells = _cells_of_md_table(html)
        route = route_statement(cells)
        print(f"  渲染 PDF 第 {page} 页…", flush=True)
        img = render_image(args.pdf, page, args.scale)
        sections.append(build_section(page, html, route, img))
        pages_json[str(page)] = {
            "route": route,
            "rows": [{"n": i, "label": r[0] if r else "", "cells": r}
                     for i, r in enumerate(_rows_of_html(html)[:150], 1)],
        }
    body = TPL.replace("__SECTIONS__", "\n".join(sections))
    body = body.replace("__PAGES_JSON__", json.dumps(pages_json, ensure_ascii=False))
    dest = out / "ocr-truth-annotate.html"
    dest.write_text(body, encoding="utf-8")
    print(f"已生成: {dest}  ({dest.stat().st_size/1e6:.1f} MB, 页面: {list(pages_json)[1:]})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
