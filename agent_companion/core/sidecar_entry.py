from __future__ import annotations

import os
from pathlib import Path
import shutil
import sys

from agent_companion.core.main import main


SEED_PATHS: tuple[str, ...] = (
    "config.example.yaml",
    "agent_companion/config",
    "agent_companion/skills",
    "agent_companion/adapters",
    "agent_companion/web_widget/assets",
    "agent_companion/shell/public/live2d/joi",
)
IMMUTABLE_SEED_PREFIXES = ("agent_companion/adapters", "agent_companion/config", "agent_companion/web_widget/assets")


def bundled_root() -> Path | None:
    # Debug shells run the current Python source but still use the same writable
    # application workspace as an installed build. Tauri supplies the source
    # root only as a seed location for missing immutable built-ins.
    root = os.environ.get("JOI_CORE_SEED_ROOT") or getattr(sys, "_MEIPASS", "")
    return Path(root).resolve() if root else None


def seed_installed_workspace(workspace: Path, source_root: Path | None = None) -> None:
    """Copy immutable built-ins into a writable installed workspace.

    User-created configuration, characters, memories and skills are never
    overwritten. PyInstaller extracts the seed files into ``_MEIPASS``; the
    installed Core copies only files that do not already exist.
    """

    source = (source_root or bundled_root())
    if source is None:
        return
    workspace.mkdir(parents=True, exist_ok=True)
    for relative in SEED_PATHS:
        origin = source / relative
        destination = workspace / relative
        if not origin.exists():
            continue
        if origin.is_file():
            destination.parent.mkdir(parents=True, exist_ok=True)
            if not destination.exists():
                shutil.copy2(origin, destination)
            continue
        for item in origin.rglob("*"):
            if not item.is_file():
                continue
            target = destination / item.relative_to(origin)
            immutable = relative.startswith(IMMUTABLE_SEED_PREFIXES)
            if target.exists() and not immutable:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)


def run() -> int:
    if "--workspace" in sys.argv:
        try:
            workspace = Path(sys.argv[sys.argv.index("--workspace") + 1]).expanduser().resolve()
        except (IndexError, OSError):
            workspace = Path.cwd().resolve()
    else:
        workspace = Path(os.environ.get("JOI_DATA_HOME") or Path.cwd()).expanduser().resolve()
    seed_installed_workspace(workspace)
    return main()


if __name__ == "__main__":
    raise SystemExit(run())
