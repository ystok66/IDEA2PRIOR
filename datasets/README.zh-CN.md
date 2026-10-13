# Public benchmark tiers

| Tier | Original input | IDs | Queries |
| --- | --- | --- | ---: |
| EASY | Idea2Paper Niche30 v0.2, A | N001–N030 | 30 |
| MEDIUM | Idea2Paper Fuzzy30 v0.5, A | C001–C030 | 30 |
| HARD | Idea2Paper Fuzzy30 v0.5, B | C001–C030 | 30 |

EASY、MEDIUM、HARD 是按提示信息量命名的预期难度，不代表全部 30 题的实测难度已经校准。三个集合的目标论文并非三个独立样本池，尤其 MEDIUM 与 HARD 使用同一组目标、不同查询。原始查询的排序也保留了；使用 ID 选题，不要假定 EASY 文件第一个记录就是 N001。

每层包含 `queries.jsonl`、`prompts/<ID>.txt`、`gold.jsonl` 和 `manifest.json`。`queries.jsonl` 的每行包含 `item_id`、原始 `query`、相对路径 `prompt_file` 和两个 SHA-256。提示词文件保留原始字节；查询字符串保留原文。`manifest.json` 保存原始来源文件及公开文件的哈希，可以检查版本与输入是否被改动。

检索程序只读 `queries.jsonl` 和 `prompts/`。**`gold.jsonl` 只供运行结束后的本地评估使用**，含指定论文的题名、DOI、被原数据集接受的 arXiv 记录及身份歧义说明。不要把 gold、标准答案、其他系统的答案或以前的同题聊天带入检索会话。公开 gold 也意味着本项目不是隐藏测试集。

指定论文是一个已知正例，宽泛查询还可能适配其他研究。Hit@5 衡量是否找回指定论文，不能单独证明其他候选无效；语义匹配和同一工作不同版本的关系仍需要人工核验。arXiv ID 是记录级关联，历史版本与期刊论文的关系也需要核验。原 v0.5 里的 `family_aliases` 保留在评估文件中；严格目标命中与同一工作家族命中分开统计。

这些文件包含原始查询与提示词、参考论文书目信息、短的策划说明、提供的网页模型回答及最终采用的流程输出，不包含下载的论文 PDF、提取全文缓存、密钥或原始请求日志。第三方论文和提供商内容保留各自权利。

运行 `python scripts/build_public_datasets.py` 可离线核验已公开的文件与哈希。开发时可用 `--source-root <原始资料所在上级目录>` 重建这三个目录。正常调用公开数据集无需原始归档。构建工具不会修改原始资料。

运行后可离线计算严格目标 Hit@5，例如：

```bash
python scripts/evaluate_public_results.py --tier easy --results outputs/results.jsonl --items N001 N002 N003 --out outputs/scores.json
```

结果行包含 `item_id`、`status` 和 `top5` 数组，候选含 `title`、`doi`、`arxiv_id` 或 `url`。`--track designated` 为默认严格目标；`--track family` 另计原参考文件显式记录的工作家族。匹配是确定性的身份对照，不调用模型；题名匹配和 arXiv 历史版本仍需人工确认。缺失运行保留 NOT_RUN，不会补记成功。

## 历史三方结果

每种难度的 `results/` 目录包含 ChatGPT、Gemini 的原始网页结果 TXT，我们最终采用的 Top 5 TXT、结构化 JSON、逐题原始 Expert 输出与哈希清单。ChatGPT HARD 使用已采用的 NEW 报告。结果仅用于检索结束后的查看和评测，运行时不会读取。参见[结果文件索引](README.md#historical-comparison-outputs)。
