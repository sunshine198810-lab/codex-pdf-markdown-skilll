# OCR 扫描通道评估：PP-StructureV3 可行性 + 与 MinerU 对比（2026-09-07）

对应设计方案：§13.1 第二独立通道（与第一通道不共享模型）、§14 引擎选型、D3 扫描轨。

## 1. 本机事实（2026-09-07 实测）

- 系统：macOS 26.6.2 **arm64**；Python 3.12.7；pip 24.2。
- 已装：MinerU 3.4.5（本地源码 `/Users/eric/Documents/GitHub/MinerU`）+ torch 2.14.0；**无任何 paddle 系**。
- `mineru` 解释器 `/usr/local/bin/python3`：**有 torch、无 paddle** → 本机 MinerU OCR 跑在 **torch 后端**。
- PyPI：`paddlepaddle 3.3.1` 提供 **cp312-macosx_11_0_arm64 wheel**（本机可装）；`paddlex 3.7.2`、`paddleocr 3.7.0`（PP-StructureV3 走 PaddleX 管线）。

## 2. PP-StructureV3 可行性评估

| 维度 | 结论 |
|---|---|
| 安装 | ✅ 可行：`pip install paddlepaddle==3.3.1 paddlex paddleocr`（cp312 arm64 wheel 存在） |
| 模型 | 首次跑 `PP-StructureV3` 管线会下载模型（数百 MB～GB），需磁盘与网络 |
| 推理 | mac arm64 paddle 主走 CPU（Metal/MPS 支持有限）；扫描页小量可控，整本会明显慢于 MinerU(MPS) |
| 独立性 | ✅ 与 MinerU(torch) 不同框架/模型 → 满足 §13.1“第二引擎与第一引擎不共享模型”的独立通道条件 |
| 中文扫描/表格 | PaddleOCR 系对中文 + 无线表结构是专长（PP-StructureV3） |
| 风险/成本 | 安装+模型体积大；paddle mac 生态更新节奏需锁版本；首次跑慢；验收仍需人工真值 |

**建议**：先小样本验证（紫金 113/114 两页装+跑 PP-StructureV3 → 与 MinerU 逐格比对一致性），
通过再决定整份/常驻；未验证前不承诺覆盖率、不升 facts。

## 3. MinerU vs PP-StructureV3（对比）

| 项 | MinerU（本机已用） | PP-StructureV3（PaddleX） |
|---|---|---|
| 框架 | PyTorch 系（本机 torch 后端） | PaddlePaddle/PaddleX 系 |
| 定位 | 端到端文档解析：版面→OCR→表格→Markdown/结构化 content_list | 组件化中文 OCR+版面+表格 管线（可单跑 PP-StructureV3） |
| OCR 模型 | MinerU 自带（torch 后端） | PaddleOCR det/rec（与 MinerU 不同源） |
| 表格结构 | 有（无框/有框表，输出 HTML `table_body`） | 有（PP-StructureV3 无线表专长） |
| 输出 | `*_content_list.json`（page_idx/bbox/table_body）+ md + 中间 json | PaddleX/官方 result（HTML/表格/文本），需写 adapter 对齐坐标/页号 |
| 本机速度 | 快（MPS；紫金 16 页约 1–2 分钟） | 未装；预计 CPU 更慢 |
| 许可 | 代码 MIT？/许可证含附加条款，按所选版本读 | PaddleOCR 代码 Apache-2.0；模型另核 |
| 在 D3 的角色 | **第一扫描通道**（已跑通 113–128） | **第二独立通道**（待装，做逐格一致性/交叉验证） |

**为什么仍要 PP-StructureV3**：evidence_chain 的“独立第二通道”要求与 MinerU **不同源**；本机 MinerU
为 torch 后端，PP-StructureV3 为 paddle 系 → 满足独立条件；两通道逐格一致 + 会计勾稽 + 人工签核，
OCR 数值才可能升格。若直接用 MinerU 自己复检一遍只算同源复检，不算第二通道。

## 4. 决策建议（D3 剩余）

1. 可选 A：小样本装+跑 PP-StructureV3（紫金 113/114），出“两通道逐格一致性”对照——推荐在准备做整本扫描前做；
2. 可选 B：先人工标注 6–10 页真值，校准路由/OCR 质量，再决定是否值得上 PP-StructureV3；
3. 无论哪条：OCR 数值保持 `single_channel`，不升 facts，直至第二通道+人工签核齐备。
