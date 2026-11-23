"""A benchmark with no item-level task gets no task audit: the collector says so, and no task audit runs.

Every test replaces the agent with a stand-in, so no test starts an agent or costs money.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from bench_audit import batch, cli
from bench_audit.config import load_benchmark_config
from bench_audit.evidence_collection import ArtifactManifest, collect_evidence, collection_has_no_task_unit, collection_is_done
from bench_audit.evidence_collection import runner as collection_runner
from bench_audit.evidence_collection.prompt import render_collect_prompt
from bench_audit.paths import resolve_output_root
from bench_audit.task_audit import audit_tasks
from bench_audit.task_audit import runner as task_runner

from .conftest import write_collection, write_config
from .test_summary import finding, record, save_records

REASON = "It is a review of 26 other benchmarks and has no items of its own."


@pytest.fixture
def config(tmp_path: Path):
    return load_benchmark_config(write_config(tmp_path / "bench"))


@pytest.fixture
def no_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(task_runner, "invoke_structured", lambda *args, **kwargs: pytest.fail("no task audit may start"))


def manifest(**fields) -> ArtifactManifest:
    return ArtifactManifest.from_dict(
        {"benchmark_name": "demo", "benchmark_type": "static", "benchmark_data_dir": "/d", "source_data_dir": "/d", "output_root": "/o", **fields}
    )


# --- the field ----------------------------------------------------------------------------------


@pytest.mark.parametrize("written", ["none", "None", "NONE", ' "none". '])
def test_manifest_reads_none_as_no_task_unit(written: str):
    assert manifest(task_unit=written).task_unit == "none" and manifest(task_unit=written).has_no_task_unit


@pytest.mark.parametrize("written", [None, "", "one row of test.jsonl: a question and its gold answer", "none of the rows has a gold answer"])
def test_other_values_mean_that_the_benchmark_has_tasks(written):
    assert not manifest(task_unit=written).has_no_task_unit


def test_manifest_keeps_the_field_in_its_file(tmp_path: Path):
    path = tmp_path / "artifact_manifest.yaml"
    manifest(task_unit="none", task_unit_reason=REASON).save_yaml(path)
    loaded = ArtifactManifest.load_yaml(path)
    assert loaded.has_no_task_unit and loaded.task_unit_reason == REASON and loaded.tasks == []
    # A manifest of an older collection has no such key.
    data = yaml.safe_load(path.read_text())
    del data["task_unit"], data["task_unit_reason"]
    path.write_text(yaml.safe_dump(data))
    assert ArtifactManifest.load_yaml(path).task_unit is None


def test_static_collector_prompt_asks_for_the_task_unit(tmp_path: Path, config):
    prompt = render_collect_prompt(config, "/out", "/out/collect.py", "/out/manifest.yaml", "/out/task_configs")
    assert '- task_unit: one sentence that says what one task of this benchmark is' in prompt
    assert 'Write the string "none" (not null) when the benchmark has no item-level task' in prompt
    assert "do not make up tasks" in prompt
    # A benchmark with recorded runs has one task per run folder, so its collector is not asked.
    trajectory_config = load_benchmark_config(write_config(tmp_path / "tb2", benchmark_type="tb2", jobs_dir="jobs"))
    assert "task_unit" not in render_collect_prompt(trajectory_config, "/out", "/out/collect.py", "/out/manifest.yaml", "/out/task_configs")


# --- the collection -----------------------------------------------------------------------------


def test_collection_with_no_task_unit_is_a_finished_collection(config, monkeypatch: pytest.MonkeyPatch, capsys):
    output_root = resolve_output_root(config)

    def collector(request, prompt, timeout=None, conversation_log_path=None):
        manifest(
            benchmark_data_dir=config.benchmark_data_dir, source_data_dir=config.source_data_dir, output_root=str(output_root),
            task_unit="None", task_unit_reason=REASON,
        ).save_yaml(output_root / "artifact_manifest.yaml")  # fmt: skip
        return ""

    monkeypatch.setattr(collection_runner, "invoke_text", collector)

    result = collect_evidence(config)

    assert result["task_unit"] == "none" and collection_is_done(output_root) and collection_has_no_task_unit(output_root)
    report = (output_root / "collection_report.md").read_text()
    assert "- Task unit: `none`." in report and REASON in report and "- Tasks discovered: `0`" in report
    err = capsys.readouterr().err
    assert "task_unit: none" in err and REASON in err


def test_collection_with_tasks_shows_its_task_unit(config):
    output_root = write_collection(config, ["task-a"], task_unit="one row of test.jsonl")
    assert not collection_has_no_task_unit(output_root)
    assert not collection_has_no_task_unit(output_root / "missing")
    assert collection_runner._task_unit_lines(ArtifactManifest.load_yaml(output_root / "artifact_manifest.yaml")) == ["- Task unit: one row of test.jsonl"]


# --- the task audit phase and the report --------------------------------------------------------


def test_no_task_audit_runs_and_the_report_says_so(config, no_agent, capsys):
    output_root = write_collection(config, [], task_unit="none", task_unit_reason=REASON)

    result = audit_tasks(config)

    assert result["task_unit"] == "none" and result["selected_tasks"] == [] and result["failed_tasks"] == []
    assert result["summary"]["task_level_audit"] is False and result["summary"]["audited"] == 0
    assert not (output_root / "task_audits_static").exists()
    report = (output_root / "audit_report_static.md").read_text()
    assert "- Task unit: `none`" in report
    assert "- Task-level audit: none." in report and "`benchmark_audit_report.md`" in report
    assert f"- Why there is no task unit: {REASON}" in report
    assert "- Tasks audited: `0`" in report
    data = json.loads((output_root / "audit_summary_static.json").read_text())
    assert data["task_unit"] == "none" and data["task_level_audit"] is False and data["tasks_with_major"]["count"] == 0
    err = capsys.readouterr().err
    assert "no task audit" in err and REASON in err


def test_tasks_and_records_from_before_are_left_out(config, no_agent, capsys):
    """The collector said "none" but the folder still holds tasks and task audit records: "none" wins."""
    output_root = write_collection(config, ["task-a", "task-b"], task_unit="none")
    save_records(output_root, [record("task-a", findings=[finding("f1", 2, "repo/eval.py")]), record("task-b")])

    result = audit_tasks(config, task_ids=["task-a"])

    assert result["selected_tasks"] == [] and result["written_task_audits"] == []
    assert result["summary"]["records"] == 0 and result["summary"]["tasks_with_major"]["count"] == 0
    report = (output_root / "audit_report_static.md").read_text()
    assert "- Task audit records of an earlier run that are left out: `2`" in report
    assert "| task-a | not_run | - | 0 | 0 | 0 |" in report
    assert "The 2 task(s) that the manifest lists are not audited." in capsys.readouterr().err


def test_benchmark_with_a_task_unit_is_audited_as_before(config, monkeypatch: pytest.MonkeyPatch):
    output_root = write_collection(config, ["task-a"], task_unit="one row of test.jsonl")
    answer = json.dumps({"overall_judgment": "audit_complete", "summary": "No problem found.", "confidence": "high", "findings": []})
    monkeypatch.setattr(task_runner, "invoke_structured", lambda *args, **kwargs: answer)

    result = audit_tasks(config)

    assert result["task_unit"] == "one row of test.jsonl" and result["selected_tasks"] == ["task-a"]
    assert result["summary"]["task_level_audit"] is True and result["summary"]["audited"] == 1
    report = (output_root / "audit_report_static.md").read_text()
    assert "- Task unit: one row of test.jsonl" in report and "Task-level audit: none" not in report


def test_report_of_an_older_collection_has_no_task_unit_line(config):
    output_root = write_collection(config, ["task-a"])
    save_records(output_root, [record("task-a")])
    assert cli.main(["report", "--config", str(Path(config.benchmark_data_dir).parent / "config.yaml")]) == 0
    assert "Task unit" not in (output_root / "audit_report_static.md").read_text()


def test_report_command_says_that_there_is_no_task_level_audit(config, no_agent, capsys):
    write_collection(config, [], task_unit="none")
    assert cli.main(["report", "--config", str(Path(config.benchmark_data_dir).parent / "config.yaml")]) == 0
    out = capsys.readouterr().out
    assert "No task-level audit" in out and "0 task(s) audited" in out


# --- the batch ----------------------------------------------------------------------------------


def test_batch_skips_the_task_stages_and_finishes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AUDIT_RUN_DIR", str(tmp_path / "runs"))
    config_path = write_config(tmp_path / "configs" / "multi_domain_all" / "demo_domain")
    commands: list[list[str]] = []

    def phase(cmd: list[str], audit_run_dir: str) -> int:
        commands.append(cmd[3:])
        bench_config = load_benchmark_config(cmd[cmd.index("--config") + 1])
        root = resolve_output_root(bench_config)
        root.mkdir(parents=True, exist_ok=True)
        if cmd[3] == "audit-benchmark":
            (root / "benchmark_audit.json").write_text(json.dumps({"overall_judgment": "audit_complete", "summary": "ok"}))
            (root / "benchmark_audit_report.md").write_text("report")
        elif cmd[3] == "collect-evidence":
            write_collection(bench_config, [], task_unit="none", task_unit_reason=REASON)
        elif cmd[3] == "report":
            assert cli.main(cmd[3:]) == 0  # the real command: it writes the report and starts no agent
        return 0

    monkeypatch.setattr(batch, "run_subprocess", phase)

    assert cli.main(["batch", "--config", str(config_path), "--max-tasks", "4"]) == 0

    assert [command[0] for command in commands] == ["audit-benchmark", "collect-evidence", "report"]
    root = tmp_path / "runs" / "static" / "demo__v1__actual"
    assert not (root / "sampled_tasks.json").exists()
    assert "- Task-level audit: none." in (root / "audit_report_static.md").read_text()
    (entry,) = json.loads((tmp_path / "runs" / batch.TRACKER_NAME).read_text())["configs"]
    assert entry["overall"] == "done" and entry["stage_sample_tasks"] == "done" and entry["stage_audit_tasks"] == "done"
