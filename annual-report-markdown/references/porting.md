# 移植到 Codex 清单（references/porting.md）

对应设计方案：§16 与现有 Skill 的衔接、§17 版本纪律。本文把 annual-report-v02
整体复制到 `~/.codex/skills/annual-report-markdown/` 的**前置准备、步骤、验证与边界**
写清楚，避免“复制即完成”的错觉。

## 1. 移植前状态（2026-09-05 实测）

| 项 | 状态 |
|---|---|
| 代码硬编码绝对路径 | 已清零（2026-09-05 移除 batch_pipeline 唯一一处 `/Users/eric/...`，改为可配置） |
| 运行时依赖 | 需 `jsonschema`（已接线）；可选 `pypdf/pdfplumber/pypdfium2/MinerU`（切片/核对/目检/探针） |
| 旧同名 skill | `~/.codex/skills/annual-report-markdown/` 已存在（旧 SKILL.md + `scripts/parse_annual_report.py`），**复制前必须备份/处置** |
| 测试 | 23 项全绿（`python3 -m unittest discover -s tests`） |

## 2. 一键复制（在 annual-report-v02 目录执行）

```bash
SRC="/Users/eric/Documents/copilot设计skilll/annual-report-v02"
DST="$HOME/.codex/skills/annual-report-markdown"

# 1) 备份旧版（若仍想留旧实现）
mv "$DST" "$DST.bak.$(date +%Y%m%d%H%M%S)"

# 2) 复制新版（可选剔除 samples：开发样例/目检 HTML 体积大且路径指向本机 PDF）
mkdir -p "$DST"
#    a) 完整复制（含样例）：
cp -R "$SRC/SKILL.md" "$SRC/agents" "$SRC/references" "$SRC/schemas" \
      "$SRC/scripts" "$SRC/tests" "$DST/"
#    b) 如需 samples 进度页也带上（目检链接是相对路径，可搬）：
cp -R "$SRC/samples" "$DST/"

# 3) 清理 Python 缓存（避免旧机器路径残留）
find "$DST" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null
find "$DST" -name '*.pyc' -delete 2>/dev/null

# 4) 移除临时测试研究包（体积大且路径指向本机 PDF，不随 skill 分发）
rm -rf "$DST/tests/_research_packages"
```

> 说明：README.md 是设计副本说明，Codex 侧可不带（SKILL.md 才是入口）；带了也无害。
> `agents/openai.yaml` 是 Codex 显示名/默认提示（name 沿用 `annual-report-markdown`）。

## 3. 依赖与环境

```bash
# 必需：Schema 校验（validation/assemble/refine 在缺省时跳过校验但会如实报告）
python3 -m pip install jsonschema

# 可选——按需：
python3 -m pip install pypdf pdfplumber pypdfium2   # inspect 页数/切片核对/目检原页图
# MinerU：外部可执行（非 pip 默认链），用于无框线表结构候选；不在本 skill 内
# 参考 scripts/requirements.txt 注释
```

验证 Codex 侧环境：
```bash
cd ~/.codex/skills/annual-report-markdown
python3 scripts/run_parse.py selfcheck        # 期望 OK；jsonschema 状态可见
python3 -m unittest discover -s tests         # 期望 23 项 OK
```

## 4. 移植后行为差异（诚实边界）

1. **batch_pipeline 默认报告目录不再硬编码**：
   - 优先级 `--pdf-root` > 环境变量 `REPORT_PDF_DIR` > 开发态探测（skill 根上一级有 5 份样例才用）。
   - 移植后无样例目录时，若不带 `--pdfs/--pdf-root` 会**明确报错退出码 2**，绝不静默找不到。
2. **samples/ 若整体复制**：其 `source_pdf.path` 指向原机器 `/Users/eric/...` 的 PDF，
   目检 HTML 本身可打开（渲染图内嵌），但“打开原 PDF”链接在别机器会失效——属样例数据路径，非代码缺陷。
3. **能力状态不变**：整体仍是 P0 骨架 + V0.2 切片观察 + B/C 小工具；`run_parse run`（标准解析）
   仍未接线、退出码 2。不得因复制成功而宣称“可解析年报”。

## 5. 复制后确认清单

- [ ] `selfcheck` 通过且 jsonschema 已装
- [ ] 23 项测试全绿
- [ ] `scaffold` + `checkschema` 对一个假 PDF 跑通（4/4）
- [ ] 用一份真实 PDF（可选）跑 `run_tableslice` 或直接用 `samples/pingan_p14-15` 跑 `run_assemble`（5/5）
- [ ] `run_refine <研究包> <指标>` 只写 candidates、`run_local --page/--table` 可生成复核记录
- [ ] SKILL.md 顶部“当前实现状态”已如实反映（不要因移植改状态声明）

## 6. 与 annual-report-research 的衔接提醒（设计 §16）

移植的是**新解析入口（annual-report-markdown）**，不等于旧的 annual-report-research（精华提取）
自动会读新包 JSON。两者仍按设计：由本入口做准入检查，未达标的包不自动调旧 skill。
升级旧 research skill 是另一任务，不在本次移植范围。
