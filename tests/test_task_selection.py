from __future__ import annotations

from types import SimpleNamespace

import pytest

from bench_audit import agent_cli
from bench_audit.agent_cli import AgentCliError, build_agent_request
from bench_audit.task_audit.context import select_task_configs


def task(task_id: str, status: str = "unscored", n_passed: int = 0, n_failed: int = 0):
    return SimpleNamespace(task_id=task_id, status=status, n_passed=n_passed, n_failed=n_failed)


TASKS = [task("c", "failed", 0, 2), task("a", "passed", 2, 0), task("b", "passed", 1, 1), task("d")]


def ids(selected) -> list[str]:
    return [item.task_id for item in selected]


def test_all_filter_keeps_every_task_in_order():
    assert ids(select_task_configs(TASKS, task_filter="all")) == ["a", "b", "c", "d"]


def test_failed_or_mixed_filter():
    assert ids(select_task_configs(TASKS, task_filter="failed_or_mixed")) == ["b", "c"]


def test_named_tasks_win_over_the_filter():
    assert ids(select_task_configs(TASKS, task_filter="failed_or_mixed", task_ids=["a"])) == ["a"]
    with pytest.raises(ValueError, match="unknown task ids"):
        select_task_configs(TASKS, task_filter="all", task_ids=["zzz"])


def test_sampling_is_repeatable():
    first = ids(select_task_configs(TASKS, task_filter="all", sample_n=2, sample_seed=7))
    second = ids(select_task_configs(TASKS, task_filter="all", sample_n=2, sample_seed=7))
    assert first == second and len(first) == 2


def test_agent_commands():
    claude = agent_cli._claude_base_command(build_agent_request("claude", "claude-opus-4-7", add_dirs=["/x"]))
    assert claude == ["claude", "-p", "--verbose", "--dangerously-skip-permissions", "--model", "claude-opus-4-7", "--add-dir", "/x"]
    codex = agent_cli._codex_base_command(build_agent_request("codex", None))
    assert codex == ["codex", "exec", "--yolo"]
    with pytest.raises(AgentCliError, match="unsupported agent_cli"):
        build_agent_request("gemini", None)


def test_missing_agent_binary_has_a_clear_message(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(agent_cli.shutil, "which", lambda name: None)
    with pytest.raises(AgentCliError, match="`cursor-agent` command is not installed"):
        agent_cli.require_agent_binary("cursor")


def test_claude_stream_output_is_parsed():
    raw = "\n".join(
        [
            '{"type": "assistant", "message": {"content": [{"type": "text", "text": "thinking"}]}}',
            '{"type": "result", "result": "done", "structured_output": {"findings": []}}',
        ]
    )
    assert agent_cli._extract_structured_output("claude", raw) == '{"findings": []}'
    assert agent_cli._extract_text_output("claude", raw) == "done"
