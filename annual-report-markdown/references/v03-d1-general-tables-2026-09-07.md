# V0.3-D1 非主表广度：全书表格分族 + 数值表观察候选（2026-09-07）

对应设计方案：§9.1 全书表格清单与分族、§11 财务语义先保住口径、§15 数据层 candidates、
§13 对象状态与使用资格。路线：2026-09-07 用户拍板——港股繁英低需求（Docling 移除）、
扫描覆盖高优先级；“把其他表纳入”= 先广度（全部非主表可分类、进候选、可定位、分族计数）
后深度（选高复用表族 → facts）。

## 背景

D1 之前，只有六张法定主表（financials）与权益变动表（equity）会产出事实；每份报告的
其余非主表（附注表、经营数据表、治理/指标表等）永远停在 M1 泛化复核项
（`表格结构候选尚未绑定表头路径…`），`数据/candidates.jsonl` 为空。

实证（茅台 C4.6 研究包 `表格/table-*.json`）：225 个片段中 **208 个含数值**、17 个纯文本
（目录/释义/公司信息表）；所有片段的 `units_declaration` 均为空——单位/期间只在主表边界内
绑定，非主表从不绑定。

## 实现

新增 `scripts/pipeline/general_tables.py`（已在 `scripts/pipeline/version.py` MODULES 登记）：

- `build_general_candidates(root, pages, table_items, evidence, review, identity,
  engine_version, *, claimed_fragments, headings=None)` —— **只处理未被六类法定主表/权益表
  消费的片段**（claimed_fragments 由调用方从 all_logical_tables 的 fragments 汇总传入，避免双口径）。
- **分族**（候选标签，非语义结论）：
  - `financial_notes`（财务报表附注附表）：优先按独立“财务报表附注”行；缺失时回退用
    “已消费主表/权益表最大物理页 + 1”作附注起点近似；
  - `management_discussion_data`（经营讨论数据表，按最近“第X节”标题的标准标签）；
  - `governance_or_general`；`unclassified_financial`；`text_table`（纯文本表只分类留存）。
- **候选生成**：仅对含数值表，按“行标签（col0 文本）+ 数值单元格”生成 observation 候选：
  - 一律 `eligible_for_calculation=false`、quality 全 `needs_review`、`status=fact_candidate`；
  - 单位/币种尽力从同页单位行继承（`semantics.parse_unit_currency`）；期间/主体/准则继承不到
    就省略字段，不置 null（observation.schema 不允许）、不猜测（`period_unbound_reason` 说明）；
  - 数值原样保留（含括号负号）；`—`/空不生成；`normalized_value` 保持 None；
  - 证据引用复用片段表级证据（`evidence_ref`），可 traceable 但不可 citable/computable。
- **接线**（`pipeline/standard.py`）：equity 之后调用，candidates 并入 `financial_result["candidates"]`；
  对象账目按 `candidate_generated`/`classified_text_table` 同步；复核项更新族标签与下一步（保持 open）；
  manifest/quality 增加 `general_tables` 分族计数与 `general_candidates`。
- **Schema**：`table.schema.json`/`object.schema.json` 的 `candidate_status` 枚举新增
  `candidate_generated`/`classified_text_table`；schema_version 升 `2026-09-07.1`。

## 结果（茅台 2026 半年报端到端）

| 指标 | 值 |
|---|---|
| 表格片段 / 逻辑主表 / 可计算事实 | 225 / 8 / **469（与基线一致，D1 不触碰 facts）** |
| 非主表分类（claimed 之外） | 204：financial_notes 160、management_discussion_data 8、governance_or_general 1、unclassified_financial 18、text_table 17 |
| 数值候选表 / 候选行数 | 187 / 2,497（全部 eligible_for_calculation=false） |
| Schema 校验 | 576/576 通过 |
| 测试 | 91 项全绿（新增 `tests/test_general_tables.py` 5 项）；selfcheck OK |

安全不变量：facts 计数与主表/权益表路径完全不变；候选永不写 facts；claimed 片段不被触碰。

## 边界与后续

- 族标签是候选（classification candidate），不是会计口径结论；160 张附注表只是“位置+结构+原值”
  候选，期间/单位/主体/准则仍未逐列绑定，不可引用/计算。
- `unclassified_financial`（茅台 18）仍待更强分类信号（如节标题子级、表内科目锚点）。
- 深度（V0.3-D2）：在 D1 分类与候选之上选高复用表族——优先固定资产/无形资产/在建工程变动表
  （期初→增减→期末天然勾稽，类 C3 门），其次存货、应收、收入拆分；逐族加“口径配置+证据门+勾稽门”。
- 扫描轨（V0.3-D3，并行预研）：PP-StructureV3 跑紫金扫描/图页主表，验收=该页人工 OCR 标注真值；
  OCR 数值 `single_channel` 不升 facts，需第二通道+签核。

## 复现

```bash
python3 scripts/run_parse.py run <600519_贵州茅台_2026年半年度报告.pdf> \
  --output tests/_research_packages/_D1check_研究包
python3 -m unittest discover -s tests
python3 scripts/run_parse.py selfcheck
```

> 临时验证包 `tests/_research_packages/_D1check_研究包` 已被 `.gitignore` 排除，不作为交付物。
