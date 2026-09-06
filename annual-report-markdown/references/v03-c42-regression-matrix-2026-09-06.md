# 年报解析回归矩阵

> 这是能力与安全边界审计，不是人工标注准确率。相邻年份按发行人聚集，不能当作独立样本。

| 报告 | 页数 | 自动升级 | 逻辑主表 | 权益表 | 主表 facts | 权益 facts | 能力状态 | 安全门 | 问题分类 |
|---|---:|---|---:|---:|---:|---:|---|---|---|
| 000651_格力电器_2025年度报告.pdf | 219 | not_requested | 6 | 2 | 530 | 99 | facts_emitted | 通过 | — |
| 000651_格力电器_2026年半年度报告.pdf | 190 | not_requested | 6 | 2 | 384 | 0 | facts_emitted | 通过 | equity_structure_without_facts, incomplete_period_context |
| 600036_招商银行_2025年度报告.pdf | 350 | not_requested | 6 | 0 | 565 | 0 | facts_emitted | 通过 | — |
| 600519_贵州茅台_2026年半年度报告.pdf | 110 | not_requested | 6 | 2 | 409 | 60 | facts_emitted | 通过 | — |
| 600941_中国移动_2023年度报告.pdf | 220 | not_requested | 3 | 1 | 61 | 0 | facts_emitted | 通过 | equity_structure_without_facts, incomplete_period_context |
| 600941_中国移动_2025年度报告.pdf | 214 | not_requested | 3 | 1 | 145 | 0 | facts_emitted | 通过 | equity_structure_without_facts, incomplete_period_context |
| 601318_中国平安_2025年度报告.pdf | 370 | not_requested | 6 | 0 | 502 | 0 | facts_emitted | 通过 | — |
| 601318_中国平安_2026年半年度报告.pdf | 176 | not_requested | 6 | 0 | 493 | 0 | facts_emitted | 通过 | — |
| 601600_中国铝业_2026年半年度报告.pdf | 206 | not_requested | 6 | 2 | 487 | 0 | facts_emitted | 通过 | equity_structure_without_facts |
| 601816_京沪高铁_2025年度报告.pdf | 195 | not_requested | 6 | 2 | 338 | 0 | facts_emitted | 通过 | equity_structure_without_facts |

## 安全不变量

- `000651_格力电器_2025年度报告.pdf`：全部为 0
- `000651_格力电器_2026年半年度报告.pdf`：全部为 0
- `600036_招商银行_2025年度报告.pdf`：全部为 0
- `600519_贵州茅台_2026年半年度报告.pdf`：全部为 0
- `600941_中国移动_2023年度报告.pdf`：全部为 0
- `600941_中国移动_2025年度报告.pdf`：全部为 0
- `601318_中国平安_2025年度报告.pdf`：全部为 0
- `601318_中国平安_2026年半年度报告.pdf`：全部为 0
- `601600_中国铝业_2026年半年度报告.pdf`：全部为 0
- `601816_京沪高铁_2025年度报告.pdf`：全部为 0

## 解释边界

- `safety_ok=true` 只表示没有把不合格数据错误写入 facts，不表示报告结构化完整。
- `structure_only` 和 `no_supported_logical_table` 都是能力缺口，不能用保守拒绝掩盖。
- 标题候选来自逐页 Markdown 的确定性扫描，仅用于失败分类，不会反向生成 facts。
