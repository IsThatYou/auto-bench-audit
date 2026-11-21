# Config Directory Guide

This directory contains benchmark collection and audit configs. The batch static audit pipeline auto-discovers only configs under `multi_domain_all/*/*.yaml`.

## Top-level directories

| Directory | YAML configs | Purpose |
| --- | ---: | --- |
| `multi_domain_all/` | 143 | Main batch-audit inventory, organized by paper-facing benchmark domain. This is what `scripts/batch_static_audit_pipeline.sh` discovers by default. |
| `harbor/` | 1 | Harbor trajectory-generation configs, used with `bench-audit generate` before auditing generated run collections. `generate` also writes `collection_<name>.yaml` here (ignored by git). |

One-off audits (`bench-audit audit <url>`) do not write here. Their config goes next to the clone, at `data/benchmark_repos/<name>/config.yaml`.

135 of the 143 configs say `benchmark_type: neurips`. That is the older name for `static` (a benchmark audited from its task definitions). Both names work. The old name stays in these files because it is part of the output path of the paper's runs.

## `multi_domain_all` domains

| Domain directory | YAML configs | Use for |
| --- | ---: | --- |
| `agent_interactive/` | 21 | Agentic, tool-use, browser/computer-use, and interactive environment benchmarks. |
| `code_swe/` | 25 | Coding, SWE, terminal, repository repair, and programming-agent benchmarks. |
| `medical_health/` | 12 | Medical, clinical, health, diagnostics, and biomedical benchmarks. |
| `multimodal_vision/` | 34 | Vision-language, document understanding, OCR, chart/image, and visual reasoning benchmarks. |
| `professional_economic_work/` | 7 | Finance, business, office, economics, and professional-work benchmarks. |
| `reasoning_math/` | 10 | Math, formal reasoning, contest, and symbolic reasoning benchmarks. |
| `retrieval_rag/` | 3 | Retrieval, search, RAG, web/navigation, and knowledge-grounded QA benchmarks. |
| `safety_alignment/` | 18 | Safety, alignment, risk, unlearning, deception, and robustness benchmarks. |
| `science_expert_reasoning/` | 13 | Science, expert knowledge, physics, chemistry, GPQA/HLE-style, and specialist reasoning benchmarks. |

## Batch audit usage

Run all domain configs:

```bash
bash scripts/batch_static_audit_pipeline.sh --max-tasks 100 --batch 50 --workers 3
```

Run one domain:

```bash
bash scripts/batch_static_audit_pipeline.sh --domain code_swe --max-tasks 100 --batch 50 --workers 3
```

Run specific configs:

```bash
bash scripts/batch_static_audit_pipeline.sh \
  --config configs/multi_domain_all/science_expert_reasoning/gpqa_diamond.yaml \
  --config configs/multi_domain_all/code_swe/frontier_swe.yaml
```

Use another agent or model for the whole batch, without editing configs:

```bash
bash scripts/batch_static_audit_pipeline.sh --domain code_swe --agent-cli codex --model gpt-5
```
