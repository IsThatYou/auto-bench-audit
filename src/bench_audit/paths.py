"""Where bench-audit reads and writes files.

Two environment variables set the data folders. Both have a default, so a
first run works with no setup:

- ``AUDIT_RUN_DIR``: audit outputs. Default ``./data/audit_runs``.
- ``BENCHMARK_REPOS_DIR``: cloned benchmark repos and datasets. Default
  ``./data/benchmark_repos``.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import BenchmarkConfig

DEFAULT_DIRS = {
    "AUDIT_RUN_DIR": "data/audit_runs",
    "BENCHMARK_REPOS_DIR": "data/benchmark_repos",
}

_ENV_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)")
_announced: set[str] = set()


def env_dir(name: str) -> Path:
    """Return the folder that ``name`` points to, or its default under the current folder."""
    if name not in DEFAULT_DIRS:
        raise KeyError(f"{name} has no default folder")
    value = os.environ.get(name, "").strip()
    if value:
        return Path(value).expanduser().resolve()
    path = (Path.cwd() / DEFAULT_DIRS[name]).resolve()
    if name not in _announced:
        _announced.add(name)
        print(f"[bench-audit] {name} is not set; using {path}", file=sys.stderr)
    return path


def expand_env(text: str, source: str | Path | None = None) -> str:
    """Replace ``$NAME`` and ``${NAME}`` in ``text``.

    ``AUDIT_RUN_DIR`` and ``BENCHMARK_REPOS_DIR`` fall back to their defaults.
    Any other variable that is not set is an error. Without the error, a path
    like ``${MY_DIR}/data`` would silently become a folder that is literally
    named ``${MY_DIR}``.
    """

    def replace(match: re.Match[str]) -> str:
        name = match.group(1) or match.group(2)
        value = os.environ.get(name, "").strip()
        if value:
            return value
        if name in DEFAULT_DIRS:
            return str(env_dir(name))
        where = f" (in {source})" if source else ""
        raise ValueError(
            f"environment variable {name} is not set, but {text!r} needs it{where}. "
            f"Set it in your shell or in a .env file."
        )

    return _ENV_REF.sub(replace, text)


def checkout_root() -> Path | None:
    """Return the root of the source checkout, or None for an installed package."""
    root = Path(__file__).resolve().parents[2]
    if (root / "pyproject.toml").is_file() and (root / "rubrics").is_dir():
        return root
    return None


def resource_root() -> Path:
    """Return the folder that holds ``rubrics/``.

    In a source checkout this is the repo root. In an installed package the
    rubrics are shipped inside the package folder.
    """
    return checkout_root() or Path(__file__).resolve().parent


def skill_path() -> Path:
    """Return the agent skill file (``skills/bench-audit/SKILL.md``)."""
    return resource_root() / "skills" / "bench-audit" / "SKILL.md"


def resolve_output_root(config: BenchmarkConfig) -> Path:
    """Return ``<AUDIT_RUN_DIR>/<benchmark_type>/<name>__<output_subdir>__<job_type>``."""
    return (
        env_dir("AUDIT_RUN_DIR")
        / sanitize_dir_part(config.benchmark_type)
        / build_output_dir_name(config)
    ).resolve()


def build_output_dir_name(config: BenchmarkConfig) -> str:
    return "__".join(
        [
            sanitize_dir_part(config.benchmark_name),
            sanitize_dir_part(config.output_subdir),
            sanitize_dir_part(config.job_type),
        ]
    )


def sanitize_dir_part(value: str) -> str:
    sanitized = value.strip().replace(os.sep, "-").replace(" ", "-")
    while "--" in sanitized:
        sanitized = sanitized.replace("--", "-")
    return sanitized or "run"


def build_agent_add_dirs(
    config: BenchmarkConfig,
    output_root: Path,
    *,
    include_repo_dir: bool = True,
) -> list[str]:
    """Return the folders that the agent CLI may read and write (``--add-dir``)."""
    candidates = [str(output_root), config.benchmark_data_dir, config.source_data_dir, config.runs_dir]
    if include_repo_dir:
        candidates.append(config.benchmark_repo_dir)
    add_dirs: list[str] = []
    for candidate in candidates:
        if not candidate:
            continue
        resolved = str(Path(candidate).resolve())
        if resolved not in add_dirs:
            add_dirs.append(resolved)
    return add_dirs
