# 表体数值核对报告

- 源 PDF：`/Users/eric/Documents/copilot设计skilll/600900_长江电力_2025年度报告.pdf`
- 方法：pdfplumber 精确文字层 + 词级 x 坐标前缀比对（对照显示网格）
- 结论：1/2 张表数值一致

## table-0001 · ❌ 不一致（19/20 数据行）
不一致行：
- 行 2 (二)终止经营净利润(净亏损以“一”号填列）：网格值 ['', '', '']；label_not_found_in_text_layer

## table-0002 · ✅ 一致（11/11 数据行）
