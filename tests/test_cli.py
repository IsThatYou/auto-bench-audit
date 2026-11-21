from __future__ import annotations

from pathlib import Path

import pytest

from bench_audit import cli
from bench_audit.audit_protocol import PHASE_ORDER, load_general_rubric, resolve_phase_sequence, resolve_repo_path

from .conftest import write_config

COMMANDS = ["audit", "audit-benchmark", "collect-evidence", "audit-tasks", "generate", "run", "batch"]


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, object, dict]]:
    """Replace the three phases with recorders. No agent runs, so the tests cost nothing."""
    seen: list[tuple[str, object, dict]] = []

    def recorder(name: str):
        def record(config, **kwargs):
            seen.append((name, config, kwargs))
            return {}

        return record

    monkeypatch.setattr(cli, "audit_benchmark", recorder("benchmark_audit"))
    monkeypatch.setattr(cli, "collect_evidence", recorder("evidence_collection"))
    monkeypatch.setattr(cli, "audit_tasks", recorder("task_audit"))
    return seen


@pytest.mark.parametrize("command", [None, *COMMANDS])
def test_help_text_exists(command: str | None, capsys: pytest.CaptureFixture[str]):
    argv = [command, "--help"] if command else ["--help"]
    with pytest.raises(SystemExit) as exit_info:
        cli.main(argv)
    assert exit_info.value.code == 0
    out = capsys.readouterr().out
    assert "usage: bench-audit" in out
    if command is None:
        for name in COMMANDS:
            assert name in out


def test_run_goes_through_all_three_phases_by_default(tmp_path: Path, calls):
    assert cli.main(["run", "--config", str(write_config(tmp_path))]) == 0
    assert [name for name, _, _ in calls] == PHASE_ORDER


def test_run_can_stop_early(tmp_path: Path, calls):
    cli.main(["run", "--config", str(write_config(tmp_path)), "--end-phase", "evidence_collection"])
    assert [name for name, _, _ in calls] == PHASE_ORDER[:2]


def test_older_phase_name_still_works(tmp_path: Path, calls):
    cli.main(["run", "--config", str(write_config(tmp_path)), "--start-phase", "per_task_evaluation"])
    assert [name for name, _, _ in calls] == ["task_audit"]


def test_older_phase_name_in_a_rubric_file(tmp_path: Path):
    rubric = tmp_path / "rubric.yaml"
    rubric.write_text(
        "version: '0.2'\n"
        "phases:\n"
        "  benchmark_audit: {enabled: true}\n"
        "  evidence_collection: {enabled: true}\n"
        "  per_task_evaluation: {enabled: true, output_dir: task_audits}\n"
    )
    assert load_general_rubric(rubric).phase("task_audit").output_dir == "task_audits"
    assert load_general_rubric(rubric).phase("per_task_evaluation").output_dir == "task_audits"
    assert load_general_rubric().phase("per_task_evaluation").output_dir == "task_audits"


def test_older_config_names_still_import():
    from bench_audit.config import BenchmarkConfig, load_benchmark_config
    from bench_audit.evidence_collection import EvidenceCollectionConfig, load_collection_config
    from bench_audit.evidence_collection.config import EvidenceCollectionConfig as from_old_module

    assert EvidenceCollectionConfig is BenchmarkConfig is from_old_module
    assert load_collection_config is load_benchmark_config


def test_model_flag_replaces_the_config_model(tmp_path: Path, calls):
    cli.main(["audit-tasks", "--config", str(write_config(tmp_path)), "--model", "claude-sonnet-4-6"])
    (_, config, _), = calls
    assert (config.agent_cli, config.model) == ("claude", "claude-sonnet-4-6")


def test_agent_flag_drops_the_other_agents_model(tmp_path: Path, calls, capsys: pytest.CaptureFixture[str]):
    cli.main(["collect-evidence", "--config", str(write_config(tmp_path)), "--agent-cli", "codex"])
    (_, config, _), = calls
    assert (config.agent_cli, config.model) == ("codex", None)
    assert "ignoring the config's model" in capsys.readouterr().err


def test_agent_and_model_flags_together(tmp_path: Path, calls):
    cli.main(["audit-benchmark", "--config", str(write_config(tmp_path)), "--agent-cli", "codex", "--model", "gpt-5"])
    (_, config, _), = calls
    assert (config.agent_cli, config.model) == ("codex", "gpt-5")


def test_task_audit_flags_reach_the_phase(tmp_path: Path, calls):
    cli.main(["audit-tasks", "--config", str(write_config(tmp_path)), "--mode", "static", "--sample-n", "3", "--max-workers", "2"])
    (_, _, kwargs), = calls
    assert kwargs["audit_mode"] == "static" and kwargs["sample_n"] == 3 and kwargs["max_workers"] == 2


def test_tasks_and_all_cannot_be_combined(tmp_path: Path, calls):
    with pytest.raises(SystemExit):
        cli.main(["audit-tasks", "--config", str(write_config(tmp_path)), "--all", "--tasks", "a"])
    assert calls == []


def test_config_errors_are_one_clean_line(tmp_path: Path, calls, capsys: pytest.CaptureFixture[str]):
    path = write_config(tmp_path, benchmark_data_dir="${NOT_SET_ANYWHERE}/data")
    assert cli.main(["collect-evidence", "--config", str(path)]) == 1
    assert "bench-audit: error: environment variable NOT_SET_ANYWHERE is not set" in capsys.readouterr().err
    assert calls == []


def test_env_file_is_read_like_a_shell_reads_it():
    values = cli.parse_env_text(
        """
        # a comment
        PLAIN=value
        DOUBLE="two words"
        SINGLE='x=y'
        export EXPORTED=yes
        TRAILING=abc # note
        EMPTY=
        not a pair
        """
    )
    assert values == {"PLAIN": "value", "DOUBLE": "two words", "SINGLE": "x=y", "EXPORTED": "yes", "TRAILING": "abc", "EMPTY": ""}


def test_env_file_does_not_replace_a_set_variable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    (tmp_path / ".env").write_text("AUDIT_RUN_DIR=from-file\nBENCHMARK_REPOS_DIR=from-file\n")
    monkeypatch.setenv("AUDIT_RUN_DIR", "from-shell")
    monkeypatch.setattr(cli, "checkout_root", lambda: None)
    cli.load_env_files()
    import os

    assert os.environ["AUDIT_RUN_DIR"] == "from-shell"
    assert os.environ["BENCHMARK_REPOS_DIR"] == "from-file"


def test_rubrics_are_found():
    rubric = load_general_rubric()
    assert resolve_phase_sequence(rubric, PHASE_ORDER[0], PHASE_ORDER[-1]) == PHASE_ORDER
    for phase in ("benchmark_audit", "task_audit"):
        assert resolve_repo_path(rubric.phase(phase).rubric).is_file()
