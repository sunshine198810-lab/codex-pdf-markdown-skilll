# annual-report-markdown —— 年报解析与研究包（workspace 开发副本）

按《年报解析Skill-重新设计方案-2026-09-05.md》实现的 **V0.3-C4.4 可运行版本**。

> 本目录是设计副本，供审阅迭代。安装到实际技能目录时整体复制到
> `.codex/skills/annual-report-markdown/`（name 沿用 `annual-report-markdown`，显示名“年报解析与研究包”）。
> **移植步骤、依赖、验证与边界见 `references/porting.md`**（含旧版同名 skill 的备份处置，复制前必读）。

## 目录

```text
annual-report-v02/
├── SKILL.md                  # 入口 skill：输入确认、调用流程、产物与验收（用户入口）
├── agents/
│   └── openai.yaml           # 显示名与默认提示
├── references/               # 深度规范（工程/质量/验收/引擎/Schema 指南）＋P0 实测报告
│   └── engines-probe.md      # 几何证据层实测（2026-09-05，茅台样本）
├── schemas/                  # 版本化 JSON Schema（统一文档结构与研究包）
├── scripts/
│   ├── run_parse.py          # A 标准解析（P0：inspect/scaffold/selfcheck/checkschema 可运行）
│   ├── run_refine.py         # B 核心财务整理（V0.2 升级：指标候选提取，只写 candidates）
│   ├── run_local.py          # C 局部复核（V0.2 升级：对页/表重跑文字层核对）
│   ├── run_tableslice.py     # V0.2 核心切片：表格结构候选+证据（需 MinerU content_list）
│   ├── run_assemble.py       # 切片→研究包规范化组装（表格/证据带入，补 document_map/复核页）
│   ├── verify_tables.py      # 表体数值核对：文字层+x坐标 对照显示网格 → 核对报告/目检状态
│   ├── batch_pipeline.py     # 批量：定位财务主表页→MinerU 切片+核对+目检
│   ├── build_progress.py     # 生成 samples/progress.html 进度总览（扫描 samples/*）
│   ├── audit_regression_matrix.py # 多研究包能力状态与安全不变量矩阵
│   ├── build_eyeball.py      # 目检 HTML：原页图+阅读顺序候选+表格结构候选
│   ├── p0_probe.py           # 几何层选型探针（探索性，需 pdfplumber）
│   ├── requirements.txt      # 已接线：jsonschema（Schema 校验）；其余可选项已注释
│   └── pipeline/             # 模块化解析骨架（intake…exports + assemble/refine）
├── samples/                  # 目检样例（平安 p14-15 切片+目检 HTML，2026-09-05）
│   ├── progress.html         # V0.2 切片进度总览（build_progress.py 生成）
│   ├── pingan_p14-15/  cmcc_p126/   batch/…
└── tests/                    # stdlib unittest 冒烟测试
```

## 现状（V0.3-C4.4 异常文字层与数值证据恢复）

- 已建立：入口流程、模块边界、对象模型、JSON Schema、验收基线、骨架 CLI；
  P0 几何实测完成（3 报告）、MinerU pipeline 对照完成、主引擎分工已定（见 `references/engines-probe.md`）。
- 已接线：`pipeline/adapters` 的 `pdfplumber_evidence` 与 `mineru_tables`（候选/证据层）；
  `run_tableslice.py` 产出表格结构候选切片（partial、未人工核对、不可计算）。
- 已接线（2026-09-05 骨架收尾）：Schema 严格校验（jsonschema + 本地 registry，产物即校验，
  `run_parse checkschema`）；`run_assemble.py` 切片→研究包组装（关键产物 5/5 过 schema）；
  B `run_refine`（指标候选定位，只写 candidates）与 C `run_local`（对页/表文字层复核）小工具。
- 已接通：`run_parse.py run` 可生成完整研究包，并对中文 A 股六类法定主表建立逻辑表、逐格证据、语义上下文、勾稽结果、`facts.jsonl` 与 `facts.csv`；可选用 MinerU 定位无框线四列区域，数值仍从 PDF 原生词回填。
- 未建立：扫描页与复杂多栏等通用困难页调度、第二独立 OCR 及人工签核、矢量图表理解、繁英港股和权益明细。C4.4 可识别矢量轮廓数字并生成隔离复核候选，但仍不把单一 OCR 数值升格；不代表行业总体准确率。
- 诚实声明：整体仍为 `partial`；只有 facts 中明确标记 computable 的观察值可用于机械计算。
- B2.1 修正：身份仍为文件名候选；无图像侧金标准时不报告内容留存率，无人工分母时不报告复核率；普通正文不自动计为 citable；表格分开报告检出、网格、规则验收和可计算单元格。
- C1 新增：权益变动表按法定标题建边界，分开当期/比较期子组，把合并单元格展开为多级列路径；行标签不足、单格多数值或跨片列数冲突时一律留在 `needs_review`。该表数值不进 facts。
- C2 新增：当权益宽表被压成单行时，从 PDF 原生词坐标重建表头列中心、视觉行、折行标签与数值落列。原生数值词数与已归属数必须完全一致；数值仍不进 facts。
- C3 新增：对同一权益列的“期初余额—本期增减变动金额—期末余额”做严格等式校验；只有三值显式存在、上下文与逐格证据齐全且等式精确成立时，才把这 3 个关键单元格写入 facts。明细行、空白列和未通过列仍不可计算。
- C3.1 新增：`audit_regression_matrix.py` 将多份研究包统一拆成“能力状态”和“安全不变量”审计。首轮冻结 10 份报告、7 个发行人；安全门 10/10，但只有 3 份产出 facts，明确暴露标题前缀、千元单位、A+H 版式和金融行业模型缺口，不以保守拒绝冒充覆盖成功。
- C3.2 新增：法定标题有界前后缀规范化、人民币元/千元/万元/百万元/亿元显式倍率、年度期间与多种企业会计准则声明证据，并输出机器可读上下文失败原因。相同十报告回归保持安全门 10/10，产出 facts 的报告由 3 份增至 7 份；总 facts 由 1,195 增至 2,266。该数量不是准确率或覆盖率。
- C3.3 新增：支持“项目＋两期金额”三列主表；资产负债表的期末/期初列必须绑定本表明确日期；`单位：元` 只有与财务报告中的人民币记账本位币声明组合后才形成 CNY 证据。十报告安全门仍为 10/10，产出 facts 的报告增至 8 份，总 facts 2,650；格力 2026 半年报新增 384，其他九份逐份不变。
- C4.1 新增：银行业科目证据配置、“年份/月日”两行表头的原生四列重建、所得税括号负数的单一符号公式，以及银行现金流量科目别名。招商银行六张主表全部建立，facts 由 137 增至 565；十报告安全门 10/10，其他九份逐份无回退，总 facts 3,078。数量不是准确率或覆盖率。
- C4.2 新增：保险科目证据画像、空 `table_body` 续页在严格原生表头门下的四列恢复、相邻重复完整表题续页合并、中期六个月期间与《企业会计准则第32号》编制依据。平安年报/半年报分别产出 502/493 facts；十报告安全门 10/10、10 份均产出 facts，总数 4,073，其他八份逐份无回退。该结果不代表保险行业总体准确率。
- C4.3 新增：保险跨发行人审计（中国人寿、中国太保、中国人保）、受限年度表题前缀与行标签安全门。13 报告、10 发行人安全门 13/13，总 facts 4,292；中国太保 219 facts，中国人寿和中国人保均保守不放行 facts。详见 `references/v03-c43-insurance-generalization-2026-09-06.md`。
- C4.4 新增：矢量轮廓数字检测、源页裁图与 OCR 候选隔离；受限日期壳表题；宽 colspan HTML 允许进入严格原生四列重建。中国人保形成六张视觉复核主表候选且 0 facts，中国人寿补齐六主表仍 0 facts，中国太保补齐六主表并增至 328 facts。十三报告安全门 13/13，总 facts 4,401。详见 `references/v03-c44-vector-numeric-evidence-2026-09-06.md`。

## 设计要点（与方案对应）

- 三层输出（阅读 Markdown / 结构表格 JSON+HTML / 数据财务事实）从**同一份统一文档结构**生成，不互相反推。
- 全程证据链 + 物理页溯源；原值、单位、期间、主体、重述状态、版本并存。
- 复杂表格以结构 JSON + HTML 为主，CSV 是派生计算视图。
- 图表先完整留存与可核对标签，再逐步加深理解。
- 财务标准化基于完整上下文，可明确保留未知，不强制同义映射。
- 质量五层检查；`可定位 ≠ 可引用 ≠ 可计算`；未解决问题可定位，不悄悄补齐。

## 运行

```bash
python3 scripts/run_parse.py selfcheck                    # 自检：Schema 与模块可加载 + schema 自我合规
python3 scripts/run_parse.py inspect  <report.pdf>        # 来源登记/体检（不接线引擎）
python3 scripts/run_parse.py scaffold <report.pdf>        # 建立空研究包骨架（版本目录+manifest+入口模板）
python3 scripts/run_parse.py checkschema <研究包目录>      # 研究包关键产物过 schema（jsonschema）
python3 scripts/run_parse.py run <report.pdf> [--output DIR] --auto-borderless # V0.3-C4.4 推荐
python3 scripts/audit_regression_matrix.py <研究包>... --json <矩阵.json> --markdown <矩阵.md>
python3 scripts/run_parse.py run <report.pdf> [--output DIR] \
  --mineru-content <content_list.json> --mineru-start <0起始页> # B1 手动回放
python3 scripts/p0_probe.py <report.pdf> --pages 2,5,8,26 --full   # 几何层选型探针（需 pdfplumber）
python3 scripts/run_tableslice.py <report.pdf> --mineru <content_list.json> \
        --mineru-start N --pages 14-15 --out <目录>   # V0.2核心切片：表格候选 JSON/HTML+证据
python3 scripts/run_assemble.py --out <研究包目录> <切片目录>…   # 切片→研究包规范化组装
python3 scripts/build_eyeball.py --pdf <report.pdf> --slice <切片目录>  # 目检 HTML（原页图+候选）
python3 scripts/verify_tables.py --pdf <report.pdf> --slice <切片目录>   # 表体数值核对（文字层+x坐标）
python3 scripts/run_refine.py <研究包目录> [指标...]       # B：指标候选提取（只写 candidates，不写 facts）
python3 scripts/run_local.py  <研究包目录> --page N | --table <id>  # C：对页/表重跑文字层核对
python3 scripts/run_signoff.py record <研究包> --object-ref <ref> --decision accepted|rejected|deferred|pending [--eligible]  # 人工签核记录（复核/signoffs.jsonl）
python3 scripts/run_signoff.py list|summary <研究包>      # 列出/汇总人工签核
python3 scripts/batch_pipeline.py --max-pages 1            # 批量（默认 5 份报告的合并资产负债表页）
python3 scripts/build_progress.py                          # 生成 samples/progress.html 进度总览
python3 -m unittest discover -s tests                     # 冒烟测试
```

核对说明：`verify_tables.py` 对每张表逐行用源 PDF 同页 pdfplumber **精确文字层**＋x 坐标与显示网格
（优先人工重建 rows，否则 MinerU 候选）比对，输出 `索引/table_verify.json/.md` 与 `目检/table_check.json`；
`build_eyeball.py` 会读取 table_check 在每表下方显示“数值核对 ✓/✗”。样例平安 7 张表已核对 7/7 一致。

目检样例（可直接浏览器打开）：
`samples/pingan_p14-15/目检/index.html` —— 平安 2025 年报物理页 14–15：左侧原页图、右侧阅读顺序
候选，下方为 7 张无框线财务表的结构候选（含 rowspan/colspan）；状态 partial、未人工核对。

V0.2 核心切片（`run_tableslice.py`）说明：把 `pipeline/adapters` 已接线的两个**候选/证据**
adapter（`pdfplumber_evidence`、`mineru_tables`）串起来，对指定页生成表格结构候选 JSON/HTML＋表级证据
JSONL；overall_state=`partial`、未人工核对、不可计算。真实样例：
`run_tableslice.py 601318_中国平安_2025年度报告.pdf --mineru <平安 content_list.json> --mineru-start 13 --pages 14-15 --out /tmp/v02slice_pingan`。

P0 探针结论（2026-09-05，茅台样本）：几何证据层可行——坐标可靠、页眉可坐标分类、
“合计”不误删、顺序可重建、有框线表可检出。详见 `references/engines-probe.md`。

## 阶段计划

P0/M1/M2 → V0.3-B1 → V0.3-B2 → V0.3-B2.1 → V0.3-C1 → V0.3-C2 → V0.3-C3 → V0.3-C3.1 → V0.3-C3.2 → V0.3-C3.3 → V0.3-C4.1 → V0.3-C4.2 → V0.3-C4.3 → V0.3-C4.4（当前：异常文字层与数值证据恢复）→ 独立 OCR 签核/权益明细 → V1.0。
各阶段进入条件见 `SKILL.md` 末尾与 `references/acceptance.md`。
