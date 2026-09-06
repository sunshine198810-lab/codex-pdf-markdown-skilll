# 统一对象模型与研究包 Schema 指南（references/schemas-guide.md）

对应设计方案：§7.3 最小对象模型、§11 财务语义、§15 输出结构、§12 证据。
Schema 文件见 `schemas/*.json`，`schema_version = 2026-09-06.1`。

## 1. 设计意图

新增“统一文档结构”层：保存文件中出现了什么、位于哪里、与什么相连，而不是先决定最终 Markdown 长什么样。**阅读 Markdown、表格 JSON/HTML、财务事实数据三者从同一结构生成，不相互反推。**

原始证据（PDF 哈希、页图、原生对象、引擎原始识别）一开始就建立标识；此后重排、合并、规范化、导出都沿用这些来源关系。

## 2. 最小对象模型（对应 §7.3）

```text
Document
 ├── Page
 │    ├── TextBlock / Heading / List / Footnote
 │    ├── TableFragment
 │    │    └── Cell
 │    ├── Figure
 │    └── UnresolvedRegion
 ├── SectionTree
 ├── LogicalTable
 ├── Observation / FactCandidate / AcceptedFact
 └── Evidence / Issue / ProcessingEvent
```

**共同字段**至少包括：对象 ID、所属文档、页及区域、对象类型、原始文字/对象引用、解析方法、模型或程序版本、来源粒度、候选状态。

**对象关系**至少包括：`belongs_to`、`reading_before`、`continues`、`header_for`、`footnote_of`、`translated_counterpart`、`derived_from`。用 JSON ID 引用即可，第一版不需要图数据库。

**对象去向账目**：每个源对象去向 ∈ {进入正文、进入表格、进入图表、归入页眉页脚、作为重复对象保留记录、未解决}。该账目能发现程序内部漏对象；不能单独证明检测器发现了所有可见区域，还需图像侧覆盖检查与标注样本验收。

## 3. 财务语义：先保住口径，再讨论标准化（对应 §11）

指标名字不能直接作为唯一键。“营业收入/营业总收入/主营业务收入”非无条件同义词；“净利润/归母净利润/经调整净利润”不能合并。

每个值保留维度（见 `schemas/observation.schema.json`）：

| 维度 | 作用 |
|---|---|
| 原始标签与概念映射 | 原文照存；标准概念可未确认，不能强制填满 |
| 主体 | 集团合并/母公司/子公司/分部；不由章节距离随意继承 |
| 期间类型与具体期间 | instant（某日）/ duration（一段期间）；年度标签只是显示辅助 |
| 比较状态 | 本期/上期/期初/期末/调整前/重述后 |
| 披露版本 | 来自哪份报告、何时披露、是否修订 |
| 币种与倍率 | HKD/CNY/USD；元/千元/百万元/亿元分别记录 |
| 数量类型 | 金额/百分比/百分点/每股金额/股数/吨数/人数 |
| 会计与计量口径 | CAS/HKFRS/IFRS/US GAAP；法定/调整后/固定汇率 |
| 业务维度 | 产品/地区/业务/渠道/分部/合同类别 |
| 适用范围 | 持续经营/终止经营/是否含税/是否含特定业务 |
| 审计/审阅状态 | 绑定具体报表或信息，不给整本年报一概赋值 |

同一 2024 数字可能来自 2024 年报原披露，也可能来自 2025 年报重述比较列——**不同观察值，不能覆盖**。财年不必然 12 月结束，不能从“2025 年”推断自然年与人民币。

### 币种与单位

- 列报币、功能币、外币风险表的币种维度分别保存，不把看到的第一个币种套用全表。
- 单位从文档/章节/表格继承，可在行/列/单元格局部覆盖；每次继承保存来源。行与列局部声明冲突时保留候选并复核，不任意决定谁优先。
- 以十进制定点解析与换算，字段：`raw_value` / `numeric_value`（字符串避免浮点损失）/ `normalized_value` / `transformation`（倍率+规则版本+输入来源）/ `display_value`（下游生成，不作核验依据）。
- **单位倍率换算 ≠ 币种换算**：默认不引入市场汇率把所有数据换成人民币；跨币种分析交由有明确汇率与日期依据的上层任务。
- 单位未知时 `normalized_value` 必须为空，而不是猜倍率。

### 空值、符号与比率

区分明确 0 / 空白 / 短横线 / 未披露 / 不适用 / 无法识别 / 约数 / 范围 / “小于某值”，保留原记号与说明，未证实的空值不补 0。括号负数与脚注括号分开；百分比变动与百分点变动分开；基本与稀释 EPS 分开；中国报表“损失以负号填列”等规则绑定适用字段，不做全局符号翻转。

### 一个事实候选的示意（字段结构示例）

```json
{
  "observation_id": "docA/table004/cell032",
  "source_kind": "table_cell",
  "original_label": "Revenue",
  "concept_mapping": {"concept": "revenue", "status": "needs_review"},
  "raw_value": "1,234.50",
  "numeric_value": "1234.50",
  "currency": "HKD",
  "scale": "1000",
  "normalized_value": "1234500.00",
  "period": {"kind": "duration", "start": "2024-04-01", "end": "2025-03-31"},
  "entity_scope": "consolidated",
  "accounting_basis": "HKFRS",
  "restatement_status": "as_reported",
  "evidence_refs": {
    "value": ["ev-cell032"],
    "row_label": ["ev-row07"],
    "period": ["ev-header2025"],
    "unit_currency": ["ev-unit004"],
    "entity_scope": ["ev-title004"],
    "accounting_basis": ["ev-basis01"]
  },
  "quality": {
    "text": "checked",
    "structure": "needs_review",
    "semantics": "needs_review",
    "normalization": "computed_from_candidate",
    "eligible_for_calculation": false
  }
}
```

（数值与日期为虚构，仅展示字段。）候选层允许保留试算值，但结构或语义未通过时不能流入正式可计算数据。实际实现须为必要上下文字段补齐证据。

## 4. 研究包目录与 Schema 清单（对应 §15）

研究包逻辑结构（无相应内容时不制造大量空占位文件）：

```text
报告名_研究包/
├── 00-阅读入口.md
├── 正文/                      # 章节阅读语料 *.md
├── 表格/   index.json + table-NNNN.json/.html
├── 图表/   index.json + figure-NNNN.png/.json
├── 数据/   facts.jsonl + candidates.jsonl
├── 索引/   manifest.json document_map.json evidence.jsonl quality.json review_queue.jsonl
├── 复核/   index.html
└── _internal/  run.json pages/ …引擎候选、操作记录与缓存
```

| Schema 文件 | 校验对象 | 内容要点 |
|---|---|---|
| `schemas/identity.schema.json` | run/manifest 中的报告身份块 | 文件/发行人/报告/版本/会计/核实信息（§6.1） |
| `schemas/page.schema.json` | 页与区域记录 | physical_page、几何、rotation、transform、printed_page_label、区域清单 |
| `schemas/object.schema.json` | 统一文档结构的块/单元格/图/未解析区 | 共同字段 + 关系（§7.3）；figure 复用 |
| `schemas/table.schema.json` | 物理表格片段 table-fragment | 表头区/数据区/行标题/合并单元格、分页片段 |
| `schemas/logical-table.schema.json` | 跨页逻辑表 | 子组边界、期间/主体差异、拼接依据 |
| `schemas/observation.schema.json` | observation/fact-candidate | 语义+证据引用+质量+可计算资格（§11.4） |
| `schemas/evidence.schema.json` | evidence.jsonl 行 | 来源粒度、引擎/处理、定位回查 |
| `schemas/document-map.schema.json` | document_map.json 章节地图 | 原始目录+标准标签+起止块 |
| `schemas/quality.schema.json` | quality.json 质量报告 | 五层检查、对象状态与使用资格、覆盖率 |
| `schemas/review-queue.schema.json` | review_queue.jsonl 行 | 对象/原因/证据/候选/已试方法/下一步 |
| `schemas/manifest.schema.json` | 索引/manifest.json | 报告身份、产物清单、整体状态、未解决问题 |
| `schemas/run.schema.json` | _internal/run.json | 源哈希、版本、配置、资源、失败原因 |

JSONL（facts/candidates/evidence/review_queue）逐行复用对应行 Schema。CSV 是派生计算视图（默认长表“每条观察值一行”），复杂表不能丢 JSON 只留 CSV。

## 5. 使用纪律

- 一个模块不能绕过 Schema 直接修改另一模块结果。
- `raw_value` 保留原字串；`normalized_value` 为十进制字符串；`display_value` 只在导出时生成。
- 每个观察值的证据引用指向证据对象 ID；上下文不能唯一确认的值不能标为 computable。
- Schema 版本随 `schema_version` 演进；换版须记录兼容与迁移。
