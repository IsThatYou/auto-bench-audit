from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import yaml

from ..agent_cli import AgentCliError, build_agent_request, invoke_text, set_aside_failed_log
from ..config import BenchmarkConfig
from ..paths import build_agent_add_dirs, build_output_dir_name, resolve_output_root  # noqa: F401  (scripts import these from here)
from .models import (
    INSTRUCTION_SOURCES,
    WRITTEN_BY_COLLECTOR,
    ArtifactManifest,
    TaskConfig,
    normalize_manifest,
)
from .prompt import render_collect_prompt

DEFAULT_COLLECT_TIMEOUT = 3600

# What a failed collection raises: the agent call failed, or its files are missing or not valid.
_COLLECTION_ERRORS = (AgentCliError, OSError, ValueError, KeyError, TypeError, yaml.YAMLError)


def collect_evidence(
    config: BenchmarkConfig,
    force: bool = False,
    timeout: int | None = DEFAULT_COLLECT_TIMEOUT,
    retry_errors: int = 1,
) -> dict[str, Any]:
    """List the benchmark's tasks and write one task config per task, with one agent call.

    A finished collection is not run again unless ``force`` is set. A failed
    collection is tried again, up to ``retry_errors`` more times in this run.
    ``timeout`` is the time limit of one agent call in seconds (None = no limit).
    """
    config.validate()
    output_root = resolve_output_root(config)
    layout = ensure_output_layout(output_root)
    collector_path = layout["collector"] / "collect_evidence.py"
    manifest_path = layout["root"] / "artifact_manifest.yaml"
    report_path = layout["root"] / "collection_report.md"
    result = {
        "collector_path": str(collector_path),
        "manifest_path": str(manifest_path),
        "task_config_dir": str(layout["task_configs"]),
        "report_path": str(report_path),
        "output_root": str(output_root),
    }

    if not force and collection_is_done(output_root):
        print(f"Evidence collection already exists at {output_root}, skipping (use --force to re-run)")
        return {**result, "skipped": True}
    # The report is written last and marks a finished collection. Remove an old one before the files change.
    report_path.unlink(missing_ok=True)

    prompt = render_collect_prompt(
        config,
        str(output_root),
        str(collector_path),
        str(manifest_path),
        str(layout["task_configs"]),
    )
    request = build_agent_request(
        config.agent_cli,
        config.model,
        add_dirs=build_agent_add_dirs(config, output_root, include_repo_dir=False),
        api_key_env=config.api_key_env,
    )
    conversation_log_path = layout["root"] / "evidence_collection_conversation.json"
    attempts = 1 + max(0, retry_errors)
    for attempt in range(1, attempts + 1):
        try:
            invoke_text(request, prompt, timeout=timeout, conversation_log_path=conversation_log_path)
            manifest = normalize_manifest(ArtifactManifest.load_yaml(manifest_path))
            manifest.save_yaml(manifest_path)
            task_configs = _load_task_configs(layout["task_configs"], manifest)
            _validate_eval_configs(layout["task_configs"], manifest)
            break
        except _COLLECTION_ERRORS as exc:
            print(f"[bench-audit] evidence collection attempt {attempt} of {attempts} failed: {exc}", file=sys.stderr)
            if attempt == attempts:
                raise
            set_aside_failed_log(conversation_log_path)

    _write_text(report_path, _collection_report(manifest, task_configs), force=True)
    if manifest.has_no_task_unit:
        print(
            "[bench-audit] the collector found no item-level task in this benchmark (task_unit: none)"
            + (f": {manifest.task_unit_reason}" if manifest.task_unit_reason else "")
            + "\n[bench-audit] No task audit runs for it. Its benchmark-level audit is its audit.",
            file=sys.stderr,
        )
    return {**result, "skipped": False, "task_unit": manifest.task_unit}


def collection_is_done(output_root: str | Path) -> bool:
    """Return True when ``output_root`` holds a finished collection that the task audit can load."""
    root = Path(output_root)
    manifest_path = root / "artifact_manifest.yaml"
    if not manifest_path.is_file() or not (root / "collection_report.md").is_file():
        return False
    try:
        manifest = ArtifactManifest.load_yaml(manifest_path)
        _load_task_configs(root / "task_configs", manifest)
        _validate_eval_configs(root / "task_configs", manifest)
    except _COLLECTION_ERRORS:
        return False
    return True


def _load_task_configs(task_config_dir: Path, manifest: ArtifactManifest) -> dict[str, TaskConfig]:
    resolver = TaskConfigDirResolver(task_config_dir)
    configs: dict[str, TaskConfig] = {}
    missing: list[str] = []
    for task in manifest.tasks:
        try:
            task_dir = resolver.resolve(task.task_id)
        except FileNotFoundError:
            missing.append(task.task_id)
            continue
        configs[task.task_id] = TaskConfig.load_json(task_dir / "task_config.json")
    if missing:
        shown = ", ".join(missing[:10]) + (f" (+{len(missing) - 10} more)" if len(missing) > 10 else "")
        raise FileNotFoundError(
            f"the agent did not write a task config for {len(missing)} of {len(manifest.tasks)} tasks: {shown} "
            f"(expected {task_config_dir}/<task_id>/task_config.json)"
        )
    return configs


def _validate_eval_configs(task_config_dir: Path, manifest: ArtifactManifest) -> None:
    resolver = TaskConfigDirResolver(task_config_dir)
    for task in manifest.tasks:
        task_dir = resolver.resolve(task.task_id)
        for eval_id in task.eval_ids:
            path = task_dir / f"{eval_id}.json"
            if not path.exists():
                raise FileNotFoundError(
                    f"Agent did not generate EvalConfig for eval {eval_id}: expected {path}"
                )


def ensure_output_layout(output_root: str | Path) -> dict[str, Path]:
    root = Path(output_root).resolve()
    layout = {
        "root": root,
        "collector": root / "collector",
        "task_configs": root / "task_configs",
    }
    for path in layout.values():
        path.mkdir(parents=True, exist_ok=True)
    return layout


class TaskConfigDirResolver:
    def __init__(self, task_configs_root: Path) -> None:
        self.root = Path(task_configs_root)
        self._index: dict[str, Path] | None = None

    def resolve(self, task_id: str) -> Path:
        for candidate in _task_dir_candidates(task_id):
            task_dir = self.root / candidate
            if task_dir.is_dir() and (task_dir / "task_config.json").exists():
                return task_dir
        if self._index is None:
            self._index = _build_task_dir_index(self.root)
        mapped = self._index.get(task_id)
        if mapped is not None and (mapped / "task_config.json").exists():
            return mapped
        tried = ", ".join(
            str(self.root / candidate / "task_config.json")
            for candidate in _task_dir_candidates(task_id)
        )
        raise FileNotFoundError(
            f"task_config.json not found for task_id {task_id!r}; tried: {tried}"
        )


def _task_dir_candidates(task_id: str) -> list[str]:
    candidates = [task_id]

    underscore_sanitized = (
        task_id
        .replace("/", "_")
        .replace("\\", "_")
        .replace(":", "_")
        .replace(" ", "_")
    )
    if underscore_sanitized not in candidates:
        candidates.append(underscore_sanitized)

    legacy_sanitized = task_id.replace("/", "__").replace("\\", "__")
    if legacy_sanitized not in candidates:
        candidates.append(legacy_sanitized)

    return candidates


def _build_task_dir_index(task_configs_root: Path) -> dict[str, Path]:
    mapping: dict[str, Path] = {}
    if not task_configs_root.is_dir():
        return mapping
    for child in sorted(task_configs_root.iterdir()):
        if not child.is_dir():
            continue
        config_path = child / "task_config.json"
        if not config_path.exists():
            continue
        try:
            data = json.loads(config_path.read_text())
        except (OSError, ValueError):
            continue
        task_id = data.get("task_id") if isinstance(data, dict) else None
        if isinstance(task_id, str):
            mapping.setdefault(task_id, child)
    return mapping


def _write_text(path: Path, content: str, force: bool) -> None:
    if path.exists() and not force:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def _task_unit_lines(manifest: ArtifactManifest) -> list[str]:
    if manifest.has_no_task_unit:
        reason = f" {manifest.task_unit_reason}" if manifest.task_unit_reason else ""
        return [f"- Task unit: `none`. The collector found no item-level task, so this benchmark gets no task audit.{reason}"]
    return [f"- Task unit: {manifest.task_unit}"] if manifest.task_unit else []


def collection_has_no_task_unit(output_root: str | Path) -> bool:
    """Return True when the collection at ``output_root`` says that the benchmark has no item-level task."""
    try:
        return ArtifactManifest.load_yaml(Path(output_root) / "artifact_manifest.yaml").has_no_task_unit
    except _COLLECTION_ERRORS:
        return False


def _instruction_sources(task_configs: dict[str, TaskConfig]) -> str:
    """How many tasks have each ``instruction_source``, as one line for the collection report."""
    counts = Counter(tc.instruction_source or "not stated" for tc in task_configs.values())
    parts = [f"`{name}`: `{counts[name]}`" for name in (*INSTRUCTION_SOURCES, "not stated") if counts[name]]
    line = ", ".join(parts) or "no tasks"
    if counts[WRITTEN_BY_COLLECTOR]:
        line += " (the audit report does not count a task whose instruction the collector wrote)"
    return line


def _collection_report(manifest: ArtifactManifest, task_configs: dict[str, TaskConfig]) -> str:
    task_rows: list[str] = []
    total_evals = 0
    for task in manifest.tasks:
        tc = task_configs.get(task.task_id)
        total_evals += len(task.eval_ids)
        if tc:
            task_rows.append(
                "| "
                + " | ".join(
                    [
                        task.task_id,
                        tc.status,
                        str(tc.n_evals),
                        str(tc.n_passed),
                        str(tc.n_failed),
                        tc.primary_eval_id or "none",
                    ]
                )
                + " |"
            )
        else:
            task_rows.append(f"| {task.task_id} | unknown | {len(task.eval_ids)} | ? | ? | none |")

    return "\n".join(
        [
            "# Collection Report",
            "",
            f"- Benchmark: `{manifest.benchmark_name}`",
            f"- Benchmark type: `{manifest.benchmark_type}`",
            f"- Job type: `{manifest.job_type}`",
            f"- Benchmark data: `{manifest.benchmark_data_dir}`",
            f"- Source data: `{manifest.source_data_dir}`",
            *_task_unit_lines(manifest),
            f"- Tasks discovered: `{len(manifest.tasks)}`",
            f"- Evals discovered: `{total_evals}`",
            f"- Instruction source: {_instruction_sources(task_configs)}",
            "",
            "| Task | Status | Evals | Passed | Failed | Primary eval |",
            "| --- | --- | --- | --- | --- | --- |",
            *task_rows,
            "",
        ]
    )
