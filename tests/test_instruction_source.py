"""A task whose instruction text the evidence collector wrote itself is not judged on that text, and is not counted.

Every test replaces the agent with a stand-in, so no test starts an agent or costs money.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bench_audit.audit_models import FAILED_MARK, TaskAuditRecord, not_counted_reason
from bench_audit.config import load_benchmark_config
from bench_audit.evidence_collection import TaskConfig
from bench_audit.evidence_collection import runner as collection_runner
from bench_audit.evidence_collection.prompt import render_collect_prompt
from bench_audit.summary import summarize_task_audits
from bench_audit.task_audit import audit_tasks
from bench_audit.task_audit import prompt as task_prompt
from bench_audit.task_audit import runner as task_runner

from .conftest import write_collection, write_config

RULE_TITLE = "## The instruction text of this task is not the benchmark's text"
MAJOR_FINDING = {
    "finding_id": "f1",
    "category": "ambiguity",
    "subtype": "missing_required_inputs",
    "severity": 2,
    "claim": "The instruction does not give the paper text.",
    "why_it_matters": "The task cannot be solved from the instruction.",
    "evidence": [{"path": "task instruction_text", "note": "no paper text"}],
    "suggested_fix": "Give the paper text.",
}
ANSWER_WITH_MAJOR = json.dumps({"overall_judgment": "audit_complete", "summary": "One defect.", "confidence": "high", "findings": [MAJOR_FINDING]})


def task_config(**fields) -> TaskConfig:
    return TaskConfig.from_dict({"benchmark_name": "demo", "benchmark_type": "static", "job_type": "actual", "task_id": "task-a", "status": "unscored", **fields})


@pytest.fixture
def config(tmp_path: Path):
    return load_benchmark_config(write_config(tmp_path / "bench"))


def set_source(output_root: Path, task_id: str, source: str) -> None:
    config_path = output_root / "task_configs" / task_id / "task_config.json"
    config_path.write_text(json.dumps({**json.loads(config_path.read_text()), "instruction_source": source}))


# --- the field ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("written", "read"),
    [
        ("verbatim", "verbatim"),
        ("rendered", "rendered"),
        ("written_by_collector", "written_by_collector"),
        ("Written-by-Collector ", "written_by_collector"),
        ("written by collector", "written_by_collector"),
        ("synthesized", None),  # not one of the three values: the same as not stated
        (None, None),  # a task config of an older collection
    ],
)
def test_task_config_reads_the_instruction_source(written, read):
    assert task_config(instruction_source=written).instruction_source == read


def test_both_collector_prompts_ask_for_the_instruction_source(tmp_path: Path, config):
    trajectory_config = load_benchmark_config(write_config(tmp_path / "tb2", benchmark_type="tb2", jobs_dir="jobs"))
    for bench_config in (config, trajectory_config):
        prompt = render_collect_prompt(bench_config, "/out", "/out/collect.py", "/out/manifest.yaml", "/out/task_configs")
        assert "do not shorten,\n  summarize, or reword it" in prompt
        for value in ('"verbatim"', '"rendered"', '"written_by_collector"'):
            assert value in prompt
        assert "the audit report does not count such a task" in prompt


def test_collection_report_shows_how_many_tasks_have_each_source(config):
    output_root = write_collection(config, ["task-a", "task-b", "task-c"])
    set_source(output_root, "task-a", "verbatim")
    set_source(output_root, "task-b", "written_by_collector")
    task_configs = {task_id: TaskConfig.load_json(output_root / "task_configs" / task_id / "task_config.json") for task_id in ("task-a", "task-b", "task-c")}
    line = collection_runner._instruction_sources(task_configs)
    assert line.startswith("`verbatim`: `1`, `written_by_collector`: `1`, `not stated`: `1`")
    assert "does not count" in line


# --- the rule in the task audit prompt ----------------------------------------------------------


def prompts(config, **fields) -> tuple[str, str]:
    task = task_config(instruction_text="Q", **fields)
    return task_prompt.render_static_task_audit_prompt(task, "RUBRIC", config), task_prompt.render_task_audit_prompt(task, [], "RUBRIC")


def test_auditor_is_told_not_to_judge_a_text_that_the_collector_wrote(config):
    for prompt in prompts(config, instruction_source="written_by_collector"):
        assert RULE_TITLE in prompt and "File no finding about `instruction_text`" in prompt
        assert '"instruction_source": "written_by_collector"' in prompt
        assert prompt.index("RUBRIC") < prompt.index(RULE_TITLE) < prompt.index('"task_id"')  # right before the task's fields


def test_other_tasks_do_not_get_the_rule(config):
    for prompt in prompts(config, instruction_source="rendered"):
        assert RULE_TITLE not in prompt and '"instruction_source": "rendered"' in prompt
    for prompt in prompts(config):  # not stated
        assert RULE_TITLE not in prompt and '"instruction_source"' not in prompt


# --- the counts ---------------------------------------------------------------------------------


def test_reason_of_a_failed_call_or_a_missing_file_wins():
    assert not_counted_reason("audit_complete", "Audited.", "written_by_collector") == "instruction_written_by_collector"
    assert not_counted_reason("audit_complete", "Audited.", "rendered") is None
    assert not_counted_reason("audit_complete", "Audited.") is None
    assert not_counted_reason("agent_error", f"Task audit {FAILED_MARK}: timeout", "written_by_collector") == "failed_agent_call"
    assert not_counted_reason("insufficient_evidence", "The data is gated.", "written_by_collector") == "insufficient_evidence"


def test_task_with_a_text_from_the_collector_is_audited_but_not_counted(config, monkeypatch: pytest.MonkeyPatch):
    output_root = write_collection(config, ["task-a", "task-b", "task-c"])
    set_source(output_root, "task-a", "written_by_collector")
    set_source(output_root, "task-b", "verbatim")
    seen: list[str] = []

    def agent(request, prompt, json_schema=None, timeout=None, conversation_log_path=None):
        seen.append(json.loads(prompt.split("## Task artifacts\n", 1)[1])["task_id"])
        return ANSWER_WITH_MAJOR

    monkeypatch.setattr(task_runner, "invoke_structured", agent)

    result = audit_tasks(config)

    assert sorted(seen) == ["task-a", "task-b", "task-c"]  # the agent still audits the rest of the task
    records = {task_id: json.loads((output_root / "task_audits_static" / f"{task_id}.json").read_text()) for task_id in seen}
    assert [records[task_id]["instruction_source"] for task_id in ("task-a", "task-b", "task-c")] == ["written_by_collector", "verbatim", None]
    assert len(records["task-a"]["findings"]) == 1  # the finding stays in the record
    counts = result["summary"]
    assert counts["audited"] == 2
    assert counts["not_counted"] == {"total": 1, "instruction_written_by_collector": ["task-a"]}
    assert counts["tasks_with_major"] == {"count": 2, "task_ids": ["task-b", "task-c"]}
    report = (output_root / "audit_report_static.md").read_text()
    assert "- Not counted: `1` (instruction written by the collector: `1`)" in report
    assert "- Tasks with a major finding (severity 2 or higher): `2` of `2` audited" in report
    assert "| task-a | audit_complete | no | 1 | 1 | 0 |" in report


def test_record_of_an_older_audit_has_no_source_and_counts(tmp_path: Path):
    old = {
        "task_id": "task-a", "benchmark_name": "demo", "task_status": "unscored", "selected_eval_ids": [],
        "overall_judgment": "audit_complete", "summary": "One defect.", "confidence": "high", "findings": [MAJOR_FINDING],
    }  # fmt: skip
    path = tmp_path / "task-a.json"
    path.write_text(json.dumps(old))
    record = TaskAuditRecord.load_json(path)
    assert record.instruction_source is None
    summary = summarize_task_audits([record])
    assert summary.audited == ["task-a"] and summary.with_major == ["task-a"] and summary.not_counted == {}
