# Graph Comparison Experiment

This directory contains the experimental evaluation script for the graph comparison feature.

## Usage

### Basic Usage

```bash
python experiments/graph_comparison_experiment.py
```

This will:
1. Load all PDFs from `./pdf_cache`
2. Parse them with mineru (if needed)
3. Extract graphs from each PDF
4. Run comparison queries
5. Save results to `./experiments/results/`

### Command Line Options

```bash
python experiments/graph_comparison_experiment.py \
    --pdf-cache ./pdf_cache \
    --output-dir ./experiments/parsed_pdfs \
    --results-dir ./experiments/results \
    --task-name graph_comparison_experiment
```

### Prerequisites

1. **mineru** must be installed for PDF parsing
2. **Embedding models** must be configured:
   - Vision embedding: CLIP model (default: `openai/clip-vit-base-patch32`)
   - Text embedding: OpenAI API key must be set in environment

### Environment Variables

```bash
export OPENAI_API_KEY=your_key_here
```

### Output

The script generates:
1. **Parsed PDFs**: Stored in `--output-dir` (default: `./experiments/parsed_pdfs/`)
   - Each PDF gets a directory with `auto/` subdirectory
   - Contains `*_content_list.json` with graph metadata
   - Contains extracted images in `auto/` directory

2. **Extracted Images**: Stored in `./imgs/<task_name>/<pdf_name>/graphs/`
   - Organized by PDF name
   - Named with graph index and caption

3. **Experiment Results**: JSON file in `--results-dir`
   - Timestamped filename: `graph_comparison_experiment_YYYYMMDD_HHMMSS.json`
   - Contains all metrics and detailed results

### Metrics Recorded

#### Extraction Metrics (per PDF):
- `success`: Whether extraction succeeded
- `graphs_extracted`: Number of graphs found
- `tables_extracted`: Number of tables found
- `graphs_with_captions`: Graphs with extracted captions
- `graphs_without_captions`: Graphs missing captions
- `extraction_time`: Time taken for extraction

#### Comparison Metrics (per query):
- `query`: The search query text
- `comparison_type`: visual, semantic, or all
- `success`: Whether comparison succeeded
- `graphs_found`: Number of similar graphs found
- `similarity_scores`: List of similarity scores
- `comparison_time`: Time taken for comparison

#### Summary Metrics:
- Total PDFs processed
- Extraction success rate
- Caption coverage rate
- Average graphs per PDF
- Average extraction/comparison times
- Query success rates

### Example Output

```
============================================================
EXPERIMENT SUMMARY
============================================================
Total PDFs processed: 3
Successful extractions: 3
Failed extractions: 0
Extraction success rate: 100.00%

Total graphs extracted: 15
Total tables extracted: 5
Graphs with captions: 12
Graphs without captions: 3
Caption coverage rate: 80.00%
Average graphs per PDF: 5.00
Average extraction time: 2.34s

Total comparison queries: 18
Successful comparisons: 18
Failed comparisons: 0
Average comparison time: 1.23s
Average graphs found per query: 4.50
Average similarity score: 0.756
============================================================
```

### Troubleshooting

1. **mineru not available**: Install mineru or ensure it's in your Python path
2. **No graphs extracted**: Check if PDFs are properly parsed (content_list.json exists)
3. **Embedding errors**: Verify API keys and model availability
4. **Memory issues**: Process fewer PDFs at a time or increase system memory

### Next Steps

After running the experiment:
1. Review the JSON results file
2. Analyze metrics for extraction quality
3. Evaluate comparison query performance
4. Use results to improve the system

