# PaperArena Hub 文件结构

## 根目录文件

```
paperarena_hub/
├── .env                                    # 环境变量配置文件
├── .env.example                            # 环境变量示例文件
├── PROJECT_DOCUMENTATION.md                # 项目文档
├── batch_run.py                            # 批量运行脚本
├── config_loader.py                        # 配置加载器
├── data_loader.py                          # 数据加载器
├── demo_research_agent.py                  # 研究代理演示
├── env_variable_audit.md                   # 环境变量审计文档
├── mini_react.py                           # Mini ReAct 实现
├── prior_art_report_test.md                # 先验技术报告测试
├── report_output.txt                       # 报告输出
├── report_output_v2.txt                    # 报告输出 v2
├── report_output_v3.txt                    # 报告输出 v3
├── result_recorder.py                      # 结果记录器
├── run_react.py                            # ReAct 运行脚本
├── slot_filling_report_test.md             # 槽填充报告测试
├── template.py                             # 模板文件
├── test_benchmark_landscape.py             # 基准测试
├── test_bm25_integration.py                # BM25 集成测试
├── test_custom_llm_endpoint.py             # 自定义 LLM 端点测试
├── test_exploration_loop.py                # 探索循环测试
├── test_integration_quick.py               # 快速集成测试
├── test_liaobots_api.py                    # Liaobots API 测试
├── test_llm_priority.py                    # LLM 优先级测试
├── test_new_tools.py                       # 新工具测试
├── test_prior_art_check.py                 # 先验技术检查测试
├── test_slot_filling.py                    # 槽填充测试
└── verify_llm_config.py                    # LLM 配置验证
```

## configs/ - 配置目录

```
configs/
└── base_config.yaml                        # 基础配置文件
```

## docs/ - 文档目录

```
docs/
├── CUSTOM_LLM_PRIORITY_FIX.md              # 自定义 LLM 优先级修复文档
├── CUSTOM_LLM_SETUP.md                     # 自定义 LLM 设置文档
├── FINAL_SOLUTION.md                       # 最终解决方案文档
├── OPENAI_PREFIX_FIX.md                    # OpenAI 前缀修复文档
└── SOLUTION_B_SUMMARY.md                   # 方案 B 总结文档
```

## pipelines/ - 流水线目录

```
pipelines/
├── __init__.py
├── exploration_loop.py                     # 探索循环
├── exploration_loop_broken_backup.py       # 探索循环备份（损坏）
├── llm_config.py                           # LLM 配置
├── state.py                                # 状态管理
│
├── clients/                                # 客户端
│   ├── __init__.py
│   └── semantic_scholar_client.py          # Semantic Scholar 客户端
│
└── tasks/                                  # 任务
    ├── __init__.py
    ├── benchmark_landscape.py              # 基准测试场景
    ├── prior_art_check.py                  # 先验技术检查
    └── slot_filling.py                     # 槽填充
```

## rag/ - RAG（检索增强生成）目录

```
rag/
├── __init__.py
├── logger.py                               # 日志记录器
│
├── embeddings/                             # 嵌入模型
│   ├── __init__.py
│   ├── base.py                             # 基础嵌入类
│   ├── gemini_embedding.py                 # Gemini 嵌入
│   ├── jina_embedding.py                   # Jina 嵌入
│   ├── mistral_embedding.py                # Mistral 嵌入
│   ├── openai_compatible_embedding.py      # OpenAI 兼容嵌入
│   ├── openai_embedding.py                 # OpenAI 嵌入
│   ├── sentence_transformers_embeddings.py # Sentence Transformers 嵌入
│   └── vlm_embedding.py                    # VLM 嵌入
│
├── loaders/                                # 加载器
│   ├── __init__.py
│   ├── apify_reader.py                     # Apify 阅读器
│   ├── base_io.py                          # 基础 I/O
│   ├── chunkr_reader.py                    # Chunkr 阅读器
│   ├── firecrawl_reader.py                 # Firecrawl 阅读器
│   ├── jina_url_reader.py                  # Jina URL 阅读器
│   └── unstructured_io.py                  # 非结构化 I/O
│
├── messages/                               # 消息处理
│   ├── __init__.py
│   ├── base.py                             # 基础消息类
│   ├── func_message.py                     # 函数消息
│   └── conversion/                         # 消息转换
│       ├── __init__.py
│       ├── alpaca.py                       # Alpaca 格式
│       ├── conversation_models.py          # 对话模型
│       └── sharegpt/                       # ShareGPT 格式
│           ├── __init__.py
│           ├── function_call_formatter.py  # 函数调用格式化
│           └── hermes/                     # Hermes 格式
│               ├── __init__.py
│               └── hermes_function_formatter.py
│
├── retrievers/                             # 检索器
│   ├── __init__.py
│   ├── auto_retriever.py                   # 自动检索器
│   ├── base.py                             # 基础检索器
│   ├── bm25_retriever.py                   # BM25 检索器
│   ├── cohere_rerank_retriever.py          # Cohere 重排序检索器
│   ├── graph_auto_retriever.py             # 图自动检索器
│   ├── simple_bm25_retriever.py            # 简单 BM25 检索器
│   ├── simple_vector_retriever.py          # 简单向量检索器
│   └── vector_retriever.py                 # 向量检索器
│
├── storages/                               # 存储
│   ├── __init__.py
│   ├── graph_storages/                     # 图存储
│   │   ├── __init__.py
│   │   ├── base.py
│   │   ├── graph_element.py                # 图元素
│   │   ├── nebula_graph.py                 # Nebula 图数据库
│   │   └── neo4j_graph.py                  # Neo4j 图数据库
│   │
│   ├── key_value_storages/                 # 键值存储
│   │   ├── __init__.py
│   │   ├── base.py
│   │   ├── in_memory.py                    # 内存存储
│   │   ├── json.py                         # JSON 存储
│   │   └── redis.py                        # Redis 存储
│   │
│   ├── object_storages/                    # 对象存储
│   │   ├── __init__.py
│   │   ├── amazon_s3.py                    # Amazon S3
│   │   ├── azure_blob.py                   # Azure Blob
│   │   ├── base.py
│   │   └── google_cloud.py                 # Google Cloud Storage
│   │
│   └── vectordb_storages/                  # 向量数据库存储
│       ├── __init__.py
│       ├── base.py
│       ├── milvus.py                       # Milvus 向量数据库
│       └── qdrant.py                       # Qdrant 向量数据库
│
├── types/                                  # 类型定义
│   ├── __init__.py
│   ├── enums.py                            # 枚举类型
│   ├── openai_types.py                     # OpenAI 类型
│   └── unified_model_type.py               # 统一模型类型
│
└── utils/                                  # 工具函数
    ├── __init__.py
    ├── async_func.py                       # 异步函数
    ├── commons.py                          # 通用函数
    ├── constants.py                        # 常量
    ├── response_format.py                  # 响应格式
    └── token_counting.py                   # Token 计数
```

## reflectors/ - 反射器目录

```
reflectors/
├── __init__.py
├── search_reflector.py                     # 搜索反射器
└── prompts/
    └── search_prompts.yaml                 # 搜索提示词
```

## scripts/ - 脚本目录

```
scripts/
├── run_claude_3_5_sonnet.sh                # Claude 3.5 Sonnet 运行脚本
├── run_claude_sonnet_4.sh                  # Claude Sonnet 4 运行脚本
├── run_gemini_2_5_pro.sh                   # Gemini 2.5 Pro 运行脚本
├── run_glm_4_5.sh                          # GLM 4.5 运行脚本
├── run_gpt4_1.sh                           # GPT-4.1 运行脚本
├── run_kimi_k2.sh                          # Kimi K2 运行脚本
├── run_o4_mini.sh                          # O4 Mini 运行脚本
├── run_qwen3_instruct.sh                   # Qwen3 Instruct 运行脚本
└── run_qwen3_thinking.sh                   # Qwen3 Thinking 运行脚本
```

## tools/ - 工具目录

```
tools/
├── async_web_crawler.py                    # 异步网页爬虫
├── async_web_crawler_backup.py             # 异步网页爬虫备份
├── code_executor.py                        # 代码执行器
├── cookies.py                              # Cookie 管理
├── cross_ref_lookup.py                     # 交叉引用查找
├── data_analyzer.py                        # 数据分析器
├── database_search.py                      # 数据库搜索
├── duckduckgo_search.py                    # DuckDuckGo 搜索
├── gemini_file_manager.py                  # Gemini 文件管理器
├── google_file_search.py                   # Google 文件搜索
├── llm_chat.py                             # LLM 聊天
├── mock_local_web_search.py                # 模拟本地网页搜索
├── mock_web_search.py                      # 模拟网页搜索
├── openalex_search.py                      # OpenAlex 搜索
├── paper_resolver.py                       # 论文解析器
├── pdf_extractor.py                        # PDF 提取器
├── pdf_parser.py                           # PDF 解析器
├── pdf_utils.py                            # PDF 工具函数
├── scorer.py                               # 评分器
├── semantic_scholar_search.py              # Semantic Scholar 搜索
├── similarity.py                           # 相似度计算
└── web_crawler_utils.py                    # 网页爬虫工具函数
```

## pdf_cache/ - PDF 缓存目录

```
pdf_cache/
├── 10.1007_s11263-019-01247-4.pdf
├── 10.1007_s11263-020-01359-2.pdf
├── 10.1007_s11263-022-01710-9.pdf
├── ... (共 58 个 PDF 文件)
└── None.pdf
```

## search_outputs/ - 搜索输出目录

```
search_outputs/
├── 3DET-Mamba_Causal_Sequence_Modelling_for_End-to-End_3D_Object_Detection_abstract.jsonl
├── 3DET-Mamba_arxiv.jsonl
├── CVPR_2024_3D_Object_Detection_paper_abstract_year_2024.jsonl
├── CVPR_2024_3D_Object_Detection_paper_year_2024.jsonl
├── ... (共 20 个 JSONL 文件)
└── best_3D_object_detection_papers_CVPR_2024_year_2024.jsonl
```

## 目录统计

### 总体统计

- **总目录数**: 38
- **总文件数**: 约 200+

### 主要模块

1. **pipelines/**: 研究流水线和任务管理
2. **rag/**: 检索增强生成系统（嵌入、检索、存储）
3. **tools/**: 各种工具函数（搜索、爬虫、PDF 处理等）
4. **reflectors/**: 反射器和提示词管理
5. **scripts/**: 各种 LLM 模型的运行脚本
6. **docs/**: 项目文档
7. **configs/**: 配置文件
8. **pdf_cache/**: PDF 文件缓存
9. **search_outputs/**: 搜索结果输出

### 核心功能

- 🔍 **学术搜索**: Semantic Scholar, OpenAlex, DuckDuckGo
- 📄 **PDF 处理**: 提取、解析、缓存
- 🤖 **LLM 集成**: 多种 LLM 模型支持（Gemini, Claude, GPT-4, Qwen 等）
- 🔎 **RAG 系统**: 完整的检索增强生成系统
- 📊 **数据分析**: 论文分析、评分、相似度计算
- 🌐 **网页爬虫**: 异步爬虫和多种数据源集成
