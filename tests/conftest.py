from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Each test starts with no data-folder variables and an empty working folder."""
    for name in ("AUDIT_RUN_DIR", "BENCHMARK_REPOS_DIR"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    from bench_audit import paths

    paths._announced.clear()
    yield


def write_config(folder: Path, **overrides) -> Path:
    import yaml

    data = {
        "benchmark_name": "demo",
        "benchmark_type": "static",
        "agent_cli": "claude",
        "model": "claude-opus-4-7",
        "output_subdir": "v1",
        "benchmark_data_dir": "data",
        "benchmark_repo_dir": "repo",
    }
    data.update(overrides)
    data = {key: value for key, value in data.items() if value is not None}
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "config.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    return path


def write_collection(config, task_ids: list[str], *, skip_configs: tuple[str, ...] = (), report: bool = True) -> Path:
    """Write the files that a finished evidence collection leaves behind. Return the output folder."""
    from bench_audit.evidence_collection import ArtifactManifest, TaskConfig, TaskEntry
    from bench_audit.paths import resolve_output_root

    output_root = resolve_output_root(config)
    for task_id in task_ids:
        if task_id in skip_configs:
            continue
        task_dir = output_root / "task_configs" / task_id
        task_dir.mkdir(parents=True, exist_ok=True)
        TaskConfig.from_dict(
            {
                "benchmark_name": config.benchmark_name,
                "benchmark_type": config.benchmark_type,
                "job_type": "actual",
                "task_id": task_id,
                "status": "unscored",
                "n_evals": 0,
                "n_passed": 0,
                "n_failed": 0,
                "instruction_text": f"Solve {task_id}.",
            }
        ).save_json(task_dir / "task_config.json")
    ArtifactManifest(
        benchmark_name=config.benchmark_name,
        benchmark_type=config.benchmark_type,
        benchmark_data_dir=config.benchmark_data_dir,
        source_data_dir=config.source_data_dir,
        output_root=str(output_root),
        tasks=[TaskEntry(task_id=task_id) for task_id in task_ids],
    ).save_yaml(output_root / "artifact_manifest.yaml")
    if report:
        (output_root / "collection_report.md").write_text("report")
    return output_root
