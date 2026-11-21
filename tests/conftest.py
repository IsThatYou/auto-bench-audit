from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Each test starts with no data-folder variables and an empty working folder."""
    for name in ("AUDIT_RUN_DIR", "BENCHMARK_REPOS_DIR"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    from bench_audit import paths

    paths._announced.clear()
    yield


def write_config(folder: Path, **overrides) -> Path:
    import yaml

    data = {
        "benchmark_name": "demo",
        "benchmark_type": "static",
        "agent_cli": "claude",
        "model": "claude-opus-4-7",
        "output_subdir": "v1",
        "benchmark_data_dir": "data",
        "benchmark_repo_dir": "repo",
    }
    data.update(overrides)
    data = {key: value for key, value in data.items() if value is not None}
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "config.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    return path
