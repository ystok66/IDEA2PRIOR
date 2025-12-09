# PaperArena Research Agent - 完整项目文档

## 📋 目录

1. [项目概述](#项目概述)
2. [项目结构](#项目结构)
3. [核心组件](#核心组件)
4. [工具详解](#工具详解)
5. [RAG 系统](#rag-系统)
6. [配置与环境](#配置与环境)
7. [更新历史](#更新历史)
8. [使用指南](#使用指南)
9. [开发指南](#开发指南)

---

## 项目概述

**PaperArena** 是一个基于 LLM 的学术研究助手系统，利用多个学术 API 和先进的检索技术来帮助研究人员查找、分析和总结学术论文。

### 核心功能

- 🔍 **多源学术搜索**: 集成 OpenAlex、Semantic Scholar、SerpAPI、DuckDuckGo
- 📚 **混合检索系统**: BM25 稀疏检索 + 向量密集检索
- 🤖 **智能 Agent**: 基于 smolagents 框架的 CodeAgent
- 📄 **PDF 分析**: Google Gemini File API 实现 RAG
- 🌐 **网页爬取**: 支持 Jina Reader 和 Crawl4AI

### 技术栈

- **LLM 框架**: smolagents, LiteLLM
- **检索**: BM25 (rank_bm25), Vector (Qdrant)
- **Embedding**: OpenAI, Gemini, Jina, Mistral
- **搜索 API**: OpenAlex, Semantic Scholar, SerpAPI, DuckDuckGo

---

## 项目结构

```
paperarena_hub/
├── .env                          # 环境变量配置
├── demo_research_agent.py        # 主入口脚本
├── pipelines/                    # 核心流水线模块 🆕
│   ├── evidence.py               # 证据系统 (EvidenceSpan, FacetCoverage)
│   ├── paper_record.py           # 论文记录 (PaperRecord, SectionRecord)
│   ├── run_manager.py            # 运行管理 (RunManager)
│   ├── coverage_classifier.py    # 覆盖率分类器
│   ├── llm_config.py             # LLM 配置
│   ├── sections.py               # PDF 分节解析 (Section, PaperSections)
│   ├── intro_rw_summarizer.py    # Intro+RW 摘要生成
│   ├── exploration_loop.py       # 探索循环
│   └── tasks/
│       ├── prior_art_check.py      # Prior Art 主入口
│       ├── prior_art_retrieval.py  # Block 1: 候选论文发现
│       ├── prior_art_scoring.py    # Block 2+3: 评分与证据
│       ├── prior_art_fulltext.py   # Block 3: 全文处理 (1.3/1.4)
│       ├── prior_art_synthesis.py  # Part 2: 综合 JSON 构建 (2.1)
│       ├── prior_art_report.py     # 报告渲染
│       ├── prior_art_types.py      # 共享类型定义
│       └── prior_art_llm_utils.py  # LLM 工具函数
├── tools/                        # 工具集合
│   ├── openalex_search.py       # OpenAlex API 工具
│   ├── semantic_scholar_search.py # Semantic Scholar API 工具
│   ├── google_file_search.py    # Google File Search (Gemini)
│   ├── duckduckgo_search.py     # DuckDuckGo 搜索工具
│   ├── async_web_crawler.py     # 核心爬虫类
│   ├── pdf_parser.py            # PDF 解析与分节
│   ├── pdf_extractor.py         # PDF 提取工具
│   └── ...
├── runs/                         # 运行产物目录 🆕
│   └── <timestamp>_<topic>/      # 单次运行目录
├── rag/                          # RAG 系统
│   ├── embeddings/              # Embedding 模型
│   │   ├── openai_embedding.py
│   │   ├── gemini_embedding.py
│   │   ├── jina_embedding.py
│   │   └── ...
│   ├── retrievers/              # 检索器
│   │   ├── simple_bm25_retriever.py
│   │   ├── simple_vector_retriever.py
│   │   └── ...
│   └── storages/                # 向量存储
├── reflectors/                   # 反思模块
│   └── search_reflector.py
├── configs/                      # 配置文件
├── scripts/                      # 辅助脚本
└── search_outputs/              # 搜索结果缓存
```

---

## 核心组件

### 1. demo_research_agent.py

**主入口脚本**，负责初始化 Agent 并执行研究任务。

#### 关键函数

##### `setup_environment()`

加载环境变量，检查 API keys。

##### `create_research_agent(model_id, api_base, api_key, use_free_search) -> CodeAgent`

创建并配置研究 Agent。

**参数**:

- `model_id`: LLM 模型 ID（如 "gemini/gemini-3-pro-preview", "gpt-4o"）
- `use_free_search`: 是否使用免费搜索（DuckDuckGo）

**返回**: 配置好的 CodeAgent 实例

**工具注册**:

```python
tools = [
    search_tool,           # SerpAPI 或 DuckDuckGo
    read_tool,             # 网页读取
    openalex_tool,         # OpenAlex 搜索
    s2_detail_tool,        # Semantic Scholar 详情
    s2_citation_tool,      # Semantic Scholar 引用
    google_file_tool       # Gemini File Search
]
```

**系统提示词**: 内置研究工作流指导，包括：

1. Discovery: 使用 OpenAlex 找论文
2. Deep Dive: 使用 Semantic Scholar 获取详情
3. Expansion: 查找引用关系
4. Full Analysis: 使用 Google File Search 分析 PDF

##### `main()`

主执行函数，运行 Agent 并处理结果。

---

### 2. SimpleCrawler (async_web_crawler.py)

**核心爬虫类**，提供搜索、爬取、RAG 功能。

#### 初始化参数

```python
SimpleCrawler(
    serpapi_key: Optional[str] = None,
    model: OpenAIServerModel = None,
    reflection: bool = True,
    roll_out: int = 0,
    search_limit: int = 10,
    serp_num: int = 10,
    topk: int = 1,
    rerank: bool = False,
    chunk_size: int = 500,
    chunk_overlap: int = 50,
    use_db: Optional[bool] = False,
    path: Optional[str] = None
)
```

#### 核心方法

##### `search(query, filter_year=None) -> str`

执行网络搜索。

**工作流**:

1. 如果启用 `roll_out`，使用 LLM 扩展查询
2. 调用 `_search()` 执行 SerpAPI 搜索
3. 聚合结果（如果多查询）
4. 格式化为字符串返回

##### `_query(query, contents) -> str`

**混合检索**核心方法。

**逻辑**:

```python
# 1. 向量检索
vector_res = self.retriever.retrieve(query, contents, limit=5)

# 2. BM25 检索
bm25_res = self.bm25_retriever.retrieve(query, contents, limit=5)

# 3. 合并结果
return f"## Vector Search Results\n{vector_res}\n\n## BM25 Search Results\n{bm25_res}"
```

##### `read_page(url) -> str`

读取网页内容。

**支持两种模式**:

- `jina_read`: 使用 Jina Reader API
- `crawl4ai`: 使用 Crawl4AI 库

##### `crawl(url) -> str`

爬取网页并执行 RAG。

**工作流**:

1. 检查历史（避免重复爬取）
2. 使用 `_read_page` 读取内容
3. 调用 `_query` 进行检索
4. 返回相关内容

---

## 工具详解

### 1. OpenAlexWorksSearchTool

**文件**: `tools/openalex_search.py`

**功能**: 使用 OpenAlex API 搜索学术论文。

#### 输入参数

- `query` (str, 可选): 关键词搜索
- `from_year` (int, 可选): 起始年份
- `to_year` (int, 可选): 结束年份
- `venue` (str, 可选): 会议/期刊名称（如 "CVPR"）
- `limit` (int, 默认 10): 返回结果数量

#### 使用示例

```python
openalex_tool.forward(
    query="3D object detection",
    venue="CVPR",
    from_year=2024,
    limit=10
)
```

#### API 配置

- Base URL: `OPENALEX_BASE_URL` (默认: https://api.openalex.org)
- Mailto: `OPENALEX_MAILTO` (用于标识用户)

---

### 2. SemanticScholarPaperDetailTool

**文件**: `tools/semantic_scholar_search.py`

**功能**: 获取论文详细信息（摘要、TLDR、引用数等）。

#### 输入参数

- `paper_id` (str): 论文 ID，支持格式：
  - S2ID: Semantic Scholar ID
  - DOI: `10.1109/CVPR...`
  - ArXiv: `ARXIV:2301...`

#### 返回字段

- Title, Year, Venue
- Authors
- Citation Count
- Abstract
- TLDR (自动生成摘要)
- PDF Link (如果可用)

#### 使用示例

```python
s2_detail_tool.forward(paper_id="10.1109/CVPR.2023.00001")
```

---

### 3. SemanticScholarCitationTool

**功能**: 查找论文的引用关系。

#### 输入参数

- `paper_id` (str): 论文 ID
- `type` (str, 默认 "citations"):
  - `"citations"`: 引用此论文的文献（前向引用）
  - `"references"`: 此论文引用的文献（后向引用）
- `limit` (int, 默认 10): 返回数量

#### 使用场景

- 文献图谱分析
- 找相关工作
- 追踪研究演进

---

### 4. GoogleFileSearchTool

**文件**: `tools/google_file_search.py`

**功能**: 上传文件到 Gemini 并进行 RAG 问答。

#### 输入参数

- `file_path` (str): 本地文件路径（支持 PDF, TXT）
- `query` (str): 对文件内容的提问

#### 工作流程

1. 上传文件到 Gemini API
2. 使用 `gemini-1.5-flash` 模型生成回答
3. 基于文件内容回答问题

#### 使用示例

```python
google_file_tool.forward(
    file_path="/path/to/paper.pdf",
    query="What is the main contribution of this paper?"
)
```

#### API 配置

- API Key: `GOOGLE_API_KEY`
- Model: `gemini-1.5-flash`

---

### 5. DuckDuckGoSearchTool

**文件**: `tools/duckduckgo_search.py`

**功能**: 免费的网络搜索工具。

#### 特点

- 无需 API Key
- 自动保存搜索结果到 JSONL
- 支持年份过滤
- 支持历史查询检测

#### 配置参数

```python
DuckDuckGoSearchTool(
    max_results=10,
    safesearch="moderate",
    output_directory="./search_outputs",
    save_results=True
)
```

---

### 6. PaperResolverTool

**文件**: `tools/paper_resolver.py`

**功能**: 统一的 PDF URL 解析器,通过多个学术数据源查找论文的可下载 PDF 链接。

#### 输入参数

- `doi` (str, 可选): 数字对象标识符 (DOI)
- `arxiv_id` (str, 可选): arXiv ID (如 "2301.12345")
- `s2_id` (str, 可选): Semantic Scholar ID
- `openalex_id` (str, 可选): OpenAlex ID
- `title` (str, 可选): 论文标题

#### 返回结果

返回包含以下字段的字典(转为字符串):

- `pdf_url`: PDF 下载链接 (如果找到)
- `metadata`: 论文元数据 (标题、作者、年份、摘要等)
- `source`: 数据来源 ("arxiv", "semanticscholar", "openalex", "unpaywall", "html_only")

#### 解析策略

采用**多源冗余 + 优先级策略**:

1. **arXiv** (最快,高可信度): 如果有 arXiv ID,直接构造 PDF URL
2. **Semantic Scholar** (丰富元数据): 查询 OA PDF 链接和外部 ID
3. **OpenAlex** (优秀的 OA 覆盖): 查询 `best_oa_location.pdf_url`
4. **Unpaywall** (DOI 兜底方案): 使用 DOI 查询开放获取链接

#### 使用示例

```python
resolver = PaperResolverTool()

# 使用 arXiv ID
result = resolver.forward(arxiv_id="2312.00752")
# {'pdf_url': 'https://arxiv.org/pdf/2312.00752.pdf', 'source': 'arxiv', ...}

# 使用 DOI
result = resolver.forward(doi="10.1109/CVPR.2023.00001")

# 使用标题搜索
result = resolver.forward(title="Attention is All You Need")
```

#### API 配置

- Semantic Scholar: `Semantic_Scholar_API_key` (可选)
- OpenAlex: `OPENALEX_MAILTO` (推荐,提升 API 限额)
- Unpaywall: `OPENALEX_MAILTO` (用作 email 参数)

---

### 7. PdfParserTool

**文件**: `tools/pdf_parser.py`

**功能**: 本地 PDF 解析与分块,使用 PyMuPDF 提取文本并切分为 RAG 友好的 chunks。

#### 输入参数

- `pdf_path` (str): 本地 PDF 文件的绝对路径
- `chunk_size` (int, 默认 500): 每个 chunk 的目标字符数

#### 返回结果

返回包含以下信息的字典(转为字符串):

- `total_pages`: 总页数
- `total_chunks`: 生成的 chunk 数量
- `chunks_preview`: 前 3 个 chunks 的预览

#### 工作流程

1. **文本提取**: 使用 `PyMuPDF` (fitz) 逐页提取文本
2. **段落分割**: 按 `\n\n` 分割段落
3. **自适应分块**:
   - 按段落累积文本
   - 当超过 `chunk_size` 时创建新 chunk
   - 记录每个 chunk 的起止页码

#### Chunk 结构

每个 chunk 包含:

```python
{
    "text": "chunk 的文本内容",
    "page_start": 3,  # 起始页码
    "page_end": 5     # 结束页码
}
```

#### 使用示例

```python
parser = PdfParserTool()

# 解析 PDF,默认 500 字符/chunk
result = parser.forward(pdf_path="/path/to/paper.pdf")

# 自定义 chunk 大小
result = parser.forward(
    pdf_path="/path/to/paper.pdf",
    chunk_size=800
)
```

#### 依赖要求

```bash
pip install pymupdf
```

#### 未来增强

- 集成 GROBID 进行结构化解析 (识别 section/图表/公式)
- 支持自定义 chunking 策略 (sliding window, semantic chunking)

---

### 8. GeminiFileManagerTool

**文件**: `tools/gemini_file_manager.py`

**功能**: 云端 PDF 解析与 RAG,利用 Google Gemini File API 进行文件上传和智能问答。

#### 输入参数

- `action` (str): 操作类型,可选 `"upload"` 或 `"query"`
- `file_path` (str, 可选): 本地文件路径 (upload 时需要)
- `file_uri` (str, 可选): 已上传文件的 URI (query 时需要)
- `query` (str, 可选): 问题内容 (query 时需要)

#### 返回结果

- **upload 操作**: 返回上传文件的 URI
- **query 操作**: 返回 Gemini 基于文件内容生成的回答

#### 工作流程

**上传文件 (upload)**:

1. 检查文件是否存在
2. 根据文件扩展名设置 MIME 类型
3. 调用 `genai.upload_file()` 上传到 Gemini
4. 返回文件 URI (格式: `https://generativelanguage.googleapis.com/v1beta/files/...`)

**查询文件 (query)**:

1. 从 URI 提取文件名 (格式: `files/abc12345`)
2. 使用 `genai.get_file()` 获取文件对象
3. 调用 `GenerativeModel.generate_content([query, file_obj])`
4. 返回 AI 生成的回答

#### 使用示例

```python
gemini_tool = GeminiFileManagerTool()

# 1. 上传文件
upload_result = gemini_tool.forward(
    action="upload",
    file_path="/path/to/paper.pdf"
)
# 输出: "File uploaded. URI: https://generativelanguage.googleapis.com/v1beta/files/..."

# 2. 提取 URI
file_uri = upload_result.split("URI: ")[1]

# 3. 查询文件内容
query_result = gemini_tool.forward(
    action="query",
    file_uri=file_uri,
    query="What is the main contribution of this paper?"
)
# 输出: Gemini 生成的回答
```

#### API 配置

- API Key: `GOOGLE_API_KEY`
- 使用模型: `gemini-1.5-flash` (成本优化)

#### 优势

- **多模态理解**: 能处理图表、公式、表格等复杂内容
- **自动 chunking**: Gemini 自动处理文档分块和索引
- **省运维**: 无需维护向量数据库和 embedding 管道

#### 注意事项

- 文件会保留在 Gemini 服务器上,可通过 `genai.delete_file()` 删除
- 首次上传会产生 embedding 费用 (~$0.15/1M tokens)
- 后续查询仅按 input/output tokens 计费

---

## RAG 系统

### 1. SimpleBM25Retriever

**文件**: `rag/retrievers/simple_bm25_retriever.py`

**算法**: BM25Okapi（基于 rank_bm25 库）

#### 关键方法

##### `__init__(text_splitter, chunk_size=500, chunk_overlap=50)`

初始化 BM25 检索器。

##### `retrieve(query, contents, limit=10) -> str`

执行 BM25 检索。

**流程**:

1. 文本分块（使用 `RecursiveCharacterTextSplitter`）
2. 对语料库分词（简单空格分割）
3. 构建 BM25 索引
4. 对查询分词
5. 获取 Top-N 相关块
6. 格式化返回

**优势**:

- 无需训练
- 对关键词匹配敏感
- 适合精确查询

---

### 2. SimpleVectorRetriever

**文件**: `rag/retrievers/simple_vector_retriever.py`

**功能**: 基于向量相似度的检索。

#### 关键方法

##### `__init__(embedding_model, text_splitter, chunk_size=500, chunk_overlap=50, path=None)`

初始化向量检索器。

**参数**:

- `embedding_model`: Embedding 模型实例
- `path`: Qdrant 存储路径（可选）

##### `retrieve(query, contents, limit=10, threshold=0.3) -> str`

执行向量检索。

**流程**:

1. 对查询生成 embedding
2. 文本分块
3. 批量生成文档 embeddings
4. 计算余弦相似度
5. 过滤（threshold）并排序
6. 返回 Top-N

**优势**:

- 语义理解能力强
- 适合模糊查询
- 支持多语言

---

### 3. Embedding 模型

系统支持多种 Embedding 模型：

#### OpenAIEmbedding

- 文件: `rag/embeddings/openai_embedding.py`
- 模型: `text-embedding-3-small` 或 `text-embedding-ada-002`
- API Key: `OPENAI_API_KEY`

#### GeminiEmbedding

- 文件: `rag/embeddings/gemini_embedding.py`
- 模型: `models/text-embedding-004`
- API Key: `GOOGLE_API_KEY`
- 通过 LiteLLM 调用

#### JinaEmbedding

- 文件: `rag/embeddings/jina_embedding.py`
- 通过 Jina AI API
- API Key: `JINA_API_KEY`

---

## 配置与环境

### 环境变量 (.env)

```bash
# LLM API Keys
GOOGLE_API_KEY=your_google_api_key
OPENAI_API_KEY=your_openai_api_key

# 搜索 API Keys
SERPAPI_API_KEY=your_serpapi_key
SERP_API=your_serp_api_key  # 备用

# 学术 API
OPENALEX_BASE_URL=https://api.openalex.org
OPENALEX_MAILTO=your_email@example.com
Semantic_Scholar_URL=https://api.semanticscholar.org/graph/v1
Semantic_Scholar_API_key=your_s2_api_key

# Jina API
JINA_API_KEY=your_jina_key

# 模型配置
MODEL_NAME=gemini/gemini-3-pro-preview  # 或 gpt-4o
USE_FREE_SEARCH=false  # true 使用 DuckDuckGo
```

### 依赖安装

```bash
# 激活 conda 环境
conda activate base310

# 核心依赖
pip install smolagents litellm
pip install openai google-generativeai

# 检索依赖
pip install rank-bm25 langchain langchain-text-splitters

# 搜索依赖
pip install serpapi ddgs

# PDF 解析依赖
pip install pymupdf

# 其他
pip install requests loguru python-dotenv
```

---

## LLM 配置系统 (2025-11-30 新增) 🆕

### 概述

系统现在支持 **双路 LLM 角色配置** (Heavy/Light)，并具备自动 Fallback 能力：

- **Heavy 模型**: 用于复杂推理、长上下文任务（如 Prior Art Check、深度分析）
- **Light 模型**: 用于轻量任务（关键词生成、进度笔记、简单改写）

### 环境变量配置

#### 推荐配置（使用自定义端点）

```bash
# 全局默认（Heavy/Light 都会回退到这里）
CUSTOM_LLM_API_BASE=
CUSTOM_LLM_API_KEY=

# Fallback（如果自定义端点不可用）
GOOGLE_API_KEY=

# Heavy 模型配置（复杂任务）
LLM_HEAVY_PROVIDER=custom
LLM_HEAVY_MODEL=gemini-2.5-flash-thinking
LLM_HEAVY_API_BASE=    # 留空则使用 CUSTOM_LLM_API_BASE
LLM_HEAVY_API_KEY=     # 留空则使用 CUSTOM_LLM_API_KEY

# Light 模型配置（轻量任务）
LLM_LIGHT_PROVIDER=custom
LLM_LIGHT_MODEL=gemini-2.5-flash-thinking
LLM_LIGHT_API_BASE=
LLM_LIGHT_API_KEY=
```

### Fallback 逻辑

系统会按以下优先级选择 LLM：

1. **角色特定配置**: `LLM_HEAVY_API_BASE/KEY` 或 `LLM_LIGHT_API_BASE/KEY`
2. **全局自定义配置**: `CUSTOM_LLM_API_BASE/KEY`
3. **Google Fallback**: `GOOGLE_API_KEY`（如果前两者缺失）

### 使用示例

#### 运行 Prior Art Check（Debug 模式）

```bash
python demo_research_agent.py \
  --task_type prior_art_check \
  --topic "monocular 3D detection" \
  --idea "3D领域的旋转数据增强" \
  --year_start 2020 \
  --year_end 2024 \
  --debug  # 限制 API 调用（仅评分 10-20 篇论文）
```

#### 内部实现

```python
# 主 Agent 使用 Heavy 模型
from pipelines.llm_config import get_litellm_config

model_id, api_base, api_key = get_litellm_config(role="heavy")
agent = create_minimal_agent(model_id, api_base, api_key)

# 关键词生成使用 Light 模型
model_id, api_base, api_key = get_litellm_config(role="light")
light_model = LiteLLMModel(model_id=model_id, api_base=api_base, api_key=api_key)
keywords = generate_keywords(light_model, query)
```

### 核心文件

- **配置模块**: `pipelines/llm_config.py`
  - `load_llm_config(role)`: 加载配置并处理 Fallback
  - `get_litellm_config(role)`: 返回 LiteLLM 所需参数
- **使用示例**: `demo_research_agent.py` (Heavy 角色)
- **使用示例**: `pipelines/tasks/prior_art_check.py` (Light 角色，关键词生成)

### LLM 职责分配表 (2025-12-07 更新)

以下是 `prior_art_check.py` 任务中各个 LLM 角色的具体职责分配：

| 函数名                                      | 用途                          | 角色       | 说明                                   |
| :------------------------------------------ | :---------------------------- | :--------- | :------------------------------------- |
| **`seed_papers_from_llm`**                  | 凭知识生成种子论文            | **Expert** | 利用 Expert 模型的广博知识回召         |
| **`generate_search_queries_from_idea`**     | 生成初始检索 Query            | **Expert** | 需要 Expert 模型对领域术语的精准把握   |
| **`generate_seed_queries_llm`**             | Block 1 增强检索 Query        | **Expert** | 确保生成高质量的检索关键词             |
| **`generate_relevance_keywords_from_idea`** | 相关性关键词生成              | **Expert** | 用于 Lexical Filter，需高召回率        |
| **`generate_evidence_keywords_from_idea`**  | Evidence 检索关键词           | **Expert** | 生成用于全文检索的技术关键词           |
| **`refine_queries_llm`**                    | Top Paper 精炼 Query          | **Heavy**  | 基于已有论文上下文进行深层推理         |
| **`decompose_idea_into_facets`**            | Idea → 3-7 Facet              | **Light**  | 结构化任务，Light 模型足够胜任         |
| **`extract_idea_features`**                 | Idea → Dataset/Metric/Methods | **Light**  | 简单的实体抽取任务                     |
| **`extract_high_level_facets_from_idea`**   | Idea → 4 类高层结构           | **Light**  | 相对固定的 Schema 抽取                 |
| **`extract_high_level_facets_from_paper`**  | Paper → 4 类高层结构          | **Light**  | 高频调用，从 Paper 文本抽取            |
| **`score_papers_with_abstract_only`**       | 摘要评分                      | **Light**  | 核心评分循环，Light 模型平衡速度与成本 |
| **`refine_top_papers_with_fulltext`**       | 全文精炼评分                  | **Light**  | 二次确认与 Role 标记                   |
| **`llm_relevance_prefilter`**               | 相关性预筛选                  | **Light**  | 简单的 True/False 判断                 |

---

## Rate Limiting & Concurrency (2025-12-09 新增) 🆕

### 概述

系统使用 **滑动窗口 Rate Limiter** 在客户端层控制 API 请求速率，替代了之前的 "batch + sleep" 模式。

### 实现

**核心文件**: `pipelines/rate_limiter.py`

```python
class SimpleRateLimiter:
    """Thread-safe sliding-window rate limiter."""
    def __init__(self, max_calls: int, period: float):
        self.max_calls = max_calls
        self.period = period
        self._timestamps = deque()
        self._lock = threading.Lock()

    def wait_for_slot(self):
        """Block until a slot is available."""
        # Sliding window logic...
```

### Rate Limiter 配置

| 服务                 | 实例名                  | 位置                                           | Max Calls | Period |
| -------------------- | ----------------------- | ---------------------------------------------- | --------- | ------ |
| **OpenAlex**         | `openalex_rate_limiter` | `tools/openalex_search.py`                     | 8         | 1.0s   |
| **Semantic Scholar** | `s2_rate_limiter`       | `pipelines/clients/semantic_scholar_client.py` | 1         | 2.0s   |
| **LLM**              | `llm_rate_limiter`      | `pipelines/tasks/prior_art_scoring.py`         | 20        | 1.0s   |
| **PDF Download**     | `pdf_rate_limiter`      | `pipelines/tasks/prior_art_scoring.py`         | 5         | 1.0s   |

### 并发配置 (PriorArtConfig)

```python
# 并发 worker 数量（控制本地线程，非 API 速率）
max_workers_openalex: int = 4     # 种子验证
max_workers_s2: int = 4            # 引用图扩展
max_workers_pdf: int = 5           # PDF 下载/解析
max_workers_llm_scoring: int = 20  # LLM 评分
max_workers_llm_prefilter: int = 4 # LLM 预过滤
```

### 双桶检索策略 (Citation vs Recency)

```python
# Dual-bucket retrieval
recent_year_threshold: int = 2024     # 2024+ 属于 "recent bucket"
recent_min_quota_ratio: float = 0.3   # 至少 30% 来自 recent
recent_max_quota_ratio: float = 0.6   # 最多 60% 来自 recent

# 排序权重
weight_relevance: float = 0.6   # 相关性 60%
weight_citation: float = 0.25   # 引用数 25%
weight_recency: float = 0.15    # 时效性 15%
```

### 使用模式

```python
# 每个任务在发起 API 请求前调用 wait_for_slot()
with ThreadPoolExecutor(max_workers=cfg.max_workers_openalex) as executor:
    for item in items:
        executor.submit(process_func, item)

def process_func(item):
    openalex_rate_limiter.wait_for_slot()  # 阻塞直到有 slot
    response = requests.get(...)            # 发起请求
```

---

## 更新历史

### 最新更新（2025-12-09）🆕

#### Rate Limiting 重构

**文件**: `pipelines/rate_limiter.py` (新增)

- 创建 `SimpleRateLimiter` 类，实现线程安全的滑动窗口限速
- 定义全局限速器实例: `openalex_rate_limiter`, `s2_rate_limiter`, `llm_rate_limiter`, `pdf_rate_limiter`
- 移除了所有 "batch + sleep" 模式，改为 per-request 限速
- 更新 `prior_art_utils.py`: `run_with_batch_throttling()` 替换为 `run_parallel()`
- 从 `PriorArtConfig` 移除 `batch_*` 配置项

#### Debug Mode 行为变更

- `debug_mode` 现在仅减少数据规模，**不再**强制 `max_workers=1`
- 这允许在 debug 模式下测试并发代码路径

---

### 更新（2025-12-08）

#### 1. Block 3 全文处理增强 (1.3/1.4)

**文件**: `pipelines/tasks/prior_art_fulltext.py`

##### ✅ 1.3 Method/Experiment Snippets

- 使用 BM25 从 Method/Experiment 章节提取相关段落
- LLM-Light 过滤并生成结构化 bullets
- 输出: `synthesis/method_exp_snippets.jsonl`

##### ✅ 1.4 Key Papers & Intro+RW Full

- 选择关键论文 (top_cited, latest, survey)
- 导出完整 Introduction + Related Work 文本
- **Anchor + Window Collection**: 修复了 Intro/RW 截断问题，现在可以捕获包含子节的完整章节
- 输出: `synthesis/key_papers_selection.json`, `synthesis/intro_rw_full/{paper_id}.json`

#### 2. Synthesis JSON 构建器 (2.1)

**文件**: `pipelines/tasks/prior_art_synthesis.py`

整合所有 Block 1-3 结果到单个自描述 JSON，支持外部 LLM/工具直接使用：

- `metadata`: topic, idea_description, facets, task (含 user_query/intent_notes), audience, usage_context
- `retrieval_summary`: 检索查询、数据源、探索统计
- `limitations`: 覆盖率限制、LLM 噪声说明
- `buckets`: high_overlap, partial_overlap, adjacent, ignored
- `papers`: 每篇论文的完整数据（intro_rw_summary/full, method_exp_snippets, key_roles）
- `prompt_templates`: 内置 full_markdown_report 和 short_related_work_section 提示词

**用法**: 默认只生成 JSON，不调用 heavy LLM。设置 `config.auto_generate_intro_rw_report=True` 可自动生成 MD 报告。

#### 3. 新增配置项

```python
@dataclass
class PriorArtConfig:
    # 1.3/1.4 配置
    enable_method_exp_snippets: bool = True
    enable_intro_rw_full: bool = True

    # 2.1 Synthesis 配置
    enable_synthesis_json: bool = True
    auto_generate_intro_rw_report: bool = False  # 默认关闭
```

---

### 更新（2025-12-07）

#### 1. 结构化覆盖率分析 (Block 3 & Phase 6) 🏗️

**文件**: `pipelines/evidence.py`, `pipelines/coverage_classifier.py`, `pipelines/tasks/prior_art_check.py`

本次更新实现了基于证据的统一覆盖率分析系统，解决了报告中统计数据矛盾的问题，并引入了更严格的领域过滤。

##### ✅ 1. 统一的证据系统 (Unified Evidence System)

- **EvidenceSpan**: 定义了覆盖率的原子单位，包含 `(paper_id, facet, section_role, sentence_role, strength)`。
- **FacetCoverageStats**: 唯一的覆盖率统计来源，不再依赖旧的正则表达式计数。
- **FacetReportView**: 用于报告生成的纯视图层，确保展示数据与底层统计完全一致。

##### ✅ 2. 智能覆盖率分类 (Coverage Classification)

引入了确定性分类器 `pipelines/coverage_classifier.py`，将论文分为四个等级：

- **Level 3 (High Overlap)**: 包含 >=2 个关键 Facet 的强证据（通常在 Method/Intro 中提及 Own Method）。
- **Level 2 (Partial Overlap)**: 包含 >=1 个关键 Facet 的强证据，或 >=2 个 Facet 的任意证据。
- **Level 1 (Adjacent Work)**: 属于相关领域，但仅有背景提及或弱证据。
- **Level 0 (Ignore)**: 领域外或无证据。

##### ✅ 3. 严格的领域过滤 (Strict Domain Filtering)

- **全局硬过滤**: 在 `PaperRecord` 层面实现 `is_in_domain_for_topic`。
- **排除无关领域**: 自动剔除 Medical, Agriculture, NLP 等非目标领域的论文，即使它们包含通用关键词（如 "3D detection"）。
- **一致性**: 确保 Adjacent Work 列表不再包含明显的领域外论文（如 MRI 分析、作物检测）。

##### ✅ 4. Run-Oriented 架构

- **RunManager**: 为每次执行创建独立的 `runs/<timestamp>_<topic>/` 目录。
- **Artifacts 保存**: 自动保存 `config.json`, `search_log.jsonl`, `md_reports/` 到运行目录，便于回溯。

---

### 之前更新（2025-12-06）

#### 1. Prior Art Check Round 3 全面升级 🔄

**文件**: `pipelines/tasks/prior_art_check.py`

本次更新对 Prior Art Check 管道进行了第三轮全面重构，重点提升了跨领域通用性、结果准确性和报告的专业性。

##### ✅ 1. 双阶段预过滤 (Two-Stage Pre-filtering)

引入了更高效的漏斗式过滤机制，大幅提升性能并减少 LLM 幻觉：

- **Stage 1 (Lexical)**: 引入 `simple_relevance_score` 增强版
  - 自动降权通用术语（如 "survey", "review", "deep learning"），避免被宽泛的综述论文淹没。
  - 使用 `GENERIC_STOPWORDS` 常量列表进行过滤。
- **Stage 2 (Light LLM)**: 新增 `llm_relevance_prefilter`
  - 使用 Light 模型对 Stage 1 筛选出的 Top-K 论文进行快速 True/False 相关性判断。
  - 在昂贵的详细评分之前，剔除明显不相关的论文。

##### ✅ 2. 领域无关的证据生成 (Domain-Agnostic Evidence)

彻底移除了代码中硬编码的 3D 视觉特定关键词，实现了真正的领域通用性：

- **移除硬编码**: 删除了 `["camera space", "optical center", ...]` 等特定领域词汇。
- **动态生成**: 新增 `generate_evidence_keywords_from_idea` 函数。
  - 使用 Light LLM 根据具体 Idea 和 Topic 动态生成证据检索关键词。
  - 结合结构化特征（Datasets, Metrics）增强检索精度。
- **通用混入**: `make_keywords_for_evidence` 现在只负责合并各来源的关键词，不再掺杂偏见。

##### ✅ 3. 相似度排序与 Adjacent Work 优化

改进了论文排序和展示逻辑，使真正相关的 Prior Art 更容易被发现：

- **相似度评分**: 打分 Prompt 新增 `task_similarity` 和 `method_similarity` (0.0-1.0) 维度。
- **智能排序**: `PriorArtMatch` 排序逻辑调整为优先考虑相似度，其次才是引用数。
  - 引用数权重被设置上限 (capped at 200)，防止高引用的通用论文霸榜。
- **Survey 限制**: 在报告的 Adjacent Work 章节，限制 Survey/Review 类论文最多显示 3 篇，避免挤占具体方法论文的版面。

##### ✅ 4. 防御性报告措辞 (Defensive Wording)

增强了报告的客观性和严谨性：

- **Gap Analysis**: 将 "Found 0 papers" 的表述改为更客观的 "⚠ Not found in retrieved corpus"。
- **免责声明**: 报告底部新增详细 Disclaimer，明确指出结果可能受限于检索召回率和 LLM 幻觉。
- **标签级计数**: 修复了 Facet 覆盖率计数逻辑，引入 `classes PriorArtMatch.covered_facet_labels`，避免因文本 Paraphrase 导致的计数偏差。

##### ✅ 5. 开发体验优化

- **Debug 文件输出**: 在 Debug 模式下，自动将 Markdown 报告保存为本地文件（如 `prior_art_debug_topic_timestamp.md`），便于回溯和对比。

---

---

### 之前更新（2025-11-30）

#### 1. LLM 配置系统重构 🎯

**文件**: `pipelines/llm_config.py`, `demo_research_agent.py`

##### 双路 LLM 角色 (Heavy/Light)

- ✅ **新配置模块**: 实现 `pipelines/llm_config.py`
  - 支持 Heavy (复杂推理) 和 Light (轻量任务) 两种角色
  - 环境变量：`LLM_HEAVY_*` 和 `LLM_LIGHT_*`
  - 自动 Fallback：Custom → Google Gemini
- ✅ **智能 Provider 选择**:
  - 优先级：角色特定配置 → 全局自定义 → Google Fallback
  - 自动处理缺失配置，确保系统始终可用
- ✅ **Debug 模式支持**:
  - 新增 `--debug` 命令行参数
  - Prior Art Check 在 debug 模式下大幅减少 LLM 调用（80→10, 100→20）

##### Prior Art Check 进一步优化

**文件**: `pipelines/tasks/prior_art_check.py`

- ✅ **轻量级预过滤**: 在昂贵的 LLM 评分前过滤无关论文
  - `generate_relevance_keywords_from_idea`: 生成相关关键词（使用 Light 模型）
  - `simple_relevance_score`: 基于关键词的快速评分
  - `prefilter_papers_for_llm_scoring`: 预过滤并排序，保留 Top-K
- ✅ **可配置上限**: `PriorArtConfig`
  - `max_abstract_scoring_initial`: 初始阶段最多评分论文数（默认 80）
  - `max_abstract_scoring_after_citation`: Citation Graph 后最多评分数（默认 100）
  - `debug_mode`: Debug 模式（10/20）
- ✅ **进度日志增强**:
  - 批量评分时显示进度（如 "Abstract scoring progress: 10/80"）
  - 可配置日志间隔

##### 优势

- **性能**: 预过滤减少 LLM 调用，Light 模型加速辅助任务
- **灵活性**: 可为不同任务配置不同模型/端点
- **稳定性**: 自动 Fallback 确保系统健壮性
- **成本**: Debug 模式大幅降低开发成本

---

### 之前更新（2025-11-29）

#### 1. Prior Art Check 系统优化 🚀

**文件**: `pipelines/tasks/prior_art_check.py`

##### LLM 调用优化

- ✅ **绕过 CodeAgent**: 实现 `call_llm_json(agent, prompt)` 辅助函数
  - 直接调用 `agent.model` 进行 JSON 生成任务
  - 避免 CodeAgent 的代码解析和执行开销
  - 消除 "Error in code parsing" 噪声日志
  - 显著减少 token 消耗和响应时间

##### 鲁棒的 JSON 解析

- ✅ **统一解析器**: 实现 `_extract_text_from_llm_response(resp)` 辅助函数
  - 处理 smolagents/LiteLLM `ChatMessage` 对象（提取 `.content`）
  - 自动移除 Markdown 代码围栏（`json ... `）
  - 支持可选的语言提示（如 "json", "python"）
  - 防御性处理 HTML `<code>` 标签

##### 更新的函数

所有 JSON 生成函数现在使用优化的调用和解析流程：

1. **`decompose_idea_into_facets`**: 从研究想法中提取 3-7 个关键技术方面
2. **`extract_idea_features`**: 提取结构化特征（数据集、指标、方法族）
3. **`extract_high_level_facets_from_idea`**: 提取高层次方面（问题、设置、机制、假设）
4. **`extract_high_level_facets_from_paper`**: 从论文中提取高层次方面
5. **`score_papers_against_idea`**: 评分论文与想法的重叠程度（0-3 级）

##### 增强的提示词

所有提示词现在包含明确的指令：

```
Return raw JSON only, without backticks or any surrounding text.
```

##### 优势

- **性能**: 每个 JSON 任务减少 1-2 次 LLM 调用
- **稳定性**: 消除对 CodeAgent 自修复机制的依赖
- **可维护性**: 更清晰的日志和错误消息
- **成本**: 减少 token 使用量（特别是对于失败 → 重试场景）

---

### 之前更新（2025-11-28）

#### 1. 混合检索系统实现

- ✅ 创建 `SimpleBM25Retriever` 类
- ✅ 在 `SimpleCrawler` 中集成 BM25 和向量检索
- ✅ `_query` 方法实现结果合并

#### 2. 多源学术 API 集成

- ✅ 实现 `OpenAlexWorksSearchTool`
- ✅ 实现 `SemanticScholarPaperDetailTool`
- ✅ 实现 `SemanticScholarCitationTool`
- ✅ 实现 `GoogleFileSearchTool`（Gemini File API）

#### 3. 系统优化

- ✅ 修复 `search_reflector` 导入路径
- ✅ 修复 `duckduckgo_search.py` 语法错误
- ✅ 优化 Embedding 模型回退逻辑（OpenAI → Gemini）
- ✅ 添加系统提示词规范 Agent 工作流

#### 4. 依赖管理

- ✅ 安装 `google-generativeai`
- ✅ 安装 `langchain`, `langchain-text-splitters`
- ✅ 安装 `pymupdf` (PyMuPDF)
- ✅ 在 `base310` 环境中验证所有依赖

#### 5. 高级研究工具 (Fetch & Parse)

- ✅ 实现 `PaperResolverTool` - 多源 PDF URL 解析
  - 支持 arXiv, Semantic Scholar, OpenAlex, Unpaywall
  - 智能优先级策略和 ID 转换
- ✅ 实现 `PdfParserTool` - 本地 PDF 解析
  - 使用 PyMuPDF 提取文本
  - 自适应段落分块策略
- ✅ 实现 `GeminiFileManagerTool` - 云端 RAG
  - 文件上传到 Gemini File API
  - 基于文件的智能问答
- ✅ 创建 `test_new_tools.py` 验证工具功能

---

## 使用指南

### 快速开始

```bash
# 1. 激活环境
conda activate base310

# 2. 配置 .env 文件（填入你的 API keys）

# 3. 运行 demo
python demo_research_agent.py
```

### 自定义查询

编辑 `demo_research_agent.py` 中的 `user_query`:

```python
user_query = (
    "Please research 'Transformer Architecture' papers from 2024. "
    "1. Use OpenAlex to find top 5 papers. "
    "2. Use Semantic Scholar to get abstracts. "
    "3. Summarize key innovations."
)
```

### 使用不同 LLM

```bash
# 使用 Gemini
export GOOGLE_API_KEY=your_key
export MODEL_NAME=gemini/gemini-3-pro-preview

# 使用 OpenAI
export OPENAI_API_KEY=your_key
export MODEL_NAME=gpt-4o
```

### 使用免费搜索

```bash
export USE_FREE_SEARCH=true  # 使用 DuckDuckGo
```

---

## 开发指南

### 添加新工具

#### 1. 创建工具文件

```python
# tools/my_new_tool.py
from smolagents import Tool

class MyNewTool(Tool):
    name = "my_tool"
    description = "Description of what this tool does"
    inputs = {
        "param1": {
            "type": "string",
            "description": "Parameter description"
        }
    }
    output_type = "string"

    def __init__(self):
        super().__init__()
        # 初始化逻辑

    def forward(self, param1: str) -> str:
        # 工具逻辑
        return result
```

#### 2. 在 Agent 中注册

在 `demo_research_agent.py` 的 `create_research_agent` 中：

```python
from tools.my_new_tool import MyNewTool

...
my_tool = MyNewTool()

agent = CodeAgent(
    tools=[
        search_tool,
        ...
        my_tool  # 添加新工具
    ],
    ...
)
```

### 添加新 Retriever

#### 1. 继承基类

```python
# rag/retrievers/my_retriever.py
from rag.retrievers.base import BaseRetriever

class MyRetriever(BaseRetriever):
    def __init__(self, **kwargs):
        super().__init__()
        # 初始化

    def retrieve(self, query, contents, limit=10):
        # 检索逻辑
        return results
```

#### 2. 在 SimpleCrawler 中集成

在 `async_web_crawler.py` 的 `__init__` 中：

```python
from rag.retrievers.my_retriever import MyRetriever

self.my_retriever = MyRetriever(...)
```

在 `_query` 方法中调用：

```python
def _query(self, query, contents):
    my_res = self.my_retriever.retrieve(query, contents)
    # 合并结果
    return combined_results
```

### 修改系统提示词

在 `demo_research_agent.py` 中修改 `system_instructions`:

```python
system_instructions = """
你可以在这里定义 Agent 的行为规范、工作流程等。

**推荐工作流:**
1. 第一步...
2. 第二步...
"""
```

### 调试技巧

#### 1. 增加日志

```python
from loguru import logger

logger.info(f"Debug info: {variable}")
```

#### 2. 查看 Agent 步骤

设置 `verbosity_level=1` 或更高：

```python
agent = CodeAgent(
    tools=[...],
    verbosity_level=2  # 0: 静默, 1: 正常, 2: 详细
)
```

#### 3. 测试单个工具

```python
tool = OpenAlexWorksSearchTool()
result = tool.forward(query="test", from_year=2024)
print(result)
```

---

## 常见问题

### Q: 如何切换搜索引擎？

A: 设置环境变量 `USE_FREE_SEARCH=true` 使用 DuckDuckGo，或提供 `SERPAPI_API_KEY` 使用 Google Search。

### Q: 如何使用本地 Embedding 模型？

A: 修改 `SimpleCrawler.__init__` 中的 embedding 初始化逻辑，使用 `SentenceTransformersEmbedding` 或自定义模型。

### Q: RAG 检索质量不好怎么办？

A: 调整参数：

- `chunk_size`: 块大小（默认 500）
- `chunk_overlap`: 块重叠（默认 50）
- `threshold`: 相似度阈值（默认 0.3）
- `limit`: 返回数量（默认 10）

### Q: 如何添加新的 LLM?

A: 使用 LiteLLM 支持的任何模型：

```python
model_id = "anthropic/claude-3-opus"
# 或
model_id = "ollama/llama2"
```
