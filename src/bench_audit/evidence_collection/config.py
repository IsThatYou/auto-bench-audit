from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from ..paths import expand_env

# "static" is a benchmark that is audited from its task definitions only.
# "neurips" is the older name for the same thing. Old configs still use it,
# and it is part of their output path, so both names stay valid.
STATIC_TYPES = {"static", "neurips"}
BENCHMARK_TYPES = {"tb2", "swe_bench"} | STATIC_TYPES
REQUIRED_FIELDS = ("benchmark_name", "benchmark_type", "agent_cli", "output_subdir", "benchmark_data_dir")


@dataclass(slots=True)
class EvidenceCollectionConfig:
    benchmark_name: str
    benchmark_type: str
    agent_cli: str
    output_subdir: str
    benchmark_data_dir: str
    jobs_type: str = "actual"
    model: str | None = None
    jobs_dir: str | None = None
    results_dir: str | None = None
    runs_dir: str | None = None
    api_key_env: str | None = None
    fetch_test_sources: bool = False
    # Phase 0 — benchmark audit fields (optional)
    benchmark_repo_dir: str | None = None
    paper_path: str | None = None
    domain_categories: list[str] | None = None
    dataset_id: str | None = None
    code_url: str | None = None
    data_acquisition_hint: str | None = None

    def validate(self) -> None:
        if not self.benchmark_name:
            raise ValueError("benchmark_name is required")
        if self.benchmark_type not in BENCHMARK_TYPES:
            raise ValueError("benchmark_type must be one of: static, tb2, swe_bench (neurips is an older name for static)")
        if not self.agent_cli:
            raise ValueError("agent_cli is required")
        if not self.output_subdir.strip():
            raise ValueError("output_subdir is required")
        if Path(self.output_subdir).is_absolute():
            raise ValueError("output_subdir must be a relative path")
        if ".." in Path(self.output_subdir).parts:
            raise ValueError("output_subdir must not contain '..'")
        if not self.benchmark_data_dir:
            raise ValueError("benchmark_data_dir is required")
        if self.jobs_type not in {"actual", "oracle"}:
            raise ValueError("jobs_type must be either 'actual' or 'oracle'")
        if self.benchmark_type == "tb2" and not self.jobs_dir:
            raise ValueError("jobs_dir is required for tb2 configs")
        if self.benchmark_type == "swe_bench" and not self.results_dir:
            raise ValueError("results_dir is required for swe_bench configs")

    @property
    def job_type(self) -> str:
        return self.jobs_type

    @property
    def is_static(self) -> bool:
        return self.benchmark_type in STATIC_TYPES

    @property
    def source_data_dir(self) -> str:
        if self.benchmark_type == "tb2":
            return str(self.jobs_dir)
        if self.is_static:
            return str(self.benchmark_data_dir)
        return str(self.results_dir)


def load_collection_config(path: str | Path) -> EvidenceCollectionConfig:
    config_path = Path(path).resolve()
    data = yaml.safe_load(config_path.read_text()) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{config_path} must contain a top-level mapping")
    missing = [name for name in REQUIRED_FIELDS if data.get(name) in (None, "")]
    if missing:
        raise ValueError(f"{config_path} is missing required field(s): {', '.join(missing)}")

    config = EvidenceCollectionConfig(
        benchmark_name=str(data["benchmark_name"]),
        benchmark_type=str(data["benchmark_type"]),
        agent_cli=str(data["agent_cli"]),
        output_subdir=str(data["output_subdir"]),
        benchmark_data_dir=_resolve_path(config_path, data["benchmark_data_dir"]),
        jobs_type=str(data.get("jobs_type", "actual")),
        model=str(data["model"]) if data.get("model") is not None else None,
        jobs_dir=_resolve_optional_path(config_path, data.get("jobs_dir")),
        results_dir=_resolve_optional_path(config_path, data.get("results_dir")),
        runs_dir=_resolve_optional_path(config_path, data.get("runs_dir")),
        api_key_env=str(data["api_key_env"]) if data.get("api_key_env") else None,
        fetch_test_sources=bool(data.get("fetch_test_sources", False)),
        benchmark_repo_dir=_resolve_optional_path(config_path, data.get("benchmark_repo_dir")),
        paper_path=_resolve_optional_path(config_path, data.get("paper_path")),
        domain_categories=data.get("domain_categories"),
        dataset_id=str(data["dataset_id"]) if data.get("dataset_id") else None,
        code_url=str(data["code_url"]) if data.get("code_url") else None,
        data_acquisition_hint=str(data["data_acquisition_hint"]) if data.get("data_acquisition_hint") else None,
    )
    config.validate()
    return config


def _resolve_optional_path(config_path: Path, value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return _resolve_path(config_path, text)


def _resolve_path(config_path: Path, value: object) -> str:
    """Expand environment variables, then resolve a relative path against the config's folder."""
    candidate = Path(expand_env(str(value), source=config_path)).expanduser()
    if not candidate.is_absolute():
        candidate = config_path.parent / candidate
    return str(candidate.resolve())
