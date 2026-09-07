# V0.3-D4 双通道 OCR：MinerU × PP-StructureV3 自动对比 + 合并路由（2026-09-07）

状态：探针完成（无人工）。
- 数值层：双通道行级一致 **65.3%**（147 匹配行中值全等 96、不一致 51）→ 暂不足以自动升 facts。
- 路由层：**16/16 页双引擎一致全路由**（含原 MinerU 0 命中的 p114/115）。

## 背景与选型
- 人工真值标注(B)被放弃（费时）→ 走 A：装 PP-StructureV3 作**第二独立 OCR 通道**（Paddle 系 vs MinerU torch 系，引擎/框架真正独立，满足 evidence-chain 第二通道要求）。
- 环境隔离：独立 venv `tests/_research_packages/_D3_paddlevenv/`
  - `paddlepaddle==3.3.1`（PyPI 有 cp312/macosx_11_0_arm64 wheel）+ `paddlex==3.7.2[ocr]`
  - **不碰** MinerU 的 torch 框架 Python（/Library/Frameworks/.../3.12）也不碰管线环境
  - 模型缓存 `~/.paddlex/official_models/`：PP-OCRv5_server_det/rec + SLANet_plus/SLANeXt_wired + RT-DETR-L wired/wireless_table_cell_det + PP-LCNet 方向 + PP-FormulaNet
  - PP 把页面上公章识别为 `seal` 块（parsing block_label）；输出含 `table_res_list[].pred_html`（SLANet 重建表 HTML）+ cell_box_list + table_ocr_pred

## 工具（已入库，均为 probe/脚本）
1. `scripts/pp_ocr_pages.py --pdf <PDF> --pages 113,... --out <dir> --scale 2.5 [--keep-raw]`
   - 用 `_D3_paddlevenv/bin/python` 跑；pypdfium2 渲染页→PP-StructureV3→每页 `pp_pNNN.json{tables:[{html,rows,cells}]}`
   - 实测 CPU ~56–126s/页（arm64 CPU；16 页约 25 分钟）
2. `scripts/compare_dual.py --content-list <MinerU json> --first-page 113 --pp-dir <out> --pages ... --out <report.md>`
   - 纯 stdlib（workspace python 即可）；按"行首标签"把 MinerU 行与 PP 行配对 → 值全等/不一致/仅MinerU/仅PP
   - 产出 md 报告 + `*_matrix.json`
3. `scripts/route_ocr_combined.py --content-list <MinerU> --pp-dir <out> --pages ...`
   - 逐页 MinerU(route_statement) ∪ PP(route_statement on pp_pNNN 全格) → final + source(both/mineru_only/pp_supplied/conflict)

## 路由规则修复（选项1 触发）
`route_ocr_main_statements.py` balance 分支原要求 `hits>=4`；但 BS 右侧"续页"页末只到 `负债合计`（权益段在下一页，如 p114 仅 3 锚点）被卡掉。
改为：`has_left`(资产总计) 分支仍 `>=4`；**纯右侧页**（含 `负债/所有者(股东)权益合计` 且右题头 `负债和股东权益|负债和所有者权益`）→ `hits>=2` 即 candidate `balance_sheet`；`side=equity_side` 由 `_has_right_side` 决定。
新增测试 `test_balance_sheet_right_partial_equity_side`，**107 全绿**。（仍 candidate、不升 facts。）

## 紫金数值对比（6 探针页：113 资产侧 / 114 负债权益右侧 / 116 合并利润 / 118 合并权益变动 / 120 合并现金流 / 124 母公司利润）

| 页 | MinerU行 | PP行 | 匹配 | 值全等 | 值不一致 | 备注 |
|---|---|---|---|---|---|---|
| 113 | 34 | 33 | 31 | 28 | 3 | MinerU 把 资产总计 数值并入上一行；PP 正确 |
| 114 | 29 | 29 | 28 | 16 | 12 | 原 MinerU 路由 0 命中页；PP 恢复出表；两引擎仍有列错位 |
| 116 | 34 | 34 | 27 | 23 | 4 | MinerU 利润总额值错入"减:所得税费用"行 |
| 118 | 22 | 21 | 12 | 4 | 8 | 宽权益表两引擎都吃紧：段标题合并/粘连 |
| 120 | 29 | 28 | 21 | 15 | 6 | MinerU 相邻两行标签+数值粘连 |
| 124 | 37 | 36 | 28 | 10 | 18 | MinerU 2025 列整体下移一行（行错位最重） |
| **合计** | 185 | 181 | **147** | **96(65.3%)** | 51 | 仅MinerU 19 / 仅PP 16 |

## 差异模式（重要）
不一致绝大多数**不是"哪个数对"之争，而是 MinerU 的扫描表解析系统性问题**：
1. **行错位/数值落错行**：MinerU 按文本流关联标签↔值，遇到"空标签行 / 其中:、减: 缩进子行 / 合计行"时，把上行数值挂到下一标签下（p116/p124 最典型；p124 2025 列整体下移一行）。
2. **相邻格粘连**：`预计负债 80,465,3538,803,482,357`、`研发费用 1,852,963,770440,958,072` —— 两格数字拼成一串。
3. PP（SLANet 网格重建）在拆分粘连、标签↔值配对、合计归属上普遍更稳。
4. 但 p114/p118 两引擎仍各有列错位；无真值/第三方时**无法裁定谁对** → 双通道仍需勾稽/签核。

## 16 页合并路由结果（113–128，全路由，每页 both=两引擎一致）

| 族 | 合并 | 母公司 |
|---|---|---|
| 资产负债表 | 113=asset_side；114/115=equity_side（114 页末到 负债合计，115 含股东权益段） | 122=asset_side；123=equity_side |
| 利润表 | 116；117=OCI 续段 | 124 |
| 权益变动表 | 118/119 | 125/126 |
| 现金流量表 | 120/121 | 127/128 |

- scope：含"少数股东权益/少数股东损益"锚点的页=consolidated（116/118/119/120/121…）；母公司页无该锚点→单页标 unresolved（candidate 层）。
- 跨页逻辑表（114↔115 同一条合并资产负债表右侧）的 scope/side 聚合在更上层做，本探针未升 facts。

## 结论与门槛（facts 升级路径）
- 双通道行级值一致率 **65.3%**：不一致处**禁止**自动采纳任一方。
- 仅当 **①两引擎逐格一致 且 ②会计勾稽通过（如 资产=负债+权益、流动资产合计=Σ、现金流入合计=Σ…）且 ③statement-type/scope 由路由+页级上下文确认**，OCR 数字才可考虑从 `single_channel` 升到更强的证据层；最终升 facts 仍走 evidence-chain 的 reconcile+signoff（人工签核可降为"抽样签核"而非逐页真值）。
- PP-StructureV3 现为**可用第二通道**：未来扫描页默认双引擎→仅保留一致行作候选证据；不一致行入 `review_queue`。
- 本次结论：D3/D4 扫描轨 = "工具链就绪 + 探针量化 + 路由 16/16"，**扫描主表数字不进 facts**（与原设计一致）。

## 产物位置
- `tests/_research_packages/_D3_paddle/`：venv、`out/pp_pNNN.json`（16 页全）、`out/raw/`、`out/work/pNNN.png`、`dual_report.md`、`dual_report_matrix.json`、`route_combined_16.md`（均 gitignore）
- `tests/_research_packages/_D3_truth_kit/`：已放弃的人工真值 html/md（保留供参考，不入流程）
