"""Pick which tasks to audit.

Two places sample tasks: `audit-tasks --sample-n` (in memory) and the batch
pipeline (saved to sampled_tasks.json, so that a later run audits the same
tasks). Both use `sample_sorted`, so a seed means the same thing everywhere.
"""

from __future__ import annotations

import json
import random
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from ..config import BenchmarkConfig
from ..paths import resolve_output_root


def sample_sorted[T](items: list[T], n: int, seed: int, key: Callable[[T], Any] | None = None) -> list[T]:
    """Return ``n`` of ``items`` chosen with ``seed``, in sorted order. Return all when there are ``n`` or fewer."""
    if len(items) <= n:
        return list(items)
    return sorted(random.Random(seed).sample(items, n), key=key)


def write_task_sample(config: BenchmarkConfig, max_tasks: int, seed: int = 42, force: bool = False) -> list[str]:
    """Sample up to ``max_tasks`` collected tasks and save them to ``<output_root>/sampled_tasks.json``.

    An existing file is reused unless ``force`` is set. ``max_tasks`` of 0 means all tasks.
    """
    output_root = resolve_output_root(config)
    task_configs_dir = output_root / "task_configs"
    sample_path = output_root / "sampled_tasks.json"

    if sample_path.exists() and not force:
        return list(json.loads(sample_path.read_text())["task_ids"])

    if not task_configs_dir.is_dir():
        raise FileNotFoundError(f"task_configs not found at {task_configs_dir}")
    all_task_ids = sorted(
        child.name for child in task_configs_dir.iterdir() if child.is_dir() and (child / "task_config.json").exists()
    )
    if not all_task_ids:
        raise FileNotFoundError(f"no task configs found in {task_configs_dir}")

    sampled = sample_sorted(all_task_ids, max_tasks, seed) if max_tasks > 0 else all_task_ids
    record = {
        "benchmark_name": config.benchmark_name,
        "total_tasks": len(all_task_ids),
        "sampled_count": len(sampled),
        "max_tasks": max_tasks,
        "seed": seed,
        "sampled_at": datetime.now(UTC).isoformat(),
        "task_ids": sampled,
    }
    sample_path.write_text(json.dumps(record, indent=2) + "\n")
    return sampled
