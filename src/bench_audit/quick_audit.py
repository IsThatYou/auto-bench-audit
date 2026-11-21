"""Audit one benchmark from its repository URL (`bench-audit audit <url>`).

Steps: clone the repo, write a small config next to it, then run the three
phases in static mode: benchmark audit, evidence collection, task audit.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

from .agent_cli import require_agent_binary
from .benchmark_audit import audit_benchmark
from .config import load_benchmark_config
from .evidence_collection import collect_evidence
from .paths import env_dir, resolve_output_root
from .task_audit import audit_tasks

DEFAULT_MODELS = {"claude": "claude-opus-4-7"}


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
    force: bool = False,
) -> dict[str, Any]:
    require_agent_binary(agent_cli)
    name = name or derive_name(url)
    model = model or DEFAULT_MODELS.get(agent_cli)

    bench_dir = env_dir("BENCHMARK_REPOS_DIR") / name
    repo_dir = bench_dir / "repo"
    (bench_dir / "data").mkdir(parents=True, exist_ok=True)

    if clone_repo(url, repo_dir):
        _log(f"cloned {url} -> {repo_dir}")
    else:
        _log(f"repo already present at {repo_dir} (skipping clone)")

    config_path = write_quick_config(bench_dir, name=name, url=url, agent_cli=agent_cli, model=model, domain=domain)
    _log(f"wrote config {config_path}")
    config = load_benchmark_config(config_path)

    if skip_benchmark_audit:
        _log("skipping the benchmark-level audit")
    else:
        _log("phase 1/3: benchmark-level audit")
        audit_benchmark(config, force=force, timeout=timeout)

    _log("phase 2/3: evidence collection")
    collect_evidence(config, force=force)

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
    )

    output_root = resolve_output_root(config)
    _log("done.")
    _log(f"output folder: {output_root}")
    _log(f"report:        {result.get('report_path', output_root / 'audit_report_static.md')}")
    _log(f"findings:      {result.get('task_audit_dir', output_root / 'task_audits_static')}")
    return {"config_path": str(config_path), "output_root": str(output_root), **result}


def _log(message: str) -> None:
    print(f"[bench-audit] {message}", file=sys.stderr, flush=True)
