"""A task whose files are not on disk is not audited, and a missing file is not a defect of the task.

Every test replaces the agent with a stand-in, so no test starts an agent or costs money.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bench_audit import cli
from bench_audit.audit_models import MISSING_INPUTS_MARK, not_counted_reason, record_is_done
from bench_audit.config import load_benchmark_config
from bench_audit.evidence_collection import ArtifactManifest, EvalConfig, TaskConfig, TaskEntry
from bench_audit.paths import resolve_output_root
from bench_audit.task_audit import audit_tasks
from bench_audit.task_audit import prompt as task_prompt
from bench_audit.task_audit import runner as task_runner
from bench_audit.task_audit.preflight import missing_input_paths

from .conftest import write_collection, write_config

GOOD_ANSWER = json.dumps({"overall_judgment": "audit_complete", "summary": "No problem found.", "confidence": "high", "findings": []})


def task_config(**fields) -> TaskConfig:
    return TaskConfig.from_dict({"benchmark_name": "demo", "benchmark_type": "static", "job_type": "actual", "task_id": "task-a", "status": "unscored", **fields})


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A benchmark folder with a few files, some with marks in their names."""
    root = tmp_path / "bench" / "repo"
    for name in ("eval.py", "score.py", "data/q1.json", "data/mimic _sdoh/ids.csv", "data/Dataset,_Evaluation,_and_Benchmark.json", "data/A;_B.json"):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x")
    return root


# --- which paths count as missing ---------------------------------------------------------------


def test_nothing_is_missing_when_every_named_file_exists(repo: Path):
    config = task_config(
        task_bundle_path=str(repo / "data" / "q1.json"),
        tests_ref=str(repo / "eval.py"),
        environment_ref=str(repo / "data"),  # a folder
        audit_notes=f"The grader is {repo}/eval.py:39 and it calls {repo}/score.py::normalize. See {repo}/data/q1.json.",
    )
    assert missing_input_paths(config, roots=[repo]) == []


def test_a_missing_path_field_is_listed(repo: Path):
    config = task_config(task_bundle_path=str(repo / "data" / "q1.json"), solution_ref=str(repo / "gold" / "q1.txt"), tests_ref=str(repo / "eval.py"))
    assert missing_input_paths(config) == [str(repo / "gold" / "q1.txt")]


def test_a_field_with_several_paths_lists_only_the_missing_ones(repo: Path):
    config = task_config(
        tests_ref=f"{repo}/eval.py;{repo}/gone.py;{repo}/score.py",
        solution_ref=f"{repo}/score.py, {repo}/also_gone.py",
    )
    assert missing_input_paths(config) == [f"{repo}/also_gone.py", f"{repo}/gone.py"]  # PATH_FIELDS order: solution_ref, then tests_ref


def test_a_file_name_with_a_space_comma_or_semicolon_is_one_path(repo: Path):
    config = task_config(
        task_bundle_path=str(repo / "data" / "mimic _sdoh" / "ids.csv"),
        solution_ref=str(repo / "data" / "Dataset,_Evaluation,_and_Benchmark.json"),
        tests_ref=str(repo / "data" / "A;_B.json"),
    )
    assert missing_input_paths(config) == []


def test_values_that_are_not_checkable_paths_are_left_alone(repo: Path):
    config = task_config(
        task_bundle_path="https://huggingface.co/datasets/demo/demo",
        solution_ref="inline",
        tests_ref=f"{repo}/tasks/{{task_id}}/test.py",
        environment_ref=f"{repo}/data/*.json",
        instruction_path=f"{repo}/eval.py:12-40",
    )
    assert missing_input_paths(config) == []


def test_a_relative_path_is_looked_up_under_the_run_folders(repo: Path):
    config = task_config(tests_ref="data/q1.json", solution_ref="data/q2.json")
    assert missing_input_paths(config, roots=[repo]) == ["data/q2.json"]


def test_notes_are_checked_only_for_paths_under_the_run_folders(repo: Path):
    notes = (
        f"The image is {repo}/data/images/q1.png. It was not downloaded. "
        f"The task's container has /app/solution.py and /tests/test_outputs.py. "
        f"The ids are in {repo}/data/mimic _sdoh/ids.csv, and the cards in ({repo}/data/Dataset,_Evaluation,_and_Benchmark.json)."
    )
    config = task_config(tests_ref=str(repo / "eval.py"), audit_notes=notes)
    assert missing_input_paths(config, roots=[repo]) == [f"{repo}/data/images/q1.png"]
    assert missing_input_paths(config) == []  # without the run folders, prose is not checked


# --- the task audit phase -----------------------------------------------------------------------


class CountingAgent:
    def __init__(self) -> None:
        self.task_ids: list[str] = []

    def __call__(self, request, prompt, json_schema=None, timeout=None, conversation_log_path=None):
        self.task_ids.append(json.loads(prompt.split("## Task artifacts\n", 1)[1])["task_id"])
        return GOOD_ANSWER


@pytest.fixture
def config(tmp_path: Path):
    return load_benchmark_config(write_config(tmp_path / "bench"))


@pytest.fixture
def agent(monkeypatch: pytest.MonkeyPatch) -> CountingAgent:
    stand_in = CountingAgent()
    monkeypatch.setattr(task_runner, "invoke_structured", stand_in)
    return stand_in


def name_file(output_root: Path, task_id: str, field: str, path: Path) -> None:
    config_path = output_root / "task_configs" / task_id / "task_config.json"
    config_path.write_text(json.dumps({**json.loads(config_path.read_text()), field: str(path)}))


def test_task_with_a_missing_file_gets_no_agent_call_and_is_not_counted(config, agent: CountingAgent, capsys):
    output_root = write_collection(config, ["task-a", "task-b"])
    image = Path(config.benchmark_data_dir) / "images" / "a.png"
    name_file(output_root, "task-a", "task_bundle_path", image)

    result = audit_tasks(config)

    assert agent.task_ids == ["task-b"]
    assert result["not_auditable_tasks"] == ["task-a"]
    assert result["failed_tasks"] == []  # nothing failed, so the exit code stays 0
    record_path = output_root / "task_audits_static" / "task-a.json"
    record = json.loads(record_path.read_text())
    assert record["overall_judgment"] == "insufficient_evidence" and record["findings"] == []
    assert MISSING_INPUTS_MARK in record["summary"] and str(image) in record["summary"]
    assert not_counted_reason(record["overall_judgment"], record["summary"]) == "missing_input_files"
    assert record_is_done(record_path)  # a batch does not see it as a failed call
    assert result["summary"]["audited"] == 1
    assert result["summary"]["not_counted"] == {"total": 1, "missing_input_files": ["task-a"]}
    assert "- Not counted: `1` (missing input files: `1`)" in (output_root / "audit_report_static.md").read_text()
    err = capsys.readouterr().err
    assert "1 of 2 tasks were not audited" in err and "task-a" in err


def test_the_next_run_checks_the_files_again(config, agent: CountingAgent):
    output_root = write_collection(config, ["task-a", "task-b"])
    image = Path(config.benchmark_data_dir) / "images" / "a.png"
    name_file(output_root, "task-a", "task_bundle_path", image)
    audit_tasks(config)
    assert agent.task_ids == ["task-b"]

    second = audit_tasks(config)  # still missing: checked again, and still no agent call
    assert agent.task_ids == ["task-b"]
    assert second["not_auditable_tasks"] == ["task-a"] and second["skipped_tasks"] == ["task-b"]

    image.parent.mkdir(parents=True)
    image.write_text("png")
    third = audit_tasks(config)  # the file is there now, so the task is audited
    assert agent.task_ids == ["task-b", "task-a"]
    assert third["not_auditable_tasks"] == [] and third["summary"]["audited"] == 2
    assert json.loads((output_root / "task_audits_static" / "task-a.json").read_text())["overall_judgment"] == "audit_complete"


def test_the_check_can_be_turned_off(config, agent: CountingAgent, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    output_root = write_collection(config, ["task-a"])
    name_file(output_root, "task-a", "tests_ref", Path(config.benchmark_data_dir) / "gone.py")

    result = audit_tasks(config, check_inputs=False)
    assert agent.task_ids == ["task-a"] and result["not_auditable_tasks"] == []

    seen: list[dict] = []
    monkeypatch.setattr(cli, "audit_tasks", lambda config, **kwargs: seen.append(kwargs) or {})
    config_path = str(tmp_path / "bench" / "config.yaml")
    cli.main(["audit-tasks", "--config", config_path])
    cli.main(["audit-tasks", "--config", config_path, "--skip-input-check"])
    assert [kwargs["check_inputs"] for kwargs in seen] == [True, False]


def test_trajectory_audit_writes_a_valid_record_for_a_task_with_a_missing_file(tmp_path: Path, agent: CountingAgent):
    config = load_benchmark_config(write_config(tmp_path / "bench", benchmark_type="tb2", jobs_dir="jobs"))
    output_root = resolve_output_root(config)
    task_dir = output_root / "task_configs" / "task-a"
    TaskConfig.from_dict(
        {
            "benchmark_name": "demo", "benchmark_type": "tb2", "job_type": "actual", "task_id": "task-a",
            "status": "failed", "primary_eval_id": "run1", "n_evals": 1, "n_failed": 1,
            "instruction_text": "Solve it.", "tests_ref": str(tmp_path / "bench" / "data" / "task-a" / "tests"),
        }
    ).save_json(task_dir / "task_config.json")
    EvalConfig.from_dict({"task_id": "task-a", "eval_id": "run1", "status": "failed", "is_primary_eval": True}).save_json(task_dir / "run1.json")
    ArtifactManifest(
        benchmark_name="demo", benchmark_type="tb2", benchmark_data_dir=config.benchmark_data_dir,
        source_data_dir=config.source_data_dir, output_root=str(output_root), tasks=[TaskEntry(task_id="task-a", eval_ids=["run1"])],
    ).save_yaml(output_root / "artifact_manifest.yaml")

    result = audit_tasks(config)

    assert agent.task_ids == [] and result["audit_mode"] == "trajectory" and result["not_auditable_tasks"] == ["task-a"]
    record = json.loads((output_root / "task_audits" / "task-a.json").read_text())
    assert record["task_status"] == "failed" and record["selected_eval_ids"] == ["run1"]
    assert record["overall_judgment"] == "insufficient_evidence"


# --- the rule in the prompt ---------------------------------------------------------------------


def test_both_task_audit_prompts_say_that_a_file_missing_here_is_not_a_defect(config):
    rule = "A file that you cannot open on this machine is not a defect of the task."
    static_prompt = task_prompt.render_static_task_audit_prompt(task_config(instruction_text="Q"), "RUBRIC", config)
    trajectory_prompt = task_prompt.render_task_audit_prompt(task_config(instruction_text="Q"), [], "RUBRIC")
    for prompt in (static_prompt, trajectory_prompt):
        assert rule in prompt and "insufficient_evidence" in prompt
        assert prompt.index(rule) < prompt.index("RUBRIC")  # part of the contract, before the rubric
