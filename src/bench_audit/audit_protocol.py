from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .evidence_collection import ArtifactManifest, EvalConfig, TaskConfig
from .evidence_collection.runner import TaskConfigDirResolver
from .paths import build_agent_add_dirs, resolve_output_root, resource_root

PHASE_ORDER = ["benchmark_audit", "evidence_collection", "task_audit"]
# Older phase names. Rubric files and --start-phase / --end-phase still accept them.
PHASE_ALIASES = {"per_task_evaluation": "task_audit"}
TASK_FILTERS = {"failed_or_mixed", "all"}

__all__ = [
    "PHASE_ALIASES",
    "PHASE_ORDER",
    "CollectionState",
    "GeneralRubric",
    "PhaseDefinition",
    "benchmark_audit_path",
    "build_agent_add_dirs",
    "default_general_rubric_path",
    "load_collection_state",
    "load_general_rubric",
    "load_yaml_mapping",
    "normalize_phase",
    "resolve_output_root",
    "resolve_phase_sequence",
    "resolve_repo_path",
    "task_audit_dir",
]


@dataclass(slots=True)
class PhaseDefinition:
    enabled: bool
    rubric: str | None = None
    output_dir: str | None = None
    output_file: str | None = None
    task_filter: str | None = None
    requires: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PhaseDefinition:
        phase = cls(
            enabled=bool(data.get("enabled", False)),
            rubric=_optional_str(data.get("rubric")),
            output_dir=_optional_str(data.get("output_dir")),
            output_file=_optional_str(data.get("output_file")),
            task_filter=_optional_str(data.get("task_filter")),
            requires=[normalize_phase(str(item)) for item in data.get("requires", [])],
        )
        phase.validate()
        return phase

    def validate(self) -> None:
        if self.task_filter is not None and self.task_filter not in TASK_FILTERS:
            raise ValueError(f"unsupported phase task_filter: {self.task_filter}")
        if self.output_dir is not None:
            output_path = Path(self.output_dir)
            if output_path.is_absolute() or ".." in output_path.parts:
                raise ValueError("phase output_dir must be a relative path inside the audit run")


@dataclass(slots=True)
class GeneralRubric:
    version: str
    phases: dict[str, PhaseDefinition]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GeneralRubric:
        version = str(data.get("version") or "")
        phases = {
            normalize_phase(str(name)): PhaseDefinition.from_dict(phase_data)
            for name, phase_data in (data.get("phases") or {}).items()
        }
        rubric = cls(version=version, phases=phases)
        rubric.validate()
        return rubric

    def validate(self) -> None:
        if not self.version:
            raise ValueError("general rubric version is required")
        missing = [phase for phase in PHASE_ORDER if phase not in self.phases]
        if missing:
            raise ValueError(f"general rubric is missing phases: {', '.join(missing)}")
        for phase_name, phase in self.phases.items():
            phase.validate()
            for requirement in phase.requires:
                if requirement not in self.phases:
                    raise ValueError(f"phase {phase_name} requires unknown phase {requirement}")

    def phase(self, phase_name: str) -> PhaseDefinition:
        phase_name = normalize_phase(phase_name)
        if phase_name not in self.phases:
            raise ValueError(f"unknown phase: {phase_name}")
        return self.phases[phase_name]


def normalize_phase(name: str) -> str:
    """Map an older phase name to the current one."""
    return PHASE_ALIASES.get(name, name)


@dataclass(slots=True)
class CollectionState:
    output_root: Path
    manifest: ArtifactManifest
    task_configs: dict[str, TaskConfig]
    eval_configs: dict[str, list[EvalConfig]]


def resolve_repo_path(path: str | None) -> Path:
    if not path:
        raise ValueError("rubric path is not configured")
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = resource_root() / candidate
    return candidate.resolve()


def default_general_rubric_path() -> Path:
    return resource_root() / "rubrics" / "general_rubric.yaml"


def load_general_rubric(path: str | Path | None = None) -> GeneralRubric:
    target = Path(path or default_general_rubric_path()).resolve()
    data = load_yaml_mapping(target)
    return GeneralRubric.from_dict(data)


def load_yaml_mapping(path: str | Path) -> dict[str, Any]:
    target = Path(path).resolve()
    data = yaml.safe_load(target.read_text()) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{target} must contain a top-level mapping")
    return data


def resolve_phase_sequence(
    general_rubric: GeneralRubric,
    start_phase: str,
    end_phase: str,
) -> list[str]:
    start_phase, end_phase = normalize_phase(start_phase), normalize_phase(end_phase)
    if start_phase not in PHASE_ORDER:
        raise ValueError(f"unsupported start phase: {start_phase}")
    if end_phase not in PHASE_ORDER:
        raise ValueError(f"unsupported end phase: {end_phase}")
    start_index = PHASE_ORDER.index(start_phase)
    end_index = PHASE_ORDER.index(end_phase)
    if start_index > end_index:
        raise ValueError("start_phase must come before end_phase")
    selected = PHASE_ORDER[start_index : end_index + 1]
    for phase_name in selected:
        phase = general_rubric.phase(phase_name)
        if not phase.enabled:
            raise ValueError(f"phase {phase_name} is disabled in the general rubric")
        for requirement in phase.requires:
            if requirement not in selected and PHASE_ORDER.index(requirement) < start_index:
                continue
            if requirement not in selected:
                raise ValueError(f"phase {phase_name} requires phase {requirement}")
    return selected


def load_collection_state(output_root: str | Path) -> CollectionState:
    root = Path(output_root).resolve()
    manifest = ArtifactManifest.load_yaml(root / "artifact_manifest.yaml")
    task_configs: dict[str, TaskConfig] = {}
    eval_configs: dict[str, list[EvalConfig]] = {}
    task_configs_root = root / "task_configs"
    resolver = TaskConfigDirResolver(task_configs_root)
    for task in manifest.tasks:
        task_dir = resolver.resolve(task.task_id)
        task_config = TaskConfig.load_json(task_dir / "task_config.json")
        canonical_task_id = task_config.task_id
        if canonical_task_id in task_configs:
            raise ValueError(f"duplicate task config id in collection state: {canonical_task_id}")
        task_configs[canonical_task_id] = task_config
        eval_configs[canonical_task_id] = [
            EvalConfig.load_json(task_dir / f"{eval_id}.json")
            for eval_id in task.eval_ids
        ]
    return CollectionState(output_root=root, manifest=manifest, task_configs=task_configs, eval_configs=eval_configs)


def benchmark_audit_path(output_root: str | Path, general_rubric: GeneralRubric) -> Path:
    phase = general_rubric.phase("benchmark_audit")
    filename = phase.output_file or "benchmark_audit.json"
    return Path(output_root).resolve() / filename


def task_audit_dir(output_root: str | Path, general_rubric: GeneralRubric, audit_mode: str = "trajectory") -> Path:
    phase = general_rubric.phase("task_audit")
    if not phase.output_dir:
        raise ValueError("task_audit.output_dir is not configured")
    dirname = phase.output_dir if audit_mode == "trajectory" else f"{phase.output_dir}_static"
    return Path(output_root).resolve() / dirname


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
