"""Counts over the task audit records of one benchmark.

Only a finished audit of the benchmark's own task is a result. A record that a failed agent call
left behind, a record that says ``insufficient_evidence``, and the record of a task whose
instruction text the evidence collector wrote itself, are not results (see ``not_counted_reason``).
They are left out of every count and rate here, and they are reported as their own number: "not counted".

Shared findings. Every task audit sees the whole benchmark repository, so one defect in shared
code (a scoring script, a prompt builder) is filed again on every task that uses the code. The
count of tasks with a major finding then says "most tasks", while the benchmark has one bug.
A major finding is marked as shared when it cites a code file that the major findings of many
audited tasks cite (``shared_threshold``, a share of the audited tasks). The count of tasks with
a major finding is given twice: with every major finding, and with the shared ones left out.
The mark is a count over the records. No record is changed and no finding is removed.
"""

from __future__ import annotations

import posixpath
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from .audit_models import MAJOR_SEVERITY, TaskAuditFinding, TaskAuditRecord, not_counted_reason

# A code file counts as shared when the major findings of at least this share of the audited tasks cite it.
DEFAULT_SHARED_THRESHOLD = 0.3
# ... and of at least this many tasks. Below 10 audited tasks the share alone would mark a file that 1 or 2 tasks cite.
SHARED_MIN_TASKS = 3
# Only code files can be shared. A data file that holds every task (questions.jsonl) is cited by many
# tasks for many different defects, so a citation of it says nothing about a shared defect.
CODE_SUFFIXES = frozenset(
    {
        ".py", ".pyx", ".ipynb", ".js", ".mjs", ".cjs", ".ts", ".jsx", ".tsx", ".sh", ".bash", ".zsh", ".java", ".kt", ".scala",
        ".c", ".h", ".cc", ".cpp", ".hpp", ".cs", ".go", ".rs", ".rb", ".php", ".pl", ".lua", ".r", ".jl", ".m", ".swift", ".sql",
    }
)  # fmt: skip

_CODE_LOCATION = re.compile(r"(::[\w.\[\]-]+|:\d+(-\d+)?(,\d+(-\d+)?)*|#L?\d+(-L?\d+)?)+$")


@dataclass(slots=True)
class TaskAuditSummary:
    """Every list holds task ids in sorted order."""

    judgments: dict[str, int] = field(default_factory=dict)  # every record, also the ones that are not counted
    audited: list[str] = field(default_factory=list)  # the tasks that count
    not_counted: dict[str, list[str]] = field(default_factory=dict)  # reason -> tasks
    with_major: list[str] = field(default_factory=list)  # audited tasks with one or more major findings
    with_major_not_shared: list[str] = field(default_factory=list)  # ... with one or more major findings that are not shared
    shared_threshold: float = DEFAULT_SHARED_THRESHOLD
    shared_min_tasks: int = SHARED_MIN_TASKS
    shared_files: dict[str, list[str]] = field(default_factory=dict)  # code file -> audited tasks whose major findings cite it
    shared_findings: dict[str, list[str]] = field(default_factory=dict)  # task -> ids of its major findings that are shared

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
            "tasks_with_major_not_shared": {"count": len(self.with_major_not_shared), "task_ids": list(self.with_major_not_shared)},
            "shared": {
                "threshold": self.shared_threshold,
                "min_tasks": self.shared_min_tasks,
                "files": [
                    {"path": path, "tasks": len(task_ids), "share": round(len(task_ids) / len(self.audited), 4)}
                    for path, task_ids in self.shared_files.items()
                ],
                "findings": {task_id: list(finding_ids) for task_id, finding_ids in self.shared_findings.items()},
            },
        }


def major_findings(record: TaskAuditRecord) -> list[TaskAuditFinding]:
    return [finding for finding in record.findings if finding.severity >= MAJOR_SEVERITY]


def cited_code_files(finding: TaskAuditFinding) -> set[str]:
    """Return the code files that the evidence of ``finding`` cites, without line numbers.

    An evidence path is written by the auditor. It can be a clean path, a path with a location
    (``eval.py:39``, ``eval.py::check``), a path inside a phrase, or a label that is no path at all.
    """
    files: set[str] = set()
    for evidence in finding.evidence:
        for word in evidence.path.split():
            word = _CODE_LOCATION.sub("", word.lstrip("`'\"([<").rstrip("`'\")]>.,;:"))
            if posixpath.splitext(word)[1].lower() in CODE_SUFFIXES:
                files.add(posixpath.normpath(word))
    return files


def same_file_names(paths: Iterable[str]) -> dict[str, str]:
    """Map every path to one name per file.

    Auditors cite one file as an absolute path and as a path relative to some folder. A shorter
    path gets the name of the one longer path that ends with it. With two such longer paths
    the shorter one is ambiguous and keeps its own name.
    """
    names: dict[str, str] = {}
    longest_first = sorted(set(paths), key=lambda path: (-len(path), path))
    for path in longest_first:
        ends_with_path = {names[longer] for longer in names if longer.endswith("/" + path)} if not path.startswith("/") else set()
        names[path] = ends_with_path.pop() if len(ends_with_path) == 1 else path
    return names


def summarize_task_audits(
    records: Iterable[TaskAuditRecord],
    shared_threshold: float = DEFAULT_SHARED_THRESHOLD,
    shared_min_tasks: int = SHARED_MIN_TASKS,
) -> TaskAuditSummary:
    if shared_threshold <= 0:
        raise ValueError("shared_threshold must be greater than 0 (a value above 1 marks no finding as shared)")
    summary = TaskAuditSummary(shared_threshold=shared_threshold, shared_min_tasks=shared_min_tasks)
    majors: dict[str, list[tuple[TaskAuditFinding, set[str]]]] = {}  # audited task -> its major findings and the code files each cites
    for record in sorted(records, key=lambda item: item.task_id):
        summary.judgments[record.overall_judgment] = summary.judgments.get(record.overall_judgment, 0) + 1
        reason = not_counted_reason(record.overall_judgment, record.summary, record.instruction_source)
        if reason is not None:
            summary.not_counted.setdefault(reason, []).append(record.task_id)
            continue
        summary.audited.append(record.task_id)
        task_majors = [(finding, cited_code_files(finding)) for finding in major_findings(record)]
        if task_majors:
            summary.with_major.append(record.task_id)
            majors[record.task_id] = task_majors

    names = same_file_names(path for task_majors in majors.values() for _, files in task_majors for path in files)
    citing_tasks: dict[str, list[str]] = {}
    for task_id, task_majors in majors.items():
        for name in sorted({names[path] for _, files in task_majors for path in files}):
            citing_tasks.setdefault(name, []).append(task_id)
    shared = {
        name: task_ids
        for name, task_ids in citing_tasks.items()
        if len(task_ids) >= shared_min_tasks and len(task_ids) / len(summary.audited) >= shared_threshold
    }
    summary.shared_files = dict(sorted(shared.items(), key=lambda item: (-len(item[1]), item[0])))

    for task_id, task_majors in majors.items():
        shared_ids = [finding.finding_id for finding, files in task_majors if any(names[path] in shared for path in files)]
        if shared_ids:
            summary.shared_findings[task_id] = shared_ids
        if len(shared_ids) < len(task_majors):
            summary.with_major_not_shared.append(task_id)
    return summary
