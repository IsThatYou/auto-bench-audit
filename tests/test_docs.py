"""Every command shown in the docs must be a command that exists.

This catches a README that points to a renamed flag or a file that is gone.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path

import pytest

from bench_audit import cli

from .conftest import REPO_ROOT

DOCS = ["README.md", "configs/README.md", "skills/bench-audit/SKILL.md"]
PATH_FLAGS = ("--config", "--harbor-config", "--general-rubric", "--rubric-text")
SCRIPTS = {"scripts/audit_one.sh": "audit", "scripts/batch_static_audit_pipeline.sh": "batch"}


def shell_commands(doc: str) -> list[str]:
    text = (REPO_ROOT / doc).read_text()
    commands = []
    for block in re.findall(r"```bash\n(.*?)```", text, flags=re.DOTALL):
        for line in block.replace("\\\n", " ").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                commands.append(line)
    return commands


def bench_audit_argv(command: str) -> list[str] | None:
    """Return the bench-audit arguments of a doc command, or None for another kind of command."""
    words = shlex.split(command, comments=True)
    if words[:2] == ["uv", "run"]:
        words = words[2:]
    if words[:2] == ["uvx", "--from"]:
        words = words[3:]
    if words and words[0] == "bench-audit":
        return words[1:]
    if words[:1] == ["bash"]:
        words = words[1:]
    if words and words[0] in SCRIPTS:
        assert (REPO_ROOT / words[0]).is_file(), f"{words[0]} does not exist"
        return [SCRIPTS[words[0]], *words[1:]]
    return None


COMMANDS = [(doc, command) for doc in DOCS for command in shell_commands(doc)]


def test_the_docs_show_commands():
    assert sum(bench_audit_argv(command) is not None for _, command in COMMANDS) >= 15


@pytest.mark.parametrize(("doc", "command"), COMMANDS, ids=[f"{doc}: {command[:60]}" for doc, command in COMMANDS])
def test_doc_command_is_valid(doc: str, command: str):
    argv = bench_audit_argv(command)
    if argv is None:
        pytest.skip("not a bench-audit command")
    if "--help" in argv:
        with pytest.raises(SystemExit) as exit_info:
            cli.build_parser().parse_args(argv)
        assert exit_info.value.code == 0
        return
    args = cli.build_parser().parse_args(argv)  # exits with an error for an unknown command or flag
    for flag in PATH_FLAGS:
        value = getattr(args, flag.lstrip("-").replace("-", "_"), None)
        for path in value if isinstance(value, list) else [value]:
            # collection_<name>.yaml is written by `bench-audit generate`, so it is not in the repo.
            # A path with <name> is a placeholder for a file that the reader's own run writes.
            if path and "collection_" not in Path(path).name and "<" not in path:
                assert (REPO_ROOT / path).is_file(), f"{doc} points to {path}, which does not exist"
