# Code Extractor Evaluation - File Structure

## 目录结构

```
evaluation/code_extractor/
├── README.md                    # 使用说明和快速开始
├── DESIGN.md                    # 设计思路和技术架构
├── RESULTS_ANALYSIS.md          # 评估结果分析
├── FILE_STRUCTURE.md            # 本文件（目录结构说明）
│
├── ground_truth/                # Ground Truth 数据
│   └── code_extractor_ground_truth.json
│
├── scripts/                     # 评估脚本
│   ├── evaluate_code_extractor.py  # 主评估脚本
│   ├── setup_pdf.sh            # PDF 设置脚本
│   └── test_ground_truth.py    # Ground Truth 测试脚本
│
├── results/                     # 评估结果（运行后生成）
│   ├── paper_*_evaluation.json      # JSON 格式完整结果
│   └── paper_*_evaluation_summary.md # Markdown 格式摘要
│
├── pdfs/                        # PDF 文件（需要手动添加）
│   └── *.pdf
│
└── .gitignore                   # Git 忽略文件配置
```

## 文件说明

### 核心文档
- **README.md**: 使用说明、快速开始、环境配置
- **DESIGN.md**: 设计思路、技术架构、关键决策
- **RESULTS_ANALYSIS.md**: 评估结果分析、问题诊断、改进建议

### 数据文件
- **ground_truth/code_extractor_ground_truth.json**: 10篇论文的人工标注数据

### 脚本文件
- **scripts/evaluate_code_extractor.py**: 主评估脚本
- **scripts/setup_pdf.sh**: PDF 文件设置说明
- **scripts/test_ground_truth.py**: Ground Truth 数据测试脚本

### 结果文件（运行后生成）
- **results/paper_*_evaluation.json**: 完整的评估结果（JSON格式）
- **results/paper_*_evaluation_summary.md**: 人类可读的评估摘要（Markdown格式）

### 临时文件（已忽略）
- **temp_output/**: PDF 解析的临时输出（已加入 .gitignore）
- **evaluation_log.txt**: 评估日志（已加入 .gitignore）

## 必要文件清单

### 必须保留
✅ `README.md` - 使用说明
✅ `DESIGN.md` - 设计思路
✅ `RESULTS_ANALYSIS.md` - 结果分析
✅ `ground_truth/code_extractor_ground_truth.json` - 基准数据
✅ `scripts/evaluate_code_extractor.py` - 评估脚本
✅ `results/paper_*_evaluation.json` - 评估结果
✅ `results/paper_*_evaluation_summary.md` - 评估摘要

### 可选保留
⚠️ `scripts/setup_pdf.sh` - PDF 设置说明
⚠️ `scripts/test_ground_truth.py` - 测试脚本

### 可以删除
❌ `CONSISTENCY_CHECK_REPORT.md` - 已整合到 DESIGN.md
❌ `MATCHING_ANALYSIS.md` - 已整合到 DESIGN.md
❌ `PARSER_FIXES.md` - 已整合到 DESIGN.md
❌ `NOTE.md` - 已整合到 README.md
❌ `USAGE.md` - 已整合到 README.md
❌ `EVALUATION_SUMMARY.md` - 已整合到 README.md
❌ `check_progress.sh` - 辅助脚本（可选保留）
❌ `temp_output/` - 临时文件（已加入 .gitignore）
❌ `evaluation_log.txt` - 日志文件（已加入 .gitignore）

## 文件大小估算

- 文档文件: ~50KB
- Ground Truth: ~10KB
- 评估脚本: ~50KB
- 评估结果: ~500KB (6篇论文)
- PDF 文件: ~20MB (10篇论文，不包含在仓库中)

## 维护建议

1. **定期清理**: 删除 `temp_output/` 和日志文件
2. **版本控制**: 只提交必要文件，忽略临时文件
3. **文档更新**: 评估结果更新后，同步更新 `RESULTS_ANALYSIS.md`
4. **结果备份**: 重要评估结果建议备份到其他位置

