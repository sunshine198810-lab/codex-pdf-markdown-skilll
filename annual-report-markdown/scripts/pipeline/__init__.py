"""年报解析与研究包 —— pipeline 解析骨架（P0）。

本包是《年报解析Skill-重新设计方案-2026-09-05》的工程骨架。**P0 阶段不接线解析引擎**；
intake/scaffold/selfcheck 可运行，engine 依赖步骤明确抛出 `NotImplementedError` 并指向阶段计划，
绝不假装成功。

模块划分（对应设计方案 §17）：

    intake       输入、哈希、身份与体检
    geometry     页面、区域和坐标变换
    adapters     第三方引擎到统一结构
    layout       内容分区与阅读顺序
    sections     目录、章节和附注映射
    tables       网格、单元格、跨页逻辑表
    figures      图表证据与标签
    semantics    期间、主体、单位和指标候选
    validation   覆盖、结构、语义与财务校验
    exports      Markdown、HTML、JSON、CSV及复核页
    runner       编排标准解析/骨架

真相声明：凡涉及真实解析的能力（正文恢复、表格结构化、图表理解、财务语义、财务校验）在引擎可用并
通过验收回归（见 references/acceptance.md）前一律不可用。
"""

from .version import (
    SCHEMA_VERSION,
    PIPELINE_VERSION,
    SKILL_NAME,
    DISPLAY_NAME,
    MODULES,
    ENGINE_CANDIDATES,
)

__all__ = [
    "SCHEMA_VERSION",
    "PIPELINE_VERSION",
    "SKILL_NAME",
    "DISPLAY_NAME",
    "MODULES",
    "ENGINE_CANDIDATES",
]
