"""Check, before a task audit starts, that the files the task config names are on disk.

An auditor that cannot open a task's input files does one of two wrong things: it files the
missing file as a defect of the task, or it audits the task without its inputs. Both give a
record that looks like a result. So a task with a missing file gets no agent call. Its record
says ``insufficient_evidence`` and lists the missing paths, and the counts leave it out.

The check must not block a task that is complete, so it reads paths with care:

- A path field (``tests_ref`` and the like) holds one path, or several joined with ``;`` or ``,``.
  A file name can hold these marks too, so the whole value is tried first.
- ``audit_notes`` is prose. Only paths under the folders of this audit run are checked there
  (``roots``), because the notes also name paths that are not on this machine by design, such as
  a path inside the task's container. A path in prose can end with a mark or a code location
  (``file.py:39``, ``file.py::name``), or stop at a space inside a file name.
- A value with a placeholder (``{id}``, ``<name>``, ``*``) or a URL is not checked.
"""

from __future__ import annotations

import os
import re
from collections.abc import Sequence
from pathlib import Path

from ..audit_models import INSUFFICIENT_EVIDENCE, MISSING_INPUTS_MARK, TaskAuditRecord
from ..evidence_collection import TaskConfig

PATH_FIELDS = ("task_bundle_path", "instruction_path", "task_toml_path", "environment_ref", "solution_ref", "tests_ref")

_PLACEHOLDER_CHARS = frozenset("{}<>*$")
_LIST_SEPARATOR = re.compile(r"\s*[;,]\s*")
_CODE_LOCATION = re.compile(r"(::[\w.\[\]-]+|:\d+(-\d+)?|#L?\d+(-L?\d+)?)+$")
_PROSE_PATH_END = r"[^\s'\"`,;)\]>]*"


def missing_input_paths(task_config: TaskConfig, roots: Sequence[str | Path] = ()) -> list[str]:
    """Return the paths that ``task_config`` names and that are not on disk, in the order they appear."""
    missing: list[str] = []

    def check(path: str, *, from_prose: bool) -> None:
        if not _is_checkable(path) or path in missing:
            return
        if not _exists(path, roots, from_prose=from_prose):
            missing.append(path)

    for name in PATH_FIELDS:
        value = getattr(task_config, name, None)
        if value:
            for path in _field_paths(str(value).strip(), roots):
                check(path, from_prose=False)
    for path in _prose_paths(task_config.audit_notes or "", roots):
        check(path, from_prose=True)
    return missing


def missing_inputs_record(
    task_config: TaskConfig,
    missing: Sequence[str],
    *,
    task_status: str,
    selected_eval_ids: Sequence[str] = (),
    rubric_path: str | None = None,
) -> TaskAuditRecord:
    """The record of a task that was not audited because ``missing`` are not on disk."""
    return TaskAuditRecord(
        task_id=task_config.task_id,
        benchmark_name=task_config.benchmark_name,
        task_status=task_status,
        selected_eval_ids=list(selected_eval_ids),
        rubric_path=rubric_path,
        audit_trajectory_path=None,
        overall_judgment=INSUFFICIENT_EVIDENCE,
        summary=(
            f"Not audited: {MISSING_INPUTS_MARK} ({len(missing)}): {'; '.join(missing)}. "
            "No agent ran. Put the files in place and run the same command again, and the task is audited then."
        ),
        confidence="high",
        findings=[],
        instruction_source=task_config.instruction_source,
    )


def _is_checkable(path: str) -> bool:
    if not path or "://" in path or _PLACEHOLDER_CHARS.intersection(path):
        return False
    # A relative value with no folder part ("inline", "n/a") is a remark and not a path.
    return os.path.isabs(os.path.expanduser(path)) or "/" in path


def _field_paths(value: str, roots: Sequence[str | Path]) -> list[str]:
    if not _is_checkable(value) or _exists(value, roots, from_prose=False):
        return [value]
    parts = [part for part in _LIST_SEPARATOR.split(value) if part]
    if len(parts) > 1 and all(_is_checkable(part) for part in parts):
        return parts
    return [value]


def _prose_paths(text: str, roots: Sequence[str | Path]) -> list[str]:
    found: list[tuple[int, str]] = []
    for root in {str(root).rstrip("/") for root in roots if str(root).strip("/")}:
        for match in re.finditer(re.escape(root) + "/" + _PROSE_PATH_END, text):
            found.append((match.start(), match.group(0).rstrip(".:")))
    return [path for _, path in sorted(found)]


def _exists(path: str, roots: Sequence[str | Path], *, from_prose: bool) -> bool:
    expanded = os.path.expanduser(path)
    candidates = [expanded]
    if not os.path.isabs(expanded):
        candidates += [os.path.join(str(root), expanded) for root in roots]
    for candidate in candidates:
        without_location = _CODE_LOCATION.sub("", candidate)
        if os.path.exists(candidate) or os.path.exists(without_location):
            return True
        if from_prose and _is_start_of_a_name(candidate):
            return True
    return False


def _is_start_of_a_name(path: str) -> bool:
    """True when ``path`` stops inside a file name that has a space or a comma in it."""
    folder, name = os.path.split(path)
    try:
        return bool(name) and any(entry.startswith(name) for entry in os.listdir(folder))
    except OSError:
        return False
