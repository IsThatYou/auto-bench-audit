"""Failed agent calls: they are tried again, shown, and not counted as finished.

Every test replaces the agent with a stand-in, so no test starts an agent or costs money.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from bench_audit import agent_cli, cli, quick_audit
from bench_audit.agent_cli import EXIT_AGENT_ERRORS, AgentCliError
from bench_audit.audit_models import BENCHMARK_AUDIT_CATEGORIES, record_is_done
from bench_audit.benchmark_audit import audit_benchmark
from bench_audit.benchmark_audit import runner as benchmark_runner
from bench_audit.config import load_benchmark_config
from bench_audit.evidence_collection import collect_evidence, collection_is_done
from bench_audit.evidence_collection import runner as collection_runner
from bench_audit.paths import resolve_output_root
from bench_audit.task_audit import audit_tasks
from bench_audit.task_audit import runner as task_runner

from .conftest import write_collection, write_config

TASK_IDS = ["task-a", "task-b", "task-c"]
GOOD_TASK_ANSWER = json.dumps({"overall_judgment": "audit_complete", "summary": "No problem found.", "confidence": "high", "findings": []})
GOOD_BENCHMARK_ANSWER = json.dumps(
    {
        "overall_judgment": "audit_complete",
        "summary": "Fine.",
        "confidence": "high",
        "category_verdicts": {category: {"severity": 0, "rationale": "ok", "findings": []} for category in BENCHMARK_AUDIT_CATEGORIES},
    }
)


class StandInAgent:
    """Answers like the agent. `failures` is how many calls fail before the answers are good."""

    def __init__(self, answer: str, failures: int = 0, write_log: bool = False) -> None:
        self.answer = answer
        self.failures = failures
        self.write_log = write_log
        self.calls = 0
        self.timeouts: list[int | None] = []

    def __call__(self, request, prompt, json_schema=None, timeout=None, conversation_log_path=None):
        self.calls += 1
        self.timeouts.append(timeout)
        if self.write_log and conversation_log_path is not None:
            conversation_log_path.parent.mkdir(parents=True, exist_ok=True)
            conversation_log_path.write_text(f"log of call {self.calls}")
        if self.calls <= self.failures:
            if self.write_log:
                return "this is not JSON"  # an answer that cannot be read
            raise AgentCliError("claude timed out after 800s")
        return self.answer


@pytest.fixture
def config(tmp_path: Path):
    return load_benchmark_config(write_config(tmp_path / "bench"))


def judgments(output_root: Path) -> dict[str, str]:
    return {p.stem: json.loads(p.read_text())["overall_judgment"] for p in sorted((output_root / "task_audits_static").glob("*.json"))}


# --- record_is_done ---------------------------------------------------------------------------


def test_only_a_valid_record_that_is_not_an_agent_error_is_done(tmp_path: Path):
    path = tmp_path / "record.json"
    assert not record_is_done(path)  # missing
    path.write_text("{ not json")
    assert not record_is_done(path)
    path.write_text(json.dumps({"overall_judgment": "agent_error"}))
    assert not record_is_done(path)
    path.write_text(json.dumps({"overall_judgment": "insufficient_evidence"}))
    assert record_is_done(path)


# --- task audit -------------------------------------------------------------------------------


def test_failed_task_audit_is_tried_again_in_the_same_run(config, monkeypatch: pytest.MonkeyPatch):
    output_root = write_collection(config, TASK_IDS)
    agent = StandInAgent(GOOD_TASK_ANSWER, failures=1, write_log=True)
    monkeypatch.setattr(task_runner, "invoke_structured", agent)

    result = audit_tasks(config)

    assert agent.calls == 4  # task-a fails once, then every call is good
    assert result["failed_tasks"] == []
    assert set(judgments(output_root).values()) == {"audit_complete"}
    conversations = output_root / "task_audits_static" / "conversations"
    assert (conversations / "failed" / "task-a.attempt1.json").read_text() == "log of call 1"
    assert (conversations / "task-a.json").read_text() == "log of call 2"


def test_task_that_keeps_failing_is_reported_and_runs_again_next_time(config, monkeypatch: pytest.MonkeyPatch, capsys):
    output_root = write_collection(config, TASK_IDS)
    broken = StandInAgent(GOOD_TASK_ANSWER, failures=99)
    monkeypatch.setattr(task_runner, "invoke_structured", broken)

    first = audit_tasks(config, retry_errors=1)
    assert broken.calls == 6  # 3 tasks, 2 attempts each
    assert first["failed_tasks"] == TASK_IDS
    assert set(judgments(output_root).values()) == {"agent_error"}
    err = capsys.readouterr().err
    assert "3 of 3 task audits failed after 2 attempt(s): task-a, task-b, task-c" in err
    assert "Run the same command again" in err

    fixed = StandInAgent(GOOD_TASK_ANSWER)
    monkeypatch.setattr(task_runner, "invoke_structured", fixed)
    second = audit_tasks(config)
    assert fixed.calls == 3 and second["skipped_tasks"] == [] and second["failed_tasks"] == []
    assert set(judgments(output_root).values()) == {"audit_complete"}

    third = audit_tasks(config)
    assert fixed.calls == 3 and third["skipped_tasks"] == TASK_IDS  # finished records are not run again


def test_retry_errors_zero_means_one_attempt(config, monkeypatch: pytest.MonkeyPatch):
    write_collection(config, TASK_IDS)
    broken = StandInAgent(GOOD_TASK_ANSWER, failures=99)
    monkeypatch.setattr(task_runner, "invoke_structured", broken)
    assert audit_tasks(config, retry_errors=0)["failed_tasks"] == TASK_IDS
    assert broken.calls == 3


def test_only_the_failed_task_runs_again(config, monkeypatch: pytest.MonkeyPatch):
    output_root = write_collection(config, TASK_IDS)
    monkeypatch.setattr(task_runner, "invoke_structured", StandInAgent(GOOD_TASK_ANSWER))
    audit_tasks(config)
    record_path = output_root / "task_audits_static" / "task-b.json"
    record = json.loads(record_path.read_text())
    record_path.write_text(json.dumps({**record, "overall_judgment": "agent_error"}))

    agent = StandInAgent(GOOD_TASK_ANSWER)
    monkeypatch.setattr(task_runner, "invoke_structured", agent)
    result = audit_tasks(config)
    assert agent.calls == 1 and result["skipped_tasks"] == ["task-a", "task-c"]
    assert judgments(output_root)["task-b"] == "audit_complete"


def test_audit_tasks_exit_code_tells_that_agent_calls_failed(tmp_path: Path, config, monkeypatch: pytest.MonkeyPatch):
    write_collection(config, TASK_IDS)
    argv = ["audit-tasks", "--config", str(tmp_path / "bench" / "config.yaml"), "--retry-errors", "0"]

    monkeypatch.setattr(task_runner, "invoke_structured", StandInAgent(GOOD_TASK_ANSWER, failures=1))
    assert cli.main(argv) == EXIT_AGENT_ERRORS

    monkeypatch.setattr(task_runner, "invoke_structured", StandInAgent(GOOD_TASK_ANSWER))
    assert cli.main(argv) == 0


# --- benchmark audit --------------------------------------------------------------------------


def test_failed_benchmark_audit_is_tried_again(config, monkeypatch: pytest.MonkeyPatch, capsys):
    output_root = resolve_output_root(config)
    broken = StandInAgent(GOOD_BENCHMARK_ANSWER, failures=99)
    monkeypatch.setattr(benchmark_runner, "invoke_structured", broken)
    first = audit_benchmark(config, retry_errors=1)
    assert broken.calls == 2 and first["failed"] is True and first["overall_judgment"] == "agent_error"
    assert not record_is_done(output_root / "benchmark_audit.json")
    assert "Run the same command again" in capsys.readouterr().err

    flaky = StandInAgent(GOOD_BENCHMARK_ANSWER, failures=1)
    monkeypatch.setattr(benchmark_runner, "invoke_structured", flaky)
    second = audit_benchmark(config)  # not skipped: the record on disk is an agent_error
    assert flaky.calls == 2 and second["failed"] is False and second["skipped"] is False
    assert record_is_done(output_root / "benchmark_audit.json")

    third = audit_benchmark(config)
    assert flaky.calls == 2 and third["skipped"] is True


def test_run_goes_on_after_a_failed_benchmark_audit(tmp_path: Path, config, monkeypatch: pytest.MonkeyPatch):
    write_collection(config, TASK_IDS)
    monkeypatch.setattr(benchmark_runner, "invoke_structured", StandInAgent(GOOD_BENCHMARK_ANSWER, failures=99))
    tasks = StandInAgent(GOOD_TASK_ANSWER)
    monkeypatch.setattr(task_runner, "invoke_structured", tasks)

    assert cli.main(["run", "--config", str(tmp_path / "bench" / "config.yaml")]) == EXIT_AGENT_ERRORS
    assert tasks.calls == 3  # the collection was finished, so it was skipped, and the task audit still ran


# --- evidence collection ----------------------------------------------------------------------


class StandInCollector(StandInAgent):
    """Writes the collection files like the agent does. `skip_configs` leaves task configs out."""

    def __init__(self, config, failures: int = 0, skip_configs: tuple[str, ...] = ()) -> None:
        super().__init__("done", failures)
        self.config = config
        self.skip_configs = skip_configs

    def __call__(self, request, prompt, timeout=None, conversation_log_path=None):
        answer = super().__call__(request, prompt, timeout=timeout, conversation_log_path=conversation_log_path)
        write_collection(self.config, TASK_IDS, skip_configs=self.skip_configs, report=False)
        return answer


def test_finished_collection_is_not_collected_again(config, monkeypatch: pytest.MonkeyPatch, capsys):
    agent = StandInCollector(config)
    monkeypatch.setattr(collection_runner, "invoke_text", agent)

    first = collect_evidence(config)
    assert agent.calls == 1 and first["skipped"] is False and agent.timeouts == [3600]
    assert collection_is_done(resolve_output_root(config))

    second = collect_evidence(config)
    assert agent.calls == 1 and second["skipped"] is True
    assert "skipping" in capsys.readouterr().out

    collect_evidence(config, force=True, timeout=120)
    assert agent.calls == 2 and agent.timeouts[-1] == 120


def test_failed_collection_is_tried_again(config, monkeypatch: pytest.MonkeyPatch):
    agent = StandInCollector(config, failures=1)
    monkeypatch.setattr(collection_runner, "invoke_text", agent)
    assert collect_evidence(config)["skipped"] is False
    assert agent.calls == 2


def test_collection_with_missing_task_configs_names_all_of_them(config, monkeypatch: pytest.MonkeyPatch, capsys):
    agent = StandInCollector(config, skip_configs=("task-a", "task-c"))
    monkeypatch.setattr(collection_runner, "invoke_text", agent)

    with pytest.raises(FileNotFoundError, match="did not write a task config for 2 of 3 tasks: task-a, task-c"):
        collect_evidence(config, retry_errors=1)
    assert agent.calls == 2
    assert "attempt 2 of 2 failed" in capsys.readouterr().err
    assert not collection_is_done(resolve_output_root(config))  # so the next run collects again


def test_forced_collection_that_fails_does_not_look_finished(config, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(collection_runner, "invoke_text", StandInCollector(config))
    collect_evidence(config)

    monkeypatch.setattr(collection_runner, "invoke_text", StandInCollector(config, failures=99))
    with pytest.raises(AgentCliError):
        collect_evidence(config, force=True, retry_errors=0)
    assert not collection_is_done(resolve_output_root(config))


# --- the agent subprocess at its time limit ---------------------------------------------------

RESULT_LINE = json.dumps({"type": "result", "result": "the answer"})
PARTIAL_LINE = json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "still working"}]}})


def slow_process(first_line: str) -> list[str]:
    """A process that prints one line and then hangs, like an agent that does not exit."""
    return [sys.executable, "-c", f"import time; print({first_line!r}, flush=True); time.sleep(30)"]


def test_timeout_before_the_final_answer_is_an_error_and_keeps_the_partial_log(tmp_path: Path):
    log = tmp_path / "conversation.json"
    with pytest.raises(AgentCliError, match="timed out after 1s before it gave a final answer"):
        agent_cli._run_command("claude", slow_process(PARTIAL_LINE), timeout=1, conversation_log_path=log)
    assert "still working" in log.read_text()


def test_timeout_after_the_final_answer_uses_the_answer(tmp_path: Path):
    stdout = agent_cli._run_command("claude", slow_process(RESULT_LINE), timeout=1)
    assert agent_cli._extract_text_output("claude", stdout) == "the answer"


def test_failed_log_is_set_aside_under_a_new_name_each_time(tmp_path: Path):
    log = tmp_path / "conversations" / "task.json"
    log.parent.mkdir()
    assert agent_cli.set_aside_failed_log(log) is None  # nothing to move
    for number in (1, 2):
        log.write_text(f"attempt {number}")
        assert agent_cli.set_aside_failed_log(log) == log.parent / "failed" / f"task.attempt{number}.json"
    assert not log.exists()


# --- bench-audit audit <url> ------------------------------------------------------------------


def test_quick_audit_reports_failed_parts_with_exit_code_3(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys):
    seen: dict[str, dict] = {}

    def phase(name: str, result: dict):
        def run(config, **kwargs):
            seen[name] = kwargs
            return result

        return run

    monkeypatch.setattr(quick_audit, "require_agent_binary", lambda agent_cli: "/fake/claude")
    monkeypatch.setattr(quick_audit, "clone_repo", lambda url, repo_dir: False)
    monkeypatch.setattr(quick_audit, "audit_benchmark", phase("benchmark", {"failed": True}))
    monkeypatch.setattr(quick_audit, "collect_evidence", phase("collect", {}))
    monkeypatch.setattr(quick_audit, "audit_tasks", phase("tasks", {"failed_tasks": ["t1", "t2"]}))

    argv = ["audit", "https://example.com/org/tiny-bench", "--retry-errors", "2", "--collect-timeout", "900"]
    assert cli.main(argv) == EXIT_AGENT_ERRORS
    assert seen["benchmark"]["retry_errors"] == 2 and seen["tasks"]["retry_errors"] == 2
    assert seen["collect"] == {"force": False, "timeout": 900, "retry_errors": 2}
    err = capsys.readouterr().err
    assert "the benchmark-level audit and 2 task audit(s) failed" in err
    assert "Run the same command again" in err
