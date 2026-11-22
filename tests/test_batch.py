"""The batch pipeline, with the three agent phases replaced by stand-ins (no cost)."""

from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

from bench_audit import batch, cli
from bench_audit.agent_cli import EXIT_AGENT_ERRORS
from bench_audit.config import load_benchmark_config
from bench_audit.paths import resolve_output_root
from bench_audit.task_audit.sampling import sample_sorted, write_task_sample

from .conftest import write_collection, write_config

TASK_IDS = [f"task-{i:02d}" for i in range(10)]
FAILED_SUMMARY = "Static task audit failed before a valid record was produced: claude timed out after 800s"


@pytest.mark.parametrize(("n", "seed"), [(3, 42), (5, 7), (9, 0)])
def test_sampling_matches_the_old_script(n: int, seed: int):
    """scripts/sample_tasks.py did `random.seed(seed); sorted(random.sample(ids, n))`. Samples must not change."""
    random.seed(seed)
    expected = sorted(random.sample(TASK_IDS, n))
    assert sample_sorted(TASK_IDS, n, seed) == expected


def test_sampling_keeps_everything_when_the_pool_is_small():
    assert sample_sorted(TASK_IDS, 10, 42) == TASK_IDS
    assert sample_sorted(TASK_IDS, 50, 42) == TASK_IDS


class FakePhases:
    """Stands in for the `bench-audit <phase>` subprocesses and writes the files a real phase writes."""

    def __init__(self) -> None:
        self.commands: list[list[str]] = []
        self.fail_on: str | None = None
        self.benchmark_agent_error = False  # the benchmark audit ends as an agent_error
        self.task_agent_errors: set[str] = set()  # these task audits end as an agent_error

    def __call__(self, cmd: list[str], audit_run_dir: str) -> int:
        phase = cmd[3]
        self.commands.append(cmd[3:])
        if phase == self.fail_on:
            return 1
        config = load_benchmark_config(cmd[cmd.index("--config") + 1])
        root = resolve_output_root(config)
        root.mkdir(parents=True, exist_ok=True)
        if phase == "audit-benchmark":
            judgment = "agent_error" if self.benchmark_agent_error else "audit_complete"
            (root / "benchmark_audit.json").write_text(json.dumps({"overall_judgment": judgment, "summary": FAILED_SUMMARY if self.benchmark_agent_error else "ok"}))
            (root / "benchmark_audit_report.md").write_text("report")
            return EXIT_AGENT_ERRORS if self.benchmark_agent_error else 0
        if phase == "collect-evidence":
            write_collection(config, TASK_IDS)
        elif phase == "audit-tasks":
            tasks = cmd[cmd.index("--tasks") + 1 : cmd.index("--mode")]
            (root / "task_audits_static").mkdir(exist_ok=True)
            failed = [task_id for task_id in tasks if task_id in self.task_agent_errors]
            for task_id in tasks:
                judgment = "agent_error" if task_id in failed else "audit_complete"
                (root / "task_audits_static" / f"{task_id}.json").write_text(json.dumps({"overall_judgment": judgment, "summary": FAILED_SUMMARY if task_id in failed else "ok"}))
            return EXIT_AGENT_ERRORS if failed else 0
        return 0


@pytest.fixture
def phases(monkeypatch: pytest.MonkeyPatch) -> FakePhases:
    fake = FakePhases()
    monkeypatch.setattr(batch, "run_subprocess", fake)
    return fake


@pytest.fixture
def bench(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("AUDIT_RUN_DIR", str(tmp_path / "runs"))
    config_path = write_config(tmp_path / "configs" / "multi_domain_all" / "demo_domain")
    for name in ("repo", "data"):
        (config_path.parent / name).mkdir()
        (config_path.parent / name / "file.txt").write_text("x")
    return config_path


def tracker(tmp_path: Path) -> dict:
    return json.loads((tmp_path / "runs" / batch.TRACKER_NAME).read_text())


def test_batch_runs_every_stage_and_cleans_up(tmp_path: Path, bench: Path, phases: FakePhases):
    argv = ["batch", "--config-dir", str(bench.parent.parent), "--max-tasks", "4", "--agent-cli", "codex", "--model", "gpt-5"]
    assert cli.main(argv) == 0

    assert [command[0] for command in phases.commands] == ["audit-benchmark", "collect-evidence", "audit-tasks"]
    for command in phases.commands:
        assert command[-6:] == ["--retry-errors", "1", "--agent-cli", "codex", "--model", "gpt-5"]
    collect = phases.commands[1]
    assert collect[collect.index("--timeout") + 1] == "3600"
    audit_tasks = phases.commands[-1]
    assert audit_tasks[audit_tasks.index("--timeout") + 1] == "1000"
    assert "--rubric-text" not in audit_tasks

    root = tmp_path / "runs" / "static" / "demo__v1__actual"
    sampled = json.loads((root / "sampled_tasks.json").read_text())
    assert sampled["sampled_count"] == 4 and sampled["total_tasks"] == 10 and sampled["seed"] == 42
    assert sorted(p.stem for p in (root / "task_audits_static").glob("*.json")) == sampled["task_ids"]

    (entry,) = tracker(tmp_path)["configs"]
    assert entry["overall"] == "done" and entry["stage_audit_tasks"] == "done" and entry["cleanup"] == "done"
    assert not (bench.parent / "repo").exists() and not (bench.parent / "data").exists()


def test_second_run_skips_finished_stages(tmp_path: Path, bench: Path, phases: FakePhases):
    argv = ["batch", "--config", str(bench), "--max-tasks", "4", "--batch", "3", "--keep"]
    assert cli.main(argv) == 0
    assert tracker(tmp_path)["configs"][0]["stage_audit_tasks"] == "partial"
    assert (bench.parent / "repo").exists()  # --keep

    phases.commands.clear()
    assert cli.main(argv) == 0
    assert [command[0] for command in phases.commands] == ["audit-tasks"]  # the one task that was left
    assert tracker(tmp_path)["configs"][0]["stage_audit_tasks"] == "done"

    phases.commands.clear()
    assert cli.main(argv) == 0
    assert phases.commands == []


def test_failed_phase_marks_the_benchmark_failed(tmp_path: Path, bench: Path, phases: FakePhases, capsys):
    phases.fail_on = "collect-evidence"
    assert cli.main(["batch", "--config", str(bench)]) == 1
    entry = tracker(tmp_path)["configs"][0]
    assert entry["overall"] == "failed" and entry["error"] == "collect_evidence exit=1"
    assert (bench.parent / "repo").exists()  # no cleanup after a failure

    cli.main(["batch", "--status"])
    assert "0/1 done" in capsys.readouterr().out


def test_old_tracker_next_to_the_configs_is_copied(tmp_path: Path, bench: Path, capsys):
    config_dir = bench.parent.parent
    (config_dir / batch.TRACKER_NAME).write_text(json.dumps({"configs": [], "summary": {"total": 0, "done": 0, "in_progress": 0, "failed": 0, "pending": 0, "cleaned": 0, "freed_mb": 0}}))
    assert cli.main(["batch", "--status", "--config-dir", str(config_dir)]) == 0
    assert (tmp_path / "runs" / batch.TRACKER_NAME).is_file()
    assert "0/0 done" in capsys.readouterr().out


def test_task_sample_is_saved_and_reused(tmp_path: Path, bench: Path):
    config = load_benchmark_config(bench)
    root = resolve_output_root(config)
    for task_id in TASK_IDS:
        (root / "task_configs" / task_id).mkdir(parents=True)
        (root / "task_configs" / task_id / "task_config.json").write_text("{}")
    first = write_task_sample(config, max_tasks=3, seed=1)
    assert len(first) == 3
    assert write_task_sample(config, max_tasks=8, seed=2) == first  # the saved sample wins
    assert len(write_task_sample(config, max_tasks=8, seed=2, force=True)) == 8
    assert write_task_sample(config, max_tasks=0, force=True) == TASK_IDS  # 0 means all


def test_shipped_config_folder_is_found():
    configs = batch.discover_configs(None, [], batch.default_config_dir())
    assert len(configs) > 100
    assert len(batch.discover_configs("medical_health", [], batch.default_config_dir())) == 12


def test_failed_task_audits_are_shown_and_tried_again(tmp_path: Path, bench: Path, phases: FakePhases, capsys):
    argv = ["batch", "--config", str(bench), "--max-tasks", "4"]
    root = tmp_path / "runs" / "static" / "demo__v1__actual"

    phases.task_agent_errors = {"task-01", "task-04"}  # seed 42 samples task-00, 01, 04, 08
    assert cli.main(argv) == 1
    entry = tracker(tmp_path)["configs"][0]
    assert entry["overall"] == "failed" and entry["stage_audit_tasks"] == "agent_error"
    assert entry["error"] == "2 of 4 task audits failed"
    assert (bench.parent / "repo").exists()  # the clone is kept for the next attempt
    out = capsys.readouterr().out
    assert "2 of 4 task audits failed" in out and "Run the same command again" in out

    cli.main(["batch", "--status"])
    assert "stage_audit_tasks  2 of 4 task audits failed" in capsys.readouterr().out

    phases.task_agent_errors = set()
    phases.commands.clear()
    assert cli.main(argv) == 0
    (retry,) = phases.commands  # only the task audit runs, and only for the two failed tasks
    assert retry[retry.index("--tasks") + 1 : retry.index("--mode")] == ["task-01", "task-04"]
    entry = tracker(tmp_path)["configs"][0]
    assert entry["overall"] == "done" and entry["error"] is None and entry["cleanup"] == "done"
    assert json.loads((root / "task_audits_static" / "task-01.json").read_text())["overall_judgment"] == "audit_complete"


def test_failed_benchmark_audit_does_not_stop_the_other_stages(tmp_path: Path, bench: Path, phases: FakePhases):
    argv = ["batch", "--config", str(bench), "--max-tasks", "2"]
    phases.benchmark_agent_error = True
    assert cli.main(argv) == 1
    assert [command[0] for command in phases.commands] == ["audit-benchmark", "collect-evidence", "audit-tasks"]
    entry = tracker(tmp_path)["configs"][0]
    assert entry["overall"] == "failed" and entry["error"] == "the benchmark audit failed"
    assert entry["stage_benchmark_audit"] == "agent_error" and entry["stage_audit_tasks"] == "done"
    assert (bench.parent / "repo").exists()

    phases.benchmark_agent_error = False
    phases.commands.clear()
    assert cli.main(argv) == 0
    assert [command[0] for command in phases.commands] == ["audit-benchmark"]
    assert tracker(tmp_path)["configs"][0]["overall"] == "done"


def test_broken_collection_is_collected_again(tmp_path: Path, bench: Path, phases: FakePhases):
    argv = ["batch", "--config", str(bench), "--max-tasks", "2", "--keep"]
    assert cli.main(argv) == 0
    root = tmp_path / "runs" / "static" / "demo__v1__actual"
    (root / "task_configs" / "task-03" / "task_config.json").unlink()

    phases.commands.clear()
    assert cli.main(argv) == 0
    assert [command[0] for command in phases.commands] == ["collect-evidence"]
