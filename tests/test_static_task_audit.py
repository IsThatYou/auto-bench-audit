"""Run the task-audit phase end to end with a stand-in for the agent.

The stand-in returns a fixed answer, so the test starts no agent and costs nothing.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bench_audit.evidence_collection import ArtifactManifest, TaskConfig, TaskEntry, load_collection_config
from bench_audit.paths import resolve_output_root
from bench_audit.task_evaluation import audit_tasks
from bench_audit.task_evaluation import runner as task_runner

from .conftest import write_config

TASK_IDS = ["task-a", "task-b", "task-c"]


def make_collection(tmp_path: Path):
    config = load_collection_config(write_config(tmp_path / "bench"))
    output_root = resolve_output_root(config)
    for task_id in TASK_IDS:
        task_dir = output_root / "task_configs" / task_id
        task_dir.mkdir(parents=True)
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
        tasks=[TaskEntry(task_id=task_id) for task_id in TASK_IDS],
    ).save_yaml(output_root / "artifact_manifest.yaml")
    return config, output_root


@pytest.fixture
def fake_agent(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    prompts: list[str] = []

    def invoke_structured(request, prompt, json_schema, timeout=None, conversation_log_path=None):
        prompts.append(prompt)
        return json.dumps({"overall_judgment": "audit_complete", "summary": "No problem found.", "confidence": "high", "findings": []})

    monkeypatch.setattr(task_runner, "invoke_structured", invoke_structured)
    return prompts


def test_static_audit_covers_every_task_without_the_all_flag(tmp_path: Path, fake_agent: list[str]):
    config, output_root = make_collection(tmp_path)
    result = audit_tasks(config, audit_mode="static")

    assert result["audit_mode"] == "static"
    assert result["selected_tasks"] == TASK_IDS
    assert len(fake_agent) == 3
    for task_id in TASK_IDS:
        record = json.loads((output_root / "task_audits_static" / f"{task_id}.json").read_text())
        assert record["task_status"] == "unscored" and record["overall_judgment"] == "audit_complete"
    assert (output_root / "audit_report_static.md").is_file()


def test_static_audit_sample_and_resume(tmp_path: Path, fake_agent: list[str]):
    config, _ = make_collection(tmp_path)
    first = audit_tasks(config, sample_n=2, sample_seed=1)  # static is the default mode for a static benchmark
    assert len(first["selected_tasks"]) == 2 and len(fake_agent) == 2

    second = audit_tasks(config, sample_n=2, sample_seed=1)
    assert second["skipped_tasks"] == first["selected_tasks"]
    assert len(fake_agent) == 2  # nothing ran again
