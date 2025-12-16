## 4.4 Cross-Paper Graph Comparison Evaluation

We evaluated the cross-paper graph comparison pipeline on nine PDFs in `pdf_cache`, exercising parsing, graph extraction, and multimodal retrieval. The experiment was run with Mineru parsing (pipeline backend), graph extraction via `ImageExtractorTool`, and graph comparison via `GraphComparisonTool` across six query intents (performance curves, architecture diagrams, ablation, etc.) and three comparison modes (visual, semantic, all).

### Setup
- Corpus: 9 research PDFs (mixed arXiv/ACM/IEEE).
- Parsing: Mineru pipeline backend, with VLM fallback enabled; outputs inspected from both `auto/` and `vlm/` directories.
- Graph extraction: graphs only (tables disabled) with caption retention.
- Queries: `["accuracy comparison curves", "performance graphs", "architecture diagrams", "ablation study results", "experimental results", "method comparison"]` × modes `{visual, semantic, all}` (18 comparisons total).
- Metrics logged to `experiments/results/graph_comparison_experiment_*.json`; human-readable summary in `experiments/results/record.md`.

### Results
- PDFs processed: 9; successful extractions: 8; failed: 1 (11.1% failure).
- Graphs extracted: 137; captions present for 134 (97.8% coverage); avg 15.2 graphs/PDF.
- Comparison queries: 18; successful: 12; failed: 6; avg comparison time: 6.73 s.
- Retrieval: avg 5.0 graphs found per successful query.

\begin{table*}[t]
\centering
\footnotesize
\setlength{\tabcolsep}{5pt}
\begin{tabular}{lcccccc}
\toprule
\textbf{Metric} & \textbf{Value} & \textbf{Success} & \textbf{Failures} & \textbf{Avg/Rate} & \textbf{Notes} \\
\midrule
PDFs processed & 9 & 8 & 1 & 88.9\% success & One PDF parse dropout \\
Graphs extracted & 137 & 134 w/ captions & 3 w/o captions & 15.2 per PDF & 97.8\% caption coverage \\
Comparison queries & 18 & 12 & 6 & 6.73 s/query & Across visual / semantic / all \\
Retrieval results & -- & 5.0 top-$k$ & -- & per successful query & Combined visual+semantic \\
\bottomrule
\end{tabular}
\caption{
Cross-paper graph comparison evaluation summary. Each run parsed PDFs with Mineru, extracted graphs, and executed 18 comparison queries across visual, semantic, and combined modes. Failures stem from missing content lists for a subset of PDFs.
}
\label{tab:cross_paper_graph_comparison}
\end{table*}

### Observations
- High caption coverage (97.8%) indicates Mineru+extraction produced rich text context for semantic retrieval.
- One PDF failed parsing; remaining PDFs produced content lists under either `auto/` or `vlm/`, confirming dual-directory handling is necessary.
- Failures in comparisons correlate with missing content lists for specific PDFs; when available, both visual and semantic modes returned ~5 candidates/query.

### Limitations and Next Steps
- Remaining dependency sensitivity: ensure `shapely`, `ftfy`, `ultralytics`, and GPU drivers are present to avoid silent parsing dropouts.
- Add a pre-flight check: verify both content list and MD outputs exist before comparison; skip/flag missing PDFs earlier.
- Consider caching embeddings to reduce ~6–7 s/query latency when scaling to larger corpora.
- Expand evaluation with precision/recall against hand-labeled relevance judgments to quantify retrieval quality, not just throughput.

