from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .audit_models import MAJOR_SEVERITY, TaskAuditRecord
from .audit_protocol import GeneralRubric, task_audit_dir
from .evidence_collection import ArtifactManifest
from .summary import TaskAuditSummary, major_findings, summarize_task_audits

NOT_COUNTED_LABELS = {
    "failed_agent_call": "failed agent calls",
    "insufficient_evidence": "insufficient evidence",
}


def load_task_audits(output_root: str | Path, general_rubric: GeneralRubric, audit_mode: str = "trajectory") -> dict[str, TaskAuditRecord]:
    """Read every task audit record of one audit run, by task id."""
    task_audits_path = task_audit_dir(Path(output_root).resolve(), general_rubric, audit_mode=audit_mode)
    task_audits: dict[str, TaskAuditRecord] = {}
    if task_audits_path.is_dir():
        for record_path in sorted(task_audits_path.glob("*.json")):
            record = TaskAuditRecord.load_json(record_path)
            task_audits[record.task_id] = record
    return task_audits


def write_audit_report(
    output_root: str | Path,
    general_rubric: GeneralRubric,
    audit_mode: str = "trajectory",
    rubric_path: str | Path | None = None,
) -> dict[str, Any]:
    """Write the report (Markdown) and the same counts as JSON. Return both paths and the counts."""
    root = Path(output_root).resolve()
    suffix = "" if audit_mode == "trajectory" else f"_{audit_mode}"
    report_path = root / f"audit_report{suffix}.md"
    summary_path = root / f"audit_summary{suffix}.json"
    manifest = ArtifactManifest.load_yaml(root / "artifact_manifest.yaml")
    task_audits = load_task_audits(root, general_rubric, audit_mode)
    summary = summarize_task_audits(task_audits.values())
    report_path.write_text(_render(manifest, task_audits, summary, audit_mode, rubric_path))
    data = {
        "benchmark_name": manifest.benchmark_name,
        "audit_mode": audit_mode,
        "tasks_discovered": len(manifest.tasks),
        **summary.to_dict(),
    }
    summary_path.write_text(json.dumps(data, indent=2) + "\n")
    return {"report_path": str(report_path), "summary_path": str(summary_path), "summary": data}


def render_audit_report(
    output_root: str | Path,
    general_rubric: GeneralRubric,
    audit_mode: str = "trajectory",
    rubric_path: str | Path | None = None,
) -> str:
    root = Path(output_root).resolve()
    manifest = ArtifactManifest.load_yaml(root / "artifact_manifest.yaml")
    task_audits = load_task_audits(root, general_rubric, audit_mode)
    return _render(manifest, task_audits, summarize_task_audits(task_audits.values()), audit_mode, rubric_path)


def _render(
    manifest: ArtifactManifest,
    task_audits: dict[str, TaskAuditRecord],
    summary: TaskAuditSummary,
    audit_mode: str,
    rubric_path: str | Path | None,
) -> str:
    # Detect audit mode from task records (all unscored = static mode).
    if task_audits:
        all_unscored = all(r.task_status == "unscored" for r in task_audits.values())
        audit_mode = "static" if all_unscored else "trajectory"
    rubric_values = _unique_nonempty_paths(
        [str(rubric_path)] if rubric_path is not None else [record.rubric_path for record in task_audits.values()]
    )
    rubric_summary = ", ".join(f"`{value}`" for value in rubric_values) if rubric_values else "`unknown`"

    task_audit_rows: list[str] = []
    for task in manifest.tasks:
        audit_record = task_audits.get(task.task_id)
        if audit_record is None:
            cells = [task.task_id, "not_run", "-", "0", "0"]
        else:
            cells = [
                task.task_id,
                audit_record.overall_judgment,
                "yes" if summary.is_counted(task.task_id) else "no",
                str(len(audit_record.findings)),
                str(len(major_findings(audit_record))),
            ]
        task_audit_rows.append("| " + " | ".join(cells) + " |")

    audited = len(summary.audited)
    return "\n".join(
        [
            "# Audit Report",
            "",
            f"- Benchmark: `{manifest.benchmark_name}`",
            f"- Benchmark type: `{manifest.benchmark_type}`",
            f"- Job type: `{manifest.job_type}`",
            f"- Audit mode: `{audit_mode}`",
            f"- Rubric: {rubric_summary}",
            f"- Tasks discovered: `{len(manifest.tasks)}`",
            f"- Task audit records: `{summary.records}`",
            f"- Tasks audited: `{audited}`",
            f"- Not counted: `{summary.not_counted_total}`{_not_counted_detail(summary)}",
            f"- Tasks with a major finding (severity {MAJOR_SEVERITY} or higher): `{len(summary.with_major)}` of `{audited}` audited",
            "",
            "A record is not counted when the agent call failed, or when its judgment is `insufficient_evidence`. "
            "Such a record is not the result of an audit, so the counts above leave it out.",
            "",
            "## Task Audit Summary",
            "",
            *(f"- `{key}`: `{value}`" for key, value in sorted(summary.judgments.items())),
            "",
            "| Task | Audit judgment | Counted | Findings | Major findings |",
            "| --- | --- | --- | --- | --- |",
            *(task_audit_rows or ["| none | none | - | 0 | 0 |"]),
            "",
        ]
    )


def _not_counted_detail(summary: TaskAuditSummary) -> str:
    parts = [f"{NOT_COUNTED_LABELS.get(reason, reason)}: `{len(task_ids)}`" for reason, task_ids in sorted(summary.not_counted.items())]
    return f" ({', '.join(parts)})" if parts else ""


def _unique_nonempty_paths(values: Iterable[str | None]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        ordered.append(text)
    return ordered
