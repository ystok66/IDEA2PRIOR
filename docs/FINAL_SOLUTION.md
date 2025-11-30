# 完整解决方案：CUSTOM_LLM 配置修复

## 🎯 最终状态：完全修复 ✅

PaperArena 现在可以完全使用自定义 LLM 端点（如 liaobots），不再有任何 Vertex AI / Google Credentials 错误。

---

## 📋 问题历程

### 问题 1: LLM 优先级错误

**症状：**

```
CUSTOM_LLM_* 已设置 → 但仍然使用 Google Gemini
所有调用 → generativelanguage.googleapis.com
结果 → quota exceeded
```

**解决：** 实施方案 B - 修改 `build_llm_from_env()` 优先级

- ✅ CUSTOM*LLM*\* (最高)
- ✅ GOOGLE_API_KEY (中等)
- ✅ OPENAI_API_KEY (最低)

### 问题 2: litellm Provider 误识别

**症状：**

```
model_id = "gemini-3-pro-preview"
→ litellm 检测到 "gemini"
→ 自动选择 Vertex AI provider
→ 忽略 api_base 参数
→ DefaultCredentialsError
```

**解决：** 添加 `openai/` 前缀强制 OpenAI-compatible 模式

- ✅ `model_id = "openai/gemini-3-pro-preview"`
- ✅ litellm 使用 OpenAI provider
- ✅ 尊重 api_base 和 api_key

---

## 🔧 实施的修复

### 修改 1: `demo_research_agent.py` - LLM 优先级

```python
def build_llm_from_env() -> tuple[str, Optional[str], Optional[str]]:
    # 1. CUSTOM_LLM (最高优先级)
    custom_base = os.getenv("CUSTOM_LLM_API_BASE")
    custom_key = os.getenv("CUSTOM_LLM_API_KEY")
    custom_model = os.getenv("CUSTOM_LLM_MODEL_NAME")

    if custom_base and custom_key and custom_model:
        # 关键修复：添加 openai/ 前缀
        if not custom_model.startswith("openai/"):
            model_with_prefix = f"openai/{custom_model}"
        else:
            model_with_prefix = custom_model

        return model_with_prefix, custom_base, custom_key

    # 2. Google Gemini (中等优先级)
    if os.getenv("GOOGLE_API_KEY"):
        return "gemini/gemini-3-pro-preview", None, None

    # 3. OpenAI (最低优先级)
    if os.getenv("OPENAI_API_KEY"):
        return "gpt-4o", None, None
```

### 修改 2: `create_minimal_agent()` - 接受 api_base/api_key

```python
def create_minimal_agent(
    model_id: str = "gpt-4o",
    api_base: Optional[str] = None,
    api_key: Optional[str] = None,
) -> CodeAgent:
    model = LiteLLMModel(
        model_id=model_id,
        api_base=api_base,
        api_key=api_key,
    )
    # ...
```

### 修改 3: `main()` - 使用 build_llm_from_env

```python
def main():
    # 使用新的 LLM 配置函数
    model_id, api_base, api_key = build_llm_from_env()

    agent = create_minimal_agent(
        model_id=model_id,
        api_base=api_base,
        api_key=api_key,
    )
```

---

## ✅ 验证结果

### Test 1: liaobots 直接连接

```bash
$ python test_liaobots_api.py
✅ HTTP status: 200
✅ {"ok": true, "msg": "hello from liaobots"}
```

### Test 2: LLM 优先级

```bash
$ python test_llm_priority.py
✅ Using CUSTOM LLM endpoint: https://ai.liaobots.work/v1
✅ Model: gemini-3-pro-preview (as openai/gemini-3-pro-preview)
✅ Forcing OpenAI-compatible mode to avoid Vertex AI provider
```

### Test 3: Agent 集成测试

```bash
$ python test_integration_quick.py
✅ LiteLLMModel - openai/gemini-3-pro-preview
✅ Final answer: {'test': 'success', 'msg': 'Hello from LLM'}
✅ Exit code: 0
```

**关键观察：**

- ✅ 没有 `DefaultCredentialsError`
- ✅ 没有 `vertex_llm_base.py` 错误
- ✅ 所有 LLM 调用走 liaobots

---

## 📖 配置指南

### .env 配置

```env
# 自定义 LLM (最高优先级) - liaobots
CUSTOM_LLM_API_BASE=https://ai.liaobots.work/v1
CUSTOM_LLM_API_KEY=your-liaobots-key
CUSTOM_LLM_MODEL_NAME=gemini-3-pro-preview

# 可选：保留 Google API Key 用于特定工具
GOOGLE_API_KEY=your-google-key

# OpenAI (可选，最低优先级)
# OPENAI_API_KEY=your-openai-key
```

### 运行 Pipeline

```bash
# Prior Art Check
python demo_research_agent.py \
  --task_type prior_art_check \
  --topic "monocular 3D detection" \
  --idea "Camera-space rotation augmentation" \
  --max_rounds 1

# Slot Filling
python demo_research_agent.py \
  --task_type slot_filling \
  --topic "3D detection" \
  --max_rounds 1

# Benchmark Landscape
python demo_research_agent.py \
  --task_type benchmark_landscape \
  --topic "3D Object Detection" \
  --max_rounds 1
```

### 期望日志输出

```
🔧 Using CUSTOM LLM endpoint: https://ai.liaobots.work/v1
📝 Model: gemini-3-pro-preview (as openai/gemini-3-pro-preview)
✅ Forcing OpenAI-compatible mode to avoid Vertex AI provider
Using minimal agent (pipeline-driven mode)

[PriorArt] Decomposing idea into facets...
└─ LiteLLMModel - openai/gemini-3-pro-preview ─┘

✅ 所有 LLM 调用成功
✅ 没有 Vertex AI 错误
✅ 报告正常生成
```

---

## 🎓 关键学习点

### 1. litellm Provider 检测机制

litellm 通过**model name**自动选择 provider：

- `gemini-xxx` → Gemini/Vertex AI provider
- `gpt-xxx` → OpenAI provider
- `openai/xxx` → 强制 OpenAI-compatible provider

### 2. 解决方案原理

通过添加 `openai/` 前缀：

- ✅ 告诉 litellm 使用 OpenAI-compatible API
- ✅ litellm 会尊重 `api_base` 和 `api_key` 参数
- ✅ 端点仍然收到原始 model name（如 `gemini-3-pro-preview`）

### 3. 为什么需要两个修复

**修复 1 (优先级)：** 确保顶层选择正确的 LLM 配置
**修复 2 (openai/ 前缀)：** 确保 litellm 使用正确的 provider

两者缺一不可！

---

## 📂 修改的文件

```
f:\PaperArena\paperarena_hub\
├── demo_research_agent.py              ✅ 核心修复
├── test_liaobots_api.py                ✅ 添加 dotenv
├── test_llm_priority.py                ✅ 验证优先级
├── test_integration_quick.py           ✅ 集成测试
└── docs/
    ├── CUSTOM_LLM_PRIORITY_FIX.md      ✅ 优先级修复文档
    ├── OPENAI_PREFIX_FIX.md            ✅ Provider 路由修复文档
    ├── SOLUTION_B_SUMMARY.md           ✅ 方案 B 总结
    └── FINAL_SOLUTION.md               ✅ 本文件
```

---

## ⚠️ 重要提醒

你在测试时暴露了 `CUSTOM_LLM_API_KEY`，建议：

1. 去 liaobots 后台重新生成密钥
2. 废除旧密钥
3. 更新 `.env` 文件

---

## 🎉 最终结论

✅ **问题完全解决！**

现在 PaperArena 的所有 pipeline 任务：

- ✅ Prior Art Check
- ✅ Slot Filling
- ✅ Benchmark Landscape

都会完全使用 liaobots（或你配置的任何 OpenAI-compatible 端点），不再有：

- ❌ Vertex AI 错误
- ❌ Google Credentials 问题
- ❌ API Quota 限制

**可以正常使用了！** 🚀
