# Evolution Mapper Evaluation

## Overview

This evaluation framework uses Connected Papers as ground truth and LLM (ChatGPT) as a judge to evaluate the Evolution Mapper tool.

## Ground Truth Data

Three Connected Papers datasets are provided:

1. **Transformers in NLP** (`ConnectedPapers-for-Attention-is-All-you-Need.bib`)
   - Seed paper: "Attention is All You Need" (2017)
   - Evolution: BERT → GPT → T5 → LLaMA
   - Evaluation focus: Coverage, representativeness, narrative quality

2. **Generative Adversarial Networks** (`ConnectedPapers-for-Generative-Adversarial-Nets.bib`)
   - Seed paper: "Generative Adversarial Nets" (2014)
   - Evolution: DCGAN → WGAN → StyleGAN
   - Evaluation focus: Temporal evolution, key paper coverage, link rationality

3. **CNN/ResNet Architecture Evolution** (`ConnectedPapers-for-ImageNet-classification-with-deep-convolutional-neural-networks.bib`)
   - Seed paper: "ImageNet classification with deep convolutional neural networks" (2012)
   - Evolution: AlexNet → VGG → ResNet → DenseNet
   - Evaluation focus: Key node identification, graph structure quality

## Evaluation Criteria

The LLM Judge evaluates the following dimensions (0-10 scale):

1. **Paper Discovery Coverage** (0-10)
   - How many papers from Connected Papers are also in our output?
   - Are key papers from Connected Papers included?
   - Are there important papers missing?

2. **Citation Relationship Accuracy** (0-10)
   - Do citation relationships match Connected Papers?
   - Are the relationships correctly represented?
   - Are there incorrect relationships?

3. **Network Structure Similarity** (0-10)
   - Does the network structure match Connected Papers?
   - Are key papers (high citations) correctly identified?
   - Is the overall topology similar?

4. **Additional Value** (0-10)
   - Does our tool provide additional insights beyond Connected Papers?
   - Are there unique features (e.g., timeline, annotations)?
   - Is the output format more useful?

## Success Criteria

- **Paper Coverage** > 70%: Our tool discovers most papers from Connected Papers
- **Citation Relationship Accuracy** > 80%: Citation relationships are mostly correct
- **LLM Overall Score** > 7.0/10: LLM considers our tool quality good
- **Additional Value** > 6.0/10: Our tool provides extra value

## Directory Structure

```
evaluation/evolution_mapper/
├── README.md                          # This file
├── ground_truth/                      # Ground truth BIB files
│   ├── ConnectedPapers-for-Attention-is-All-you-Need.bib
│   ├── ConnectedPapers-for-Generative-Adversarial-Nets.bib
│   └── ConnectedPapers-for-ImageNet-classification-with-deep-convolutional-neural-networks.bib
├── scripts/                          # Evaluation scripts
│   ├── evaluate_evolution_mapper.py  # Main evaluation pipeline
│   └── bib_parser.py                 # BIB file parser
└── results/                          # Evaluation results
    ├── transformers_evaluation.json
    ├── gan_evaluation.json
    └── imagenet_evaluation.json
```

## Usage

```bash
# Run evaluation for all topics
python evaluation/evolution_mapper/scripts/evaluate_evolution_mapper.py

# Run evaluation for a specific topic
python evaluation/evolution_mapper/scripts/evaluate_evolution_mapper.py --topic transformers
```

## Environment Variables

Set the following environment variables:

```bash
# ChatGPT API key for LLM Judge
export OPENAI_API_KEY="sk-proj-..."

# Semantic Scholar API key (for Evolution Mapper)
export SEMANTIC_SCHOLAR_API_KEY="..."

# Gemini API key (for Evolution Mapper main model)
export GEMINI_API_KEY="..."
```

