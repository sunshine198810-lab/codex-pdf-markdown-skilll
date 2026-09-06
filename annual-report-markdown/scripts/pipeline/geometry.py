"""geometry：页面、区域和坐标变换（对应设计方案 §7.1）。

统一约定：工作坐标 = 旋转与裁切后的可见页面、左上角原点、单位 point；
physical_page 从 1 开始与原 PDF 一一对应；印刷页码另存 printed_page_label。
"""

from __future__ import annotations

from typing import Optional

WORK_ORIGIN = "top_left"
WORK_UNIT = "point"


def make_transform(rotation_deg: int = 0, dpi: Optional[int] = None) -> dict:
    """登记原坐标→工作坐标、工作坐标→渲染像素的变换（纯数学占位）。

    P0 未实现真实 PDF 几何解码；返回值仅供结构占位，需引擎后补。
    """
    return {
        "origin": WORK_ORIGIN,
        "unit": WORK_UNIT,
        "rotation_deg": rotation_deg,
        "dpi": dpi,
        "from_engine_bbox": None,
        "note": "占位变换；真实变换须由渲染/引擎适配后写入",
    }


def bbox_from_engine(page_size: tuple, norm_bbox: tuple, page_idx: int) -> tuple:
    """把引擎归一化 bbox 换算为工作坐标 bbox（示例：MinerU 0-1000 归一化）。

    仅演示换算关系，P0 不接线真实引擎。page_size=(w,h,unit)。
    """
    w, h = page_size[0], page_size[1]
    x0, y0, x1, y1 = norm_bbox
    return (x0 / 1000.0 * w, y0 / 1000.0 * h, x1 / 1000.0 * w, y1 / 1000.0 * h)


capabilities = {
    "engine_wired": False,
    "implemented": ["坐标约定与占位变换登记", "归一化 bbox 换算示例"],
    "pending": ["从 PDF 提取 MediaBox/CropBox/rotation 并做真实变换（需渲染/引擎）"],
}
