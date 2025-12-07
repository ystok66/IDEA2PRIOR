# Code Extractor Evaluation - Design Document

## 1. 设计目标

设计一个全面的评估框架，用于评估 Code Extractor 工具在以下方面的表现：
- 代码/伪代码提取质量
- 完整性评估准确性
- 可复现性评估准确性
- 依赖识别准确性
- 模型结构分析准确性
- 缺失组件检测准确性

## 2. 评估架构

### 2.1 三层评估体系

```
┌─────────────────────────────────────┐
│   Ground Truth (人工标注数据)        │
│   - 10篇论文的标注数据               │
│   - 包含完整性、可复现性、依赖等     │
└─────────────────────────────────────┘
              ↓
┌─────────────────────────────────────┐
│   Code Extractor Tool               │
│   - 从PDF提取代码/伪代码             │
│   - 评估完整性和可复现性             │
│   - 识别依赖和框架                   │
└─────────────────────────────────────┘
              ↓
┌─────────────────────────────────────┐
│   LLM Judge (ChatGPT)               │
│   - Standalone Evaluation           │
│   - Comparison Evaluation            │
└─────────────────────────────────────┘
```

### 2.2 核心组件

#### 2.2.1 Ground Truth 数据
- **来源**: 人工标注的10篇论文数据
- **字段**:
  - `pseudocode_completeness` (0-2): 伪代码完整性
  - `reproducibility` (1-3): 可复现性评分
  - `dependencies`: 依赖列表
  - `model_structure`: 模型结构
  - `missing_components`: 缺失组件列表

#### 2.2.2 Code Extractor Tool
- **功能**: 从PDF中提取代码/伪代码，评估完整性和可复现性
- **输出格式**: Markdown报告，包含：
  - Completeness score (0-1.0)
  - Reproducibility score (0-1.0)
  - Identified dependencies
  - Model structure
  - Missing components

#### 2.2.3 Output Parser
- **功能**: 解析 Code Extractor 的输出
- **关键修复**:
  - **范围映射**: 将工具输出的 0-1.0 映射到 ground truth 的 0-2 (completeness) 和 1-3 (reproducibility)
  - **Model Structure 提取**: 从代码块中提取 JSON 格式的模型结构
  - **Missing Components 优化**: 优先从 "LLM Evaluation Results" 部分提取

#### 2.2.4 LLM Judge
- **模型**: ChatGPT (gpt-4o-mini)
- **评估类型**:
  1. **Standalone Evaluation**: 独立评估工具输出质量（不依赖 ground truth）
  2. **Comparison Evaluation**: 与 ground truth 对比评估
- **评估指标** (0-10分):
  - Pseudocode Completeness Match
  - Reproducibility Score Match
  - Dependency Identification
  - Model Structure Analysis
  - Missing Components Detection

## 3. 关键技术决策

### 3.1 PDF 匹配算法

**问题**: 论文标题与PDF文件名格式不一致，需要智能匹配

**解决方案**: 三层匹配策略
1. **精确匹配**: 标准化文本后完全匹配
2. **子串匹配**: 80%相似度阈值
3. **词权重匹配**: 基于词长度和重要性的加权匹配，至少60%匹配度

**实现**:
```python
def normalize_text(text: str) -> str:
    # 处理特殊字符、连字符、空格等
    # 统一转换为小写，规范化空格

def find_pdf_for_paper(paper_title: str) -> Optional[str]:
    # 1. 精确匹配
    # 2. 子串匹配（80%阈值）
    # 3. 词权重匹配（60%阈值）
```

### 3.2 范围映射问题

**问题**: 
- Tool 输出: Completeness (0-1.0), Reproducibility (0-1.0)
- Ground Truth: Completeness (0-2), Reproducibility (1-3)

**解决方案**: 在 Parser 中进行范围映射
- Completeness: `value * 2` (0-1.0 → 0-2)
- Reproducibility: `value * 2 + 1` (0-1.0 → 1-3)

### 3.3 多次评估取平均

**目的**: 提高评估结果的可靠性和稳定性

**实现**: 
- 默认运行3次 LLM Judge 评估
- 计算平均值和标准差
- 聚合所有评估的反馈和建议

## 4. 评估流程

### 4.1 单篇论文评估流程

```
1. 加载 Ground Truth
   ↓
2. 查找 PDF 文件（智能匹配）
   ↓
3. 运行 Code Extractor
   - 提取代码/伪代码
   - 评估完整性和可复现性
   - 识别依赖和框架
   ↓
4. 解析工具输出
   - 提取关键字段
   - 范围映射
   ↓
5. Standalone LLM Judge 评估（3次）
   - 独立评估工具输出质量
   ↓
6. Comparison LLM Judge 评估（3次）
   - 与 Ground Truth 对比
   ↓
7. 聚合结果
   - 计算平均值和标准差
   - 生成摘要报告
   ↓
8. 保存结果
   - JSON 格式（完整数据）
   - Markdown 格式（人类可读）
```

### 4.2 批量评估

- 支持评估所有论文或指定论文
- 支持并行评估（未来优化）

## 5. 输出格式

### 5.1 JSON 输出
```json
{
  "paper_id": 1,
  "paper_title": "...",
  "standalone_evaluation": {
    "averaged_result": {
      "overall_score": 3.0,
      "scores": {...}
    },
    "individual_results": [...]
  },
  "comparison_evaluation": {
    "averaged_result": {...},
    "individual_results": [...]
  }
}
```

### 5.2 Markdown 摘要
- 论文信息
- Ground Truth 摘要
- Standalone 评估结果
- Comparison 评估结果
- 详细分数、优势、弱点、建议

## 6. 成功标准

- **Pseudocode Completeness Match** > 80% (8.0/10)
- **Reproducibility Score Match** > 70% (7.0/10)
- **Dependency Identification** > 70% (7.0/10)
- **Model Structure Analysis** > 70% (7.0/10)
- **Missing Components Detection** > 70% (7.0/10)
- **Overall Score** > 7.0/10

## 7. 已知限制和改进方向

### 7.1 当前限制
1. **Gemini API 依赖**: LLM 辅助提取功能依赖 Gemini API，如果 API 被暂停，功能会降级
2. **PDF 解析质量**: 依赖 PDFExtractorRAGTool 的解析质量
3. **评估时间**: 每篇论文需要 2-5 分钟（包括多次 LLM 评估）

### 7.2 改进方向
1. **Web 搜索增强**: 集成 web 搜索功能，补充 PDF 中缺失的信息
2. **缓存机制**: 缓存 PDF 解析结果和评估结果
3. **并行评估**: 支持多篇论文并行评估
4. **更智能的匹配**: 使用语义相似度进行 PDF 匹配
5. **评估指标优化**: 根据实际使用情况调整评估指标权重

## 8. 文件结构

```
evaluation/code_extractor/
├── README.md                    # 使用说明
├── DESIGN.md                    # 本文档（设计思路）
├── RESULTS_ANALYSIS.md          # 结果分析
├── ground_truth/                # Ground Truth 数据
│   └── code_extractor_ground_truth.json
├── scripts/                     # 评估脚本
│   ├── evaluate_code_extractor.py
│   └── setup_pdf.sh
├── results/                     # 评估结果
│   ├── paper_*_evaluation.json
│   └── paper_*_evaluation_summary.md
└── pdfs/                        # PDF 文件
    └── *.pdf
```

## 9. 依赖关系

- **Code Extractor Tool**: 核心工具
- **PDF Extractor**: PDF 解析
- **Semantic Scholar Tool**: 论文搜索（可选）
- **OpenAI API**: LLM Judge
- **Gemini API**: LLM 辅助提取（可选）

## 10. 使用示例

```bash
# 评估所有论文
python evaluation/code_extractor/scripts/evaluate_code_extractor.py

# 评估特定论文
python evaluation/code_extractor/scripts/evaluate_code_extractor.py --paper-id 1

# 多次评估取平均
python evaluation/code_extractor/scripts/evaluate_code_extractor.py --num-evaluations 5
```

