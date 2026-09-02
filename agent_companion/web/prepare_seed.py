"""Create a clean guest seed from explicit, character-only sources."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
from typing import Any

from agent_companion.core.character_packages import (
    EXECUTABLE_SUFFIXES,
    MAX_FILES,
    MAX_UNPACKED_BYTES,
    SKIP_BUILTIN_MARKER,
    CharacterPackageError,
    CharacterPackageManager,
)


JOIDEBUG_WEB_CHARACTER_IDS = (
    "momose-hiyori",
    "avatarsample-a",
    "test-mmd-miku",
    "test-tachie-catgirl",
)
DEVELOPMENT_DIRECTORY_NAMES = frozenset({".venv", ".transcribe-venv", "__pycache__", "node_modules"})


def _character_ids(value: str) -> tuple[str, ...]:
    rows = tuple(dict.fromkeys(part.strip() for part in value.split(",") if part.strip()))
    if not rows:
        raise ValueError("at least one character id is required")
    for character_id in rows:
        if (
            len(character_id) > 64
            or not character_id[0].isalnum()
            or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789-" for character in character_id)
        ):
            raise ValueError(f"invalid character id: {character_id}")
    return rows


def _packages_dir(source: Path) -> Path:
    """Accept a package directory, a Joi workspace, or any data root inside it.

    An installed Joi keeps its packages under
    ``<app data>/data/agent_companion/characters/packages``, so the natural
    thing to hand this script is one of four paths depending on how deep the
    person happened to copy from. Accepting only some of them and reporting
    "does not contain Joi character packages" for the rest sends you looking
    for a missing directory that is in fact right there.
    """

    candidates = (
        source,
        source / "data" / "agent_companion" / "characters" / "packages",
        source / "agent_companion" / "characters" / "packages",
        source / "characters" / "packages",
        source / "packages",
    )
    for candidate in candidates:
        if candidate.is_dir() and any(candidate.glob("*/manifest.json")):
            return candidate.resolve()
    looked = "\n  ".join(str(candidate) for candidate in candidates)
    raise ValueError(
        "character source does not contain Joi character packages. Looked for a "
        f"directory holding <id>/manifest.json at:\n  {looked}"
    )


def _read_manifest(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"cannot read character manifest: {path.parent.name}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"invalid character manifest: {path.parent.name}")
    return payload


def _copy_character_packages(
    manager: CharacterPackageManager,
    source: Path,
    character_ids: tuple[str, ...],
) -> list[dict[str, str]]:
    """Copy selected declarative package content, never runtime or tooling.

    Models, motions, expressions, voices, metadata and readmes stay byte exact.
    Development environments and executable helpers are not character content
    and cannot enter a public Core package. Every such source file is recorded
    by package-relative path and hash in the seed report rather than disappearing
    silently.
    """

    omitted_development_files: list[dict[str, str]] = []
    shutil.rmtree(manager.packages_dir)
    manager.packages_dir.mkdir(parents=True)
    for character_id in character_ids:
        package = source / character_id
        manifest_path = package / "manifest.json"
        if not manifest_path.is_file():
            raise ValueError(f"character package does not exist: {character_id}")
        raw = _read_manifest(manifest_path)
        normalized = manager._normalize_manifest(raw)
        if str(normalized.get("id") or "") != character_id:
            raise ValueError(f"character directory and manifest id differ: {character_id}")
        manager._reject_secrets(raw)
        report = manager._appearance_report(normalized, package)
        if report.get("errors"):
            raise CharacterPackageError(
                "invalid_character_assets",
                str(report["errors"][0]),
                details={"character_id": character_id, "asset_report": report},
            )
        destination = manager.packages_dir / character_id
        destination.mkdir(parents=True)
        files = [path for path in package.rglob("*") if path.is_file()]
        if len(files) > MAX_FILES:
            raise CharacterPackageError("package_file_limit", "角色包文件数量超过安全限制。")
        total = 0
        manifest_text = json.dumps(raw, ensure_ascii=False)
        for path in files:
            relative = path.relative_to(package)
            if relative == Path("manifest.json"):
                continue
            development_only = any(part in DEVELOPMENT_DIRECTORY_NAMES for part in relative.parts)
            executable = path.suffix.casefold() in EXECUTABLE_SUFFIXES
            if development_only or executable:
                relative_text = relative.as_posix()
                if relative_text in manifest_text:
                    raise CharacterPackageError(
                        "executable_character_asset_blocked",
                        f"角色清单引用了不可公开执行的文件：{relative_text}",
                    )
                omitted_development_files.append(
                    {
                        "character_id": character_id,
                        "path": relative_text,
                        "sha256": hashlib.sha256(
                            (os.readlink(path).encode("utf-8") if path.is_symlink() else path.read_bytes())
                        ).hexdigest(),
                        "reason": "development environment" if development_only else "executable helper",
                    }
                )
                continue
            if path.is_symlink() or any(part in {"..", ""} for part in relative.parts):
                raise CharacterPackageError("unsafe_package_path", "角色包包含不安全路径。")
            manager._validate_asset_file(path)
            total += path.stat().st_size
            if total > MAX_UNPACKED_BYTES:
                raise CharacterPackageError("package_unpacked_limit", "角色包解压后超过安全大小限制。")
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
        shutil.copy2(manifest_path, destination / "manifest.json")

    # ``CharacterPackageManager`` bootstraps a default role for ordinary Joi
    # workspaces. A public seed instead starts with exactly the selected roles.
    shutil.rmtree(manager.runtime_dir)
    manager.runtime_dir.mkdir(parents=True)
    (manager.root / SKIP_BUILTIN_MARKER).write_text("curated web seed\n", encoding="utf-8")
    manager.activate(character_ids[0])
    return omitted_development_files


def prepare_seed(
    *,
    output: Path,
    guest_config: Path,
    replace: bool = False,
    character_source: Path | None = None,
    character_ids: tuple[str, ...] = JOIDEBUG_WEB_CHARACTER_IDS,
) -> dict[str, Any]:
    repository = Path(__file__).resolve().parents[2]
    output = output.expanduser().resolve()
    config = guest_config.expanduser().resolve()
    if not config.is_file():
        raise ValueError("guest config does not exist")
    if output in {Path("/"), repository, repository.parent}:
        raise ValueError("refusing broad seed output path")
    if output.exists():
        if not replace:
            raise FileExistsError("seed output already exists; pass --replace to replace it")
        shutil.rmtree(output)

    try:
        return _build_seed(repository, output, config, character_source, character_ids)
    except BaseException:
        # A half-written seed is worse than none: it still has a config.yaml and
        # an empty package tree, so the broker starts and every visitor meets a
        # character-less Joi instead of an error anyone would notice.
        shutil.rmtree(output, ignore_errors=True)
        raise


def _build_seed(
    repository: Path,
    output: Path,
    config: Path,
    character_source: Path | None,
    character_ids: tuple[str, ...],
) -> dict[str, Any]:
    (output / "agent_companion" / "config").mkdir(parents=True)
    shutil.copy2(
        repository / "agent_companion" / "config" / "default_character.yaml",
        output / "agent_companion" / "config" / "default_character.yaml",
    )
    widget_assets = repository / "agent_companion" / "web_widget" / "assets"
    destination_assets = output / "agent_companion" / "web_widget" / "assets"
    destination_assets.mkdir(parents=True)
    for source in widget_assets.glob("*.png"):
        shutil.copy2(source, destination_assets / source.name)
    shutil.copy2(config, output / "config.yaml")

    manager = CharacterPackageManager(output)
    selected = ("builtin-hikari",)
    omitted_development_files: list[dict[str, str]] = []
    if character_source is not None:
        packages = _packages_dir(character_source.expanduser().resolve())
        omitted_development_files = _copy_character_packages(manager, packages, character_ids)
        selected = character_ids

    # Bootstrap inputs are not runtime inputs. Session Core instances read the
    # clean package tree under data/ and the root guest config only.
    shutil.rmtree(output / "agent_companion")
    report = {
        "schema": "joi.web_seed_report.v1",
        "character_ids": list(selected),
        "omitted_development_files": omitted_development_files,
    }
    (output / "web-seed-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {"output": str(output), **report}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prepare a clean Joi web guest workspace seed.")
    parser.add_argument("--output", required=True)
    parser.add_argument("--guest-config", required=True)
    parser.add_argument(
        "--character-source",
        help="JoiDebug workspace, data directory, or character packages directory",
    )
    parser.add_argument(
        "--character-ids",
        default=",".join(JOIDEBUG_WEB_CHARACTER_IDS),
        help="Comma-separated package ids copied in full, in initial-display order",
    )
    parser.add_argument("--replace", action="store_true", help="Replace an existing seed directory")
    args = parser.parse_args(argv)

    result = prepare_seed(
        output=Path(args.output),
        guest_config=Path(args.guest_config),
        replace=bool(args.replace),
        character_source=Path(args.character_source) if args.character_source else None,
        character_ids=_character_ids(args.character_ids),
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
