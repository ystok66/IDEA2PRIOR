"use strict";
Object.assign(texts.en,{reuseKeyHelp:"These profiles use the same provider and endpoint; their models can differ.",keyCycle:"Enter a key in one profile before reusing it."});
Object.assign(texts.zh,{reuseKeyHelp:"相同提供商和端点的配置可复用密钥，模型可不同。",keyCycle:"请先在一个配置中填写密钥，再让其他配置复用。"});
Object.assign(texts.en,{
parseTask:"Document parsing / OCR",searchTask:"Document vector search",startYear:"Start year",endYear:"End year",chunkSize:"Chunk characters",chunkOverlap:"Overlap characters",topK:"Returned passages",embeddingConfig:"Independent embedding API",embeddingHelp:"Embeddings use this explicit profile. Model listing does not guarantee embedding access.",advancedSettings:"Advanced settings",yearFilter:"Limit retrieval years",concurrency:"Concurrent API requests",requestTimeout:"Request timeout · seconds",outputBudget:"Model output budget · tokens",expertBudget:"Expert output budget · tokens",maxRounds:"Retrieval rounds",citationGraph:"Expand citation neighbors",pipelineOverrides:"Additional pipeline settings · JSON",configFiles:"Configuration import / export",importConfig:"Import configuration JSON",exportConfig:"Export configuration · without secrets",exportHelp:"Keys are omitted. Custom endpoint URLs remain in your configuration; review before sharing.",reportSource:"Markdown source",savedReports:"Reports & attachments",evaluateResult:"Evaluate this benchmark result · offline",invalidSettings:"Check the advanced settings and configuration format.",importedConfig:"Configuration imported. Enter keys before starting a task.",catalogNoGuarantee:"Models listed; embedding access is verified only when running.",preview:"Preview",download:"Download",reportNote:"Report files are available below. Check diagnostics for incomplete stages.",reportMetric:"Report files",partialNote:"Results were produced with incomplete stages. Review the diagnostics and evidence scope; no automatic rerun was started."
});
Object.assign(texts.zh,{
parseTask:"文档解析 / OCR",searchTask:"文档向量检索",startYear:"起始年份",endYear:"结束年份",chunkSize:"分块字符数",chunkOverlap:"重叠字符数",topK:"返回片段数",embeddingConfig:"独立 Embedding API",embeddingHelp:"向量使用这里的独立配置。模型列表不保证当前密钥拥有向量调用权限。",advancedSettings:"高级设置",yearFilter:"限制检索年份",concurrency:"API 并发请求数",requestTimeout:"请求超时 · 秒",outputBudget:"模型输出预算 · tokens",expertBudget:"Expert 输出预算 · tokens",maxRounds:"检索轮数",citationGraph:"扩展引文邻居",pipelineOverrides:"其他流程参数 · JSON",configFiles:"配置导入 / 导出",importConfig:"导入配置 JSON",exportConfig:"导出配置 · 不含密钥",exportHelp:"导出不含密钥，但保留自定义端点 URL；分享前请检查。",reportSource:"Markdown 源文",savedReports:"报告与附件",evaluateResult:"离线评测本次基准结果",invalidSettings:"请检查高级参数与配置格式。",importedConfig:"已导入配置，请填写密钥后启动任务。",catalogNoGuarantee:"已取得模型列表，向量权限需在实际运行时验证。",preview:"预览",download:"下载",reportNote:"报告文件见下方；请检查运行诊断中的未完成阶段。",reportMetric:"报告文件",partialNote:"已产生结果，但部分阶段未完成；请检查诊断和证据范围，没有自动重跑。"
});
Object.assign(phaseKeys,{extension_document_parse:"parseTask",extension_document_search:"searchTask",report_generation:"reportMetric"});
window.WorkspaceFeatures={};
function boundedNumber(id,min,max){const value=Number($(id).value);if(!Number.isInteger(value)||value<min||value>max)throw new UIError("invalidSettings");return value;}
function embeddingProfile(requireKey=false){
  const profile={provider:$("embedding-provider").value,model:$("embedding-model").value.trim(),api_key:$("embedding-key").value.trim()};
  if(profile.provider!=="gemini")profile.base_url=$("embedding-base").value.trim() || undefined;
  if(!profile.model || requireKey&&!profile.api_key || profile.provider==="custom"&&!profile.base_url)throw new UIError("invalidSettings");
  return profile;
}
window.WorkspaceFeatures.config=function(config,dryRun){
  let overrides;
  try{overrides=JSON.parse($("pipeline-overrides").value);}catch(_){throw new UIError("invalidSettings");}
  if(!overrides||typeof overrides!=="object"||Array.isArray(overrides))throw new UIError("invalidSettings");
  config.years=$("use-years").checked?[boundedNumber("start-year",1800,2200),boundedNumber("end-year",1800,2200)]:null;
  if(config.years && config.years[0]>config.years[1])throw new UIError("invalidSettings");
  config.request_timeout_seconds=boundedNumber("request-timeout",1,1800);
  config.max_output_tokens=boundedNumber("output-budget",1024,65536);
  config.expert_max_output_tokens=boundedNumber("expert-budget",1024,65536);
  config.max_rounds=boundedNumber("max-rounds",1,10);config.use_citation_graph=$("citation-graph").checked;
  config.pipeline={...overrides,max_in_flight_requests:boundedNumber("concurrency",1,32)};
  if($("task-kind").value==="document_search")config.embedding=embeddingProfile(!dryRun);
  return config;
};
// Explicitly allowlist configuration fields; arbitrary imported strings cannot hide keys in exports.
function withoutSecrets(config){
  const profile=p=>({provider:p.provider,model:p.model,...(p.base_url?{base_url:p.base_url}:{})});
  const output={...config,profiles:Object.fromEntries(Object.entries(config.profiles).map(([name,p])=>[name,profile(p)])),search:Object.fromEntries(Object.entries(config.search||{}).filter(([key])=>!key.includes("api_key")))};
  if(output.embedding)output.embedding=profile(output.embedding);
  const secrets=[...profiles.map(p=>p.card.querySelector(".key").value),$("embedding-key").value,$("serpapi-key").value,$("s2-key").value].filter(Boolean);
  const clean=value=>{
    if(Array.isArray(value))return value.map(clean);
    if(value && typeof value==="object")return Object.fromEntries(Object.entries(value).filter(([k])=>!/api.?key|secret|password|authorization/i.test(k)).map(([k,v])=>[k,clean(v)]));
    if(typeof value==="string")for(const secret of secrets)value=value.split(secret).join("[REDACTED]");
    return value;
  };
  return clean(output);
}
function downloadText(text,name,type="application/json"){
  const url=URL.createObjectURL(new Blob([text],{type})),a=document.createElement("a");a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
}
$("export-config").addEventListener("click",()=>{
  try{downloadText(JSON.stringify(withoutSecrets(buildConfig(true)),null,2),"idea2prior-config.json");}catch(error){showError(error);}
});
$("import-config").addEventListener("change",async()=>{
  try{
    const file=$("import-config").files[0];if(!file)return;if(file.size>128000)throw new Error("Configuration too large");
    const data=JSON.parse(await file.text());
    if(!data.profiles || !Object.keys(data.profiles).length || Object.keys(data.profiles).length>20)throw new Error("Invalid profiles");
    for(const [name,p] of Object.entries(data.profiles)){
      if(!name || !p || !["openai","gemini","custom"].includes(p.provider)||typeof p.model!=="string")throw new Error("Invalid profile");
    }
    if(data.roles && ["heavy","light","expert"].some(role=>!data.profiles[data.roles[role]]))throw new Error("Invalid roles");
    profiles.forEach(invalidateCatalog);profiles=[];$("profiles").replaceChildren();
    for(const [name,p] of Object.entries(data.profiles)){
      const entry=addProfile({name,provider:p.provider,model:p.model});
      entry.card.querySelector(".base").value=p.base_url||""; // Secrets are intentionally not imported.
    }
    if(data.roles)for(const role of ["heavy","light","expert"])$(role+"-role").value=data.roles[role];
    $("mode").value=data.mode||"retrieval_eval";$("use-years").checked=Array.isArray(data.years);
    [$("start-year").value,$("end-year").value]=data.years||[2015,2025];
    for(const [id,key,fallback] of [["request-timeout","request_timeout_seconds",120],["output-budget","max_output_tokens",8192],["expert-budget","expert_max_output_tokens",16384],["max-rounds","max_rounds",1]])$(id).value=data[key]??fallback;
    $("citation-graph").checked=data.use_citation_graph!==false;
    const pipeline={...(data.pipeline||{})};$("concurrency").value=pipeline.max_in_flight_requests??4;delete pipeline.max_in_flight_requests;$("pipeline-overrides").value=JSON.stringify(pipeline,null,2);
    $("serpapi-key").value="";$("s2-key").value="";$("duckduckgo").checked=data.search?.enable_duckduckgo!==false;
    if(data.embedding){$("embedding-provider").value=data.embedding.provider;$("embedding-model").value=data.embedding.model;$("embedding-base").value=data.embedding.base_url||"";}
    $("embedding-key").value="";$("config-status").textContent=t("importedConfig");showError(null);
  }catch(_){showError(new UIError("invalidSettings"));}
});
let embeddingTimer;
async function discoverEmbeddings(){
  try{
    $("embedding-status").textContent=t("catalogLoading");
    const payload=await api("/api/models",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({profile:embeddingProfile(true),purpose:"embedding"})});
    $("embedding-choices").replaceChildren(new Option(t("manualModel"),""));
    for(const row of payload.models)$("embedding-choices").add(new Option(row.label,row.id));
    $("embedding-status").textContent=t("catalogNoGuarantee");
  }catch(error){$("embedding-status").textContent=t("catalogFailed");showError(error);}
}
$("embedding-discover").addEventListener("click",discoverEmbeddings);
$("embedding-choices").addEventListener("change",()=>{if($("embedding-choices").value)$("embedding-model").value=$("embedding-choices").value;});
for(const id of ["embedding-key","embedding-base"])$(id).addEventListener("input",()=>{clearTimeout(embeddingTimer);if($("embedding-key").value)embeddingTimer=setTimeout(discoverEmbeddings,700);});
$("embedding-provider").addEventListener("change",()=>{$("embedding-key").value="";$("embedding-base").value=$("embedding-provider").value==="openai"?"https://api.openai.com/v1":"";$("embedding-base").disabled=$("embedding-provider").value==="gemini";$("embedding-choices").replaceChildren(new Option(t("manualModel"),""));});
let artifactBinding="",activeArtifact="",artifactEpoch=0;
window.WorkspaceFeatures.render=function(snapshot){
  const result=snapshot.result||snapshot,files=snapshot.artifacts||[],binding=JSON.stringify([currentJob,files,locale]),finished=terminals.has(result.status);
  const critic=result.extension_result?.details?.evaluation;
  if(result.status==="PARTIAL" && !result.error && critic?.meets_requirements===false){
    renderDiagnostics({code:"CRITIC_REVIEW_UNSATISFIED",message:locale==="zh"?"Critic 认为仍有质量问题；详情保留在结构化输出中，没有自动重新执行。":"Critic identified unresolved quality concerns. Feedback is preserved in the structured output; no automatic re-execution occurred.",phase:"planner_critic"},"planner_critic",[],[]);
  }
  $("artifact-panel").classList.toggle("hidden",!files.length);
  if(binding!==artifactBinding){
    artifactBinding=binding;activeArtifact="";artifactEpoch++;$("artifact-list").replaceChildren();$("artifact-preview").replaceChildren();
    for(const item of files){
      const row=document.createElement("div");row.className="artifact-row";const title=document.createElement("span");title.textContent=item.name;
      const url="/api/jobs/"+currentJob+"/artifacts/"+item.id;
      const preview=document.createElement("button");preview.type="button";preview.className="text-button";preview.textContent=t("preview");
      preview.addEventListener("click",async()=>{const epoch=++artifactEpoch;activeArtifact=item.id;try{const response=await fetch(url);if(!response.ok)throw new Error("Artifact unavailable");const source=await response.text();if(epoch!==artifactEpoch)return;if(item.name.endsWith(".md"))await ResearchRenderer.render($("artifact-preview"),source);else{$("artifact-preview").replaceChildren();const pre=document.createElement("pre");pre.textContent=source;$("artifact-preview").append(pre);}}catch(_){if(epoch===artifactEpoch)showError(new UIError("requestFailed"));}});
      const download=document.createElement("a");download.href=url;download.download=item.name;download.className="text-button";download.textContent=t("download");
      row.append(title,preview,download);$("artifact-list").append(row);
    }
  }
  $("evaluation-panel").classList.toggle("hidden",!finished||!snapshot.benchmark_evaluation_available);
  if(result.config?.mode==="full_report" && result.operation!=="extension"){
    $("paper-count").textContent=String(files.filter(f=>f.name.endsWith(".md")).length);
    document.querySelector('[data-i18n="rankedPapers"],[data-i18n="outputItems"]').textContent=t("reportMetric");
    if(files.length){$("empty-state").classList.add("hidden");if(result.status==="SUCCEEDED")$("result-note").textContent=t("reportNote");}
  }
};
$("evaluate-result").addEventListener("click",async()=>{
  try{$("evaluation-output").textContent=JSON.stringify(await api("/api/evaluate",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({job_id:currentJob})}),null,2);}catch(error){showError(error);}
});
applyTranslations();OptionalExtensions.refresh();if(currentSnapshot)window.WorkspaceFeatures.render(currentSnapshot);
