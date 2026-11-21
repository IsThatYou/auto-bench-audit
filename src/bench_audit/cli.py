from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .agent_cli import AGENT_BINARIES, AgentCliError
from .audit_protocol import PHASE_ORDER, load_general_rubric, normalize_phase, resolve_phase_sequence
from .batch import add_batch_arguments, run_batch
from .benchmark_audit import audit_benchmark
from .config import BenchmarkConfig, load_benchmark_config
from .evidence_collection import collect_evidence
from .paths import checkout_root
from .quick_audit import run_quick_audit
from .task_audit import audit_tasks
from .trajectory_generation import generate_trajectories

DESCRIPTION = """\
Audit an LLM or agent benchmark for unclear tasks, broken environments, and
wrong graders.

Quick start (clones the repo, then audits 5 sampled tasks):

  bench-audit audit https://github.com/centerforaisafety/hle

An audit has three phases. `audit` and `run` do all of them for one benchmark,
and `batch` does them for many. You can also run one phase at a time:

  1. audit-benchmark    review the benchmark as a whole
  2. collect-evidence   list the tasks and write one task config per task
  3. audit-tasks        review each task and write its findings

Every phase starts an agent command-line tool (claude, codex, or cursor) as a
subprocess. The agent runs with its permission checks off. Each phase costs
API money or subscription usage.
"""

AGENT_CHOICES = sorted(AGENT_BINARIES)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bench-audit",
        description=DESCRIPTION,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True, metavar="<command>")

    def add(name: str, help_text: str, description: str | None = None) -> argparse.ArgumentParser:
        return subparsers.add_parser(
            name,
            help=help_text,
            description=description or help_text,
            formatter_class=argparse.RawDescriptionHelpFormatter,
        )

    quick = add(
        "audit",
        "Audit one benchmark from its repository URL (all three phases).",
        "Clone a benchmark repository and audit it in static mode.\n\n"
        "The clone goes to $BENCHMARK_REPOS_DIR/<name>/repo (default ./data/benchmark_repos).\n"
        "The results go to $AUDIT_RUN_DIR/static/<name>__quick__actual (default ./data/audit_runs).\n\n"
        "Example:\n  bench-audit audit https://github.com/centerforaisafety/hle --sample-n 3",
    )
    quick.add_argument("url", nargs="?", help="Benchmark repository URL (anything `git clone` accepts)")
    quick.add_argument("--url", dest="url_flag", help=argparse.SUPPRESS)
    quick.add_argument("--name", help="Benchmark name (default: derived from the URL)")
    quick.add_argument("--sample-n", type=int, default=5, help="How many tasks to audit (default: 5)")
    quick.add_argument("--sample-seed", type=int, default=42, help="Random seed for task sampling (default: 42)")
    quick.add_argument("--domain", default="misc", help="Domain label stored in the config (default: misc)")
    quick.add_argument("--agent-cli", choices=AGENT_CHOICES, default="claude", help="Agent command to run (default: claude)")
    quick.add_argument("--model", help="Model id passed to the agent (default for claude: claude-opus-4-7)")
    quick.add_argument("--skip-benchmark-audit", action="store_true", help="Skip phase 1, the benchmark-level audit")
    quick.add_argument("--max-workers", type=int, default=1, help="Tasks to audit in parallel (default: 1)")
    quick.add_argument("--timeout", type=int, default=800, help="Seconds per agent call (default: 800)")
    quick.add_argument("--force", action="store_true", help="Run again even if results exist")

    bench = add("audit-benchmark", "Phase 1: review the benchmark as a whole.")
    _add_config_args(bench)
    bench.add_argument("--general-rubric", help="Path to a general rubric YAML (default: rubrics/general_rubric.yaml)")
    bench.add_argument("--rubric-text", help="Path to a .txt benchmark rubric file (overrides the default)")
    bench.add_argument("--force", action="store_true", help="Run again even if benchmark_audit.json exists")
    bench.add_argument("--timeout", type=int, default=800, help="Seconds for the agent call (default: 800)")

    collect = add("collect-evidence", "Phase 2: list the tasks and write one task config per task.")
    _add_config_args(collect)
    collect.add_argument("--job-type", choices=["actual"], help="Kind of recorded runs to collect (default: from the config)")
    collect.add_argument("--force", action="store_true", help="Run again even if results exist")

    tasks = add("audit-tasks", "Phase 3: review each task and write its findings.")
    _add_config_args(tasks)
    _add_task_audit_args(tasks)

    generate = add(
        "generate",
        "Record agent runs with Harbor, for trajectory audits (needs the `harbor` extra).",
    )
    generate.add_argument("--harbor-config", required=True, help="Harbor job config YAML, for example configs/harbor/eval_claude_code_sonnet.yaml")
    generate.add_argument("--benchmark-type", required=True, help="Benchmark type for the collection config: tb2 or swe_bench")
    generate.add_argument("--agent-cli", required=True, choices=AGENT_CHOICES, help="Agent command the auditor will use later")
    generate.add_argument("--model", required=True, help="Model id the auditor will use later")
    generate.add_argument("--dataset", help="Override dataset name in Harbor config")
    generate.add_argument("--download-dir", help="Override dataset download_dir in Harbor config")
    generate.add_argument("--save-dir", help="Folder that holds the benchmark data (default: the Harbor download folder)")
    generate.add_argument("--jobs-type", default="actual", choices=["actual"], help="Kind of runs to record (default: actual)")

    run = add(
        "run",
        "Run all three phases for one config (or a range of phases).",
        "Run the phases in order for one config. By default: all three.\n"
        "Use --start-phase and --end-phase to run a part.",
    )
    _add_config_args(run)
    run.add_argument("--job-type", choices=["actual"], help="Kind of recorded runs to collect (default: from the config)")
    run.add_argument("--start-phase", type=normalize_phase, choices=PHASE_ORDER, default=PHASE_ORDER[0], help=f"First phase (default: {PHASE_ORDER[0]})")
    run.add_argument("--end-phase", type=normalize_phase, choices=PHASE_ORDER, default=PHASE_ORDER[-1], help=f"Last phase (default: {PHASE_ORDER[-1]})")
    _add_task_audit_args(run)

    batch = add(
        "batch",
        "Audit many benchmarks from a folder of configs, with a progress tracker.",
        "Run the phases for every config under configs/multi_domain_all/ (or the configs you name).\n"
        "Per benchmark: audit-benchmark, collect-evidence, sample tasks, audit-tasks, then delete the clone.\n"
        "A second run continues where the first one stopped.\n\n"
        "Examples:\n"
        "  bench-audit batch --domain medical_health --max-tasks 20\n"
        "  bench-audit batch --status",
    )
    add_batch_arguments(batch)

    return parser


def _add_config_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", required=True, help="Benchmark config YAML, for example configs/multi_domain_all/medical_health/clinbench.yaml")
    parser.add_argument("--agent-cli", choices=AGENT_CHOICES, help="Use this agent command instead of the config's agent_cli")
    parser.add_argument("--model", help="Use this model instead of the config's model")


def _add_task_audit_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--general-rubric", help="Path to a general rubric YAML (default: rubrics/general_rubric.yaml)")
    parser.add_argument("--rubric-text", help="Path to a .txt rubric file (overrides the default derived from general rubric)")
    parser.add_argument("--tasks", nargs="+", help="Audit only these task ids")
    parser.add_argument("--all", action="store_true", help="Audit every task, also tasks that the rubric's filter would skip")
    parser.add_argument("--force", action="store_true", help="Run again even if results exist")
    parser.add_argument("--eval-strategy", choices=["mixed", "all_failed", "all"], default="mixed",
                        help="Eval selection strategy: 'mixed' (primary + contrast, default), 'all_failed' (all failed evals), or 'all' (all evals)")
    parser.add_argument("--mode", choices=["trajectory", "static"],
                        help="Audit mode: 'trajectory' (default for tb2/swe_bench) uses recorded runs, 'static' (default for static benchmarks) audits task definitions only")
    parser.add_argument("--sample-n", type=int, help="Randomly sample N tasks to audit (default: all)")
    parser.add_argument("--sample-seed", type=int, default=42, help="Random seed for task sampling (default: 42)")
    parser.add_argument("--max-workers", type=int, default=1, help="Tasks to audit in parallel (default: 1)")
    parser.add_argument("--timeout", type=int, default=800, help="Seconds per agent call (default: 800)")


def load_env_files() -> None:
    """Read ``.env`` from the current folder, then from the source checkout.

    A variable that is already set is not changed. Values are read the way a
    shell reads them: ``export`` and surrounding quotes are dropped.
    """
    folders = [Path.cwd()]
    root = checkout_root()
    if root is not None and root != Path.cwd():
        folders.append(root)
    for folder in folders:
        env_path = folder / ".env"
        if env_path.is_file():
            for key, value in parse_env_text(env_path.read_text()).items():
                os.environ.setdefault(key, value)


def parse_env_text(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        if stripped.startswith("export "):
            stripped = stripped[len("export "):].lstrip()
        key, value = stripped.split("=", 1)
        key, value = key.strip(), value.strip()
        if not key:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        values[key] = value
    return values


def load_config(args: argparse.Namespace) -> BenchmarkConfig:
    """Load ``--config`` and apply ``--agent-cli`` / ``--model``."""
    config = load_benchmark_config(args.config)
    agent_cli = getattr(args, "agent_cli", None)
    model = getattr(args, "model", None)
    if agent_cli and agent_cli != config.agent_cli:
        if not model and config.model:
            # The config's model belongs to the config's agent. Do not send it to another agent.
            print(
                f"[bench-audit] --agent-cli {agent_cli} replaces {config.agent_cli}; "
                f"ignoring the config's model ({config.model}). Pass --model to choose one.",
                file=sys.stderr,
            )
        config.agent_cli = agent_cli
        config.model = None
    if model:
        config.model = model
    if getattr(args, "job_type", None):
        config.jobs_type = args.job_type
    config.validate()
    return config


def main(argv: list[str] | None = None) -> int:
    load_env_files()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return _dispatch(args, parser)
    except (ValueError, FileNotFoundError, AgentCliError) as exc:
        print(f"bench-audit: error: {exc}", file=sys.stderr)
        return 1


def _dispatch(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    if args.command == "audit":
        url = args.url or args.url_flag
        if not url:
            parser.error("audit needs a repository URL, for example: bench-audit audit https://github.com/centerforaisafety/hle")
        run_quick_audit(
            url,
            name=args.name,
            sample_n=args.sample_n,
            sample_seed=args.sample_seed,
            domain=args.domain,
            agent_cli=args.agent_cli,
            model=args.model,
            skip_benchmark_audit=args.skip_benchmark_audit,
            max_workers=args.max_workers,
            timeout=args.timeout,
            force=args.force,
        )
        return 0

    if args.command == "batch":
        return run_batch(args)

    if args.command == "generate":
        generate_trajectories(
            harbor_config=Path(args.harbor_config).resolve(),
            benchmark_type=args.benchmark_type,
            agent_cli=args.agent_cli,
            model=args.model,
            dataset=args.dataset,
            download_dir=Path(args.download_dir) if args.download_dir else None,
            save_dir=Path(args.save_dir) if args.save_dir else None,
            jobs_type=args.jobs_type,
        )
        return 0

    if args.command == "audit-benchmark":
        _audit_benchmark(load_config(args), args)
        return 0

    if args.command == "collect-evidence":
        collect_evidence(load_config(args), force=args.force)
        return 0

    if args.command == "audit-tasks":
        _validate_task_filters(args.tasks, args.all)
        _audit_tasks(load_config(args), args)
        return 0

    if args.command == "run":
        _validate_task_filters(args.tasks, args.all)
        config = load_config(args)
        general_rubric = load_general_rubric(args.general_rubric)
        phases = resolve_phase_sequence(general_rubric, args.start_phase, args.end_phase)
        if "benchmark_audit" in phases:
            _audit_benchmark(config, args)
        if "evidence_collection" in phases:
            collect_evidence(config, force=args.force)
        if "task_audit" in phases:
            _audit_tasks(config, args)
        return 0

    raise SystemExit(f"unknown command: {args.command}")


def _audit_benchmark(config: BenchmarkConfig, args: argparse.Namespace) -> None:
    audit_benchmark(
        config,
        general_rubric_path=args.general_rubric,
        rubric_text_path=args.rubric_text,
        force=args.force,
        timeout=args.timeout,
    )


def _audit_tasks(config: BenchmarkConfig, args: argparse.Namespace) -> None:
    audit_tasks(
        config,
        general_rubric_path=args.general_rubric,
        rubric_text_path=args.rubric_text,
        task_ids=args.tasks,
        include_all=args.all,
        force=args.force,
        max_workers=args.max_workers,
        timeout=args.timeout,
        eval_strategy=args.eval_strategy,
        audit_mode=args.mode,
        sample_n=args.sample_n,
        sample_seed=args.sample_seed,
    )


def _validate_task_filters(task_ids: list[str] | None, include_all: bool) -> None:
    if task_ids and include_all:
        raise SystemExit("--tasks and --all cannot be used together")
