# Frontier Benchmark Configs

These configs use the existing `neurips`-style schema for compatibility with the generic benchmark-repo workflow.

`code_url` selection notes:
- `browsecomp.yaml`: uses `openai/simple-evals`, which explicitly hosts the BrowseComp reference implementation.
- `finance_agent.yaml`: uses the benchmark's dedicated repo `vals-ai/finance-agent`.
- `mcp_atlas.yaml`: uses the benchmark's dedicated repo `scaleapi/mcp-atlas`.
- `tau2_bench_telecom.yaml`: uses the dedicated `sierra-research/tau2-bench` repo.
- `toolathlon.yaml`: uses the dedicated `hkust-nlp/Toolathlon` repo.
- `gdpval_aa.yaml`: uses `ArtificialAnalysis/Stirrup` as the closest public GitHub codebase for the GDPval-AA agentic evaluation setup; I did not find a standalone GDPval-AA benchmark repo.
- `osworld_verified.yaml`: uses `xlang-ai/OSWorld`, since OSWorld-Verified is a variant/eval setting on top of OSWorld rather than a separate repo.
- `mmmu_pro.yaml`: uses `MMMU-Benchmark/MMMU`, since MMMU-Pro is part of that benchmark family.
- `omnidocbench.yaml`: uses the benchmark's dedicated repo `opendatalab/OmniDocBench`.
- `gpqa_diamond.yaml`: uses `idavidrein/gpqa`.
- `humanitys_last_exam_hle.yaml`: uses `centerforaisafety/hle`.
- `imoanswerbench.yaml`: uses `google-deepmind/superhuman`, where IMOAnswerBench is released.
- `longbench_v2.yaml`: uses `THUDM/LongBench`, which is the public repo for the LongBench benchmark family including LongBench v2.
- `frontier_swe.yaml`: uses the benchmark's dedicated repo `Proximal-Labs/frontier-swe` (ultra long-horizon coding agent benchmark covering implementation, performance engineering, and ML research).
