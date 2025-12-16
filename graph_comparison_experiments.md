# Graph Comparison Feature - Experimental Evaluation Plan

## 1. Graph Extraction and Indexing Evaluation

### 1.1 Extraction Success Rate

- **Metric**: Percentage of graphs successfully extracted from PDFs
- **Test Cases**:
  - Extract graphs from a curated set of 50-100 papers across different domains (CV, NLP, ML)
  - Compare extraction rate across different PDF formats (arXiv, conference proceedings, journal articles)
  - Measure impact of PDF quality (scanned vs. native PDFs, resolution, compression)
- **Baseline**: Manual count of graphs per paper
- **Expected Issues**: Missing captions, corrupted images, complex layouts

### 1.2 Caption Extraction Accuracy
- **Metric**: Precision/Recall of extracted captions vs. ground truth
- **Test Cases**:
  - Manually annotate 200-300 graphs with ground truth captions
  - Evaluate caption extraction for different caption formats (Figure X, Table Y, etc.)
  - Test handling of multi-paragraph captions and cross-references
- **Baseline**: Mineru's default caption extraction

### 1.3 Metadata Completeness

- **Metric**: Percentage of graphs with complete metadata (page number, caption, source PDF)
- **Test Cases**:
  - Measure metadata completeness across different paper sources
  - Evaluate handling of edge cases (missing page numbers, duplicate captions)

## 2. Similarity Search Evaluation

### 2.1 Retrieval Performance (Precision@K, Recall@K, MRR)
- **Metric**: Precision@K, Recall@K, Mean Reciprocal Rank (MRR)
- **Test Cases**:
  - Create a labeled dataset of query-graph pairs:
    - 50 queries covering different graph types (performance curves, architecture diagrams, ablation studies, confusion matrices)
    - For each query, manually label relevant graphs (top-10 most relevant)
  - Evaluate across different query types:
    - Text queries: "accuracy comparison curves", "architecture diagrams", "ablation study results"
    - Image queries: Provide example graph images and find similar ones
  - Test on different domains (3D detection, NLP, general ML)
- **Baselines**: 
  - Random retrieval
  - Keyword-based search (BM25 on captions)
  - Visual-only similarity
  - Semantic-only similarity

### 2.2 Visual vs Semantic vs Combined Performance
- **Metric**: Precision@K for each modality
- **Test Cases**:
  - Compare performance of:
    - Visual-only similarity (CLIP embeddings)
    - Semantic-only similarity (text embeddings on captions)
    - Combined (60% visual, 40% semantic)
    - Different weight combinations (50-50, 70-30, 80-20)
- **Hypothesis**: Combined should outperform individual modalities, but optimal weights may vary by query type

### 2.3 Query Type Analysis
- **Metric**: Performance breakdown by query category
- **Test Cases**:
  - Performance curves queries (e.g., "accuracy over epochs")
  - Architecture diagrams queries (e.g., "neural network architecture")
  - Ablation study queries (e.g., "component contribution analysis")
  - Comparison charts queries (e.g., "method comparison bar charts")
- **Expected**: Different modalities may excel for different query types

### 2.4 Cross-Domain Generalization
- **Metric**: Retrieval performance across different research domains
- **Test Cases**:
  - Test on papers from: Computer Vision, NLP, General ML, Robotics, etc.
  - Measure if performance degrades when training/embedding models are domain-agnostic
  - Evaluate transfer learning scenarios

## 3. Pairwise Comparison Quality Evaluation

### 3.1 Comparison Report Quality (Human Evaluation)
- **Metric**: Expert ratings on comparison quality (1-5 scale)
- **Test Cases**:
  - Generate comparisons for 50-100 graph pairs
  - Have domain experts rate:
    - Completeness: Does it cover all key similarities/differences?
    - Accuracy: Are the observations correct?
    - Usefulness: Would this help a researcher?
    - Clarity: Is the comparison easy to understand?
- **Baseline**: Manual expert-written comparisons for same pairs

### 3.2 Comparison Dimension Coverage
- **Metric**: Percentage of key dimensions covered (similarities, differences, performance, patterns)
- **Test Cases**:
  - Annotate ground truth comparison dimensions for 50 graph pairs
  - Measure which dimensions the system identifies vs. misses
  - Evaluate false positives (incorrectly identified similarities/differences)

### 3.3 Agreement with Expert Annotations
- **Metric**: Inter-annotator agreement (Cohen's Kappa) between system and experts
- **Test Cases**:
  - Have 3-5 experts independently compare same graph pairs
  - Compare system output to expert consensus
  - Measure agreement on: similarity claims, difference claims, performance rankings

### 3.4 Multimodal vs Text-Only Comparison
- **Metric**: Quality improvement when using images vs. captions only
- **Test Cases**:
  - Generate comparisons with full multimodal input (images + captions)
  - Generate comparisons with text-only (captions only)
  - Compare quality metrics
- **Hypothesis**: Multimodal should provide richer insights, especially for visual patterns

## 4. Integration with Research Tasks Evaluation

### 4.1 Benchmark Landscape Task Integration
- **Metric**: Time savings, quality of identified benchmarks
- **Test Cases**:
  - Use graph comparison in benchmark landscape analysis for 3-5 topics
  - Measure:
    - Time to identify key performance graphs vs. manual search
    - Number of relevant graphs discovered
    - Accuracy of identified state-of-the-art results
- **Baseline**: Manual benchmark landscape analysis

### 4.2 Prior Art Check Integration
- **Metric**: Detection of visual evidence for prior art
- **Test Cases**:
  - Run prior art check on 10-20 research ideas
  - Use graph comparison to find papers with similar experimental results
  - Measure:
    - True positives: Correctly identified prior art with visual evidence
    - False positives: Incorrectly flagged as prior art
    - False negatives: Missed prior art that has similar graphs
- **Baseline**: Text-only prior art checking

### 4.3 End-to-End Workflow Evaluation
- **Metric**: User satisfaction, task completion time
- **Test Cases**:
  - Have researchers use the full pipeline (extraction → search → comparison) for real research tasks
  - Collect feedback via surveys/interviews
  - Measure time savings vs. manual process

## 5. Ablation Studies

### 5.1 Embedding Model Comparison
- **Metric**: Retrieval performance with different embedding models
- **Test Cases**:
  - Visual embeddings: CLIP-ViT-B/32, CLIP-ViT-L/14, other vision models
  - Semantic embeddings: OpenAI text-embedding-3-small, text-embedding-ada-002, other text models
  - Measure trade-offs: accuracy vs. computation time vs. model size

### 5.2 Weight Combination Analysis
- **Metric**: Optimal visual/semantic weight ratio
- **Test Cases**:
  - Test weight combinations: 100-0, 80-20, 60-40, 50-50, 40-60, 20-80, 0-100
  - Measure performance for different query types
  - Identify if optimal weights are query-dependent

### 5.3 Top-K Selection Impact
- **Metric**: How top-K value affects downstream comparison quality
- **Test Cases**:
  - Vary K from 3 to 20
  - Measure: retrieval quality, comparison report quality, computation time
  - Find optimal K for different use cases

### 5.4 Caption Quality Impact
- **Metric**: Performance degradation with missing/poor captions
- **Test Cases**:
  - Artificially remove or corrupt captions for subset of graphs
  - Measure impact on semantic similarity and overall retrieval
  - Evaluate fallback strategies

## 6. Scalability and Performance

### 6.1 Corpus Size Scalability
- **Metric**: Computation time, memory usage vs. corpus size
- **Test Cases**:
  - Test on corpora of size: 10, 50, 100, 500, 1000 papers
  - Measure:
    - Graph extraction time
    - Embedding computation time
    - Similarity search time
    - Memory footprint
- **Expected**: Linear or sub-linear scaling with caching

### 6.2 Batch Processing Efficiency
- **Metric**: Throughput (graphs processed per second)
- **Test Cases**:
  - Compare batch vs. sequential embedding computation
  - Measure impact of batch size on speed and memory
  - Optimize batch processing pipeline

### 6.3 Caching Effectiveness
- **Metric**: Cache hit rate, time savings
- **Test Cases**:
  - Run multiple queries over same corpus
  - Measure cache hit rate and time savings
  - Evaluate cache invalidation strategies

## 7. Error Analysis and Failure Cases

### 7.1 Failure Mode Analysis
- **Metric**: Categorization and frequency of failure modes
- **Test Cases**:
  - Identify common failure patterns:
    - Low-quality graph extraction
    - Poor similarity matches
    - Inaccurate comparisons
    - Missing relevant graphs
  - Analyze root causes and propose improvements

### 7.2 Edge Case Handling
- **Metric**: Success rate on challenging cases
- **Test Cases**:
  - Hand-drawn diagrams
  - Complex multi-panel figures
  - Graphs with unusual formats
  - Low-resolution images
  - Non-English captions

## 8. User Study

### 8.1 Usability Study
- **Metric**: Task completion time, user satisfaction, error rate
- **Test Cases**:
  - Recruit 10-20 researchers from different domains
  - Give them research tasks requiring graph comparison
  - Measure:
    - Time to complete tasks
    - Number of errors
    - User satisfaction (SUS score)
    - Learning curve

### 8.2 Comparison with Manual Process
- **Metric**: Time savings, quality comparison
- **Test Cases**:
  - Same researchers perform tasks manually
  - Compare time, quality, completeness
  - Identify tasks where automation is most/least beneficial

## 9. Dataset Creation

### 9.1 Curated Test Dataset
- **Components**:
  - 100-200 papers across multiple domains
  - 500-1000 extracted graphs with metadata
  - 50-100 labeled queries with ground truth relevant graphs
  - 100-200 graph pairs with expert comparison annotations
  - Domain annotations (CV, NLP, ML, etc.)
  - Graph type annotations (performance curve, architecture, ablation, etc.)

### 9.2 Benchmark Dataset Release
- **Goal**: Create publicly available benchmark for graph comparison in scientific literature
- **Components**:
  - Standardized evaluation protocol
  - Evaluation scripts
  - Baseline results
  - Leaderboard (if applicable)

## 10. Quantitative Metrics Summary

### Key Metrics to Report:
1. **Extraction**: Success rate, caption accuracy, metadata completeness
2. **Retrieval**: Precision@1, Precision@5, Precision@10, Recall@10, MRR
3. **Comparison**: Expert rating scores, dimension coverage, agreement scores
4. **Integration**: Time savings, accuracy improvements, user satisfaction
5. **Performance**: Throughput, latency, memory usage, scalability curves

### Statistical Analysis:
- Statistical significance tests (t-tests, Wilcoxon signed-rank)
- Confidence intervals for all metrics
- Error bars on all plots
- Ablation study results with clear comparisons

