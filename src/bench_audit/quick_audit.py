"""Audit one benchmark from its repository URL or a local folder (`bench-audit audit <target>`).

Steps: clone the repo (or copy the folder), write a small config next to it,
then run the three phases in static mode: benchmark audit, evidence
collection, task audit.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

from .agent_cli import require_agent_binary
from .benchmark_audit import audit_benchmark
from .config import load_benchmark_config
from .evidence_collection import DEFAULT_COLLECT_TIMEOUT, collect_evidence
from .paths import env_dir, resolve_output_root
from .task_audit import audit_tasks

# The model that `bench-audit audit` passes to the agent when --model is not given.
# codex and cursor have no entry: they use their own default model.
# The 143 configs of the paper name claude-opus-4-7 themselves, so this does not change them.
DEFAULT_MODELS = {"claude": "claude-opus-5-5"}

# Folders that are not part of a benchmark and can be very large. A copy leaves them out.
COPY_SKIP = {".venv", "venv", "node_modules", "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox", ".bench-audit"}


def derive_name(url: str) -> str:
    """Turn a repo URL into a folder-safe name: ``.../Terminal-Bench-2.git`` -> ``terminal_bench_2``."""
    base = url.rstrip("/").rsplit("/", 1)[-1]
    if base.endswith(".git"):
        base = base[: -len(".git")]
    name = re.sub(r"[^a-z0-9]", "_", base.lower()).strip("_")
    if not name:
        raise ValueError(f"cannot derive a benchmark name from {url!r}; pass --name")
    return name


def clone_repo(url: str, repo_dir: Path) -> bool:
    """Clone ``url`` into ``repo_dir``. Return False when the clone is already there."""
    if (repo_dir / ".git").is_dir():
        return False
    repo_dir.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(["git", "clone", url, str(repo_dir)], check=False)
    if result.returncode != 0:
        raise SystemExit(f"git clone failed for {url} (exit code {result.returncode})")
    return True


def local_folder(target: str) -> Path | None:
    """Return the folder that ``target`` names, or None when it is a URL."""
    path = Path(target).expanduser()
    return path.resolve() if path.is_dir() else None


def copy_folder(source: Path, repo_dir: Path, *, force: bool = False) -> bool:
    """Copy the benchmark folder ``source`` to ``repo_dir``. Return False when a copy is already there.

    The audit works on the copy, so the agents do not change the user's folder,
    and files that are not committed are part of the audit. bench-audit's own
    data folders are left out, because they can be inside ``source``.
    """
    source, repo_dir = source.resolve(), repo_dir.resolve()
    if repo_dir == source or repo_dir in source.parents:
        raise ValueError(f"{source} is inside bench-audit's own copy ({repo_dir}); give the original benchmark folder")
    if repo_dir.is_dir() and any(repo_dir.iterdir()):
        if not force:
            return False
        shutil.rmtree(repo_dir)
    own_folders = {env_dir("AUDIT_RUN_DIR"), env_dir("BENCHMARK_REPOS_DIR")}

    def skip(folder: str, names: list[str]) -> set[str]:
        return {name for name in names if name in COPY_SKIP or (Path(folder) / name).resolve() in own_folders}

    repo_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, repo_dir, symlinks=True, ignore=skip)
    return True


def write_quick_config(
    bench_dir: Path,
    *,
    name: str,
    url: str,
    agent_cli: str,
    model: str | None,
    domain: str,
) -> Path:
    """Write ``<bench_dir>/config.yaml``. Paths in it are relative to the config's folder."""
    data: dict[str, Any] = {
        "benchmark_name": name,
        "benchmark_type": "static",
        "agent_cli": agent_cli,
    }
    if model:
        data["model"] = model
    data.update(
        {
            "output_subdir": "quick",
            "code_url": url,
            "benchmark_data_dir": "data",
            "benchmark_repo_dir": "repo",
            "domain_categories": [domain],
        }
    )
    bench_dir.mkdir(parents=True, exist_ok=True)
    config_path = bench_dir / "config.yaml"
    config_path.write_text(yaml.safe_dump(data, sort_keys=False))
    return config_path


def run_quick_audit(
    url: str,
    *,
    name: str | None = None,
    sample_n: int = 5,
    sample_seed: int = 42,
    domain: str = "misc",
    agent_cli: str = "claude",
    model: str | None = None,
    skip_benchmark_audit: bool = False,
    max_workers: int = 1,
    timeout: int = 800,
    collect_timeout: int | None = DEFAULT_COLLECT_TIMEOUT,
    retry_errors: int = 1,
    force: bool = False,
) -> dict[str, Any]:
    require_agent_binary(agent_cli)
    source = local_folder(url)
    if source is not None:
        url = str(source)
    name = name or derive_name(url)
    model = model or DEFAULT_MODELS.get(agent_cli)

    bench_dir = env_dir("BENCHMARK_REPOS_DIR") / name
    repo_dir = bench_dir / "repo"

    if source is not None:
        if copy_folder(source, repo_dir, force=force):
            _log(f"copied {source} -> {repo_dir} (the audit works on this copy)")
        else:
            _log(f"copy already present at {repo_dir} (pass --force to copy the folder again)")
    elif clone_repo(url, repo_dir):
        _log(f"cloned {url} -> {repo_dir}")
    else:
        _log(f"repo already present at {repo_dir} (skipping clone)")
    (bench_dir / "data").mkdir(parents=True, exist_ok=True)

    config_path = write_quick_config(bench_dir, name=name, url=url, agent_cli=agent_cli, model=model, domain=domain)
    _log(f"wrote config {config_path}")
    config = load_benchmark_config(config_path)

    failures: list[str] = []
    if skip_benchmark_audit:
        _log("skipping the benchmark-level audit")
    else:
        _log("phase 1/3: benchmark-level audit")
        if audit_benchmark(config, force=force, timeout=timeout, retry_errors=retry_errors).get("failed"):
            failures.append("the benchmark-level audit")

    _log("phase 2/3: evidence collection")
    collect_evidence(config, force=force, timeout=collect_timeout, retry_errors=retry_errors)

    _log(f"phase 3/3: task audit (static, {sample_n} sampled tasks)")
    result = audit_tasks(
        config,
        include_all=True,
        force=force,
        max_workers=max_workers,
        timeout=timeout,
        audit_mode="static",
        sample_n=sample_n,
        sample_seed=sample_seed,
        retry_errors=retry_errors,
    )
    failed_tasks = result.get("failed_tasks") or []
    if failed_tasks:
        failures.append(f"{len(failed_tasks)} task audit(s)")

    output_root = resolve_output_root(config)
    if failures:
        _log(f"finished with failures: {' and '.join(failures)} failed.")
        _log("Run the same command again to try only the failed parts again.")
    else:
        _log("done.")
    _log(f"output folder: {output_root}")
    _log(f"report:        {result.get('report_path', output_root / 'audit_report_static.md')}")
    _log(f"findings:      {result.get('task_audit_dir', output_root / 'task_audits_static')}")
    counts = result.get("summary")
    if counts:
        _log(
            f"counts:        {counts['audited']} task(s) audited, {counts['tasks_with_major']['count']} with a major finding "
            f"({counts['tasks_with_major_not_shared']['count']} with shared findings left out), {counts['not_counted']['total']} not counted"
        )
    return {"config_path": str(config_path), "output_root": str(output_root), **result, "failures": failures}


def _log(message: str) -> None:
    print(f"[bench-audit] {message}", file=sys.stderr, flush=True)
