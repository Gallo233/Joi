"""Refuse a deploy that would move this box to a tree it cannot run.

`deploy-joi.sh` moves the checkout with `git reset --hard "origin/$BRANCH"`,
and the branch is a variable with a default. Point it at a branch that predates
Joi Web and the reset still succeeds: the seed step then fails, and the box is
left on a tree whose Core does not understand a single argument the broker
passes it. The site keeps rendering, so nothing looks wrong until a visitor
arrives and cannot open a session.

So the target is checked before anything is written, and not by asking whether
the branch name looks right -- that is exactly what the person typing it already
believed. What is checked is the two contracts that have to hold on the other
side of the reset:

    systemd unit  --flags->  broker      (this box's own configuration)
    broker        --flags->  Core        (one process launching another)

Both sides are read out of the target itself, so this carries no hand-written
list of flags to go stale the first time someone adds one. A flag the unit
passes that the target's broker does not declare, or a flag the target's broker
hands Core that the target's Core does not declare, is a deploy that would come
up broken -- reported here, with the flags named, while the checkout is still
untouched.
"""

from __future__ import annotations

import argparse
import ast
from pathlib import Path
import shlex
import subprocess
import sys


# The files a tree needs before any of this is worth checking. A branch missing
# one of them is not a Joi Web deployment at all, which is the ordinary way this
# goes wrong: a default branch from before the web experience existed.
REQUIRED = (
    "agent_companion/web/broker.py",
    "agent_companion/web/prepare_seed.py",
    "agent_companion/core/server.py",
)
BROKER = "agent_companion/web/broker.py"
CORE = "agent_companion/core/server.py"
BROKER_MODULE = "agent_companion.web.broker"


class TargetError(Exception):
    """Something about the target tree that must stop the deploy."""


def read_from_target(repo: Path, ref: str, path: str) -> str:
    """One file as the target commit has it, without touching the checkout."""

    result = subprocess.run(
        ["git", "-C", str(repo), "show", f"{ref}:{path}"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise TargetError(f"{ref} does not contain {path}")
    return result.stdout


def spawn_flags(source: str) -> set[str]:
    """The flags the broker hands the Core it launches.

    Read from `_spawn_core` rather than from the whole module, because the
    broker's own command line lives in the same file and those flags belong to
    the other contract.
    """

    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.FunctionDef) and node.name == "_spawn_core":
            return {
                child.value
                for child in ast.walk(node)
                if isinstance(child, ast.Constant)
                and isinstance(child.value, str)
                and child.value.startswith("--")
            }
    raise TargetError(f"{BROKER} has no _spawn_core; this tree cannot start a visitor Core")


def declared_flags(source: str) -> set[str]:
    """Every option an `argparse` parser in this module accepts."""

    flags: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        function = node.func
        if not isinstance(function, ast.Attribute) or function.attr != "add_argument":
            continue
        for argument in node.args:
            if isinstance(argument, ast.Constant) and isinstance(argument.value, str) and argument.value.startswith("--"):
                flags.add(argument.value)
    return flags


def unit_flags(text: str) -> set[str]:
    """The options this box's systemd unit passes to the broker.

    Returns an empty set for a unit that starts something else, so a wrong
    `--unit` path degrades to "one contract checked" rather than to a
    confident verdict about a file that was never the broker's.
    """

    lines = text.splitlines()
    for index, line in enumerate(lines):
        if not line.lstrip().startswith("ExecStart="):
            continue
        command = line.split("=", 1)[1]
        while command.rstrip().endswith("\\") and index + 1 < len(lines):
            index += 1
            command = f"{command.rstrip().rstrip(chr(92))} {lines[index]}"
        if BROKER_MODULE not in command:
            return set()
        try:
            tokens = shlex.split(command)
        except ValueError:
            tokens = command.split()
        return {token for token in tokens if token.startswith("--")}
    return set()


def verify(repo: Path, ref: str, unit: Path | None) -> list[str]:
    """Everything wrong with `ref`, or an empty list. Reads nothing but git."""

    problems: list[str] = []
    sources: dict[str, str] = {}
    for path in REQUIRED:
        try:
            sources[path] = read_from_target(repo, ref, path)
        except TargetError as exc:
            problems.append(str(exc))
    if problems:
        # Without the files there is no contract to compare, and saying "and
        # also every flag is missing" would bury the one fact that matters.
        return problems

    core = declared_flags(sources[CORE])
    try:
        handed_to_core = spawn_flags(sources[BROKER])
    except TargetError as exc:
        return [str(exc)]
    missing = sorted(handed_to_core - core)
    if missing:
        problems.append(
            f"{ref} broker starts Core with {' '.join(missing)}, which its own Core does not accept"
        )

    if unit is not None:
        try:
            wanted = unit_flags(unit.read_text(encoding="utf-8"))
        except OSError as exc:
            problems.append(f"cannot read {unit}: {exc.strerror or exc}")
        else:
            if wanted:
                missing_unit = sorted(wanted - declared_flags(sources[BROKER]))
                if missing_unit:
                    problems.append(
                        f"{unit} starts the broker with {' '.join(missing_unit)}, "
                        f"which the broker in {ref} does not accept"
                    )
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=True, help="Git ref the deploy is about to reset to")
    parser.add_argument("--repo", default=".", help="Checkout to read the target out of")
    parser.add_argument("--unit", default="", help="systemd unit that runs the broker on this box")
    args = parser.parse_args(argv)

    unit = Path(args.unit).expanduser() if args.unit else None
    problems = verify(Path(args.repo).expanduser().resolve(), args.target, unit)
    if not problems:
        checked = "broker->Core" + (", unit->broker" if unit is not None else "")
        print(f"    {args.target} can run this deployment ({checked})")
        return 0
    print(f"!! refusing to deploy {args.target}: it cannot run this deployment.", file=sys.stderr)
    for problem in problems:
        print(f"   - {problem}", file=sys.stderr)
    print("   Nothing has been changed. Set JOI_BRANCH to the branch this box runs.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
