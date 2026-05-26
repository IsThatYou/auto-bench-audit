# BixBench-Verified-50

A curated subset of [BixBench](https://huggingface.co/datasets/futurehouse/BixBench) with 50 verified questions across 33 unique data capsules, designed for reliable evaluation of AI agents on computational biology tasks.

## Overview

BixBench-Verified-50 was created to isolate real agent performance from benchmark issues. Starting from the full BixBench benchmark, we sampled questions and identified problematic ones. Some were removed entirely. For others, we revised the question text for clarity or corrected the expected answer, while being careful not to overcorrect: we used our best judgment to leave in questions where we believe a competent expert should be able to fill in the details themselves. Questions were reviewed together with several domain experts to ensure correctness of ground truth, sufficiency of provided context, and clarity of the expected answer.

For more context on why we created this curated subset and our approach to evaluating AI agents in biology, see our blog post: [Evaluating AI Agents in Biology: Why We Need to Look Beyond the Final Answer](https://phylo.bio/blog/evaluating-ai-agents-in-biology-why-we-need-to-look-beyond-the-final-answer).

## Revision Notes

Of the 50 questions, 17 were revised from the original BixBench training set. Changes include clarifying question wording, specifying tools or methods based on the groundtruth, and correcting ideal answers where needed. A detailed comparison is provided in **`sample50_comparison.csv`**, which contains the original and updated question/ideal for each question along with notes describing what was changed and why.

## Dataset Statistics

| Property | Value |
|----------|-------|
| Total questions | 50 |
| Unique data capsules | 33 |
| Evaluation modes | `llm_verifier` (20), `str_verifier` (17), `range_verifier` (13) |
| Categories covered | 19 distinct category combinations |

## Files

- **`BixBench-Verified-50.jsonl`** — The dataset file with all 50 questions (one per line).
- **`sample50_comparison.csv`** — Comparison of original vs. revised questions/answers with notes on each change.
- **`CapsuleFolder-{uuid}.zip`** — Data capsules containing the underlying data for each question. Each zip contains:
  - `CapsuleData-{uuid}/` — The data files needed to answer the question.
  - `CapsuleNotebook-{uuid}/` — Reference notebook with executed analysis.

## Schema

Each row in the JSONL file contains the following fields:

| Field | Type | Description |
|-------|------|-------------|
| `question_id` | string | Unique question identifier |
| `question` | string | The benchmark question |
| `ideal` | string | The ideal/correct answer |
| `answer` | boolean | Whether the hypothesis was confirmed |
| `hypothesis` | string | The scientific hypothesis being tested |
| `result` | string | Results summary |
| `eval_mode` | string | Evaluation method (`str_verifier`, `range_verifier`, or `llm_verifier`) |
| `capsule_uuid` | string | UUID of the associated data capsule |
| `data_folder` | string | Name of the capsule zip file |
| `categories` | string | Research domain categories |
| `paper` | string | Associated paper reference |
| `distractors` | list | Incorrect answer choices (for multiple-choice evaluation) |
| `canary` | string | Canary string for data provenance tracking |

## Usage

```python
from datasets import load_dataset

dataset = load_dataset("yuanhaoqu/BixBench-Verified-50")
```

Or load directly with pandas:

```python
import pandas as pd

df = pd.read_json("BixBench-Verified-50.jsonl", lines=True)
```

## Local Copy in This Repository

The dataset file, comparison CSV, and smaller capsules are checked in. Ten capsule zips exceed GitHub's 100 MB per-file limit and are omitted (and listed in `.gitignore` so they cannot be re-staged accidentally):

| Size  | File                                                            |
|-------|-----------------------------------------------------------------|
| 203 M | `CapsuleFolder-37829656-f147-5fd2-8f10-40fa15cfdf03.zip`        |
| 212 M | `CapsuleFolder-d38392ec-84b8-485d-ab02-b8788e6f1f43.zip`        |
| 212 M | `CapsuleFolder-ebc7c8a6-ba34-46db-bf8c-b3e310434ba9.zip`        |
| 219 M | `CapsuleFolder-17baa727-5eb7-4544-a466-3148a48b3cde.zip`        |
| 219 M | `CapsuleFolder-4cb6f8ce-4d81-40b2-8d9c-85868796ee73.zip`        |
| 219 M | `CapsuleFolder-975f3e91-53b0-44b1-ac9f-20023d9c8cd0.zip`        |
| 219 M | `CapsuleFolder-c66d3ed9-0a95-46be-a237-ed68498ea7f6.zip`        |
| 219 M | `CapsuleFolder-cd811ead-7887-4369-b175-05aff4223765.zip`        |
| 234 M | `CapsuleFolder-5ddf0a38-bc41-5ebb-93af-88c62ca29be7.zip`        |
| 459 M | `CapsuleFolder-bda54b38-9b49-4009-aad6-d5f52675438a.zip`        |

To restore them, re-download the dataset from Hugging Face (`yuanhaoqu/BixBench-Verified-50`) and copy the listed zips into this directory.

## Use in Our Paper

We use BixBench-Verified-50 as one of the two task sets for the BenchGuard validation in our benchmark-audit paper. We run ABA on this subset and compare findings against BenchGuard's gold-issue set, using the same alignment judge and matched Claude Opus 4.6 setup as the BenchGuard single-model baseline.


## License

Apache 2.0 (same as the original BixBench dataset).
