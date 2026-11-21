from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

from bench_audit import cli, quick_audit
from bench_audit.agent_cli import AgentCliError


@pytest.mark.parametrize(
    ("url", "name"),
    [
        ("https://github.com/centerforaisafety/hle", "hle"),
        ("https://github.com/harbor-framework/terminal-bench-2", "terminal_bench_2"),
        ("https://github.com/Org/Some.Bench.git", "some_bench"),
        ("git@github.com:org/tau2-bench.git/", "tau2_bench"),
    ],
)
def test_name_from_url(url: str, name: str):
    assert quick_audit.derive_name(url) == name


def test_name_that_cannot_be_derived():
    with pytest.raises(ValueError, match="pass --name"):
        quick_audit.derive_name("https://example.com/---")


@pytest.fixture
def local_repo(tmp_path: Path) -> Path:
    """A small git repo on disk. `git clone` of a local path needs no network."""
    repo = tmp_path / "source" / "Tiny-Bench"
    repo.mkdir(parents=True)
    (repo / "README.md").write_text("# Tiny bench\n")
    git = ["git", "-c", "user.name=test", "-c", "user.email=test@example.com", "-c", "commit.gpgsign=false"]
    subprocess.run([*git, "init", "-q"], cwd=repo, check=True)
    subprocess.run([*git, "add", "."], cwd=repo, check=True)
    subprocess.run([*git, "commit", "-q", "-m", "init"], cwd=repo, check=True)
    return repo


@pytest.fixture
def phases(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, object, dict]]:
    seen: list[tuple[str, object, dict]] = []

    def recorder(name: str):
        def record(config, **kwargs):
            seen.append((name, config, kwargs))
            return {}

        return record

    monkeypatch.setattr(quick_audit, "audit_benchmark", recorder("benchmark"))
    monkeypatch.setattr(quick_audit, "collect_evidence", recorder("collect"))
    monkeypatch.setattr(quick_audit, "audit_tasks", recorder("tasks"))
    monkeypatch.setattr(quick_audit, "require_agent_binary", lambda agent_cli: f"/fake/{agent_cli}")
    return seen


def test_audit_command_clones_writes_a_config_and_runs_three_phases(tmp_path: Path, local_repo: Path, phases):
    assert cli.main(["audit", str(local_repo), "--sample-n", "3"]) == 0

    bench_dir = tmp_path / "data" / "benchmark_repos" / "tiny_bench"
    assert (bench_dir / "repo" / "README.md").is_file()
    assert (bench_dir / "data").is_dir()

    written = yaml.safe_load((bench_dir / "config.yaml").read_text())
    assert written == {
        "benchmark_name": "tiny_bench",
        "benchmark_type": "static",
        "agent_cli": "claude",
        "model": "claude-opus-4-7",
        "output_subdir": "quick",
        "code_url": str(local_repo),
        "benchmark_data_dir": "data",
        "benchmark_repo_dir": "repo",
        "domain_categories": ["misc"],
    }

    assert [name for name, _, _ in phases] == ["benchmark", "collect", "tasks"]
    _, config, kwargs = phases[-1]
    assert config.benchmark_repo_dir == str((bench_dir / "repo").resolve())
    assert kwargs["audit_mode"] == "static" and kwargs["include_all"] is True and kwargs["sample_n"] == 3


def test_audit_command_options(local_repo: Path, phases, tmp_path: Path):
    argv = ["audit", "--url", str(local_repo), "--name", "tiny", "--agent-cli", "codex", "--skip-benchmark-audit", "--domain", "code_swe"]
    assert cli.main(argv) == 0
    written = yaml.safe_load((tmp_path / "data" / "benchmark_repos" / "tiny" / "config.yaml").read_text())
    assert written["agent_cli"] == "codex"
    assert "model" not in written  # codex picks its own default model
    assert written["domain_categories"] == ["code_swe"]
    assert [name for name, _, _ in phases] == ["collect", "tasks"]


def test_second_run_reuses_the_clone(local_repo: Path, phases, capsys: pytest.CaptureFixture[str]):
    cli.main(["audit", str(local_repo)])
    cli.main(["audit", str(local_repo)])
    assert "skipping clone" in capsys.readouterr().err


def test_missing_agent_command_stops_before_the_clone(tmp_path: Path, local_repo: Path, monkeypatch: pytest.MonkeyPatch, capsys):
    def missing(agent_cli: str) -> str:
        raise AgentCliError("the `claude` command is not installed or not on PATH.")

    monkeypatch.setattr(quick_audit, "require_agent_binary", missing)
    assert cli.main(["audit", str(local_repo)]) == 1
    assert "not installed" in capsys.readouterr().err
    assert not (tmp_path / "data").exists()


def test_audit_command_needs_a_url(capsys: pytest.CaptureFixture[str]):
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["audit"])
    assert exit_info.value.code == 2
    assert "needs a repository URL" in capsys.readouterr().err
