"use strict";
const $ = id => document.getElementById(id);
const texts = {
  en: {
    title: "Idea2Prior · Find papers from an idea", brandTag: "RESEARCH WORKSPACE", localWorkspace: "Local workspace", language: "Interface language",
    eyebrow: "IDEA → EVIDENCE → TOP 5", heading: "Turn a research idea into a shortlist.", introCopy: "Search, verify, and rank papers. Choose the models for each stage.", introLabel: "Reproducible literature retrieval",
    researchIdea: "Research idea", customInput: "Write an idea", benchmark: "Benchmark", selectItem: "Select an item", blindInputOnly: "Only blind research inputs are loaded.",
    queryPlaceholder: "Describe the research problem, approach, or assumptions. You can also choose a benchmark item.", queryLanguages: "English or Chinese", characters: "{count} characters",
    modelsAndApi: "Models & API", addApi: "＋ Add API", modelHelp: "The default setup uses Gemini Flash Lite for retrieval and scoring, and Gemini Flash for Expert ranking. You can change every model and provider.",
    heavyRole: "Retrieval & abstract scoring", lightRole: "Keywords & light tasks", expertRole: "Expert ranking", retrievalProfile: "Gemini · Retrieval", expertProfile: "Gemini · Expert",
    provider: "Provider", customProvider: "Custom / OpenAI compatible", modelChoices: "Model choices", modelId: "Model ID · editable", modelPlaceholder: "Enter your model ID", manualModel: "Manual / custom model ID",
    apiKey: "API key", keyPlaceholder: "Used only in this local session", baseUrl: "API base URL", remove: "Remove", loadModels: "Load models", loadingModels: "Loading…",
    catalogHelp: "Enter an API key (and a custom API URL when applicable) to detect models automatically. Manual model IDs are always supported.", catalogLoaded: "{count} models loaded from this API.", catalogEmpty: "No generation models were returned. Enter a model ID manually.", catalogPending: "Checking model configuration…", catalogLoading: "Detecting available models…", catalogInvalidUrl: "Enter a valid HTTP(S) API URL without embedded credentials or query parameters.", catalogAborted: "Model lookup cancelled.",
    catalogFailed: "Unable to load models. Check the key and endpoint, or enter a model ID manually.", catalogMissingKey: "Enter an API key before loading models.", catalogPageLimit: "The model catalog exceeds the page limit. Enter a model ID manually.",
    reuseKey: "Reuse the API key from {name}", reuseKeyHelp: "Both profiles use the same native Gemini key. Their models can differ.", ownKeyHelp: "Only profiles assigned to a role need an API key to run.",
    retrievalSettings: "Retrieval settings & output", outputMode: "Output mode", top5Mode: "Retrieval evaluation · Top 5", reportMode: "Full research report", timeLimit: "Time limit per item (minutes)",
    serpapiKey: "SerpAPI key (optional)", serpapiPlaceholder: "Use public search sources when blank", s2Key: "Semantic Scholar key (optional)", s2Placeholder: "For authenticated scholarly retrieval", supplementarySearch: "Enable supplementary web search",
    top5Protocol: "Top 5 mode reviews up to 60 shortlisted papers and makes five fixed full-text attempts. Missing full text retains abstract evidence, without replacements.",
    startRetrieval: "Start retrieval", checkConfig: "Check configuration", stopRun: "Stop run", privacy: "API keys stay in memory for this local session and are not saved to browser storage or result files. Starting retrieval calls the selected APIs.",
    retrievalResults: "Retrieval results", waiting: "Ready when you are", elapsed: "Elapsed", reportedTokens: "Reported tokens", rankedPapers: "Ranked papers", preparing: "Preparing",
    runningDetail: "You can stop at any time. Results and stage records stay local.", emptyHeading: "Relevant work, backed by evidence.", emptyCopy: "Enter an idea and configure your API. Ranked candidate papers will appear here.",
    identityCheck: "Identity checks", abstractScoring: "Abstract scoring", pdfExcerpts: "PDF excerpts", expertRanking: "Expert ranking", downloadJson: "Download result JSON", runInformation: "Run information",
    footerProject: "Idea2Prior · Research, experiments & comparison", footerLicense: "Noncommercial use · Contact the author for commercial licensing",
    keepProfile: "Keep at least one API profile.", missingModel: "Enter the model ID for {name}.", missingKey: "Enter an API key for the assigned profile {name}.", missingBase: "Enter the API base URL for {name}.", keyCycle: "Enter a key in one Gemini profile before reusing it.",
    missingQuery: "Enter a research idea or select a benchmark item.", invalidTime: "The time limit must be between 1 and 120 minutes.", requestFailed: "The local request failed. Check your configuration and local server.",
    datasetUnavailable: "Benchmark data is unavailable. You can still write your own idea.", datasetDescription: "{label} · {count} items · blind research inputs only", unknown: "Unknown", missingTitle: "Title unavailable",
    evidenceMetadata: "Metadata only", evidenceAbstract: "Complete abstract", evidenceSnippets: "Selected PDF excerpts", evidenceFulltext: "Full PDF text", evidenceAbstractSnippets: "Abstract + PDF excerpts", evidenceAbstractFulltext: "Abstract + full PDF text", evidenceAvailable: "Available abstract / PDF evidence",
    dryRunNote: "Configuration checked without retrieval or generation calls. Credentials and model availability have not been tested. Model catalog lookups are separate.",
    resultNote: "Rankings use abstracts and available PDF evidence. Missing token usage stays unknown. Full reports, if requested, are saved in the run directory.",
    failureNote: "The run did not produce a validated ranking. Check the local run records; no automatic rerun was started.", timeoutNote: "The time limit was reached. Existing run records were retained; no automatic rerun was started.", cancelledNote: "The run was stopped. Existing results and stage records were retained.",
    partialNote: "A ranking was produced with incomplete checks. Review the diagnostics and evidence scope for each paper; no automatic rerun was started.", emptyNote: "No eligible candidates were available for ranking. This is not an adjudicated benchmark miss.", resumingJob: "Restoring run progress…", resumeUnavailable: "This previous job is unavailable in the local server. No new run was started.",
    diagnostics: "Run diagnostics", failureReason: "Reason", failureStage: "Stage", failureCode: "Error code", httpStatus: "HTTP status", adapterHttpStatus: "Adapter HTTP status", withheldDetails: "Sensitive provider details were withheld.",
    additionalDiagnostics: "Additional diagnostics ({count} groups)", diagnosticCount: "×{count} records", diagnosticRecordsOnly: "Repeated entries are grouped. Counts use the largest supplied source count; recent request lists may be incomplete. These are not additional API calls.", diagnosticTruncated: "Showing {shown} of {total} groups.",
    causeAuth: "The selected API rejected its credentials. Check the key and its permissions.", causeRateLimit: "The selected API rate limit was reached. Adjust the request rate or wait before starting a new run.", causeModel: "The selected model is unavailable or unsupported on this API. Choose an available model ID.", causeApiTimeout: "An API request timed out. Existing run records were retained.", causeNetwork: "An API connection failed. Check connectivity and API availability.", causeInvalidResponse: "The model response could not be validated as the required structured output.", causeBudget: "The configured request limit was reached.",
    causeBilling: "The provider reports exhausted prepaid API credits. Check project billing before another run.", causeRouting: "The API has no available route for this model request. Check model availability at that provider.", causePermission: "The API denied access. Check this key's permissions and model access.", causeContext: "The model context limit was exceeded. Select a model with sufficient context.", causeRequest: "The API rejected the request parameters or format. Check model and protocol compatibility.", causeProviderFailure: "The API returned a server error. Existing run records were retained.", causeDependency: "A required Python dependency is unavailable. Install the project's declared dependencies.", causeLocalFile: "A local file could not be read or written. Check paths and directory permissions.", causeConfiguration: "Configuration or response validation failed. Check API profiles and the recorded stage.", causeMissingKey: "A selected API profile has no key. Enter its key or set its configured environment variable.", causeConfigJson: "The configuration file is not valid JSON. Check its syntax.", causePipeline: "A pipeline stage could not finish. Inspect the local records for the recorded stage.",
    causeFulltextReview: "Some full-text material was unavailable or could not be validated. Available abstract evidence was retained.", causeAbstractReview: "Some abstract assessments failed or returned invalid output. Unvalidated grades are not successful scores.", phaseFulltextMaterial: "Full-text material", phasePdfIdentity: "PDF identity checks", phaseFulltextReview: "Full-text review", phaseRetrievalHttp: "Scholarly service requests",
    statusQueued: "Queued", statusStarting: "Preparing", statusRunning: "Retrieving", statusComplete: "Completed", statusPartial: "Partially complete", statusFailed: "Run failed", statusCancelled: "Stopped", statusTimeout: "Timed out", statusEmpty: "No candidates", statusChecked: "Configuration checked",
    phaseConfiguration: "Checking configuration", phaseStarting: "Starting retrieval", phaseRetrieval: "Retrieving papers", phaseSeed: "Finding seed papers", phaseWeb: "Searching the web", phaseIdentity: "Verifying paper identities", phaseQueries: "Planning search queries", phaseExploration: "Exploring related work", phaseIdea: "Analyzing the research idea", phaseCandidates: "Retrieving candidate papers", phaseRefinement: "Refining search queries", phaseKeywords: "Identifying relevant keywords", phasePatterns: "Preparing evidence review", phaseAbstracts: "Scoring abstracts", phaseCitations: "Exploring citation neighbors", phasePdf: "Reviewing five fixed PDFs", phaseExpert: "Expert ranking", phaseFinished: "Finished"
  },
  zh: {
    title: "Idea2Prior · 从想法找到论文", brandTag: "研究工作台", localWorkspace: "本地工作台", language: "界面语言",
    eyebrow: "想法 → 证据 → TOP 5", heading: "从一个研究想法，找到相关论文。", introCopy: "检索、核验与排序。为每个阶段选择合适的模型。", introLabel: "可复现的文献检索",
    researchIdea: "研究想法", customInput: "自由输入", benchmark: "基准测试", selectItem: "选择题目", blindInputOnly: "只加载盲测研究想法。",
    queryPlaceholder: "描述研究问题、方法或关键假设。也可以从基准测试中选择一道题。", queryLanguages: "中文或英文均可", characters: "{count} 字符",
    modelsAndApi: "模型与 API", addApi: "＋ 添加 API", modelHelp: "默认使用 Gemini Flash Lite 检索与评分，由 Gemini Flash 完成 Expert 排序。模型和提供商均可修改。",
    heavyRole: "检索与摘要评分", lightRole: "关键词与轻量任务", expertRole: "Expert 最终排序", retrievalProfile: "Gemini · 检索", expertProfile: "Gemini · Expert",
    provider: "提供商", customProvider: "自定义 / OpenAI 兼容", modelChoices: "模型选项", modelId: "模型 ID · 可手动修改", modelPlaceholder: "填写你的模型 ID", manualModel: "手动 / 自定义模型 ID",
    apiKey: "API Key", keyPlaceholder: "仅在当前本地会话中使用", baseUrl: "API Base URL", remove: "移除", loadModels: "加载模型", loadingModels: "加载中…",
    catalogHelp: "填写密钥后会自动识别模型；自定义 API 还需要有效的 URL。也可直接手动填写模型 ID。", catalogLoaded: "已从此 API 加载 {count} 个模型。", catalogEmpty: "未返回可生成内容的模型，请手动填写模型 ID。", catalogPending: "检查模型配置中…", catalogLoading: "正在识别可用模型…", catalogInvalidUrl: "请填写有效的 HTTP(S) API URL，不要包含凭据或查询参数。", catalogAborted: "模型查询已取消。",
    catalogFailed: "无法加载模型。请检查密钥和端点，或手动填写模型 ID。", catalogMissingKey: "请先输入 API Key，再加载模型。", catalogPageLimit: "模型列表超过分页上限，请手动填写模型 ID。",
    reuseKey: "复用 {name} 的 API Key", reuseKeyHelp: "两个配置使用同一个原生 Gemini 密钥，但可以选择不同模型。", ownKeyHelp: "开始运行时，只要求已分配角色的 API 配置提供密钥。",
    retrievalSettings: "检索设置与运行模式", outputMode: "输出模式", top5Mode: "检索评测 · Top 5", reportMode: "完整研究报告", timeLimit: "每题时间上限（分钟）",
    serpapiKey: "SerpAPI Key（可选）", serpapiPlaceholder: "留空时使用可用的公开检索来源", s2Key: "Semantic Scholar Key（可选）", s2Placeholder: "用于已认证的学术检索", supplementarySearch: "启用补充网页检索",
    top5Protocol: "Top 5 模式复核最多 60 篇短名单，固定尝试 5 篇全文；缺失全文保留摘要证据，不追加候补。",
    startRetrieval: "开始检索", checkConfig: "检查配置", stopRun: "停止运行", privacy: "密钥仅用于当前本地会话，不保存在浏览器存储或结果文件中。开始检索会调用所选 API。",
    retrievalResults: "检索结果", waiting: "等待开始", elapsed: "用时", reportedTokens: "已回报 tokens", rankedPapers: "候选结果", preparing: "准备任务",
    runningDetail: "可随时停止；结果和阶段状态会保留在本地。", emptyHeading: "让相关工作有据可查。", emptyCopy: "输入想法并配置 API 后，候选论文将在这里按相关性排列。",
    identityCheck: "身份核验", abstractScoring: "摘要评分", pdfExcerpts: "PDF 片段", expertRanking: "Expert 排序", downloadJson: "下载结果 JSON", runInformation: "运行信息",
    footerProject: "Idea2Prior · 研究、实验与比较", footerLicense: "非商业使用许可 · 商业授权请联系作者",
    keepProfile: "至少保留一个 API 配置。", missingModel: "请填写 {name} 的模型 ID。", missingKey: "请为已分配角色的 {name} 填写 API Key。", missingBase: "请填写 {name} 的 API Base URL。", keyCycle: "请先在一个 Gemini 配置中填写密钥，再让其他配置复用。",
    missingQuery: "请先输入研究想法或选择基准测试题目。", invalidTime: "运行时间上限应为 1–120 分钟。", requestFailed: "本地服务请求失败，请检查配置及本地服务。",
    datasetUnavailable: "基准测试数据尚未就绪，仍可自由输入。", datasetDescription: "{label} · {count} 题 · 只加载盲测研究想法", unknown: "未知", missingTitle: "未提供标题",
    evidenceMetadata: "仅元数据", evidenceAbstract: "完整摘要", evidenceSnippets: "选取的 PDF 片段", evidenceFulltext: "完整 PDF 文本", evidenceAbstractSnippets: "摘要与 PDF 片段", evidenceAbstractFulltext: "摘要与完整 PDF 文本", evidenceAvailable: "可用摘要 / PDF 证据",
    dryRunNote: "配置格式检查完成，未执行检索或生成调用；尚未验证真实凭据与模型可用性。模型列表查询单独进行。",
    resultNote: "结果依据摘要与可获得的 PDF 证据。token 缺失时保留未知，不由文本长度估算。完整报告（若选择）保存在运行目录。",
    failureNote: "本次未产生有效排名。请查看本地运行记录，没有自动重跑。", timeoutNote: "已达到时间上限。现有运行记录已保留，没有自动重跑。", cancelledNote: "运行已停止，现有结果与阶段记录已保留。",
    partialNote: "已生成排名，但部分检查尚未完成。请查看诊断及每篇论文的证据范围；没有自动重跑。", emptyNote: "暂无符合条件的候选论文可参与排序，这不代表已经判定基准测试未命中。", resumingJob: "正在恢复运行进度…", resumeUnavailable: "本地服务中已找不到此前的任务，没有启动新的运行。",
    diagnostics: "运行诊断", failureReason: "原因", failureStage: "失败阶段", failureCode: "错误代码", httpStatus: "HTTP 状态", adapterHttpStatus: "适配层 HTTP 状态", withheldDetails: "敏感的提供商详情已隐藏。",
    additionalDiagnostics: "其他诊断（{count} 组）", diagnosticCount: "×{count} 条记录", diagnosticRecordsOnly: "重复记录已合并；计数取各来源中较大的已提供记录数，近期请求列表可能不完整。它们不是额外 API 调用次数。", diagnosticTruncated: "显示 {total} 组中的 {shown} 组。",
    causeAuth: "所选 API 拒绝了认证凭据，请检查密钥及其权限。", causeRateLimit: "所选 API 已达到请求速率限制，请调整速率或等待后另行启动。", causeModel: "所选模型在此 API 上不可用或不受支持，请选择可用的模型 ID。", causeApiTimeout: "API 请求超时，现有运行记录已保留。", causeNetwork: "API 连接失败，请检查网络及 API 可用性。", causeInvalidResponse: "模型响应未能通过所需结构化输出的校验。", causeBudget: "已达到配置的请求次数上限。",
    causeBilling: "提供商报告预付 API 额度已耗尽，请先检查项目账单再进行新的运行。", causeRouting: "此 API 暂无可用路由处理该模型请求，请核对该提供商的模型可用性。", causePermission: "API 拒绝了访问，请检查密钥权限及模型访问许可。", causeContext: "已超过模型上下文上限，请选择上下文容量足够的模型。", causeRequest: "API 拒绝了请求参数或格式，请检查模型及协议兼容性。", causeProviderFailure: "API 返回了服务端错误，现有运行记录已保留。", causeDependency: "缺少所需 Python 依赖，请安装项目声明的依赖。", causeLocalFile: "无法读取或写入本地文件，请检查路径及目录权限。", causeConfiguration: "配置或响应校验失败，请检查 API 配置及记录中的失败阶段。", causeMissingKey: "已选择的 API 配置没有密钥，请填写密钥或设置其环境变量。", causeConfigJson: "配置文件不是有效的 JSON，请检查文件语法。", causePipeline: "一个流程阶段未能完成，请查看该阶段的本地记录。",
    causeFulltextReview: "部分全文材料无法取得或未能通过校验，已有摘要证据保留。", causeAbstractReview: "部分摘要评分失败或输出无效，未通过校验的等级不视为有效评分。", phaseFulltextMaterial: "全文材料", phasePdfIdentity: "PDF 身份校验", phaseFulltextReview: "全文复核", phaseRetrievalHttp: "学术检索接口",
    statusQueued: "等待运行", statusStarting: "准备任务", statusRunning: "检索中", statusComplete: "已完成", statusPartial: "部分完成", statusFailed: "运行失败", statusCancelled: "已停止", statusTimeout: "已超时", statusEmpty: "暂无候选", statusChecked: "配置已检查",
    phaseConfiguration: "检查配置", phaseStarting: "开始检索", phaseRetrieval: "检索论文", phaseSeed: "寻找种子论文", phaseWeb: "网页搜索", phaseIdentity: "核验论文身份", phaseQueries: "规划搜索查询", phaseExploration: "探索相关研究", phaseIdea: "分析研究想法", phaseCandidates: "检索候选论文", phaseRefinement: "完善搜索查询", phaseKeywords: "整理相关关键词", phasePatterns: "准备证据复核", phaseAbstracts: "摘要评分", phaseCitations: "扩展引文邻居", phasePdf: "复核固定的五篇 PDF", phaseExpert: "Expert 排序", phaseFinished: "已结束"
  }
};
let locale = "en";
try { if (localStorage.getItem("paperarena.locale") === "zh") locale = "zh"; } catch (_) { /* Storage is optional. */ }
let profiles = [], profileCounter = 0, datasets = [], difficulty = "easy", benchmarkMode = false;
let currentJob = null, currentResult = null, currentSnapshot = null, polling = null, formError = null;
const modelCache = new Map(); // Includes credential bindings; memory only, never persisted.
const recommendedGeminiModels = ["gemini-3.1-flash-lite", "gemini-3.8-flash"];
const terminals = new Set(["COMPLETED", "PARTIAL", "FAILED", "CANCELLED", "TIMEOUT", "DRY_RUN", "SUCCEEDED", "DONE", "FINISHED", "NOT_RUN", "EMPTY"]);
const statusKeys = {QUEUED:"statusQueued",STARTING:"statusStarting",RUNNING:"statusRunning",COMPLETED:"statusComplete",SUCCEEDED:"statusComplete",DONE:"statusComplete",FINISHED:"statusComplete",PARTIAL:"statusPartial",FAILED:"statusFailed",CANCELLED:"statusCancelled",TIMEOUT:"statusTimeout",EMPTY:"statusEmpty",DRY_RUN:"statusChecked",NOT_RUN:"statusChecked"};
const phaseKeys = {configuration:"phaseConfiguration",validated:"phaseConfiguration",starting_pipeline:"phaseStarting",retrieval:"phaseRetrieval",seed_recall:"phaseSeed",web_search:"phaseWeb",identity_verification:"phaseIdentity",search_queries:"phaseQueries",exploration:"phaseExploration",idea_analysis:"phaseIdea",idea_features:"phaseIdea",idea_facets:"phaseIdea",candidate_retrieval:"phaseCandidates",query_refinement:"phaseRefinement",keywords:"phaseKeywords",evidence_patterns:"phasePatterns",abstract_scoring:"phaseAbstracts",citation_expansion:"phaseCitations",fixed_five_pdf_review:"phasePdf",expert_ranking:"phaseExpert",finished:"phaseFinished",queued:"statusQueued",cancelled:"statusCancelled",cancelled_before_start:"statusCancelled",deadline:"statusTimeout",failed:"statusFailed",worker_exit:"statusFailed",runner_error:"statusFailed"};
Object.assign(phaseKeys,{fulltext_material:"phaseFulltextMaterial",expert_fulltext_material_validation:"phasePdfIdentity",fulltext_refinement:"phaseFulltextReview",retrieval_http:"phaseRetrievalHttp"});
function t(key, values={}) { return (texts[locale][key] || texts.en[key] || key).replace(/\{(\w+)\}/g, (_,name)=>String(values[name] ?? "")); }
class UIError extends Error { constructor(key, values={}, diagnostic=null) { super(t(key,values)); this.key=key; this.values=values; this.diagnostic=diagnostic; } }
function safeDiagnosticText(value) {
  if(typeof value!=="string")return "";let result=value;
  profiles.forEach(profile=>{const key=profile.card.querySelector(".key").value.trim();if(key)result=result.split(key).join("[REDACTED]");});
  ["serpapi-key","s2-key"].forEach(id=>{const key=$(id).value.trim();if(key)result=result.split(key).join("[REDACTED]");});
  result=result.replace(/https?:\/\/\S+/gi,"[endpoint hidden]").replace(/\bBearer\s+[^\s,;]+/gi,"Bearer [REDACTED]").replace(/\b(?:sk-[A-Za-z0-9_-]{8,}|AIza[A-Za-z0-9_-]{15,})\b/g,"[REDACTED]");
  if(/^\s*[\[{]/.test(result) || /(?:authorization|api[_ -]?key|access[_ -]?token)\s*[:=]/i.test(result))return t("withheldDetails");
  return result.replace(/[\r\n\t]+/g," ").slice(0,700);
}
function diagnostic(error, phase=null) {
  if(!error)return null;if(typeof error==="string")error={message:error};if(typeof error!=="object")return null;
  const token=value=>typeof value==="string" && /^[A-Za-z0-9_.:-]{1,96}$/.test(value) && safeDiagnosticText(value)===value?value:null;
  const http=error.http_status ?? error.status_code,adapterHttp=error.adapter_http_status,code=token(error.code || error.type),stage=token(error.phase || error.stage || phase);
  return {message:safeDiagnosticText(error.message || error.reason || ""),code,phase:stage,missing_dependency:token(error.missing_dependency),install_hint:safeDiagnosticText(error.install_hint || ""),http_status:Number.isInteger(http)&&http>=100&&http<=599?http:null,adapter_http_status:Number.isInteger(adapterHttp)&&adapterHttp>=100&&adapterHttp<=599?adapterHttp:null};
}
function diagnosticMessage(detail) {
  if(!detail)return "";const code=(detail.code || "").toUpperCase(),status=detail.http_status;
  const causes={AUTHENTICATION_REJECTED:"causeAuth",AUTHENTICATION_ERROR:"causeAuth",AUTHENTICATIONERROR:"causeAuth",PERMISSION_DENIED:"causePermission",PERMISSIONDENIEDERROR:"causePermission",INVALID_API_KEY:"causeAuth",RATE_LIMITED:"causeRateLimit",RATE_LIMIT_ERROR:"causeRateLimit",RATELIMITERROR:"causeRateLimit",MODEL_UNAVAILABLE:"causeModel",MODEL_NOT_FOUND:"causeModel",INVALID_MODEL:"causeModel",PROVIDER_TIMEOUT:"causeApiTimeout",API_TIMEOUT:"causeApiTimeout",APITIMEOUTERROR:"causeApiTimeout",TIMEOUTERROR:"causeApiTimeout",NETWORK_CONNECTION_FAILED:"causeNetwork",CONNECTION_ERROR:"causeNetwork",APICONNECTIONERROR:"causeNetwork",INVALID_MODEL_RESPONSE:"causeInvalidResponse",INVALID_RESPONSE:"causeInvalidResponse",OUTPUT_VALIDATION_ERROR:"causeInvalidResponse",ATTEMPTLIMITERROR:"causeBudget",DEADLINEEXCEEDED:"timeoutNote",BILLING_CREDITS_EXHAUSTED:"causeBilling",PROVIDER_ROUTE_UNAVAILABLE:"causeRouting",CONTEXT_LIMIT_EXCEEDED:"causeContext",INVALID_API_REQUEST:"causeRequest",PROVIDER_FAILURE:"causeProviderFailure",RUNTIME_DEPENDENCY_MISSING:"causeDependency",LOCAL_FILE_ERROR:"causeLocalFile",INVALID_CONFIGURATION_OR_RESPONSE:"causeConfiguration",MISSING_API_KEY:"causeMissingKey",INVALID_CONFIG_JSON:"causeConfigJson",PIPELINE_ERROR:"causePipeline"};
  Object.assign(causes,{FULLTEXT_REVIEW_INCOMPLETE:"causeFulltextReview",ABSTRACT_SCORING_INCOMPLETE:"causeAbstractReview",PARTIAL_COMPLETION:"partialNote",NO_CANDIDATES:"emptyNote"});
  const key=causes[code] || (status===401?"causeAuth":status===403?"causePermission":status===429?"causeRateLimit":null),summary=key?t(key):null;
  const message=summary?[summary,detail.message && detail.message!==summary && detail.message!==t("withheldDetails")?detail.message:null].filter(Boolean).join(" "):detail.message || t("failureNote");return [message,detail.missing_dependency?`[${detail.missing_dependency}]`:null,detail.install_hint].filter(Boolean).join(" ");
}
function phaseText(phase) { return phaseKeys[phase]?t(phaseKeys[phase]):safeDiagnosticText(phase) || t("phaseRetrieval"); }
function diagnosticSuffix(detail) { if(!detail)return "";return [detail.phase?`${t("failureStage")}: ${phaseText(detail.phase)}`:null,detail.code?`${t("failureCode")}: ${detail.code}`:null,detail.http_status?`${t("httpStatus")}: ${detail.http_status}`:null,detail.adapter_http_status?`${t("adapterHttpStatus")}: ${detail.adapter_http_status}`:null].filter(Boolean).join(" · "); }
function showError(error) { formError=error || null;const detail=error?.diagnostic;$("form-error").textContent=error ? [t(error.key || "requestFailed",error.values),detail?diagnosticMessage(detail):null,detail?diagnosticSuffix(detail):null].filter(Boolean).join(" ") : ""; $("form-error").classList.toggle("hidden",!error); }
function applyTranslations(root=document) {
  root.querySelectorAll("[data-i18n]").forEach(node=>{node.textContent=t(node.dataset.i18n);});
  root.querySelectorAll("[data-i18n-placeholder]").forEach(node=>{node.placeholder=t(node.dataset.i18nPlaceholder);});
  root.querySelectorAll("[data-i18n-aria]").forEach(node=>{node.setAttribute("aria-label",t(node.dataset.i18nAria));});
}
function profileName(profile) { return profile.labelKey ? t(profile.labelKey) : profile.name; }
function setLocale(value) {
  locale=value === "zh" ? "zh" : "en";
  try { localStorage.setItem("paperarena.locale",locale); } catch (_) { /* Never store config or credentials. */ }
  document.documentElement.lang=locale === "zh" ? "zh-CN" : "en"; document.title=t("title"); applyTranslations();
  $("lang-en").setAttribute("aria-pressed",String(locale === "en")); $("lang-zh").setAttribute("aria-pressed",String(locale === "zh"));
  profiles.forEach(profile=>{applyTranslations(profile.card);renderModelOptions(profile);updateProfileUi(profile);});
  roleOptions(); updateCharCount(); describeDataset(); showError(formError); if(currentSnapshot) render(currentSnapshot);
}
function setBusy(value) { $("run-button").disabled=value; $("dry-run-button").disabled=value; $("cancel-button").classList.toggle("hidden",!value); }
function roleOptions() {
  ["heavy-role","light-role","expert-role"].forEach(id=>{const select=$(id),old=select.value;select.replaceChildren();profiles.forEach(profile=>{const option=document.createElement("option");option.value=profile.name;option.textContent=profileName(profile);select.append(option);});if(profiles.some(p=>p.name===old))select.value=old;});
}
function sourceProfile(profile) { return profiles.find(p=>p!==profile && p.card.querySelector(".provider").value==="gemini"); }
function profileKey(profile, seen=new Set(), strict=true) {
  if(seen.has(profile)) { if(strict)throw new UIError("keyCycle"); return ""; }
  seen.add(profile); const card=profile.card;
  if(card.querySelector(".provider").value==="gemini" && card.querySelector(".reuse-key").checked) { const source=sourceProfile(profile); if(source)return profileKey(source,seen,strict); }
  return card.querySelector(".key").value.trim();
}
function renderModelOptions(profile) {
  const select=profile.card.querySelector(".model-select"),model=profile.card.querySelector(".model").value.trim(); select.replaceChildren();
  const rows=profile.models || (profile.card.querySelector(".provider").value==="gemini" ? recommendedGeminiModels.map(id=>({id,label:id})) : []);
  rows.forEach(row=>{const option=document.createElement("option");option.value=row.id;option.textContent=row.label === row.id ? row.id : `${row.label} · ${row.id}`;select.append(option);});
  const manual=document.createElement("option");manual.value="__manual__";manual.textContent=t("manualModel");select.append(manual);select.value=rows.some(row=>row.id===model)?model:"__manual__";
}
function updateProfileUi(profile) {
  const card=profile.card,provider=card.querySelector(".provider").value,source=sourceProfile(profile);
  card.querySelector(".profile-name").textContent=profileName(profile); card.querySelector(".base-label").classList.toggle("hidden",provider==="gemini");
  card.querySelector(".reuse-label").classList.toggle("hidden",provider!=="gemini" || !source);
  card.querySelector(".reuse-text").textContent=t("reuseKey",{name:source ? profileName(source) : ""});
  const reused=provider==="gemini" && source && card.querySelector(".reuse-key").checked;
  card.querySelector(".key").disabled=!!reused; card.querySelector(".key-help").textContent=t(reused?"reuseKeyHelp":"ownKeyHelp");
  card.querySelector(".load-models").disabled=!!profile.loading; card.querySelector(".load-models").textContent=t(profile.loading?"loadingModels":"loadModels");
  card.querySelector(".catalog-help").textContent=[t(profile.loading?"catalogLoading":profile.catalogMessage || "catalogHelp",{count:profile.models?.length || 0}),profile.catalogDiagnostic?diagnosticMessage(profile.catalogDiagnostic):null,profile.catalogDiagnostic?diagnosticSuffix(profile.catalogDiagnostic):null].filter(Boolean).join(" ");
  card.querySelector(".catalog-help").classList.toggle("catalog-error",!!profile.catalogDiagnostic || profile.catalogMessage==="catalogInvalidUrl");
}
function catalogInput(profile, strict=false) {
  const provider=profile.card.querySelector(".provider").value,key=profileKey(profile,new Set(),strict);if(!key){if(strict)throw new UIError("catalogMissingKey");return null;}
  const body={provider,model:profile.card.querySelector(".model").value.trim() || "catalog-placeholder",api_key:key};
  if(provider!=="gemini"){
    const base=profile.card.querySelector(".base").value.trim();if(provider==="custom" && !base){if(strict)throw new UIError("catalogInvalidUrl");return null;}
    if(base){let url;try{url=new URL(base);}catch(_){if(strict)throw new UIError("catalogInvalidUrl");return null;}if(!["http:","https:"].includes(url.protocol)||!url.hostname||url.username||url.password||url.search||url.hash){if(strict)throw new UIError("catalogInvalidUrl");return null;}body.base_url=base.replace(/\/+$/,"");}
  }
  return {body,binding:JSON.stringify([provider,body.base_url || "",key])};
}
function invalidateCatalog(profile) {
  profile.catalogEpoch=(profile.catalogEpoch || 0)+1;clearTimeout(profile.catalogTimer);profile.catalogTimer=null;
  const entry=modelCache.get(profile.activeBinding);if(entry){entry.subscribers.delete(profile);if(!entry.resolved && !entry.subscribers.size){entry.controller.abort();if(modelCache.get(profile.activeBinding)===entry)modelCache.delete(profile.activeBinding);}}
  profile.activeBinding=null;profile.loading=false;profile.models=null;profile.catalogMessage=null;profile.catalogDiagnostic=null;
}
function scheduleModelDiscovery(profile) {
  invalidateCatalog(profile);renderModelOptions(profile);const input=catalogInput(profile,false);
  if(input){profile.catalogMessage="catalogPending";profile.catalogTimer=setTimeout(()=>{profile.catalogTimer=null;loadModels(profile,{automatic:true});},650);}
  else if(profileKey(profile,new Set(),false) && profile.card.querySelector(".provider").value==="custom")profile.catalogMessage="catalogInvalidUrl";
  updateProfileUi(profile);
}
function addProfile(options={}) {
  const count=++profileCounter,name=options.name || `api-${count}`,card=document.createElement("div");card.className="api-profile";
  card.innerHTML='<div class="profile-top"><span class="profile-name"></span><button class="remove-profile" type="button" data-i18n="remove">Remove</button></div><div class="profile-grid"><label><span data-i18n="provider">Provider</span><select class="provider"><option value="gemini">Google Gemini</option><option value="openai">OpenAI</option><option value="custom" data-i18n="customProvider">Custom / OpenAI compatible</option></select></label><label><span data-i18n="modelChoices">Model choices</span><select class="model-select"></select></label><label class="wide"><span data-i18n="modelId">Model ID · editable</span><input class="model" placeholder="Enter your model ID" data-i18n-placeholder="modelPlaceholder" autocomplete="off" spellcheck="false"></label><div class="wide catalog-row"><button class="load-models secondary-button" type="button" data-i18n="loadModels">Load models</button><p class="catalog-help field-help" aria-live="polite"></p></div><label class="wide"><span data-i18n="apiKey">API key</span><input class="key" type="password" autocomplete="off" spellcheck="false" placeholder="Used only in this local session" data-i18n-placeholder="keyPlaceholder"></label><label class="checkbox-label wide reuse-label hidden"><input type="checkbox" class="reuse-key"><span class="reuse-text"></span></label><p class="wide key-help field-help"></p><label class="wide base-label"><span data-i18n="baseUrl">API base URL</span><input class="base" type="url" placeholder="https://api.example.com/v1" autocomplete="off" spellcheck="false"></label></div>';
  const profile={name,labelKey:options.labelKey,card,models:null,loading:false,catalogMessage:null};profiles.push(profile);$("profiles").append(card);
  const provider=card.querySelector(".provider"),base=card.querySelector(".base"),model=card.querySelector(".model"); provider.value=options.provider || "gemini"; model.value=options.model || recommendedGeminiModels[0]; base.value=provider.value==="openai"?"https://api.openai.com/v1":""; card.querySelector(".reuse-key").checked=!!options.reuseKey;
  provider.addEventListener("change",()=>{card.querySelector(".key").value="";base.value=provider.value==="openai"?"https://api.openai.com/v1":"";profiles.forEach(scheduleModelDiscovery);});
  card.querySelector(".key").addEventListener("input",()=>profiles.forEach(scheduleModelDiscovery));
  base.addEventListener("input",()=>scheduleModelDiscovery(profile));
  model.addEventListener("input",()=>renderModelOptions(profile));
  card.querySelector(".model-select").addEventListener("change",event=>{if(event.target.value!=="__manual__")model.value=event.target.value;else model.focus();});
  card.querySelector(".model-select").addEventListener("pointerdown",()=>{try {if(profileKey(profile,new Set(),false))loadModels(profile);}catch(_){/* Explicit button reports validation errors. */}});
  card.querySelector(".load-models").addEventListener("click",()=>loadModels(profile)); card.querySelector(".reuse-key").addEventListener("change",()=>profiles.forEach(scheduleModelDiscovery));
  card.querySelector(".remove-profile").addEventListener("click",()=>{if(profiles.length===1){showError(new UIError("keepProfile"));return;}invalidateCatalog(profile);profiles=profiles.filter(p=>p!==profile);card.remove();profiles.forEach(scheduleModelDiscovery);roleOptions();});
  applyTranslations(card);renderModelOptions(profile);profiles.forEach(updateProfileUi);roleOptions();return profile;
}
async function api(path,options) {
  let response,payload;try {response=await fetch(path,options);payload=await response.json();}catch(error){throw new UIError(error.name==="AbortError"?"catalogAborted":"requestFailed");}
  if(!response.ok){const keys={MISSING_API_KEY:"catalogMissingKey",CATALOG_PAGE_LIMIT:"catalogPageLimit"},detail=diagnostic(typeof payload.error==="object"?payload.error:{message:payload.error,code:payload.code,http_status:payload.http_status,phase:payload.phase});throw new UIError(path==="/api/models" ? keys[payload.code] || "catalogFailed" : "requestFailed",{},detail);}return payload;
}
async function loadModels(profile,{automatic=false}={}) {
  if(profile.loading)return;clearTimeout(profile.catalogTimer);profile.catalogTimer=null;const epoch=profile.catalogEpoch || 0;
  try {
    const input=catalogInput(profile,!automatic);if(!input)return;const {body,binding}=input;profile.loading=true;profile.catalogDiagnostic=null;profile.activeBinding=binding;updateProfileUi(profile);if(!automatic)showError(null);
    if(!modelCache.has(binding)){const entry={controller:new AbortController(),subscribers:new Set(),resolved:false};entry.promise=api("/api/models",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({profile:body}),signal:entry.controller.signal}).then(payload=>{if(!Array.isArray(payload.models))throw new UIError("catalogFailed");entry.resolved=true;return payload.models;}).catch(error=>{if(modelCache.get(binding)===entry)modelCache.delete(binding);throw error;});modelCache.set(binding,entry);}
    const entry=modelCache.get(binding);entry.subscribers.add(profile);const rows=await entry.promise,current=catalogInput(profile,false);
    if(profiles.includes(profile) && epoch===(profile.catalogEpoch || 0) && current?.binding===binding){profile.models=rows;profile.catalogMessage=rows.length?"catalogLoaded":"catalogEmpty";renderModelOptions(profile);}
  }catch(error){if(epoch===(profile.catalogEpoch || 0) && profiles.includes(profile) && error.key!=="catalogAborted"){profile.catalogMessage=error.key && error.key.startsWith("catalog")?error.key:"catalogFailed";profile.catalogDiagnostic=error.diagnostic || null;if(!automatic)showError(error instanceof UIError?error:new UIError("catalogFailed"));}}
  finally{if(epoch===(profile.catalogEpoch || 0)){const entry=modelCache.get(profile.activeBinding);if(entry)entry.subscribers.delete(profile);profile.activeBinding=null;profile.loading=false;updateProfileUi(profile);}}
}
function buildConfig(dryRun) {
  const roles={heavy:$("heavy-role").value,light:$("light-role").value,expert:$("expert-role").value},requiredRoles=window.OptionalExtensions?window.OptionalExtensions.requiredRoles():["heavy","light","expert"],routed=new Set(requiredRoles.map(role=>roles[role])),values={};
  profiles.forEach(profile=>{const {name,card}=profile,provider=card.querySelector(".provider").value,model=card.querySelector(".model").value.trim();if(!model)throw new UIError("missingModel",{name:profileName(profile)});
    const key=profileKey(profile,new Set(),routed.has(name)&&!dryRun);if(!key && routed.has(name)&&!dryRun)throw new UIError("missingKey",{name:profileName(profile)});values[name]={provider,model,api_key:key || undefined};
    if(provider!=="gemini"){const base=card.querySelector(".base").value.trim();if(provider==="custom" && !base)throw new UIError("missingBase",{name:profileName(profile)});if(base)values[name].base_url=base;}
  });
  const serpapi=$("serpapi-key").value.trim(),s2=$("s2-key").value.trim();return {profiles:values,roles,mode:$("mode").value,search:{enable_serpapi:!!serpapi,enable_duckduckgo:$("duckduckgo").checked,serpapi_api_key:serpapi || undefined,semantic_scholar_api_key:s2 || undefined}};
}
function describeDataset() { const dataset=datasets.find(d=>d.difficulty===difficulty);$("dataset-description").textContent=dataset?t("datasetDescription",{label:dataset.label,count:dataset.items.length}):t("blindInputOnly"); }
function fillBenchmark() {const dataset=datasets.find(d=>d.difficulty===difficulty);if(!dataset)return;$("benchmark-item").replaceChildren();dataset.items.forEach(item=>{const option=document.createElement("option");option.value=item.item_id;option.textContent=item.item_id;$("benchmark-item").append(option);});describeDataset();updateBenchmarkQuery();}
function updateBenchmarkQuery() {const dataset=datasets.find(d=>d.difficulty===difficulty),item=dataset?.items.find(i=>i.item_id===$("benchmark-item").value);if(item&&benchmarkMode){$("query").value=item.query;updateCharCount();}}
function updateCharCount() {$("char-count").textContent=t("characters",{count:$("query").value.length});}
function toggleSource(value) {benchmarkMode=value;$("custom-tab").classList.toggle("active",!value);$("benchmark-tab").classList.toggle("active",value);$("benchmark-controls").classList.toggle("hidden",!value);if(value)fillBenchmark();}
function tokenText(usage) {if(!usage)return t("unknown");const tokens=usage.reported_token_subtotal?.total_tokens ?? usage.total_tokens ?? usage.reported_total_tokens ?? usage.reported_tokens ?? usage.total;return typeof tokens==="number"?tokens.toLocaleString(locale==="zh"?"zh-CN":"en-US"):t("unknown");}
function evidenceText(paper) {const scopes={metadata_only:"evidenceMetadata",complete_abstract:"evidenceAbstract",selected_pdf_snippets:"evidenceSnippets",full_native_pdf_page_text:"evidenceFulltext",complete_abstract_and_selected_pdf_snippets:"evidenceAbstractSnippets",complete_abstract_and_full_native_pdf_page_text:"evidenceAbstractFulltext"};return t(scopes[paper.evidence_scope] || (paper.fulltext_complete?"evidenceFulltext":"evidenceAvailable"));}
function groupedDiagnostics(records=[],requestErrors=[]) {
  const groups=new Map();
  [["stage",records],["request",requestErrors]].forEach(([source,rows])=>{if(!Array.isArray(rows))return;rows.slice(0,200).forEach(row=>{const detail=diagnostic(row);if(!detail || (!detail.code && !detail.phase && !detail.message))return;
    const key=JSON.stringify([detail.phase,detail.code,detail.http_status,detail.adapter_http_status]);if(!groups.has(key))groups.set(key,{detail,counts:{stage:0,request:0}});
    const group=groups.get(key),supplied=row && typeof row==="object"?(row.occurrence_count ?? row.occurrences ?? row.count):null;group.counts[source]+=Number.isSafeInteger(supplied)&&supplied>0&&supplied<=1000000?supplied:1;
    if(!group.detail.message && detail.message)group.detail.message=detail.message;
  });});
  return [...groups.values()].map(group=>({...group,count:Math.max(group.counts.stage,group.counts.request)})).sort((a,b)=>Number(b.counts.request>0)-Number(a.counts.request>0) || b.count-a.count);
}
function renderDiagnostics(error,phase,records=[],requestErrors=[]) {
  const detail=diagnostic(error,phase),groups=groupedDiagnostics(records,requestErrors);$("failure-details").classList.toggle("hidden",!detail && !groups.length);$("failure-fields").replaceChildren();$("diagnostic-list").replaceChildren();$("diagnostic-list-details").classList.toggle("hidden",!groups.length);
  $("failure-reason").textContent=detail?diagnosticMessage(detail):"";
  if(detail)[["failureStage",detail.phase?phaseText(detail.phase):null],["failureCode",detail.code],["httpStatus",detail.http_status],["adapterHttpStatus",detail.adapter_http_status]].forEach(([label,value])=>{if(value===null || value===undefined)return;const term=document.createElement("dt"),description=document.createElement("dd");term.textContent=t(label);description.textContent=String(value);$("failure-fields").append(term,description);});
  $("diagnostic-list-summary").textContent=t("additionalDiagnostics",{count:groups.length});
  groups.slice(0,12).forEach(group=>{const item=document.createElement("li"),heading=document.createElement("div"),message=document.createElement("p"),count=document.createElement("span");heading.className="diagnostic-row-heading";heading.textContent=diagnosticSuffix(group.detail);count.className="diagnostic-count";count.textContent=t("diagnosticCount",{count:group.count});message.textContent=diagnosticMessage(group.detail);heading.append(count);item.append(heading,message);$("diagnostic-list").append(item);});
  $("diagnostic-list-note").textContent=[groups.length>12?t("diagnosticTruncated",{shown:12,total:groups.length}):null,t("diagnosticRecordsOnly")].filter(Boolean).join(" ");
}
function render(snapshot) {
  currentSnapshot=snapshot;const result=snapshot.result || snapshot,status=(snapshot.status || result.status || "RUNNING").toUpperCase();$("status-badge").textContent=t(statusKeys[status] || "statusRunning");$("status-badge").className=`status-badge ${["QUEUED","STARTING","RUNNING"].includes(status)?"running":["FAILED","TIMEOUT"].includes(status)?"error":""}`;
  const seconds=result.duration_seconds ?? snapshot.duration_seconds ?? 0;$("elapsed").textContent=`${Math.floor(seconds/60)}:${String(Math.floor(seconds%60)).padStart(2,"0")}`;$("token-count").textContent=tokenText(result.usage || snapshot.usage);
  const papers=result.top5 || snapshot.top5 || [];$("paper-count").textContent=`${papers.length} / 5`;const running=!terminals.has(status);$("run-state").classList.toggle("hidden",!running);$("phase-label").textContent=t(phaseKeys[snapshot.phase || result.phase] || "phaseRetrieval");$("phase-detail").textContent=running?t("runningDetail"):"";$("empty-state").classList.toggle("hidden",papers.length>0);$("paper-list").replaceChildren();
  renderDiagnostics(result.error || snapshot.error,result.phase || snapshot.phase,result.diagnostics || snapshot.diagnostics || [],(result.usage || snapshot.usage)?.recent_request_errors || []);
  papers.forEach((paper,index)=>{const card=document.createElement("li");card.className="paper-card";const rank=document.createElement("div");rank.className="paper-rank";rank.textContent=String(paper.rank || index+1).padStart(2,"0");const content=document.createElement("div"),title=document.createElement("a");title.className="paper-title";title.textContent=paper.title || t("missingTitle");const url=paper.url || paper.doi_url || (paper.doi?`https://doi.org/${paper.doi}`:null);if(url&&/^https?:\/\//i.test(url)){title.href=url;title.target="_blank";title.rel="noopener noreferrer";}content.append(title);const meta=document.createElement("p");meta.className="paper-meta";const authors=Array.isArray(paper.authors)?paper.authors.join(", "):paper.authors;meta.textContent=[authors,paper.year,paper.venue].filter(Boolean).join(" · ");content.append(meta);const chip=document.createElement("span");chip.className="evidence-chip";chip.textContent=evidenceText(paper);content.append(chip);const reason=document.createElement("p");reason.className="paper-reason";reason.textContent=paper.expert_reason || paper.match_explanation || paper.reason || "";content.append(reason);card.append(rank,content);$("paper-list").append(card);});
  $("result-note").classList.toggle("hidden",running);let note=status==="DRY_RUN" || status==="NOT_RUN"?"dryRunNote":status==="TIMEOUT"?"timeoutNote":status==="CANCELLED"?"cancelledNote":papers.length && (status==="PARTIAL" || result.error || snapshot.error)?"partialNote":status==="EMPTY"?"emptyNote":status==="FAILED" || status==="PARTIAL" || result.error || snapshot.error?"failureNote":"resultNote";$("result-note").textContent=t(note);$("result-actions").classList.toggle("hidden",running);$("technical-details").classList.toggle("hidden",running);$("technical-output").textContent=JSON.stringify({job_id:currentJob,status,run_dir:result.run_dir || snapshot.run_dir,usage:result.usage || snapshot.usage,error:diagnostic(result.error || snapshot.error,result.phase || snapshot.phase)},null,2);
  if(window.OptionalExtensions)window.OptionalExtensions.render(snapshot);
  if(!running){setBusy(false);clearInterval(polling);currentResult=snapshot;}
}
function rememberJob(jobId) { if(!/^[0-9a-f]{32}$/i.test(jobId))return;try{const url=new URL(window.location.href);url.search="";url.searchParams.set("job",jobId);url.hash="";window.history.replaceState(null,"",url.toString());}catch(_){/* Job tracking also works without browser history. */} }
function resumedJobId() { try{const value=new URL(window.location.href).searchParams.get("job");return value && /^[0-9a-f]{32}$/i.test(value)?value:null;}catch(_){return null;} }
async function poll() {if(!currentJob)return false;try{render(await api(`/api/jobs/${currentJob}`));return true;}catch(error){showError(error);clearInterval(polling);setBusy(false);return false;}}
async function resumeJob() {const jobId=resumedJobId();if(!jobId)return;currentJob=jobId;setBusy(true);$("status-badge").textContent=t("resumingJob");const available=await poll();if(!available){showError(new UIError("resumeUnavailable"));return;}if($("run-button").disabled)polling=setInterval(poll,1200);}
async function start(dryRun=false) {
    try{showError(null);const query=$("query").value.trim() || ($("task-kind").value==="code_extractor"?"Analyze the supplied document":"");if(!query)throw new UIError("missingQuery");const needModel=window.OptionalExtensions?window.OptionalExtensions.needsModel():true;const config=buildConfig(dryRun || !needModel),timeout=Math.round(Number($("timeout").value)*60);if(!Number.isFinite(timeout)||timeout<60||timeout>7200)throw new UIError("invalidTime");setBusy(true);currentResult=null;$("paper-list").replaceChildren();$("result-actions").classList.add("hidden");const extension=window.OptionalExtensions?await window.OptionalExtensions.request(query):undefined;const benchmark=benchmarkMode&&!extension?{difficulty,item_id:$("benchmark-item").value}:undefined;const response=await api("/api/jobs",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({query,config,benchmark,extension,timeout_seconds:timeout,dry_run:dryRun})});currentJob=response.job_id;rememberJob(currentJob);clearInterval(polling);await poll();if($("run-button").disabled)polling=setInterval(poll,1200);}catch(error){showError(error instanceof UIError?error:new UIError("requestFailed"));setBusy(false);}
}
$("lang-en").addEventListener("click",()=>setLocale("en"));$("lang-zh").addEventListener("click",()=>setLocale("zh"));$("add-profile").addEventListener("click",()=>addProfile());$("query").addEventListener("input",updateCharCount);$("custom-tab").addEventListener("click",()=>toggleSource(false));$("benchmark-tab").addEventListener("click",()=>toggleSource(true));
document.querySelectorAll("[data-difficulty]").forEach(button=>button.addEventListener("click",()=>{difficulty=button.dataset.difficulty;document.querySelectorAll("[data-difficulty]").forEach(b=>b.classList.toggle("active",b===button));fillBenchmark();}));$("benchmark-item").addEventListener("change",updateBenchmarkQuery);$("run-button").addEventListener("click",()=>start(false));$("dry-run-button").addEventListener("click",()=>start(true));
$("cancel-button").addEventListener("click",async()=>{try{await api(`/api/jobs/${currentJob}/cancel`,{method:"POST"});await poll();}catch(error){showError(error);}});$("download-json").addEventListener("click",()=>{if(!currentResult)return;const blob=new Blob([JSON.stringify(currentResult,null,2)],{type:"application/json"}),url=URL.createObjectURL(blob),a=document.createElement("a");a.href=url;a.download=`idea2prior-${currentJob}.json`;a.click();URL.revokeObjectURL(url);});
const retrievalProfile=addProfile({name:"gemini-retrieval",labelKey:"retrievalProfile",provider:"gemini",model:recommendedGeminiModels[0]});const expertProfile=addProfile({name:"gemini-expert",labelKey:"expertProfile",provider:"gemini",model:recommendedGeminiModels[1],reuseKey:true});$("heavy-role").value=retrievalProfile.name;$("light-role").value=retrievalProfile.name;$("expert-role").value=expertProfile.name;setLocale(locale);
api("/api/catalog").then(data=>{datasets=data.datasets;fillBenchmark();}).catch(()=>showError(new UIError("datasetUnavailable")));
resumeJob();
