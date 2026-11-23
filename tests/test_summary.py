"""The counts over the task audits of one benchmark: which records count as audited tasks."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bench_audit.audit_models import FAILED_MARK, TaskAuditRecord, not_counted_reason
from bench_audit.audit_protocol import load_general_rubric
from bench_audit.config import load_benchmark_config
from bench_audit.report import render_audit_report, write_audit_report
from bench_audit.summary import summarize_task_audits
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
    assert "| failed-call | agent_error | no | 0 | 0 |" in report
    assert "| no-evidence | insufficient_evidence | no | 1 | 1 |" in report
    assert "| own-agent-error | agent_error | yes | 1 | 1 |" in report
    assert "| never-run | not_run | - | 0 | 0 |" in report


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
