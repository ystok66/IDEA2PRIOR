# Evolution Mapper Evaluation - Usage Guide

## Quick Start

### 1. Set Environment Variables

```bash
# Required: ChatGPT API key for LLM Judge
export OPENAI_API_KEY="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"

# Optional: Semantic Scholar API key (for Evolution Mapper)
export SEMANTIC_SCHOLAR_API_KEY="your_key_here"
```

### 2. Test BIB Parser

```bash
# Test parsing all ground truth files
python evaluation/evolution_mapper/scripts/test_bib_parser.py
```

### 3. Run Evaluation

```bash
# Evaluate all topics (default: 3 evaluations averaged)
python evaluation/evolution_mapper/scripts/evaluate_evolution_mapper.py

# Evaluate specific topic
python evaluation/evolution_mapper/scripts/evaluate_evolution_mapper.py --topic transformers

# Evaluate with custom max_papers
python evaluation/evolution_mapper/scripts/evaluate_evolution_mapper.py --topic gan --max-papers 15

# Evaluate with multiple runs and averaging (recommended for reliability)
python evaluation/evolution_mapper/scripts/evaluate_evolution_mapper.py --topic transformers --num-evaluations 5
```

## Evaluation Topics

1. **transformers**: Attention is All You Need, Transformer, BERT, GPT
2. **gan**: Generative Adversarial Networks, GAN, DCGAN, WGAN
3. **imagenet**: ImageNet classification, deep convolutional neural networks, AlexNet, ResNet

## Output Files

Results are saved in `evaluation/evolution_mapper/results/`:

- `{topic}_evaluation.json`: Full evaluation results (JSON)
- `{topic}_evaluation_summary.md`: Human-readable summary (Markdown)

## Evaluation Process

1. **Load Ground Truth**: Parse BIB file from Connected Papers
2. **Run Evolution Mapper**: Generate evolution map for the topic (only once, as it's deterministic)
3. **LLM Judge Evaluation**: Use ChatGPT to compare outputs (multiple times, default: 3)
4. **Average Results**: Calculate mean and standard deviation across all evaluations
5. **Save Results**: Save JSON and Markdown summaries (including all individual evaluations)

## Evaluation Metrics

The LLM Judge evaluates 4 dimensions (0-10 scale):

1. **Paper Coverage** (0-10): How many papers from Connected Papers are found?
2. **Citation Accuracy** (0-10): Are citation relationships correct?
3. **Network Similarity** (0-10): Does the network structure match?
4. **Additional Value** (0-10): Does our tool provide unique insights?

**Overall Score**: Average of the 4 dimensions

### Multiple Evaluations and Averaging

By default, the evaluation runs **3 times** and averages the results to improve reliability:
- **Mean scores**: Average across all successful evaluations
- **Standard deviation**: Measures consistency across runs
- **Individual evaluations**: All individual results are saved in the JSON file
- **Common feedback**: Strengths, weaknesses, and recommendations are aggregated from all runs

This approach reduces the impact of LLM randomness and provides more reliable evaluation scores.

## Success Criteria

- **Paper Coverage** > 70%
- **Citation Accuracy** > 80%
- **Overall Score** > 7.0/10
- **Additional Value** > 6.0/10

## Troubleshooting

### Import Errors

If you get import errors, make sure you're running from the project root:

```bash
cd /Users/jay/Documents/ml_coding/agent/PaperArena
python evaluation/evolution_mapper/scripts/evaluate_evolution_mapper.py
```

### API Key Issues

Make sure your OpenAI API key is set:

```bash
echo $OPENAI_API_KEY
```

### Semantic Scholar API

If you don't have a Semantic Scholar API key, the evaluation will still work but Evolution Mapper may have limited functionality. You can get a free API key from: https://www.semanticscholar.org/product/api

## Example Output

```json
{
  "topic": "transformers",
  "seed_paper": "Attention is All You Need",
  "evaluation": {
    "scores": {
      "paper_coverage": 8.5,
      "citation_accuracy": 7.8,
      "network_similarity": 8.0,
      "additional_value": 7.5
    },
    "overall_score": 7.95,
    "strengths": [
      "Good coverage of key papers",
      "Clear timeline visualization"
    ],
    "weaknesses": [
      "Missing some recent papers",
      "Citation relationships could be more accurate"
    ]
  }
}
```

