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
def local_repo(tmp_path: Path) -> str:
    """The file:// URL of a small git repo on disk. `git clone` of it needs no network."""
    repo = tmp_path / "source" / "Tiny-Bench"
    repo.mkdir(parents=True)
    (repo / "README.md").write_text("# Tiny bench\n")
    git = ["git", "-c", "user.name=test", "-c", "user.email=test@example.com", "-c", "commit.gpgsign=false"]
    subprocess.run([*git, "init", "-q"], cwd=repo, check=True)
    subprocess.run([*git, "add", "."], cwd=repo, check=True)
    subprocess.run([*git, "commit", "-q", "-m", "init"], cwd=repo, check=True)
    return repo.as_uri()  # a URL is cloned; a plain folder path would be copied


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


def test_audit_command_clones_writes_a_config_and_runs_three_phases(tmp_path: Path, local_repo: str, phases):
    assert cli.main(["audit", local_repo, "--sample-n", "3"]) == 0

    bench_dir = tmp_path / "data" / "benchmark_repos" / "tiny_bench"
    assert (bench_dir / "repo" / "README.md").is_file()
    assert (bench_dir / "data").is_dir()

    written = yaml.safe_load((bench_dir / "config.yaml").read_text())
    assert written == {
        "benchmark_name": "tiny_bench",
        "benchmark_type": "static",
        "agent_cli": "claude",
        "model": quick_audit.DEFAULT_MODELS["claude"],
        "output_subdir": "quick",
        "code_url": local_repo,
        "benchmark_data_dir": "data",
        "benchmark_repo_dir": "repo",
        "domain_categories": ["misc"],
    }

    assert [name for name, _, _ in phases] == ["benchmark", "collect", "tasks"]
    _, config, kwargs = phases[-1]
    assert config.benchmark_repo_dir == str((bench_dir / "repo").resolve())
    assert kwargs["audit_mode"] == "static" and kwargs["include_all"] is True and kwargs["sample_n"] == 3


def test_audit_command_options(local_repo: str, phases, tmp_path: Path):
    argv = ["audit", "--url", local_repo, "--name", "tiny", "--agent-cli", "codex", "--skip-benchmark-audit", "--domain", "code_swe"]
    assert cli.main(argv) == 0
    written = yaml.safe_load((tmp_path / "data" / "benchmark_repos" / "tiny" / "config.yaml").read_text())
    assert written["agent_cli"] == "codex"
    assert "model" not in written  # codex picks its own default model
    assert written["domain_categories"] == ["code_swe"]
    assert [name for name, _, _ in phases] == ["collect", "tasks"]


def test_second_run_reuses_the_clone(local_repo: str, phases, capsys: pytest.CaptureFixture[str]):
    cli.main(["audit", local_repo])
    cli.main(["audit", local_repo])
    assert "skipping clone" in capsys.readouterr().err


def test_missing_agent_command_stops_before_the_clone(tmp_path: Path, local_repo: str, monkeypatch: pytest.MonkeyPatch, capsys):
    def missing(agent_cli: str) -> str:
        raise AgentCliError("the `claude` command is not installed or not on PATH.")

    monkeypatch.setattr(quick_audit, "require_agent_binary", missing)
    assert cli.main(["audit", local_repo]) == 1
    assert "not installed" in capsys.readouterr().err
    assert not (tmp_path / "data").exists()


def test_audit_command_needs_a_url(capsys: pytest.CaptureFixture[str]):
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["audit"])
    assert exit_info.value.code == 2
    assert "needs a repository URL" in capsys.readouterr().err


@pytest.fixture
def plain_folder(tmp_path: Path) -> Path:
    """A benchmark folder that is not a git repository, with folders that a copy must leave out."""
    folder = tmp_path / "work" / "My Bench"
    (folder / "tasks").mkdir(parents=True)
    (folder / "tasks" / "t1.json").write_text("{}")
    (folder / ".venv" / "lib").mkdir(parents=True)
    (folder / ".venv" / "lib" / "big.bin").write_text("x")
    return folder


def test_audit_copies_a_local_folder(tmp_path: Path, plain_folder: Path, phases, monkeypatch: pytest.MonkeyPatch, capsys):
    # The data folders are inside the benchmark folder, as in the agent skill. The copy must not copy itself.
    monkeypatch.chdir(plain_folder)
    monkeypatch.setenv("AUDIT_RUN_DIR", str(plain_folder / ".bench-audit" / "runs"))
    monkeypatch.setenv("BENCHMARK_REPOS_DIR", str(plain_folder / ".bench-audit" / "repos"))
    (plain_folder / ".bench-audit" / "runs").mkdir(parents=True)
    (plain_folder / ".bench-audit" / "runs" / "old.json").write_text("{}")

    assert cli.main(["audit", "."]) == 0

    bench_dir = plain_folder / ".bench-audit" / "repos" / "my_bench"
    assert (bench_dir / "repo" / "tasks" / "t1.json").is_file()
    assert not (bench_dir / "repo" / ".venv").exists() and not (bench_dir / "repo" / ".bench-audit").exists()
    written = yaml.safe_load((bench_dir / "config.yaml").read_text())
    assert written["benchmark_name"] == "my_bench" and written["code_url"] == str(plain_folder.resolve())
    assert [name for name, _, _ in phases] == ["benchmark", "collect", "tasks"]
    assert "the audit works on this copy" in capsys.readouterr().err


def test_local_folder_is_copied_again_only_with_force(plain_folder: Path, phases, tmp_path: Path, capsys):
    copy = tmp_path / "data" / "benchmark_repos" / "my_bench" / "repo"
    cli.main(["audit", str(plain_folder)])
    (plain_folder / "tasks" / "t2.json").write_text("{}")

    cli.main(["audit", str(plain_folder)])
    assert not (copy / "tasks" / "t2.json").exists()
    assert "pass --force to copy the folder again" in capsys.readouterr().err

    cli.main(["audit", str(plain_folder), "--force"])
    assert (copy / "tasks" / "t2.json").is_file()


def test_the_copy_itself_is_not_a_valid_target(plain_folder: Path, phases, tmp_path: Path, capsys):
    cli.main(["audit", str(plain_folder)])
    copy = tmp_path / "data" / "benchmark_repos" / "my_bench" / "repo"
    assert cli.main(["audit", str(copy), "--name", "my_bench", "--force"]) == 1
    assert "give the original benchmark folder" in capsys.readouterr().err
    assert (copy / "tasks" / "t1.json").is_file()  # nothing was deleted
