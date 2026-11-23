"""The counts over the task audits of one benchmark: which records count as audited tasks."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bench_audit import cli
from bench_audit.audit_models import FAILED_MARK, TaskAuditFinding, TaskAuditRecord, not_counted_reason
from bench_audit.audit_protocol import load_general_rubric
from bench_audit.config import load_benchmark_config
from bench_audit.report import render_audit_report, write_audit_report
from bench_audit.summary import cited_code_files, same_file_names, summarize_task_audits
from bench_audit.task_audit import audit_tasks
from bench_audit.task_audit import runner as task_runner

from .conftest import write_collection, write_config


def finding(finding_id: str, severity: int, *paths: str) -> dict:
    return {
        "finding_id": finding_id,
        "category": "test_quality",
        "subtype": "grader",
        "severity": severity,
        "claim": "The grader rejects a correct answer.",
        "why_it_matters": "A correct answer fails.",
        "evidence": [{"path": path, "note": "seen here"} for path in paths],
        "suggested_fix": "Fix the grader.",
    }


def record(task_id: str, judgment: str = "audit_complete", summary: str = "Audited.", findings: list[dict] | None = None) -> TaskAuditRecord:
    return TaskAuditRecord.from_dict(
        {
            "task_id": task_id,
            "benchmark_name": "demo",
            "task_status": "unscored",
            "selected_eval_ids": [],
            "overall_judgment": judgment,
            "summary": summary,
            "confidence": "high",
            "findings": findings or [],
        }
    )


def save_records(output_root: Path, records: list[TaskAuditRecord]) -> None:
    for item in records:
        item.save_json(output_root / "task_audits_static" / f"{item.task_id}.json")


@pytest.fixture
def config(tmp_path: Path):
    return load_benchmark_config(write_config(tmp_path / "bench"))


MIXED_RECORDS = [
    record("clean"),
    record("major", findings=[finding("f1", 2, "repo/eval.py")]),
    record("minor", findings=[finding("f1", 1, "repo/eval.py")]),
    # The agent call failed. bench-audit wrote this record itself.
    record("failed-call", "agent_error", f"Static task audit {FAILED_MARK}: claude timed out after 800s"),
    # The auditor could not judge the task. Its finding must not make the task a "task with a major finding".
    record("no-evidence", "insufficient_evidence", "The task's images are not on this machine.", [finding("f1", 2, "data/img.png")]),
    # The auditor's own agent_error with a normal verdict is a finished audit.
    record("own-agent-error", "agent_error", "The agent under test made the mistake.", [finding("f1", 2, "repo/eval.py")]),
]


def test_failed_call_and_insufficient_evidence_are_not_counted():
    assert not_counted_reason("agent_error", f"Task audit {FAILED_MARK}: timeout") == "failed_agent_call"
    assert not_counted_reason("insufficient_evidence", "The data is gated.") == "insufficient_evidence"
    assert not_counted_reason("agent_error", "The agent under test made the mistake.") is None
    assert not_counted_reason("audit_complete", "Audited.") is None


def test_summary_leaves_records_that_are_not_results_out_of_every_count():
    summary = summarize_task_audits(MIXED_RECORDS)

    assert summary.records == 6
    assert summary.audited == ["clean", "major", "minor", "own-agent-error"]
    assert summary.not_counted == {"failed_agent_call": ["failed-call"], "insufficient_evidence": ["no-evidence"]}
    assert summary.not_counted_total == 2
    assert summary.with_major == ["major", "own-agent-error"]  # not "no-evidence", although it has a severity 2 finding
    assert summary.judgments == {"audit_complete": 3, "agent_error": 2, "insufficient_evidence": 1}


def test_report_and_summary_file_show_not_counted_as_its_own_number(config):
    task_ids = [item.task_id for item in MIXED_RECORDS] + ["never-run"]
    output_root = write_collection(config, task_ids)
    save_records(output_root, MIXED_RECORDS)

    result = write_audit_report(output_root, load_general_rubric(), audit_mode="static")

    data = json.loads((output_root / "audit_summary_static.json").read_text())
    assert data == result["summary"]
    assert data["tasks_discovered"] == 7 and data["records"] == 6 and data["audited"] == 4
    assert data["not_counted"] == {"total": 2, "failed_agent_call": ["failed-call"], "insufficient_evidence": ["no-evidence"]}
    assert data["tasks_with_major"] == {"count": 2, "task_ids": ["major", "own-agent-error"]}

    report = (output_root / "audit_report_static.md").read_text()
    assert report == render_audit_report(output_root, load_general_rubric(), audit_mode="static")
    assert "- Task audit records: `6`" in report
    assert "- Tasks audited: `4`" in report
    assert "- Not counted: `2` (failed agent calls: `1`, insufficient evidence: `1`)" in report
    assert "- Tasks with a major finding (severity 2 or higher): `2` of `4` audited" in report
    assert "| failed-call | agent_error | no | 0 | 0 | 0 |" in report
    assert "| no-evidence | insufficient_evidence | no | 1 | 1 | 0 |" in report
    assert "| own-agent-error | agent_error | yes | 1 | 1 | 0 |" in report
    assert "| never-run | not_run | - | 0 | 0 | 0 |" in report


def test_report_with_no_records(config):
    output_root = write_collection(config, ["task-a"])
    data = write_audit_report(output_root, load_general_rubric(), audit_mode="static")["summary"]
    assert data["audited"] == 0 and data["not_counted"] == {"total": 0} and data["tasks_with_major"]["count"] == 0
    assert "- Not counted: `0`\n" in (output_root / "audit_report_static.md").read_text()


def test_audit_tasks_returns_the_counts(config, monkeypatch: pytest.MonkeyPatch):
    """A task that the auditor answers with insufficient_evidence is finished, but it is not an audited task."""
    output_root = write_collection(config, ["task-a", "task-b"])

    def agent(request, prompt, json_schema=None, timeout=None, conversation_log_path=None):
        if '"task_id": "task-a"' in prompt:
            return json.dumps({"overall_judgment": "insufficient_evidence", "summary": "The input video is not here.", "confidence": "low", "findings": []})
        return json.dumps({"overall_judgment": "audit_complete", "summary": "Audited.", "confidence": "high", "findings": [finding("f1", 2, "repo/eval.py")]})

    monkeypatch.setattr(task_runner, "invoke_structured", agent)
    result = audit_tasks(config)

    assert result["failed_tasks"] == []
    assert result["summary"]["audited"] == 1
    assert result["summary"]["not_counted"] == {"total": 1, "insufficient_evidence": ["task-a"]}
    assert result["summary"]["tasks_with_major"]["task_ids"] == ["task-b"]
    assert Path(result["summary_path"]) == output_root / "audit_summary_static.json"


# --- shared findings ----------------------------------------------------------------------------

REPO = "/work/benchmark_repos/demo/repo"
GRADER = f"{REPO}/benchmark/evaluation.py"


def shared_defect_records() -> list[TaskAuditRecord]:
    """Ten tasks. Four of them carry the same major finding about the shared grader."""
    return [
        record("t01", findings=[finding("f1", 2, GRADER)]),
        record("t02", findings=[finding("f1", 2, f"{GRADER}:384")]),
        record("t03", findings=[finding("f1", 2, "benchmark/evaluation.py:380-390", f"{REPO}/data/tasks/t03/task.json")]),
        # t04 has the shared finding and a defect of its own: it stays a task with a major finding.
        record("t04", findings=[finding("f1", 2, GRADER), finding("f2", 2, f"{REPO}/data/tasks/t04/task.json")]),
        # t05's major finding cites code that only this task uses.
        record("t05", findings=[finding("f1", 2, f"{REPO}/tasks/t05/tests/test_outputs.py")]),
        # A minor finding about the grader is not a major finding, so t06 does not add to the share.
        record("t06", findings=[finding("f1", 1, GRADER)]),
        record("t07"),
        record("t08"),
        record("t09"),
        record("t10"),
    ]


def test_cited_code_files_reads_the_forms_that_auditors_write():
    def cited(*paths: str) -> set[str]:
        return cited_code_files(TaskAuditFinding.from_dict(finding("f1", 2, *paths)))

    assert cited(GRADER, f"{GRADER}:384", f"{GRADER}:380-390", f"{GRADER}::score_answer", f"{GRADER}#L380-L390") == {GRADER}
    assert cited("repo/benchmark/evaluation.py (line 384)", "`repo/run.sh`, lines 3-9") == {"repo/benchmark/evaluation.py", "repo/run.sh"}
    # Labels and data files are not code files.
    assert cited("rubric calibration", "task instruction_text", f"{REPO}/data/hle_test.jsonl", "task_bundle: hle_test.jsonl id=66 (reference_answer field)") == set()


def test_a_relative_path_and_an_absolute_path_of_one_file_get_one_name():
    names = same_file_names([GRADER, "benchmark/evaluation.py", "evaluation.py", "/other/tool/utils.py", f"{REPO}/utils.py", "utils.py"])
    assert names["benchmark/evaluation.py"] == names["evaluation.py"] == names[GRADER] == GRADER
    assert names["utils.py"] == "utils.py"  # two files end with it, so it is not merged with either


def test_major_finding_that_cites_a_widely_cited_code_file_is_marked_as_shared():
    summary = summarize_task_audits(shared_defect_records())

    assert summary.shared_files == {GRADER: ["t01", "t02", "t03", "t04"]}  # 4 of 10 audited tasks: 40%, above the 30% default
    assert summary.shared_findings == {"t01": ["f1"], "t02": ["f1"], "t03": ["f1"], "t04": ["f1"]}
    assert summary.with_major == ["t01", "t02", "t03", "t04", "t05"]
    assert summary.with_major_not_shared == ["t04", "t05"]


def test_the_threshold_is_a_parameter():
    records = shared_defect_records()
    assert summarize_task_audits(records, shared_threshold=0.4).shared_files  # 40% is "at least 40%"
    strict = summarize_task_audits(records, shared_threshold=0.5)
    assert strict.shared_files == {} and strict.shared_findings == {}
    assert strict.with_major_not_shared == strict.with_major == ["t01", "t02", "t03", "t04", "t05"]
    assert summarize_task_audits(records, shared_threshold=1.5).shared_files == {}  # above 1: nothing is shared
    with pytest.raises(ValueError, match="greater than 0"):
        summarize_task_audits(records, shared_threshold=0)


def test_a_file_that_one_or_two_tasks_cite_is_not_shared_in_a_small_audit():
    two_of_five = [record("t1", findings=[finding("f1", 2, GRADER)]), record("t2", findings=[finding("f1", 2, GRADER)]), record("t3"), record("t4"), record("t5")]
    assert summarize_task_audits(two_of_five).shared_files == {}  # 40%, but only 2 tasks

    three_of_five = two_of_five[:2] + [record("t3", findings=[finding("f1", 2, GRADER)]), record("t4"), record("t5")]
    assert summarize_task_audits(three_of_five).with_major_not_shared == []


def test_a_data_file_that_every_task_cites_is_not_shared():
    data_file = f"{REPO}/data/questions.jsonl"
    records = [record(f"t{i}", findings=[finding("f1", 2, data_file)]) for i in range(6)]
    summary = summarize_task_audits(records)
    assert summary.shared_files == {} and len(summary.with_major_not_shared) == 6


def test_records_that_are_not_counted_do_not_decide_what_is_shared():
    failed = [record(f"x{i}", "insufficient_evidence", "No data.", [finding("f1", 2, GRADER)]) for i in range(8)]
    counted = [record("t1", findings=[finding("f1", 2, GRADER)]), *(record(f"t{i}") for i in range(2, 11))]
    summary = summarize_task_audits(failed + counted)
    assert len(summary.audited) == 10 and summary.shared_files == {}  # 1 of 10 audited tasks cites the grader
    assert summary.with_major_not_shared == ["t1"]


def test_report_gives_both_counts_and_no_record_changes(config):
    records = shared_defect_records()
    output_root = write_collection(config, [item.task_id for item in records])
    save_records(output_root, records)
    record_files = sorted((output_root / "task_audits_static").glob("*.json"))
    before = [path.read_text() for path in record_files]

    data = write_audit_report(output_root, load_general_rubric(), audit_mode="static")["summary"]

    assert [path.read_text() for path in record_files] == before  # the mark is in the report, the findings stay as they are
    assert data["tasks_with_major"]["count"] == 5
    assert data["tasks_with_major_not_shared"] == {"count": 2, "task_ids": ["t04", "t05"]}
    assert data["shared"] == {
        "threshold": 0.3,
        "min_tasks": 3,
        "files": [{"path": GRADER, "tasks": 4, "share": 0.4}],
        "findings": {"t01": ["f1"], "t02": ["f1"], "t03": ["f1"], "t04": ["f1"]},
    }
    report = (output_root / "audit_report_static.md").read_text()
    assert "- Tasks with a major finding (severity 2 or higher): `5` of `10` audited" in report
    assert "- Tasks with a major finding, shared findings left out: `2` of `10` audited" in report
    assert f"| `{GRADER}` | 4 | 40% |" in report
    assert "| t04 | audit_complete | yes | 2 | 2 | 1 |" in report
    assert "| t05 | audit_complete | yes | 1 | 1 | 0 |" in report


def test_report_command_counts_again_without_an_agent(config, tmp_path: Path, capsys, monkeypatch: pytest.MonkeyPatch):
    records = shared_defect_records()
    output_root = write_collection(config, [item.task_id for item in records])
    save_records(output_root, records)
    monkeypatch.setattr(task_runner, "invoke_structured", lambda *args, **kwargs: pytest.fail("the report must not start an agent"))
    config_path = str(tmp_path / "bench" / "config.yaml")

    assert cli.main(["report", "--config", config_path]) == 0
    out = capsys.readouterr().out
    assert "10 task(s) audited, 0 not counted. Tasks with a major finding: 5; with shared findings left out: 2." in out
    assert str(output_root / "audit_report_static.md") in out

    assert cli.main(["report", "--config", config_path, "--shared-threshold", "0.5"]) == 0
    data = json.loads((output_root / "audit_summary_static.json").read_text())
    assert data["shared"]["threshold"] == 0.5 and data["tasks_with_major_not_shared"]["count"] == 5
    assert "No code file is cited that often" in (output_root / "audit_report_static.md").read_text()

    with pytest.raises(SystemExit) as exit_info:
        cli.main(["report", "--config", config_path, "--shared-threshold", "0"])
    assert exit_info.value.code == 2


def test_audit_tasks_passes_the_threshold_to_the_report(config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    seen: list[dict] = []
    monkeypatch.setattr(cli, "audit_tasks", lambda config, **kwargs: seen.append(kwargs) or {})
    config_path = str(tmp_path / "bench" / "config.yaml")
    cli.main(["audit-tasks", "--config", config_path])
    cli.main(["run", "--config", config_path, "--start-phase", "task_audit", "--shared-threshold", "0.5"])
    assert [kwargs["shared_threshold"] for kwargs in seen] == [0.3, 0.5]

    records = shared_defect_records()
    output_root = write_collection(config, [item.task_id for item in records])
    save_records(output_root, records)
    result = audit_tasks(config, shared_threshold=0.5)  # every task has a record, so no agent runs
    assert result["skipped_tasks"] == [item.task_id for item in records]
    assert result["summary"]["shared"]["threshold"] == 0.5 and result["summary"]["shared"]["files"] == []
