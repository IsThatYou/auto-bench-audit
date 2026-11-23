"""Counts over the task audit records of one benchmark.

Only a finished audit is a result. A record that a failed agent call left behind, and a record
that says ``insufficient_evidence``, are not results (see ``not_counted_reason``). They are left
out of every count and rate here, and they are reported as their own number: "not counted".
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from .audit_models import MAJOR_SEVERITY, TaskAuditRecord, not_counted_reason


@dataclass(slots=True)
class TaskAuditSummary:
    """Every list holds task ids in sorted order."""

    judgments: dict[str, int] = field(default_factory=dict)  # every record, also the ones that are not counted
    audited: list[str] = field(default_factory=list)  # the tasks that count
    not_counted: dict[str, list[str]] = field(default_factory=dict)  # reason -> tasks
    with_major: list[str] = field(default_factory=list)  # audited tasks with one or more major findings

    @property
    def records(self) -> int:
        return sum(self.judgments.values())

    @property
    def not_counted_total(self) -> int:
        return sum(len(task_ids) for task_ids in self.not_counted.values())

    def is_counted(self, task_id: str) -> bool:
        return task_id in self.audited

    def to_dict(self) -> dict[str, Any]:
        return {
            "records": self.records,
            "judgments": dict(sorted(self.judgments.items())),
            "audited": len(self.audited),
            "not_counted": {"total": self.not_counted_total, **{reason: list(ids) for reason, ids in sorted(self.not_counted.items())}},
            "major_severity": MAJOR_SEVERITY,
            "tasks_with_major": {"count": len(self.with_major), "task_ids": list(self.with_major)},
        }


def major_findings(record: TaskAuditRecord) -> list[Any]:
    return [finding for finding in record.findings if finding.severity >= MAJOR_SEVERITY]


def summarize_task_audits(records: Iterable[TaskAuditRecord]) -> TaskAuditSummary:
    summary = TaskAuditSummary()
    for record in sorted(records, key=lambda item: item.task_id):
        summary.judgments[record.overall_judgment] = summary.judgments.get(record.overall_judgment, 0) + 1
        reason = not_counted_reason(record.overall_judgment, record.summary)
        if reason is not None:
            summary.not_counted.setdefault(reason, []).append(record.task_id)
            continue
        summary.audited.append(record.task_id)
        if major_findings(record):
            summary.with_major.append(record.task_id)
    return summary
