"""版本与常量登记（对应 §17 版本纪律）。"""

SKILL_NAME = "annual-report-markdown"
DISPLAY_NAME = "年报解析与研究包"

# Schema 版本：与 schemas/*.json 顶部保持一致
SCHEMA_VERSION = "2026-09-06.1"

# V0.3-C4.6：有框线主表过切分→原生词 3 列重建（广核 0→116）；含 C4.5 新发行人回归、
# 声明识别扩展、多通道逐格一致性与人工签核机制
PIPELINE_VERSION = "0.3.0-c4.6"

# 模块划分（§17）
MODULES = [
    "intake",
    "geometry",
    "adapters",
    "layout",
    "sections",
    "tables",
    "figures",
    "semantics",
    "naming",
    "validation",
    "exports",
    "assemble",
    "refine",
    "runner",
    "standard",
    "financials",
    "borderless",
    "equity",
    "vector_evidence",
    "evidence_chain",
]

# 引擎候选（§14，仅登记，不安装、不导入）
ENGINE_CANDIDATES = {
    "renderer+pdfplumber": {
        "role": "页面证据、字符与线条位置、原生表格候选",
        "note": "不自带 OCR，不负责财务口径",
        "selected": False,
        "license": "MIT",
    },
    "docling": {
        "role": "版面、阅读顺序、表格、图片和结构化文档表示（主干候选）",
        "note": "需验证本机性能、繁体与复杂财报；默认关闭远程服务",
        "selected": False,
        "license": "MIT",
    },
    "pp-structure-v3": {
        "role": "中文扫描及复杂区域的 OCR、表格/版面候选",
        "note": "后端/模型/依赖需实测",
        "selected": False,
        "license": "Apache-2.0（代码）；模型另核",
    },
    "mineru": {
        "role": "全流程对照，必要时替换主方法或处理困难页",
        "note": "输出需适配坐标；许可证须读所选版本",
        "selected": False,
        "license": "见所选版本（勿沿用旧印象）",
    },
    "pymupdf": {
        "role": "可选渲染、文字/表格提取通道",
        "note": "使用前确认 AGPL 或商业许可，不作为必须依赖",
        "selected": False,
        "license": "AGPL-3.0-or-later（商业需另行确认）",
    },
}
