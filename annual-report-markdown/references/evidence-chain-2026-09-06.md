# V0.3 证据链机制与币种上下文规则（2026-09-06）

对应设计方案：§12 证据、§13.4 复核队列、§11.2 币种与单位。

## 背景

C4.4 结论指出：矢量轮廓数值（中国人保）当前只有 MinerU 一个识别通道，无法交叉验证，
且缺人工签核记录；在完成"第二独立识别通道 + 逐格一致性判定 + 人工签核"之前，这些数值
不可引用或计算。同时 C3.2 仍留若干"证据上下文"缺口（如格力半年报因 `单位：元` 未明确
人民币而保持 0 facts）。本轮先把机制骨架落地，再补一条通用币种规则。

## 交付 A：多通道一致性 + 人工签核机制骨架

新增 `scripts/pipeline/evidence_chain.py`（纯标准库，不引入新依赖）：

- `parse_numeric` / `normalize_numeric`：只剥离格式（括号负数、千分位逗号、百分号），
  用 Decimal 保精度、避免科学计数法；不做单位倍率/币种换算。
- `register_channel` / `list_channels`：独立识别通道注册表（通道必须区分来源与模型）。
- `compare_channel_candidates`：对同一单元格的多个通道候选做逐格一致性判定。
- `per_cell_consistency` / `fragment_consistency`：对结构网格做逐格一致性 + 汇总。
  `counts` 按 consistent / conflict / single_channel / insufficient 分类。
- 签核记录：`new_signoff` / `write_signoffs` / `load_signoffs` / `aggregate_signoffs`。
  只有 `decision=accepted` 且 `eligible_for_calculation=True` 才开放计算资格；
  记录追加写入 `复核/signoffs.jsonl`，保留旧候选，重跑不抹掉人工决定。

接线：

- `vector_evidence.py`：每个矢量片段登记 MinerU 为第 1 通道，计算 `evidence_chain`
  逐格一致性（当前为 `single_channel`），并写入结果与通道元数据。
- `standard.py`：对每个矢量主表 statement 生成 pending 签核记录并写入 `复核/signoffs.jsonl`；
  review_queue 的 `next_step` 更新为"接入第二通道 + run_signoff.py 签核"；
  `review_burden`、`open_issues` 增加矢量签核计数。
- 新 CLI `scripts/run_signoff.py`：`record` / `list` / `summary`。
- 新 schema `schemas/signoff.schema.json`，并接入 `schemas_io` 与 `validation.validate_package`
  （对 `复核/signoffs.jsonl` 逐行过 schema）。

诚实边界：本机制只是"骨架"。当前仍只有 MinerU 一个通道，矢量轮廓数字的逐格一致性
仍为 `single_channel`，**不可引用或计算**；要升格为 facts 必须先接入第二个独立识别通道，
并完成逐格一致性与人工签核。

测试：新增 `tests/test_evidence_chain.py`（归一化、逐格判定、通道注册、签核读写汇总），
以及 `test_v03_borderless` 中矢量隔离行为不退化。

## 交付 D（第一条规则）：人民币记账本位币声明的通用识别

- `semantics.is_cny_functional_currency_statement`：从仅匹配"本公司以人民币为记账本位币"
  扩展为支持"记账/记帐"两种写法、"为/是"、"本公司/公司/集团"主体，以及
  "人民币为…的记账本位币""记账本位币为人民币"等表述。仍要求包含"记账本位币"语境，
  一般人民币提及不匹配。
- `financials.py`：当"主表范围内"找不到记账本位币声明时，做**文档级回退**（声明常位于
  财务报表附注"公司基本情况"节，早于第一张主表）；仍要求在文档中出现明确的本位币声明。

作用：把 C3.2 观察到的 `currency_not_explicit_cny` 缺口进一步收窄——若某报告确实声明人民币
记账本位币、但表述是旧正则没覆盖的常见变体（记帐、为/是、人民币在前等），不再因 `单位：元`
未显式带"人民币"而误判。这是**通用健壮性改进**，不是发行人特判。

## 真实回归（格力半年报，2026-09-06）

- 样本：`000651_格力电器_2026年半年度报告.pdf`（190 页，原生中文 A 股）
- 命令：`python3 scripts/run_parse.py run <pdf> --output <dir> --auto-borderless`
- 结果：表格片段 179、逻辑主表 8（六类主表 + 2 权益表）、**可计算事实 384**、
  Schema **579/579** 通过；vector pages 0；整体 `partial`（3 稀疏页待复核等 4 项开放问题）。
- 六个主表逻辑表 `currency=CNY`、`currency_basis=functional_currency_declaration`。
- **诚实结论**：384 与 C4.4 基线一致，**本轮改动无回退**；但格力半年报的 currency 缺口
  实际上在 C3.3 已被解决（三列相对期间 + CNY bridge），并非本轮新解锁。因此本规则的
  增量价值无法用格力半年报证明，需在 C4.5 新发行人（含表述变体的报告）上验证。

## 测试结果

- 全套 `python3 -m unittest discover -s tests`：83 项全绿（此前 82，新增语义变体 1）。
- `python3 scripts/run_parse.py selfcheck`：OK；schema 自我合规 ✓。
- `run_signoff.py` 对不存在的目录退出码 2（诚实报错）。

## 后续（未做）

- 接入第二个独立识别通道（图像 OCR / PP-StructureV3 / Docling）并接线到
  `evidence_chain`，使矢量格从 `single_channel` 进入 `consistent/conflict`。
- 曲线字形直接解码。
- 其余 D 项：中国移动 A+H 权益行模式、中国平安原生证据网格（需真实报告回归验证）。
- C4.5：加入新华保险等新发行人验证；同时天然校验本币种规则在表述变体上的增量价值。
