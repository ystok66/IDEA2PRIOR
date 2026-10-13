"use strict";
Object.assign(texts.en, {
  taskType:"Task", retrievalTask:"Find and rank papers · default", codeTask:"Code & reproducibility analysis",
  evolutionTask:"Technology evolution map", plannerTask:"Planner–Worker research assistant", landscapeTask:"Benchmark landscape", citationTask:"Citation gap analysis",
  extensionHelp:"Optional extensions run separately. They do not change the default Top 5 protocol.", documentUpload:"Document · PDF, Markdown or text (up to 12 MiB)", documentPath:"Or a document path on this computer", documentParser:"PDF parser", nativeParser:"Native text · default", mineruParser:"MinerU · requires optional installation",
  llmAnalysis:"Use the Light model for implementation analysis", executeSnippets:"Execute extracted Python snippets", codeExecutionHelp:"Execution is off by default. Syntax checks can run without executing snippets; neither proves the paper's algorithm correct.", extensionPaperLimit:"Map / scoring paper limit (1–60)", plannerSteps:"Maximum tool steps (1–20)", criticReview:"Review execution with Critic · no automatic re-execution", manuscriptExcerpt:"Manuscript excerpt", knownReferences:"Known reference titles or IDs · one per line", extensionReport:"Extension report", downloadReport:"Download Markdown report", startExtension:"Start extension", outputItems:"Output items", missingDocument:"Choose a document or enter its local path.", documentTooLarge:"The document exceeds 12 MiB.", missingManuscript:"Enter a manuscript excerpt for citation gap analysis.", invalidExtensionLimit:"Check the paper and step limits.", extensionNote:"This is an optional analysis result, separate from the Top 5 benchmark. Review its evidence scope and diagnostics.", basedOn:"Built on PaperArena", upstreamPaper:"Paper", upstreamCode:"Code"
});
Object.assign(texts.zh, {
  taskType:"任务", retrievalTask:"检索与排序论文 · 默认", codeTask:"代码与可复现性分析", evolutionTask:"技术演化图", plannerTask:"Planner–Worker 研究助手", landscapeTask:"基准与评测概览", citationTask:"引用缺口分析", extensionHelp:"可选扩展独立运行，不改变默认 Top 5 协议。", documentUpload:"文档 · PDF、Markdown 或文本（最多 12 MiB）", documentPath:"或填写本机文档路径", documentParser:"PDF 解析器", nativeParser:"原生文本 · 默认", mineruParser:"MinerU · 需安装可选依赖", llmAnalysis:"使用 Light 模型进行实现分析", executeSnippets:"执行提取出的 Python 片段", codeExecutionHelp:"默认不执行代码。语法检查可独立运行；语法检查或片段执行均不等于证明论文算法正确。", extensionPaperLimit:"图谱 / 评分论文上限（1–60）", plannerSteps:"最多工具步骤（1–20）", criticReview:"使用 Critic 复核执行结果 · 不自动重新执行", manuscriptExcerpt:"论文草稿片段", knownReferences:"已引用论文标题或 ID · 每行一个", extensionReport:"扩展分析报告", downloadReport:"下载 Markdown 报告", startExtension:"启动扩展", outputItems:"输出条目", missingDocument:"请选择文档或填写本机路径。", documentTooLarge:"文档超过 12 MiB。", missingManuscript:"引用缺口分析需要论文草稿片段。", invalidExtensionLimit:"请检查论文数及步骤数范围。", extensionNote:"这是独立扩展分析，与 Top 5 评测分开。请查看证据范围和运行诊断。", basedOn:"基于 PaperArena 扩展", upstreamPaper:"论文", upstreamCode:"代码"
});
Object.assign(phaseKeys,{extension:"extensionReport",extension_document:"documentParser",extension_code_extractor:"codeTask",extension_evolution_mapper:"evolutionTask",extension_planner_worker:"plannerTask",extension_benchmark_landscape:"landscapeTask",extension_slot_filling:"citationTask"});
Object.assign(texts.en,{analysisResults:"Analysis results",extensionFailureNote:"The extension did not complete. Review its stage and failure reason; no automatic rerun was started.",noModelCalls:"No model calls",privacy:"API keys stay in memory for this local session and are not saved to browser storage or result files. Tasks call APIs only when their selected analysis requires them."});
Object.assign(texts.zh,{analysisResults:"分析结果",extensionFailureNote:"扩展未完成，请查看失败阶段和原因；没有自动重跑。",noModelCalls:"无模型调用",privacy:"密钥仅用于当前本地会话，不保存在浏览器存储或结果文件中；仅任务所选分析需要时调用 API。"});
window.OptionalExtensions = {
  requiredRoles() {const name=$("task-kind").value;if(name==="retrieval")return ["heavy","light","expert"];if(name==="code_extractor")return $("extension-llm").checked?["light"]:[];if(name==="slot_filling")return ["heavy"];if(name==="planner_worker")return ($("extension-file").files[0] || $("extension-path").value.trim()) && $("extension-llm").checked?["heavy","light"]:["heavy"];return [];},
  needsModel() { const name=$("task-kind").value;return name==="retrieval" || name==="planner_worker" || name==="slot_filling" || name==="code_extractor" && $("extension-llm").checked; },
  refresh() {
    const name=$("task-kind").value, optional=name!=="retrieval", hasDocument=name==="code_extractor" || name==="planner_worker";
    $("extension-options").classList.toggle("hidden",!optional);$("document-options").classList.toggle("hidden",!hasDocument);$("paper-limit-options").classList.toggle("hidden",name==="code_extractor");$("planner-options").classList.toggle("hidden",name!=="planner_worker");$("citation-options").classList.toggle("hidden",name!=="slot_filling");$("benchmark-tab").disabled=optional;
    if(optional && benchmarkMode)toggleSource(false);
    const heading=document.querySelector('[data-i18n="retrievalResults"], [data-i18n="analysisResults"]');heading.dataset.i18n=optional?"analysisResults":"retrievalResults";applyTranslations(heading.parentElement);
    $("run-button").querySelector("[data-i18n]").dataset.i18n=optional?"startExtension":"startRetrieval";applyTranslations($("run-button"));
  },
  async request(query) {
    const name=$("task-kind").value;if(name==="retrieval")return undefined;
    const parameters={topic:query,query,max_papers:Number($("extension-paper-limit").value),max_steps:Number($("extension-steps").value),llm_analysis:$("extension-llm").checked,execute_code:$("extension-execute").checked,enable_critic:$("extension-critic").checked,parser:$("extension-parser").value};
    if(name==="code_extractor")parameters.query=$("query").value.trim();
    if(!Number.isInteger(parameters.max_papers)||parameters.max_papers<1||parameters.max_papers>60||!Number.isInteger(parameters.max_steps)||parameters.max_steps<1||parameters.max_steps>20)throw new UIError("invalidExtensionLimit");
    if(name==="code_extractor" || name==="planner_worker") {
      parameters.document_path=$("extension-path").value.trim();const file=$("extension-file").files[0];
      if(file) {if(file.size>12*1024*1024)throw new UIError("documentTooLarge");const base64=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(",",2)[1]);reader.onerror=reject;reader.readAsDataURL(file);});const upload=await api("/api/documents",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({filename:file.name,base64})});parameters.document_path=upload.document_path;}
      if(name==="code_extractor"&&!parameters.document_path)throw new UIError("missingDocument");
      if(!parameters.document_path)delete parameters.document_path;
    }
    if(name==="slot_filling") {parameters.manuscript_excerpt=$("extension-manuscript").value.trim();parameters.known_refs=$("extension-refs").value.split("\n").map(s=>s.trim()).filter(Boolean);if(!parameters.manuscript_excerpt)throw new UIError("missingManuscript");}
    return {name,parameters};
  },
  render(snapshot) {
    const result=snapshot.result || snapshot, output=result.extension_result, isExtension=result.operation==="extension";
    const name=result.extension_name || output?.name;if(isExtension && name && !this.userSelected && $("task-kind").value!==name){$("task-kind").value=name;this.refresh();}
    $("extension-result").classList.toggle("hidden",!output);$("extension-report").textContent=output?.report_markdown || "";
    document.querySelector('[data-i18n="rankedPapers"], [data-i18n="outputItems"]').textContent=t(isExtension?"outputItems":"rankedPapers");
    if(isExtension && result.usage?.model_attempts===0)$("token-count").textContent=t("noModelCalls");
    if(isExtension && !output && terminals.has(result.status))$("result-note").textContent=t(result.status==="NOT_RUN"?"dryRunNote":"extensionFailureNote");
    if(isExtension){const details=output?.details || {},count=details.paper_count ?? (details.analysis?(details.analysis.code_blocks?.length || 0)+(details.analysis.pseudocode_blocks?.length || 0):Object.keys(details.network?.papers || {}).length);$("paper-count").textContent=String(count);if(output){$("empty-state").classList.add("hidden");$("result-note").textContent=t("extensionNote");}}
  }
};
$("task-kind").addEventListener("change",()=>{window.OptionalExtensions.userSelected=true;window.OptionalExtensions.refresh();});
$("lang-en").addEventListener("click",()=>window.OptionalExtensions.refresh());$("lang-zh").addEventListener("click",()=>window.OptionalExtensions.refresh());
$("download-extension").addEventListener("click",()=>{const output=(currentResult?.result || currentResult)?.extension_result;if(!output)return;const blob=new Blob([output.report_markdown],{type:"text/markdown;charset=utf-8"}),url=URL.createObjectURL(blob),anchor=document.createElement("a");anchor.href=url;anchor.download=`idea2prior-${output.name}.md`;anchor.click();URL.revokeObjectURL(url);});
applyTranslations();window.OptionalExtensions.refresh();if(currentSnapshot)window.OptionalExtensions.render(currentSnapshot);
