from __future__ import annotations

from pathlib import Path

import pytest

from bench_audit.config import load_benchmark_config
from bench_audit.paths import build_agent_add_dirs, resolve_output_root

from .conftest import REPO_ROOT, write_config

SHIPPED_CONFIGS = sorted((REPO_ROOT / "configs" / "multi_domain_all").glob("*/*.yaml"))


def test_relative_paths_resolve_against_the_config_folder(tmp_path: Path):
    config = load_benchmark_config(write_config(tmp_path / "bench"))
    assert config.benchmark_data_dir == str((tmp_path / "bench" / "data").resolve())
    assert config.benchmark_repo_dir == str((tmp_path / "bench" / "repo").resolve())


def test_benchmark_repos_dir_has_a_default(tmp_path: Path):
    path = write_config(tmp_path, benchmark_data_dir="${BENCHMARK_REPOS_DIR}/demo/data")
    config = load_benchmark_config(path)
    assert config.benchmark_data_dir == str((tmp_path / "data" / "benchmark_repos" / "demo" / "data").resolve())


def test_benchmark_repos_dir_from_the_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("BENCHMARK_REPOS_DIR", str(tmp_path / "elsewhere"))
    path = write_config(tmp_path, benchmark_data_dir="${BENCHMARK_REPOS_DIR}/demo/data")
    assert load_benchmark_config(path).benchmark_data_dir == str((tmp_path / "elsewhere" / "demo" / "data").resolve())


def test_unset_variable_is_an_error_not_a_literal_folder(tmp_path: Path):
    path = write_config(tmp_path, benchmark_data_dir="${NOT_SET_ANYWHERE}/demo/data")
    with pytest.raises(ValueError, match="NOT_SET_ANYWHERE is not set"):
        load_benchmark_config(path)


def test_missing_field_is_named(tmp_path: Path):
    path = write_config(tmp_path, agent_cli=None)
    with pytest.raises(ValueError, match="missing required field.*agent_cli"):
        load_benchmark_config(path)


@pytest.mark.parametrize("name", ["static", "neurips"])
def test_static_and_its_old_name(tmp_path: Path, name: str):
    config = load_benchmark_config(write_config(tmp_path, benchmark_type=name))
    assert config.is_static
    assert config.source_data_dir == config.benchmark_data_dir


def test_unknown_benchmark_type_is_rejected(tmp_path: Path):
    with pytest.raises(ValueError, match="benchmark_type must be one of"):
        load_benchmark_config(write_config(tmp_path, benchmark_type="webarena"))


def test_trajectory_types_need_their_run_folder(tmp_path: Path):
    with pytest.raises(ValueError, match="jobs_dir is required"):
        load_benchmark_config(write_config(tmp_path, benchmark_type="tb2"))
    config = load_benchmark_config(write_config(tmp_path, benchmark_type="tb2", jobs_dir="jobs"))
    assert not config.is_static


def test_output_root_default_and_layout(tmp_path: Path):
    config = load_benchmark_config(write_config(tmp_path, benchmark_name="my bench", output_subdir="v1"))
    assert resolve_output_root(config) == (tmp_path / "data" / "audit_runs" / "static" / "my-bench__v1__actual").resolve()


def test_output_root_keeps_the_old_type_in_the_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AUDIT_RUN_DIR", str(tmp_path / "runs"))
    config = load_benchmark_config(write_config(tmp_path, benchmark_type="neurips"))
    assert resolve_output_root(config) == (tmp_path / "runs" / "neurips" / "demo__v1__actual").resolve()


def test_agent_add_dirs_have_no_duplicates(tmp_path: Path):
    config = load_benchmark_config(write_config(tmp_path))
    output_root = tmp_path / "out"
    with_repo = build_agent_add_dirs(config, output_root)
    without_repo = build_agent_add_dirs(config, output_root, include_repo_dir=False)
    assert with_repo == [str(output_root.resolve()), config.benchmark_data_dir, config.benchmark_repo_dir]
    assert without_repo == [str(output_root.resolve()), config.benchmark_data_dir]


def test_there_are_shipped_configs():
    assert len(SHIPPED_CONFIGS) > 100


# Nine configs audit recorded runs. They also need these folders (see .env.example).
RUN_FOLDER_VARIABLES = ("HARBOR_DATASETS_DIR", "TB2_JOBS_DIR", "SWEBENCH_DIR")


@pytest.mark.parametrize("path", SHIPPED_CONFIGS, ids=lambda p: f"{p.parent.name}/{p.stem}")
def test_every_shipped_config_loads(path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    for name in RUN_FOLDER_VARIABLES:
        monkeypatch.setenv(name, str(tmp_path / name.lower()))
    config = load_benchmark_config(path)
    assert config.agent_cli in {"claude", "codex", "cursor"}
    for value in (config.benchmark_data_dir, config.benchmark_repo_dir, config.jobs_dir, config.results_dir):
        assert value is None or "$" not in value


def test_static_configs_need_no_setup():
    """Without any variable set, every static config loads. The others name the variable they need."""
    need_setup = []
    for path in SHIPPED_CONFIGS:
        try:
            assert load_benchmark_config(path).is_static
        except ValueError as error:
            assert any(f"{name} is not set" in str(error) for name in RUN_FOLDER_VARIABLES), error
            need_setup.append(path.stem)
    assert len(need_setup) == 9
