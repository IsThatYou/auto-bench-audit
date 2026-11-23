<h1 align="center">Automated Benchmark Auditing for AI Agents and LLMs
</h1>

<p align="center">
  <a href="https://arxiv.org/abs/2605.26079">
    <img alt="arXiv" src="https://img.shields.io/badge/arXiv-2605.26079-b31b1b.svg">
  </a>
  <a href="https://autobenchaudit.com">
    <img alt="Website" src="https://img.shields.io/badge/Website-autobenchaudit.com-4c8bf5.svg">
  </a>
  <a href="https://www.python.org/downloads/release/python-3120/">
    <img alt="Python" src="https://img.shields.io/badge/python-3.12+-blue.svg">
  </a>
</p>

**`auto-bench-audit`** (ABA) is an agentic pipeline that audits LLM and agent benchmarks for task ambiguity, environment conflicts, and evaluation defects — issues that even original benchmark authors miss. It operates across agentic, patch-based, and static-QA benchmarks under a single protocol: (1) a benchmark-level audit, (2) evidence collection that normalizes heterogeneous artifacts into a uniform task manifest, and (3) a per-task audit emitting structured findings (claim, severity, evidence file path, suggested fix).

## Setup

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
```

ABA does not call a model API itself. It starts an agent command-line tool as a subprocess, so one of these must be installed and signed in:

| `agent_cli` | Command | Sign-in |
| --- | --- | --- |
| `claude` (default) | [`claude`](https://docs.claude.com/en/docs/claude-code/overview) | `ANTHROPIC_API_KEY`, or the CLI's own login |
| `codex` | `codex` | the CLI's own login |
| `cursor` | `cursor-agent` | the CLI's own login |

Optional: `cp .env.example .env` to set API keys and data folders. `bench-audit` reads `.env` from the current folder and from the repo root. Without it, clones go to `./data/benchmark_repos` and results go to `./data/audit_runs`.

> [!WARNING]
> The auditor agent runs with its permission checks turned off (`claude --dangerously-skip-permissions`, `codex exec --yolo`) and with shell access, on a benchmark repository that you cloned from the internet. Run audits on a machine or in a container where that is acceptable, and audit only repositories you trust. Each phase also spends API credits or subscription usage.

## Use it from Claude Code or Codex

Paste this into Claude Code or Codex, in the folder of the benchmark you want to audit:

```text
Run `uvx --from git+https://github.com/IsThatYou/auto-bench-audit bench-audit skill` and follow what it prints to audit the benchmark in this folder.
```

The command prints a skill: instructions that tell the agent how to run the audit, what to ask you first (the audit starts agents with their permission checks off, and it costs money), and how to check and explain the findings. You need `uv` and the agent CLI, and nothing else. The first run downloads this repository (about 300 MB). To audit a public benchmark instead, end the sentence with its repository URL.

To keep the skill in Claude Code for every session:

```bash
uvx --from git+https://github.com/IsThatYou/auto-bench-audit bench-audit skill --install ~/.claude/skills
```

The skill itself is [`skills/bench-audit/SKILL.md`](skills/bench-audit/SKILL.md).

## Audit any benchmark in one command

Give `bench-audit audit` the GitHub URL of any public benchmark, or a local folder:

```bash
uv run bench-audit audit https://github.com/centerforaisafety/hle --sample-n 3
uv run bench-audit audit https://github.com/harbor-framework/terminal-bench-2 --sample-n 3
uv run bench-audit audit path/to/my-benchmark --sample-n 3
```

This clones the repo (or copies the folder, so files that are not committed are audited too and the agents do not change your folder) to `data/benchmark_repos/<name>/repo`, writes a minimal config to `data/benchmark_repos/<name>/config.yaml`, and runs the three audit phases end-to-end in static mode (`audit-benchmark` → `collect-evidence` → `audit-tasks --mode static --sample-n N`).

The command works for static-QA benchmarks (HLE, GPQA, MMLU-style) and agentic ones (SWE-bench, OSWorld, τ-bench) alike — the collector agent figures out each repo's structure on its own. For full control over phases, configs, trajectory-mode audits, and batch runs, see the rest of this README.

```bash
uv run bench-audit audit --help    # all flags
uv run bench-audit --help          # all commands
```

(`scripts/audit_one.sh --url <url>` still works. It calls `bench-audit audit`.)

## What you get

Results land in `data/audit_runs/<benchmark_type>/<name>__<output_subdir>__actual/`:

```
benchmark_audit.json          # phase 1: one verdict per rubric category
benchmark_audit_report.md
artifact_manifest.yaml        # phase 2: the list of tasks
task_configs/<task>/task_config.json
task_audits_static/<task>.json  # phase 3: findings for each task
audit_report_static.md        # summary table over all audited tasks
audit_summary_static.json     # the counts of the report, for scripts
```

The report counts a task as audited only when its record is the result of an audit. Two kinds of record are not: a record that a failed agent call left behind, and a record whose judgment is `insufficient_evidence` (the auditor could not judge the task, or the task's files are missing on this machine). The report shows these as their own number, `Not counted`, and leaves them out of `Tasks audited` and of `Tasks with a major finding`.

Each task audit holds a list of findings. This one is from Terminal-Bench 2, task `configure-git-webserver` (file paths shortened):

```json
{
  "category": "ambiguity",
  "subtype": "hidden-identifier",
  "severity": 2,
  "claim": "The test hardcodes the SSH username `git`, but the prompt's example shows `user@server`, giving no indication the username must be `git`.",
  "evidence": [
    {"path": "instruction.md:2", "note": "`git clone user@server:/git/server` — username appears as `user`."},
    {"path": "tests/verify.sh:21", "note": "Test clones with username `git`: `spawn git clone git@localhost:/git/server server-test`."}
  ],
  "suggested_fix": "State explicitly in the instruction that the server user must be named `git` ..."
}
```

The audits from the paper (168 benchmarks) are browsable at [autobenchaudit.com](https://autobenchaudit.com). The raw JSON is public at `https://data.autobenchaudit.com` (start from `index.json`).

## When an agent call fails

An agent call can fail: it reaches its time limit, the command crashes, or the answer is not valid JSON. `bench-audit` then does three things.

1. It tries the call again. `--retry-errors N` sets the number of extra attempts (default 1, and each attempt costs money; `0` turns retries off).
2. If the call still fails, the command finishes the rest of its work and writes a record with `"overall_judgment": "agent_error"`. It prints the failed tasks, and its exit code is `3`.
3. A record that says `agent_error` is not finished. Run the same command again: it skips every finished record and tries only the failed ones. `--force` is not needed.

Evidence collection has a time limit of 3600 seconds (`--timeout` on `collect-evidence`, `--collect-timeout` on `audit`, `run`, and `batch`). A finished collection is not collected again; pass `--force` to collect it again. The log of a failed attempt is kept in a `failed/` folder next to the conversation logs.

In a batch, a benchmark with a failed agent call is marked `failed` in the tracker, and its clone is kept. The next `bench-audit batch` tries only the failed parts again.

## When a task's files are missing

A task config names files: the task's data, its tests, its reference answer, and the paths in `audit_notes`. Before a task audit starts, `bench-audit` checks that these files are on disk. If one is missing (gated data, media that was not downloaded, a clone that was deleted), the task gets no agent call. Its record says `"overall_judgment": "insufficient_evidence"` and its `summary` lists the missing paths. The report shows the task under `Not counted`, and the exit code stays `0`.

Put the files in place and run the same command again: the check runs again for these tasks, and a task whose files are there now is audited. `--skip-input-check` on `audit-tasks` and `run` turns the check off.

The task-audit prompt has the matching rule for files that the check cannot see: a file that the auditor cannot open on its own machine is not a defect of the task.

## Batch of Benchmarks Audit Pipeline

For batch runs across many benchmarks, use `bench-audit batch`. It runs five stages per benchmark: `audit-benchmark` → `collect-evidence` → `sample-tasks` → `audit-tasks` → cleanup. A second run continues where the first one stopped, and tries failed agent calls again.

```bash
# Run all benchmarks under configs/multi_domain_all/
bench-audit batch

# Limit to one domain, 20 sampled tasks per benchmark
bench-audit batch --domain medical_health --max-tasks 20

# Run a specific config
bench-audit batch \
    --config configs/multi_domain_all/medical_health/clinbench.yaml

# Inspect tracker state
bench-audit batch --status
```

The pipeline writes its tracker to `$AUDIT_RUN_DIR/streaming_pipeline_tracking.json`, next to the results. By default it deletes each benchmark's clone and data after a full success; pass `--keep` to keep them.

(`bash scripts/batch_static_audit_pipeline.sh` still works. It calls `bench-audit batch` with the output folder `data/multi_domain_all`; set `MULTI_DOMAIN_AUDIT_RUN_DIR` to change it.)

## Step-by-Step Audit Pipeline

To run an audit phase by phase, use the CLI commands below in order: `audit-benchmark` → `collect-evidence` → `audit-tasks`. `bench-audit run --config <config>` runs all three in a row (`--start-phase` / `--end-phase` pick a part).

Each command takes a config. A minimal one:

```yaml
benchmark_name: clinbench
benchmark_type: static          # static | tb2 | swe_bench  (older configs say "neurips" for static)
agent_cli: claude               # claude | codex | cursor
model: claude-opus-4-7
output_subdir: v1

code_url: https://github.com/ismaelvillanuevamiranda/ClinBench
benchmark_data_dir: ${BENCHMARK_REPOS_DIR}/clinbench/data
benchmark_repo_dir: ${BENCHMARK_REPOS_DIR}/clinbench/repo
```

`configs/multi_domain_all/` has the 143 configs used in the paper. Relative paths are resolved against the config's folder. `${BENCHMARK_REPOS_DIR}` defaults to `./data/benchmark_repos`; any other unset variable is an error.

### Step 1 — Benchmark-level audit

The auditor agent is given the benchmark's repository and documentation and characterizes the benchmark as a whole — its scope, evaluation methodology, known limitations, and structural risks. This stage runs once per benchmark and produces a benchmark-level report that guides downstream task sampling.

```bash
bench-audit audit-benchmark \
    --config configs/multi_domain_all/medical_health/clinbench.yaml
```

### Step 2 — Evidence collection

The collector agent resolves the benchmark's heterogeneous artifacts (repo, dataset, recorded trajectories) into a uniform manifest plus per-task `TaskConfig` / `EvalConfig` JSONs that downstream auditors can consume without knowing each benchmark's idiosyncratic layout.

```bash
bench-audit collect-evidence \
    --config configs/multi_domain_all/medical_health/clinbench.yaml
```

### Step 3 — Task-level audit

The auditor agent is initialized per task with the task configuration and file paths to the relevant artifacts. Using shell tools, it inspects each task against a standardized rubric and emits structured findings (claim, category, severity, evidence, why-it-matters, suggested fix). Findings are emitted along three independent axes — Instruction, Environment, Evaluation — each scored 0/1/2.

Run the auditor in one of two modes:

**Static audit** — the auditor reads only the task definition: the instruction the agent would receive, the tests its solution would be graded against, the evaluation configuration, etc. No execution is observed. A static audit covers every collected task; use `--sample-n N` or `--tasks <id> ...` to audit fewer.

```bash
bench-audit audit-tasks \
    --config configs/multi_domain_all/medical_health/clinbench.yaml \
    --mode static --sample-n 20
```

**Trajectory audit** — the auditor additionally reads recorded agent traces (`trajectory_path`) and test outputs (`test_output_path`), allowing it to catch runtime issues that static inspection cannot. By default it audits tasks with at least one failed run; `--all` audits every task.

If you do not already have recorded trajectories, `auto-bench-audit` can generate them by wrapping [Harbor](https://github.com/harbor-framework/harbor) (`uv sync --extra harbor`). The example below runs Claude Code Sonnet on Terminal-Bench 2 and writes a ready-to-audit collection config to `configs/harbor/collection_<name>.yaml`:

```bash
# 1. Generate trajectories via Harbor (writes collection_<name>.yaml).
#    The Harbor config reads TB2_JOBS_DIR and HARBOR_DATASETS_DIR (see .env.example).
bench-audit generate \
    --harbor-config configs/harbor/eval_claude_code_sonnet.yaml \
    --benchmark-type tb2 \
    --agent-cli claude \
    --model claude-sonnet-4-6

# 2. Audit the resulting trajectories.
bench-audit audit-tasks \
    --config configs/harbor/collection_tb2.yaml \
    --mode trajectory --all
```

## Switching Agent CLIs

Each config names its auditor in `agent_cli` (`claude`, `cursor`, or `codex`) and `model`. Every command takes `--agent-cli` and `--model` to replace them for one run, so there is no need to edit a config. The model is passed through for Claude and Codex; Cursor currently ignores it. If you pass `--agent-cli` without `--model`, the config's model is dropped and the agent uses its own default.

`bench-audit audit` writes its own config. For `claude` it uses `claude-opus-5-5` unless you pass `--model`; `codex` and `cursor` use their own default model. The 143 configs of the paper name `claude-opus-4-7`, the model of the paper's runs.

```bash
# One-off audit
bench-audit audit https://github.com/centerforaisafety/hle \
    --agent-cli codex --model gpt-5 --sample-n 3

# One phase
bench-audit audit-tasks \
    --config configs/multi_domain_all/science_expert_reasoning/gpqa_diamond.yaml \
    --agent-cli codex --model gpt-5 --mode static --sample-n 20

# Batch
bench-audit batch \
    --config configs/multi_domain_all/science_expert_reasoning/gpqa_diamond.yaml \
    --agent-cli codex --model gpt-5 \
    --max-tasks 100 --batch 50 --workers 3
```

## Project Layout

```
auto-bench-audit/
├── src/bench_audit/   # Audit CLI implementation (`bench-audit`)
├── rubrics/           # general_rubric.yaml + benchmark/task rubric .txt files
├── skills/            # bench-audit/SKILL.md: instructions for Claude Code or Codex (`bench-audit skill`)
├── configs/           # Per-benchmark YAML configs: multi_domain_all/ (paper inventory),
│                      #   harbor/ (trajectory generation)
├── scripts/           # Old entry points, kept as thin wrappers: audit_one.sh
│                      #   (`bench-audit audit`), batch_static_audit_pipeline.sh (`bench-audit batch`)
├── tests/             # Unit tests; they start no agent and cost nothing
├── benchmarks/        # Paper analysis data (leaderboards, BenchGuard comparison,
│                      #   benchmark selection). Not used by the code.
└── pyproject.toml
```

## Development

```bash
uv run pytest       # unit tests
uv run ruff check   # lint
```

The tests replace the agent with a stand-in, so they need no API key. One test parses every `bench-audit` command in this README, so a renamed flag or a moved file fails CI.

## License

The code is released under the [MIT License](LICENSE). Files under `benchmarks/` that come from other projects (leaderboard exports, BenchGuard data) keep the licenses of their sources.

## Citation

If you find our work helpful, please cite:

```bibtex
@misc{wang2026automatedbenchmarkauditingai,
  title         = {Automated Benchmark Auditing for AI Agents and Large Language Models},
  author        = {Junlin Wang and Federico Bianchi and Shang Zhu and Fan Nie and Yongchan Kwon and Bhuwan Dhingra and James Zou},
  year          = {2026},
  eprint        = {2605.26079},
  archivePrefix = {arXiv},
  primaryClass  = {cs.CL},
  url           = {https://arxiv.org/abs/2605.26079},
}
```
