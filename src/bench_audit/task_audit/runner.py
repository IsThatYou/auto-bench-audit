from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from ..agent_cli import AgentCliError, build_agent_request, invoke_structured, set_aside_failed_log
from ..audit_models import (
    AGENT_ERROR,
    FAILED_MARK,
    TaskAuditRecord,
    is_failed_call,
    is_missing_inputs,
    parse_agent_json_object,
    record_is_done,
    record_is_missing_inputs,
    task_audit_record_json_schema,
)
from ..audit_protocol import (
    build_agent_add_dirs,
    load_collection_state,
    load_general_rubric,
    resolve_output_root,
    resolve_repo_path,
    task_audit_dir,
)
from ..config import BenchmarkConfig
from ..evidence_collection import TaskConfig
from ..progress import ProgressTracker
from ..report import write_audit_report
from ..summary import DEFAULT_SHARED_THRESHOLD
from .context import EvalArtifactPaths, eval_artifact_paths, select_eval_configs, select_task_configs
from .preflight import missing_input_paths, missing_inputs_record
from .prompt import render_static_task_audit_prompt, render_task_audit_prompt

AUDIT_MODES = {"trajectory", "static"}


def resolve_audit_mode(audit_mode: str | None, config: BenchmarkConfig) -> str:
    """Resolve audit mode: explicit value, or auto-detect from benchmark type."""
    if audit_mode is not None:
        if audit_mode not in AUDIT_MODES:
            raise ValueError(f"unsupported audit mode: {audit_mode}")
        return audit_mode
    if config.is_static:
        return "static"
    return "trajectory"


def audit_tasks(
    config: BenchmarkConfig,
    general_rubric_path: str | Path | None = None,
    rubric_text_path: str | Path | None = None,
    task_ids: list[str] | None = None,
    include_all: bool = False,
    force: bool = False,
    max_workers: int = 1,
    timeout: int = 800,
    eval_strategy: str = "mixed",
    audit_mode: str | None = None,
    sample_n: int | None = None,
    sample_seed: int = 42,
    retry_errors: int = 1,
    check_inputs: bool = True,
    shared_threshold: float = DEFAULT_SHARED_THRESHOLD,
) -> dict[str, Any]:
    """Audit the selected tasks, one agent call per task.

    A task whose record is an ``agent_error`` is not finished: the agent call
    failed before it gave a valid answer. Such a task is tried again, up to
    ``retry_errors`` more times in this run, and again on the next run.

    With ``check_inputs``, a task gets no agent call when a file that its task
    config names is not on disk. Its record says ``insufficient_evidence`` and
    lists the missing paths. The next run checks the files again.

    ``shared_threshold`` goes to the report: see ``bench_audit.summary``.
    """
    mode = resolve_audit_mode(audit_mode, config)
    attempts = 1 + max(0, retry_errors)
    output_root = resolve_output_root(config)
    general_rubric = load_general_rubric(general_rubric_path)
    phase = general_rubric.phase("task_audit")
    if rubric_text_path:
        rubric_txt_path = Path(rubric_text_path).resolve()
    else:
        rubric_txt_path = resolve_repo_path(phase.rubric).with_suffix(".txt")
    if not rubric_txt_path.exists():
        raise FileNotFoundError(f"rubric text file not found: {rubric_txt_path}")
    rubric_text = rubric_txt_path.read_text()
    collection = load_collection_state(output_root)

    # A static audit has no pass/fail results, so there is nothing to filter on.
    # The rubric's task_filter (failed_or_mixed) applies to trajectory audits only.
    task_filter = "all" if mode == "static" else (phase.task_filter or "failed_or_mixed")
    selected_tasks = select_task_configs(
        list(collection.task_configs.values()),
        task_filter=task_filter,
        task_ids=task_ids,
        include_all=include_all,
        sample_n=sample_n,
        sample_seed=sample_seed,
    )

    output_dir = task_audit_dir(output_root, general_rubric, audit_mode=mode)
    output_dir.mkdir(parents=True, exist_ok=True)
    conversation_dir = output_dir / "conversations"
    conversation_dir.mkdir(parents=True, exist_ok=True)
    add_dirs = build_agent_add_dirs(config, output_root)
    request = build_agent_request(
        config.agent_cli,
        config.model,
        add_dirs=add_dirs,
        api_key_env=config.api_key_env,
    )

    progress = ProgressTracker(
        len(selected_tasks),
        label="Per-task audit",
        info={
            "benchmark": config.benchmark_name,
            "model": config.model or "(default)",
            "agent": config.agent_cli,
            "mode": mode,
            "rubric": str(rubric_txt_path),
            "filter": task_filter,
            "eval_strategy": eval_strategy if mode == "trajectory" else "N/A",
            "sample": f"{sample_n} (seed={sample_seed})" if sample_n else "all",
            "output": str(output_dir),
            "workers": str(max_workers),
            "timeout": f"{timeout}s",
            "retry_errors": str(attempts - 1),
            "check_inputs": str(check_inputs),
            "force": str(force),
        },
    )

    # Separate finished (skipped) tasks from tasks that need work.
    # A record that is an agent_error is not finished, so its task runs again.
    # A task that was not audited because its files were missing is checked again: the files may be there now.
    to_run: list[tuple[Any, Path]] = []  # (task_config, target_path)
    written_paths: list[str] = []
    skipped_tasks: list[str] = []
    failed_tasks: list[str] = []
    not_auditable_tasks: list[str] = []
    for task_config in selected_tasks:
        target_path = output_dir / f"{task_config.task_id}.json"
        if not force and record_is_done(target_path) and not record_is_missing_inputs(target_path):
            skipped_tasks.append(task_config.task_id)
            progress.skip(task_config.task_id)
        else:
            to_run.append((task_config, target_path))

    def _selected_evals(task_config: Any) -> list[Any]:
        eval_configs = collection.eval_configs.get(task_config.task_id, [])
        return select_eval_configs(task_config, eval_configs, eval_strategy=eval_strategy)

    def _missing_inputs(task_config: Any) -> TaskAuditRecord | None:
        """The record of a task that cannot be audited here, or None when every file it names is on disk."""
        missing = missing_input_paths(task_config, roots=add_dirs) if check_inputs else []
        if not missing:
            return None
        progress.note(f"{task_config.task_id} — not audited, {len(missing)} file(s) that its task config names are missing: {_one_line('; '.join(missing))}")
        if mode == "static":
            return missing_inputs_record(task_config, missing, task_status="unscored", rubric_path=str(rubric_txt_path))
        return missing_inputs_record(
            task_config,
            missing,
            task_status=task_config.status,
            selected_eval_ids=[ec.eval_id for ec in _selected_evals(task_config)],
            rubric_path=str(rubric_txt_path),
        )

    def _audit_once(task_config: Any, conversation_log_path: Path) -> TaskAuditRecord:
        if mode == "static":
            return _run_single_static_task(
                request, task_config, rubric_text, str(rubric_txt_path), config,
                timeout=timeout, agent_cli=request.agent_cli,
                long_text_dir=output_dir / "long_inputs" / task_config.task_id,
                conversation_log_path=conversation_log_path,
            )
        selected_raw = _selected_evals(task_config)
        selected_paths = [eval_artifact_paths(ec) for ec in selected_raw]
        selected_ids = [ec.eval_id for ec in selected_raw]
        return _run_single_task(
            request,
            task_config,
            selected_paths,
            selected_ids,
            rubric_text,
            str(rubric_txt_path),
            timeout=timeout,
            agent_cli=request.agent_cli,
            conversation_log_path=conversation_log_path,
        )

    def _process(task_config: Any, target_path: Path) -> tuple[str, Path, TaskAuditRecord]:
        task_id = task_config.task_id
        progress.start(task_id)
        conversation_log_path = conversation_dir / f"{_sanitize_task_id(task_id)}.json"
        if target_path.exists() and not record_is_done(target_path):
            set_aside_failed_log(conversation_log_path)  # the log of the failed attempt from an earlier run
        not_auditable = _missing_inputs(task_config)
        if not_auditable is not None:
            return task_id, target_path, not_auditable
        record = _audit_once(task_config, conversation_log_path)
        for attempt in range(2, attempts + 1):
            if not is_failed_call(record.overall_judgment, record.summary):
                break
            progress.note(f"{task_id} — failed, starting attempt {attempt} of {attempts}: {_one_line(record.summary)}")
            set_aside_failed_log(conversation_log_path)
            record = _audit_once(task_config, conversation_log_path)
        return task_id, target_path, record

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_process, tc, tp): tc.task_id for tc, tp in to_run}
        for future in as_completed(futures):
            task_id, target_path, record = future.result()
            is_error = is_failed_call(record.overall_judgment, record.summary)
            record.save_json(target_path)
            written_paths.append(str(target_path))
            if is_error:
                failed_tasks.append(task_id)
            if is_missing_inputs(record.overall_judgment, record.summary):
                not_auditable_tasks.append(task_id)
            progress.done(task_id, error=is_error)

    progress.finish()
    failed_tasks.sort()
    not_auditable_tasks.sort()
    if not_auditable_tasks:
        shown = ", ".join(not_auditable_tasks[:10]) + (f" (+{len(not_auditable_tasks) - 10} more)" if len(not_auditable_tasks) > 10 else "")
        print(
            f"[bench-audit] {len(not_auditable_tasks)} of {len(to_run)} tasks were not audited, because files that their task config names "
            f"are not on this machine: {shown}\n"
            f"[bench-audit] Their records say insufficient_evidence and list the missing paths. No agent ran for them, and the report does "
            f"not count them. Put the files in place and run the same command again.",
            file=sys.stderr,
        )
    if failed_tasks:
        shown = ", ".join(failed_tasks[:10]) + (f" (+{len(failed_tasks) - 10} more)" if len(failed_tasks) > 10 else "")
        print(
            f"[bench-audit] {len(failed_tasks)} of {len(to_run)} task audits failed after {attempts} attempt(s): {shown}\n"
            f"[bench-audit] Their records say agent_error. Run the same command again to try only these tasks again.",
            file=sys.stderr,
        )

    report = write_audit_report(output_root, general_rubric, audit_mode=mode, rubric_path=rubric_txt_path, shared_threshold=shared_threshold)
    return {
        "output_root": str(output_root),
        "task_audit_dir": str(output_dir),
        "report_path": report["report_path"],
        "summary_path": report["summary_path"],
        "summary": report["summary"],
        "rubric_path": str(rubric_txt_path),
        "audit_mode": mode,
        "selected_tasks": [task.task_id for task in selected_tasks],
        "written_task_audits": written_paths,
        "skipped_tasks": skipped_tasks,
        "failed_tasks": failed_tasks,
        "not_auditable_tasks": not_auditable_tasks,
    }


def _run_single_task(
    request: Any,
    task_config: TaskConfig,
    selected_evals: list[EvalArtifactPaths],
    selected_eval_ids: list[str],
    rubric_text: str,
    rubric_path: str,
    timeout: int | None = None,
    agent_cli: str = "claude",
    conversation_log_path: Path | None = None,
) -> TaskAuditRecord:
    try:
        prompt = render_task_audit_prompt(task_config, selected_evals, rubric_text, agent_cli=agent_cli)
        response = invoke_structured(
            request,
            prompt,
            task_audit_record_json_schema(),
            timeout=timeout,
            conversation_log_path=conversation_log_path,
        )
        payload = parse_agent_json_object(response)
        payload["task_id"] = task_config.task_id
        payload["benchmark_name"] = task_config.benchmark_name
        payload["task_status"] = task_config.status
        payload["selected_eval_ids"] = list(selected_eval_ids)
        payload["rubric_path"] = rubric_path
        payload["audit_trajectory_path"] = str(conversation_log_path) if conversation_log_path else None
        payload["instruction_source"] = task_config.instruction_source
        return TaskAuditRecord.from_dict(payload)
    except (AgentCliError, ValueError, TypeError, KeyError) as exc:
        return TaskAuditRecord(
            task_id=task_config.task_id,
            benchmark_name=task_config.benchmark_name,
            task_status=task_config.status,
            selected_eval_ids=list(selected_eval_ids),
            rubric_path=rubric_path,
            audit_trajectory_path=str(conversation_log_path) if conversation_log_path else None,
            overall_judgment=AGENT_ERROR,
            summary=f"Task audit {FAILED_MARK}: {exc}",
            confidence="low",
            findings=[],
            instruction_source=task_config.instruction_source,
        )


def _run_single_static_task(
    request: Any,
    task_config: TaskConfig,
    rubric_text: str,
    rubric_path: str,
    config: BenchmarkConfig,
    timeout: int | None = None,
    agent_cli: str = "claude",
    conversation_log_path: Path | None = None,
    long_text_dir: Path | None = None,
) -> TaskAuditRecord:
    task_status = "unscored"  # static mode ignores eval results
    try:
        prompt = render_static_task_audit_prompt(
            task_config, rubric_text, config, agent_cli=agent_cli, long_text_dir=long_text_dir
        )
        response = invoke_structured(
            request,
            prompt,
            task_audit_record_json_schema(),
            timeout=timeout,
            conversation_log_path=conversation_log_path,
        )
        payload = parse_agent_json_object(response)
        payload["task_id"] = task_config.task_id
        payload["benchmark_name"] = task_config.benchmark_name
        payload["task_status"] = task_status
        payload["selected_eval_ids"] = []
        payload["rubric_path"] = rubric_path
        payload["audit_trajectory_path"] = str(conversation_log_path) if conversation_log_path else None
        payload["instruction_source"] = task_config.instruction_source
        return TaskAuditRecord.from_dict(payload)
    except (AgentCliError, ValueError, TypeError, KeyError) as exc:
        return TaskAuditRecord(
            task_id=task_config.task_id,
            benchmark_name=task_config.benchmark_name,
            task_status=task_status,
            selected_eval_ids=[],
            rubric_path=rubric_path,
            audit_trajectory_path=str(conversation_log_path) if conversation_log_path else None,
            overall_judgment=AGENT_ERROR,
            summary=f"Static task audit {FAILED_MARK}: {exc}",
            confidence="low",
            findings=[],
            instruction_source=task_config.instruction_source,
        )


def _one_line(text: str, limit: int = 200) -> str:
    flat = " ".join(str(text).split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def _sanitize_task_id(task_id: str) -> str:
    sanitized = (
        task_id
        .replace("/", "_")
        .replace("\\", "_")
        .replace(":", "_")
        .replace(" ", "_")
    )
    return sanitized or "task"
