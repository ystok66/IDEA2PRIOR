evaluation/code_extractor/
├── README.md                    # Usage instructions and quick start
├── DESIGN.md                    # Design rationale and technical architecture
├── RESULTS_ANALYSIS.md          # Evaluation results analysis
├── FILE_STRUCTURE.md            # This file (directory structure overview)
│
├── ground_truth/                # Ground Truth data
│   └── code_extractor_ground_truth.json
│
├── scripts/                     # Evaluation scripts
│   ├── evaluate_code_extractor.py  # Main evaluation script
│   ├── setup_pdf.sh            # PDF setup script
│   └── test_ground_truth.py    # Ground Truth validation script
│
├── results/                     # Evaluation outputs (generated after execution)
│   ├── paper_*_evaluation.json         # Full evaluation results in JSON format
│   └── paper_*_evaluation_summary.md   # Human-readable summary in Markdown format
│
├── pdfs/                        # PDF files (to be added manually)
│   └── *.pdf
│
└── .gitignore                   # Git ignore configuration
