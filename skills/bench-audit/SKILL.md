---
name: bench-audit
description: Audit an LLM or agent benchmark for defects in the benchmark itself - unclear or underspecified tasks, wrong reference answers, graders that reject correct solutions, broken environments - using Auto Benchmark Audit (the `bench-audit` command). Use when the user asks to audit, review, sanity-check, or find problems in a benchmark, eval set, or task suite, either their own in a local folder or a public one by repository URL.
---

# Audit a benchmark with Auto Benchmark Audit

Auto Benchmark Audit (ABA) finds defects in a benchmark, not in the models that take it: a task prompt that leaves out what the grader checks, a reference answer that is wrong, a test that rejects a correct solution, an environment that contradicts the instructions. It is the method behind https://autobenchaudit.com.

Your part is to drive the `bench-audit` command for the user and then explain what it found. The command does the auditing. It starts a separate agent process for each step (one for the benchmark as a whole, one to list the tasks, then one per task), each with a fresh context and a fixed rubric, and each writes a structured JSON record. Do not audit the tasks yourself in this conversation instead: the separate processes are what keep one task's audit from colouring the next, and the fixed rubric is what makes the findings comparable with the published ones.

## 1. Check what you have

- **The target.** Either a repository URL, or a local folder (use `.` for the folder you are in). If the user did not say which benchmark, ask.
- **The command.** Every `bench-audit` command below is written with this prefix, which needs only `uv`:

  ```bash
  uvx --from git+https://github.com/IsThatYou/auto-bench-audit bench-audit --help
  ```

  The first use downloads the repository (about 300 MB) and can take a few minutes. If `bench-audit` is already on the PATH, use it directly. If `uv` is missing, tell the user how to install it (https://docs.astral.sh/uv/) and stop.
- **The agent command.** `bench-audit` starts `claude` (Claude Code) or `codex` (Codex) as subprocesses. Use the one you are running as, and pass it as `--agent-cli claude` or `--agent-cli codex`. It must be on the PATH and signed in.
- **Size, for a local folder.** `bench-audit` works on a copy of the folder. Check the folder's size first. If it is more than a few GB, tell the user before you copy it.

## 2. Tell the user what will happen, and wait for a yes

Do not start an audit until the user has agreed to these three points in this conversation. They are the user's risks to accept, not yours.

1. **Permission checks are off.** The audit agents run as `claude -p --dangerously-skip-permissions` or `codex exec --yolo`, so they can run any command without asking. They are told to read the benchmark and to write only their results, and for a local folder they work on a copy. For a benchmark repository that the user does not trust, recommend a container or a virtual machine.
2. **It costs money or subscription usage.** An audit of N tasks makes N + 2 agent calls (plus one more attempt for each call that fails). Each call reads files and can run for up to 800 seconds; the call that lists the tasks can run for up to an hour. Say which agent and model will be used (for `claude`, `bench-audit` uses `claude-opus-5-5` unless you pass `--model`).
3. **It takes a while.** Usually tens of minutes for a few tasks.

Propose `--sample-n 5` for a first run unless the user asked for something else. A sample finds the kinds of defects a benchmark has; it does not clear the tasks that were not sampled.

## 3. Run the audit

Keep everything under `.bench-audit/` in the current folder, so nothing mixes with the benchmark's own files:

```bash
export AUDIT_RUN_DIR="$PWD/.bench-audit/runs" BENCHMARK_REPOS_DIR="$PWD/.bench-audit/repos"
uvx --from git+https://github.com/IsThatYou/auto-bench-audit bench-audit audit . --sample-n 5 --max-workers 3 --agent-cli claude
```

Replace `.` with the repository URL to audit a public benchmark. The two variables must be set in the same shell as every `bench-audit` command, including later ones.

The audit runs longer than a foreground command is allowed to in most agent harnesses. Start it as a background job, send its output to `.bench-audit/audit.log`, and record its exit code. While it runs, check on it now and then: the end of the log shows the current phase, and the number of files in `.bench-audit/runs/static/*/task_audits_static/` is the number of finished task audits. Do not start a second audit of the same benchmark while one is running.

If you cannot start the agent subprocess from where you are (a sandbox blocks it, or it is not signed in), do not work around it. Give the user the two lines above to run in their own terminal, and continue from step 5 when they say it is finished.

If the folder is a git repository, suggest adding `.bench-audit/` to `.gitignore`.

## 4. Read the exit code

- `0`: every phase finished.
- `3`: finished, but one or more agent calls failed after a retry. Their records say `"overall_judgment": "agent_error"`. Run the exact same command once more: it skips everything that is finished and tries only the failed parts. If it returns `3` again, go on with what exists and tell the user which parts failed and the reason in each record's `summary`.
- anything else: the command stopped. Read the last lines of the log; the message says what is missing.

Do not add `--force` to recover from a failure. It repeats every call, and the user pays for all of them again.

## 5. Read the results

The log ends with the output folder. It is `.bench-audit/runs/static/<name>__quick__actual/`, where `<name>` is the benchmark's folder or repository name in lower case.

- `benchmark_audit.json`: the benchmark as a whole. `category_verdicts` has one entry for each of six categories (documentation, contamination, eval_methodology, dataset_quality, reproducibility, eval_infrastructure), each with a `severity`, a `rationale`, and `findings`.
- `task_audits_static/<task_id>.json`: one record per audited task. `findings` is empty when the task is sound. Each finding has a `category` (`ambiguity`, `environment`, or `test_quality`), a `severity`, a `claim`, `why_it_matters`, `evidence` (a list of `path` and `note`), and a `suggested_fix`.
- `audit_report_static.md` and `benchmark_audit_report.md`: short tables of the same.

Severity: `0` is no defect, `1` is a minor defect (a competent person can still get to the expected answer with some work), `2` is a major defect (the task cannot be solved as written, or the grader checks something other than what the task asks).

For a local folder, evidence paths point into the copy at `.bench-audit/repos/<name>/repo/`. The same relative path in the user's folder is the file they need to change.

## 6. Check each finding before you report it

The findings are claims by an auditor agent, and some are wrong. For every finding with severity 1 or 2, open the files it cites and check the claim against them yourself. Then put it in one of three groups: **confirmed** (you saw the defect in the files), **not confirmed** (you could not tell), or **wrong** (the files contradict the claim). This step costs little and is the difference between a list the user can act on and a list they have to redo.

## 7. Report

Lead with the numbers: how many tasks were audited out of how many exist, and how many have a severity 2 finding, a severity 1 finding, or none. Then:

- the benchmark-level findings with severity 1 or 2;
- each confirmed task finding: the task id, the defect in a sentence or two, the file and line that shows it, and the suggested fix;
- the findings you could not confirm or think are wrong, kept apart and marked as such;
- anything that failed, and that the tasks outside the sample were not audited.

Write the defects in plain words. The user wants to know what is broken and where, not the rubric's vocabulary.

## 8. Offer the next step

- **Fix the defects.** With the user's go-ahead, make the fixes in their real folder (not in the copy). After fixes, a new audit of the changed tasks needs `--force`, which copies the folder again and repeats every call, so ask first.
- **Audit more tasks.** Run the same command with a larger `--sample-n`. Tasks that already have a finished record are not audited again.
- **Audit particular tasks.** Use the config that the first run wrote:

  ```bash
  uvx --from git+https://github.com/IsThatYou/auto-bench-audit bench-audit audit-tasks --config .bench-audit/repos/<name>/config.yaml --mode static --tasks <task_id> <task_id>
  ```

A benchmark that has recorded agent runs (trajectories) can also be audited against those runs, which catches defects that only show up at run time. That needs a hand-written config; see "Step-by-Step Audit Pipeline" in the repository's README.
