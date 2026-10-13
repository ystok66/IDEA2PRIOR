# Idea2Prior

从研究想法查找并排序已有研究。

**从研究想法检索论文，以摘要和可获得的全文证据生成 Top 5。**

项目以 [英文说明](README.md) 和英文界面为主；网页支持切换中文。

提供本地网页、Python API、命令行和 Jupyter Notebook 四种入口。可配置多个 OpenAI、Gemini 或 OpenAI 兼容 API，并为检索与评分、轻量任务、Expert 排序分别选择模型。在本次 30 个 idea-to-paper 查询案例中，按已采用口径统计的总体 Hit@5 为 53.3%，高于 ChatGPT、Gemini 网页版 Deep Research 的 43.3% 和 36.7%。历史样本的平均任务费用低于用时表后列出的 Deep Research API 费用参考。

Idea2Prior 基于 [PaperArena: An Evaluation Benchmark for Tool-Augmented Agentic Reasoning on Scientific Literature](https://arxiv.org/abs/2510.10909) 及其[官方代码](https://github.com/ustc-ai4science/PaperArena)，在此基础上增加了根据 idea 检索论文、分阶段核验与排序、实现分析、技术演化图及本地网页/Notebook 等功能。

## 2026 年 10 月历史评测

以下比较各难度的**前 10 题**。ChatGPT 和 Gemini 使用 **2026 年 10 月网页版 Deep Research** 测试，我们使用 API 流程。Hit@5 表示指定参考论文出现在返回 Top 5 中的题目比例。

| 难度 | 题目 ID | Idea2Prior（我们） | ChatGPT Deep Research 网页版 | Gemini Deep Research 网页版 |
| --- | --- | ---: | ---: | ---: |
| EASY | N001–N010 | **90%（9/10）** | 70%（7/10） | 70%（7/10） |
| MEDIUM | C001–C010 | **50%（5/10）** | 30%（3/10） | 30%（3/10） |
| HARD | C001–C010 | 20%（2/10） | **30%（3/10）** | 10%（1/10） |

EASY 采用[评测口径文档](docs/BENCHMARK_RESULTS.md)中的约定展示方式，其中记录了假定项与覆盖率分母。我们的 EASY 使用运行失败题补跑后的最终结果。这些历史成绩不代表公开版本迁移后的 Gemini 默认配置成绩；每种难度提供 30 题，此处只比较前 10 题。

**Top 5 相互覆盖率**：为检查检索表现是否受到所选研究领域或提示词设计的影响，我们在目标论文 Hit@5 之外，也考察 Idea2Prior、ChatGPT Deep Research 和 Gemini Deep Research 的论文结果重合程度。分子为已记录的同题共同论文数之和，每种难度的分母统一为 **50 个计划席位（10 题 × 5）**，不考虑排名顺序。它描述系统间的结果一致程度，不能单独证明数据集具有代表性或不存在选择偏向。

| 组合 | EASY | MEDIUM | HARD |
| --- | ---: | ---: | ---: |
| 我们 ↔ ChatGPT | 28%（14/50） | 22%（11/50） | 6%（3/50） |
| 我们 ↔ Gemini | 30%（15/50） | 12%（6/50） | 16%（8/50） |
| ChatGPT ↔ Gemini | 34%（17/50） | 20%（10/50） | 14%（7/50） |

**我们的平均单题用时和 API 已回报 token：**

| 范围 | 题数 | 平均用时 | 平均输入 tokens | 平均输出 tokens | 平均已回报总 tokens |
| --- | ---: | ---: | ---: | ---: | ---: |
| EASY | 10 | 8 分 54 秒 | 288,821.8 | 88,153.6 | 376,975.4 |
| MEDIUM | 10 | 9 分 49 秒 | 304,122.9 | 91,695.7 | 395,818.6 |
| HARD | 10 | 7 分 57 秒 | 302,838.2 | 88,621.0 | 391,459.2 |
| 合计 | 30 | **8 分 53 秒** | **298,594.3** | **89,490.1** | **388,084.4** |

用户提供的网页版平均用时为 **ChatGPT 5.2 分钟/题、Gemini 4.7 分钟/题**，没有独立的逐题计时日志。我们的均值按最终采用运行计算，排除被替换的整题尝试。token 合计覆盖所有模型阶段；有 **1,491 次请求缺少 usage**，因此只是已回报小计，完整消耗未知。

**平均任务费用。** 用户提供的历史 API 费用样本平均为 **$0.0945/任务**。下表列出官方 **Deep Research API** 报价（美元，2026-10-13 查询）。OpenAI 估算采用我们选定运行的平均已回报用量：每题 **298,594.3 个输入 tokens + 89,490.1 个输出 tokens**。

| Deep Research API | 官方计费 | 单题费用参考 |
| --- | --- | --- |
| [OpenAI o3 Deep Research](https://developers.openai.com/api/docs/models/o3-deep-research) | 输入 $10 / 输出 $40 每百万 tokens；缓存输入 $2.50 | 按我们的已知 tokens 估算 **$6.57**，另计工具费用 |
| [OpenAI o4-mini Deep Research](https://developers.openai.com/api/docs/models/o4-mini-deep-research) | 输入 $2 / 输出 $8 每百万 tokens；缓存输入 $0.50 | 按我们的已知 tokens 估算 **$1.31**，另计工具费用 |
| [Gemini Deep Research](https://ai.google.dev/gemini-api/docs/deep-research#availability-and-pricing) | 按底层模型 tokens 和工具使用计费 | 官方典型任务估计 **$1–$3** |
| [Gemini Deep Research Max](https://ai.google.dev/gemini-api/docs/deep-research#availability-and-pricing) | 按底层模型 tokens 和工具使用计费 | 官方典型任务估计 **$3–$7** |

OpenAI 估算采用未缓存单价，不含未回报用量；这是对我们已有用量重新计价，并非预测研究代理的实际消耗。Google 的范围是预览阶段示例任务的费用估计，不是固定单题报价，也不是根据我们 tokens 算出的价格。要估算 Gemini 同等用量的代理费用，还需各模型用量、缓存和工具次数。**我们的 ChatGPT、Gemini 评测使用 2026 年 10 月网页版 Deep Research，未测量它们的 API 用量或后台模型。** 详见[费用口径](docs/BENCHMARK_RESULTS.md#api-cost-estimates)。

详见[评测口径与来源记录](docs/BENCHMARK_RESULTS.md)及[汇总数据](docs/benchmark_results.json)。

数据集同时保留 ChatGPT、Gemini 的原始结果 TXT，以及我们的最终采用 Top 5、结构化结果和原始 Expert 输出。[查看三方结果文件](datasets/README.md#historical-comparison-outputs)。

## 快速开始

需要 Python 3.10 或更高版本。在项目目录执行：

```bash
python -m venv .venv
```

Windows PowerShell 激活：

```powershell
.venv\Scripts\Activate.ps1
```

macOS / Linux 激活：

```bash
source .venv/bin/activate
```

安装并启动网页：

```bash
python -m pip install -e .
python -m idea2prior web --port 7860
```

打开 **http://127.0.0.1:7860**，输入研究想法，填写 API Key。默认推荐当前 Gemini 配置：检索与评分使用 `gemini-3.1-flash-lite`，Expert 使用 `gemini-3.8-flash`。模型可以从下拉列表选择，也可以手动填写；请确认账户和端点支持这些名称。可以添加多个 API，分别指定三个角色。点击“检查配置”只验证配置格式，不调用网络，也不验证真实凭据或模型可用性；点击“开始检索”会使用所选 API。

网页仅监听本机。支持进度、用时、已回报 token、停止任务、Top 5 和 JSON 下载。运行结果保存在 `outputs/`，该目录不进入 Git。

原检索型号 `gemini-3.1-flash-lite-preview` 已被 Google 停用，默认配置采用正式替代型号。原配置另存于 [`configs/gemini_previous_workflow.example.json`](configs/gemini_previous_workflow.example.json)，用于明确选择支持该名称的自定义端点；程序不会静默替换模型。

输入密钥和有效 API URL 后会自动加载端点的模型列表，也可点击“加载模型”手动刷新。列表读取不生成模型回答；模型是否可调用仍受账户额度、权限和端点路由影响。网页与 Notebook 会显示失败阶段、具体安全原因、错误代码及已知的 HTTP 状态。网页刷新后可通过 URL 内的任务 ID 恢复视图，前提是本地服务仍保留该任务；不会因此重跑。

原有 Python 模块与命令 `paperarena` 继续兼容；新示例使用 `idea2prior`。

## API 配置

| provider | 调用方式 | 配置 |
|---|---|---|
| `openai` | OpenAI Chat Completions | `model`、`api_key` 或 `api_key_env`；默认官方 API 地址 |
| `gemini` | Gemini 原生 API，经 LiteLLM 适配 | `model`、`api_key` 或 `api_key_env`；不填 `base_url` |
| `custom` | OpenAI 兼容接口 | `model`、`base_url`、`api_key` 或 `api_key_env` |

模型名称由用户填写；使用账户实际可用、支持对应协议的模型。自定义接口须支持 Chat Completions。Gemini 的 OpenAI 兼容接口也可作为 `custom` 配置，见 [Google 官方说明](https://ai.google.dev/gemini-api/docs/openai)。原生 Gemini 适配见 [LiteLLM 文档](https://docs.litellm.ai/docs/providers/gemini)。

示例文件：

- [`configs/gemini_recommended.example.json`](configs/gemini_recommended.example.json)：当前 Gemini 推荐配置，检索和 Expert 分别设置模型。
- [`configs/openai.example.json`](configs/openai.example.json)：单个 OpenAI API。
- [`configs/gemini.example.json`](configs/gemini.example.json)：单个 Gemini API。
- [`configs/multiple.example.json`](configs/multiple.example.json)：多个 API 与角色路由。

`roles.heavy` 用于检索和摘要评分；`roles.light` 用于关键词等轻量任务；`roles.expert` 用于最终排序。三个角色可以使用同一 profile，也可以分别使用不同服务、模型、端点和密钥。每个任务在独立子进程中运行，避免并发配置互相覆盖；不会自动切换 provider。

复制示例为 `configs/run.local.json`，替换 `YOUR_*_MODEL` 后使用。推荐环境变量引用，避免在 JSON 中保存密钥：

```powershell
$env:OPENAI_API_KEY = "YOUR_API_KEY"
$env:GEMINI_API_KEY = "YOUR_API_KEY"
```

```bash
export OPENAI_API_KEY="YOUR_API_KEY"
export GEMINI_API_KEY="YOUR_API_KEY"
```

`.env.example` 只说明变量名；程序不会自动读取本地 `.env`。网页输入的密钥只用于当前本地任务，不存入浏览器持久化存储、运行配置或日志。模型请求经 stdin 传入 worker，结果配置会脱敏。

检索还使用 Semantic Scholar、arXiv、Crossref 和可选网页搜索。`search.semantic_scholar_api_key_env` 可配置学术检索密钥；有 SerpAPI Key 时设置 `search.serpapi_api_key_env` 与 `enable_serpapi: true`。无需 SerpAPI 时保留 `enable_serpapi: false`，可使用补充公开网页搜索；不同来源可用性会影响召回，未承诺所有环境均得到相同结果。

## 命令行

先检查配置和盲测输入：

```bash
python -m idea2prior run --dataset EASY --item N001 --config configs/openai.example.json --dry-run
```

检查不发送模型或搜索请求。真实运行时填写本地配置，去掉 `--dry-run`：

```bash
python -m idea2prior run --dataset EASY --item N001 --config configs/run.local.json --timeout-seconds 1800
python -m idea2prior run --dataset MEDIUM --item C001 --config configs/run.local.json
python -m idea2prior run --dataset HARD --item C001 --config configs/run.local.json
python -m idea2prior run --query "Describe your research idea here." --config configs/run.local.json
```

每次创建新的运行目录，默认单题 30 分钟停止，不自动整题重跑。可用 `--output-dir` 修改输出位置，`--mode full_report` 启用完整报告。已安装后也可用 `idea2prior run ...`。

## Python / Notebook

```python
import json
from idea2prior import RunConfig, run_retrieval

config = RunConfig.from_dict(json.load(open("configs/run.local.json", encoding="utf-8")))
result = run_retrieval(
    "Describe your research idea here.", config,
    output_dir="outputs/python", timeout_seconds=1800,
)
for paper in result["top5"]:
    print(paper["rank"], paper["title"], paper.get("url"))
print(result["duration_seconds"], result["usage"])
```

返回状态区分成功、部分完成、失败、空候选、停止、超时与未运行；返回排名不等于指定论文必然命中。`usage.reported_token_subtotal` 是 API 已回报小计；完整 token 字段在缺失 usage 时保持 `null`，不按字符数估算费用。

Notebook：[`notebooks/idea2prior_quickstart.ipynb`](notebooks/idea2prior_quickstart.ipynb)。

```bash
python -m pip install -e ".[notebook]"
jupyter lab notebooks/idea2prior_quickstart.ipynb
```

Notebook 默认 `RUN_REAL_REQUESTS = False`。主动开启后才会调用 API；密钥可通过环境变量或 `getpass` 输入。公开分享前清除全部执行输出。

## 检索流程与两种模式

1. 从研究想法生成检索线索，合并模型种子、学术检索与可用网页来源。
2. 去重、核验论文身份、补齐元数据和摘要；评分区分 `3 高度重合 / 2 部分重合 / 1 相邻研究 / 0 不重合`。
3. 从高相关种子扩展引文邻居，由 LLM 结合研究输入、检索 query 和关键词评分筛选。
4. 构建最多 60 篇复核短名单，准备全文证据并完成最终排序。

`retrieval_eval` 是默认模式：固定尝试 5 篇全文，不因 PDF 缺失追加候补；本地提取每篇最多 6 个相关片段、每段最多 1,800 字符。Expert 接收完整短名单摘要、摘要等级、取得的全文片段与全文缺失状态，输出 Top 5。后续报告生成被省略。

`full_report` 保留 prior-art 报告流程，包含更完整的证据整理与综述输出。它与检索评测的全文规则及最终排名规则不同，不应直接混用两种模式的指标。

调用层保留已有批量元数据、HTTP 连接复用、同任务有效响应复用和受限并发，新增统一配置、角色分流与可停止的独立 worker。默认不启用自动整题重跑，也不通过缩减候选池来声称精度不变。默认最多 4 个同时执行的模型请求；可通过 `pipeline.max_in_flight_requests` 调整。Expert 与其他阶段具有独立输出预算；增加预算可能增加费用。

## 可选研究扩展

在网页的“任务”下拉列表或 Notebook 第 6 节选择扩展。它们独立运行，默认检索评测流程保持不变。

| 任务 | 输入 | 输出 |
| --- | --- | --- |
| `code_extractor` | 上传/本机 PDF、Markdown 或文本，可附算法查询 | 代码/伪代码、实现与完整性诊断、Markdown 和 JSON |
| `evolution_mapper` | 主题、论文上限及可选年份 | 引文网络、时间线、演化路径和 Mermaid 报告 |
| `planner_worker` | 研究任务，可附文档 | 计划、受限工具执行、可选 Critic 复核和逐步诊断 |
| `benchmark_landscape` | 研究主题 | 初步数据集、指标、方法及代表论文概览 |
| `slot_filling` | 主题、论文草稿片段及已引用论文 | 引用缺口建议、排序和理由 |

```bash
python -m pip install -e ".[extensions,notebook]"
# 仅选择 MinerU 时安装结构化 PDF 解析依赖：
python -m pip install -e ".[mineru]"
# 旧版向量检索，可单独提供 embedding key：
python -m pip install -e ".[pdf-rag]"
```

```python
from idea2prior import run_extension
analysis = run_extension(
    "code_extractor", config,
    {"document_path": "inputs/my_paper.pdf", "llm_analysis": True,
     "execute_code": False, "parser": "native"},
    output_dir="outputs/extensions", timeout_seconds=1800,
)
print(analysis["status"], analysis.get("error"))
```

网页可上传最多 12 MiB 的文档或填写本机路径，预览报告并下载 Markdown/JSON；Notebook 包含五个扩展的示例。默认不执行提取出的代码，`llm_analysis=False` 的本地分析不调用模型。原生 PDF 文本提取不包含仅以图像存在的内容；MinerU 可能需要下载模型。Planner–Worker 使用恢复的代码分析和演化图工具，限制步骤数，不自动重新执行。旧版基准概览/引用缺口沿用其探索流程，与 Top 5 评测协议分开。缺失依赖、文件问题、API 失败和未完成步骤均有诊断。详见[扩展说明](docs/OPTIONAL_EXTENSIONS.md)及[验证记录](docs/VALIDATION.md)。

## 公开 Benchmark

| 难度 | ID | 题数 |
|---|---|---:|
| EASY | N001–N030 | 30 |
| MEDIUM | C001–C030 | 30 |
| HARD | C001–C030 | 30 |

`MDEIUM` 拼写作为 CLI 别名接受，正式名称为 `MEDIUM`。这些标签表示设计上的预期难度，不是经大规模实测校准的难度等级。

每档包含 `queries.jsonl`、原始完整 `prompts/`、SHA-256 manifest 和独立 `gold.jsonl`。数据集运行核对查询与 prompt 的 SHA，调用原始完整盲测 prompt；Gold 仅由离线评估读取，网页不会提供答案接口。自由输入调用与数据集调用的输入表示不同。修改 Benchmark 文本后应建立新的版本，不覆盖原始查询。

核验数据：

```bash
python scripts/build_public_datasets.py
```

真实检索结束后，将 CLI 输出的 `output_dir/result.json` 路径交给评估器：

```bash
python scripts/evaluate_public_results.py --tier easy --results outputs/SELECTED_RUN/result.json --items N001 --out outputs/scores.json
```

也支持多题 JSON 数组或 JSONL，每行含 `item_id`、`status`、`top5`；重复 item 必须先明确选择一次尝试。评估区分有效排名命中、运行失败、未运行和工作家族匹配，不将缺结果推定为正确。指定目标是一篇已知正例，并不代表其他高相关论文没有价值。详细说明见 [`datasets/README.md`](datasets/README.md)。

### 评测 prompt 的结构

同一题的三个系统使用相同的完整查找 prompt，包含：①寻找对应论文的任务；②2015–2025 年发表范围及经核验预印本规则；③最多五篇、身份核验、版本去重和排序要求；④盲测研究 idea。不提供目标论文标题、DOI 或 Gold 答案作为答案线索。

```text
寻找最符合以下 idea 的研究论文。
发表范围：2015–2025；可接受经核验的同一研究版本。
按完整 idea 匹配程度返回最多五项不同研究。
每项提供准确标题、作者、年份、发表渠道、DOI/稳定链接及匹配理由。
核验权威身份，不编造引用，不用同一论文的多个版本占多个名次。
Research idea:
<盲测研究想法>
```

上面是结构示意；原始完整英文 prompt 保存在各难度的 `prompts/` 中。EASY 保留较多识别细节，MEDIUM 减少细节，HARD 的 idea 更短或更含糊。按维护者的构造记录，prompt 通过 ChatGPT 中 GPT-6 Astra 抽取和生成。首页比较使用每个难度的前十题及已说明的评分口径。

## 验证与目录

```bash
python -m pip install -e ".[test]"
python -m pytest tests_public -q
python scripts/audit_public_release.py --json
```

测试使用离线模拟 API 与本地 PDF；不会运行付费 Benchmark。当前发布的验证与限制见 [`docs/VALIDATION.md`](docs/VALIDATION.md)。

```text
idea2prior/    公开 Python API 与 CLI 入口
paperarena/    核心运行时、兼容 API、worker 与网页
pipelines/      检索、身份核验、评分与证据流程
tools/          所需检索与 PDF 工具
prompts/        引文邻居评分规则
datasets/       EASY、MEDIUM、HARD 盲测输入和独立 Gold
notebooks/      可执行使用示例
configs/        不含密钥的配置示例
scripts/        数据核验、离线评估、发布检查
tests_public/   API、网页、数据与核心流程的离线测试
docs/           使用与验证记录
```

未采用的旧实验、原始请求日志、缓存、旧版数据集目录和旧 Git 历史保留在发布目录之外。采用的三方比较回答和输出已放入 `datasets/*/results/`。本地工作文件 `.local/` 和生成的运行目录 `outputs/` 不进入公开 Git 或发布 ZIP；不要上传这些目录或私人归档。清理记录保存在私人归档中。

## 评测范围

我们仅比较**根据 idea 检索并排序论文的能力**。本次评测不衡量通用网页调查、论坛搜索、报告写作或每篇返回论文的相关性。网页和论坛搜索支持仍有限，尚未与 ChatGPT、Gemini 做相应 Benchmark。总体结果限于这些选定查询，不表示每个难度都优于所有对照。API 价格参考及历史样本费用也不代表网页版服务的真实单题费用。

## 上游项目引用

本项目基于 [PaperArena 论文](https://arxiv.org/abs/2510.10909)及其[官方代码](https://github.com/ustc-ai4science/PaperArena)扩展。使用本项目时，请同时引用上游研究；BibTeX 见[英文说明](README.md#upstream-acknowledgement-and-citation)。

## 许可与商业授权

Idea2Prior 原创部分采用 [PolyForm Noncommercial 1.0.0](LICENSE)。非商业使用按正式条款授权；**商业使用请先通过本仓库的 GitHub Issues 联系作者，并取得单独商业授权**。这是公开源码的非商业许可，不是 MIT / Apache-2.0 式的开源许可。正式英文条款优先，见 [PolyForm 官方文本](https://github.com/polyformproject/polyform-licenses/blob/1.0.0/PolyForm-Noncommercial-1.0.0.md)。

依赖项目保留各自许可。论文标题、DOI、书目信息与链接用于定位文献；本项目不重新许可第三方论文，也不分发其 PDF 或完整正文。

上游组件保留其适用许可；恢复的 CAMEL-AI RAG 源码保留 Apache-2.0，附有[原许可文本](rag/LICENSE)。详见 [NOTICE](NOTICE)。
